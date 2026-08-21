"""PII detection guardrail (Plan 11, Phase B — task_11_04).

Registers the ``pii`` guardrail type. It scans the hook's primary text
for personally identifiable information (emails, phone numbers, credit
cards, national IDs, IBANs, IPs, names, ...) at ``pre_llm`` (the inbound
prompt) and ``post_llm`` (the model output).

Backend strategy (heavy/model-dep honesty — Presidio precedent)
---------------------------------------------------------------
Microsoft Presidio (``presidio-analyzer``, which in turn pulls spaCy +
an NER model) is the high-quality engine, but it is a multi-hundred-MB
dependency. It is therefore an OPTIONAL extra (``shared-guardrails[pii]``)
imported LAZILY, never a base dependency. The guardrail picks a backend
per ``check`` according to its configured ``backend``:

  * ``auto`` (default) — use Presidio if importable, else fall back to the
    built-in high-confidence regex detector.
  * ``presidio``       — require Presidio; if it (or its spaCy model) is
    absent, degrade to a typed *unavailable* result (``triggered=False``,
    ``payload={"available": False, ...}``) instead of crashing.
  * ``regex``          — always use the pure-Python regex detector
    (no heavy dependency, works everywhere incl. CI).

The regex detector covers the high-confidence, structurally-verifiable
patterns (email / credit card with a Luhn check / phone / IBAN / IPv4 /
US SSN). Presidio additionally finds context-dependent entities (PERSON,
LOCATION, ...) via its NER model. Either way the result reports the set
of detected entity types and the matched (raw) spans so the host's
``redact`` action can mask them.

The detection here is pure (no I/O beyond the lazy import). The engine
applies the action; this module only *suggests* one — configurable, and
defaulting to ``redact`` on ``post_llm`` / ``block`` on ``pre_llm`` per
the plan baseline (PII must never leak to an external LLM, and model
output is masked before it reaches logs / the user).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# --------------------------------------------------------------------------- #
# Detected-entity record                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PiiMatch:
    """One detected PII span.

    ``entity_type`` uses Presidio's vocabulary (``EMAIL_ADDRESS``,
    ``CREDIT_CARD``, ``PHONE_NUMBER``, ``IBAN_CODE``, ``IP_ADDRESS``,
    ``US_SSN``, ``PERSON``, ...) so the regex fallback and the Presidio
    backend produce comparable output.
    """

    entity_type: str
    text: str
    start: int
    end: int
    score: float


# --------------------------------------------------------------------------- #
# Backend protocol — both detectors satisfy it                                #
# --------------------------------------------------------------------------- #


@runtime_checkable
class PiiAnalyzer(Protocol):
    """A PII detector: text in, matches out. Pure, no I/O."""

    def analyze(self, text: str, entities: list[str] | None) -> list[PiiMatch]: ...


# --------------------------------------------------------------------------- #
# Pure-Python high-confidence regex detector (always available)               #
# --------------------------------------------------------------------------- #

# Email: deliberately conservative (one @, a dotted domain). High signal.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)
# Credit-card-shaped digit runs (13-19 digits, optional space/dash groups).
# Confirmed with a Luhn check below to keep false positives down.
_CARD_RE = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
# IBAN: 2 letters + 2 check digits + up to 30 alnum.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
# IPv4.
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
)
# US SSN: NNN-NN-NNNN.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Phone numbers: optional +country, then 9-15 digits with common separators.
# Requires at least one separator OR a leading + to avoid matching plain
# long integers (those are handled by the card detector).
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[ \-.]?)?(?:\(?\d{2,4}\)?[ \-.]){1,4}\d{2,4}(?!\w)",
)


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — weeds out random 16-digit runs."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class RegexPiiAnalyzer:
    """Lightweight, dependency-free detector for high-confidence PII.

    Covers the structurally-verifiable entity types that do not need an
    NER model. Used as the ``auto`` fallback when Presidio is absent and
    as the ``regex`` backend. Deterministic and importable everywhere.
    """

    # entity_type -> (compiled regex, base confidence score)
    _PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
        ("EMAIL_ADDRESS", _EMAIL_RE, 1.0),
        ("IBAN_CODE", _IBAN_RE, 0.9),
        ("US_SSN", _SSN_RE, 0.85),
        ("IP_ADDRESS", _IPV4_RE, 0.6),
    )

    def analyze(self, text: str, entities: list[str] | None) -> list[PiiMatch]:
        wanted = set(entities) if entities else None
        out: list[PiiMatch] = []

        def _want(entity_type: str) -> bool:
            return wanted is None or entity_type in wanted

        for entity_type, pattern, score in self._PATTERNS:
            if not _want(entity_type):
                continue
            for m in pattern.finditer(text):
                out.append(PiiMatch(entity_type, m.group(0), m.start(), m.end(), score))

        # Credit cards — regex shape then Luhn confirmation.
        if _want("CREDIT_CARD"):
            for m in _CARD_RE.finditer(text):
                raw = m.group(0)
                digits = re.sub(r"\D", "", raw)
                if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                    out.append(PiiMatch("CREDIT_CARD", raw, m.start(), m.end(), 0.95))

        # Phone numbers — only on spans not already claimed by a stronger
        # entity (card / IBAN) to avoid double counting.
        if _want("PHONE_NUMBER"):
            claimed = [(p.start, p.end) for p in out]
            for m in _PHONE_RE.finditer(text):
                s, e = m.start(), m.end()
                if any(s < ce and e > cs for cs, ce in claimed):
                    continue
                digits = re.sub(r"\D", "", m.group(0))
                if 9 <= len(digits) <= 15:
                    out.append(PiiMatch("PHONE_NUMBER", m.group(0), s, e, 0.7))

        out.sort(key=lambda p: p.start)
        return out


# --------------------------------------------------------------------------- #
# Lazy Presidio backend                                                       #
# --------------------------------------------------------------------------- #


class PresidioUnavailableError(RuntimeError):
    """Presidio (or its spaCy model) could not be loaded."""


def presidio_available() -> bool:
    """Whether ``presidio-analyzer`` can be imported in this environment.

    Cheap, lazy import-probe — does not instantiate the (expensive)
    ``AnalyzerEngine`` / load the NER model. Used by the ``auto`` backend
    to decide whether to prefer Presidio.
    """
    try:
        import presidio_analyzer  # noqa: F401
    except Exception:
        return False
    return True


class PresidioPiiAnalyzer:
    """Adapter over Presidio's ``AnalyzerEngine`` (lazy, optional).

    Constructing it imports + builds the engine, which loads the spaCy
    NER model — heavy, hence lazy. Raises :class:`PresidioUnavailableError`
    when Presidio or its model is missing so the guardrail can degrade to
    a typed *unavailable* result instead of crashing.
    """

    def __init__(self, language: str = "en", engine: Any | None = None) -> None:
        self._language = language
        if engine is not None:
            self._engine = engine
            return
        try:
            from presidio_analyzer import AnalyzerEngine
        except Exception as exc:  # pragma: no cover - env without the extra
            raise PresidioUnavailableError(
                "presidio-analyzer is not installed. Install the "
                "'shared-guardrails[pii]' extra to enable the Presidio backend."
            ) from exc
        try:
            self._engine = AnalyzerEngine()
        except Exception as exc:  # pragma: no cover - model not downloaded
            raise PresidioUnavailableError(
                "Presidio AnalyzerEngine could not start (the spaCy model is "
                "likely missing — run `python -m spacy download en_core_web_lg`)."
            ) from exc

    def analyze(self, text: str, entities: list[str] | None) -> list[PiiMatch]:
        results = self._engine.analyze(text=text, entities=entities, language=self._language)
        out: list[PiiMatch] = []
        for r in results:
            out.append(
                PiiMatch(
                    entity_type=str(r.entity_type),
                    text=text[r.start : r.end],
                    start=int(r.start),
                    end=int(r.end),
                    score=float(r.score),
                )
            )
        out.sort(key=lambda p: p.start)
        return out


# --------------------------------------------------------------------------- #
# Config coercion helpers (mirroring builtins.py shape)                       #
# --------------------------------------------------------------------------- #


def _coerce_severity(value: Any, default: Severity = Severity.HIGH) -> Severity:
    if value is None:
        return default
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid severity {value!r}.") from exc


def _coerce_action(value: Any) -> Action | None:
    if value is None:
        return None
    if isinstance(value, Action):
        return value
    try:
        return Action(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid action {value!r}.") from exc


_VALID_BACKENDS = ("auto", "presidio", "regex")


# --------------------------------------------------------------------------- #
# The guardrail                                                               #
# --------------------------------------------------------------------------- #


class PiiGuardrail:
    """Detects PII in the hook's primary text (``pre_llm`` / ``post_llm``).

    Config:
      - ``backend``           ``auto`` | ``presidio`` | ``regex`` (default
        ``auto``: Presidio when importable, else the regex fallback).
      - ``entities``          optional list[str] to restrict the entity
        types looked for (Presidio vocabulary). Unset = all known.
      - ``language``          Presidio language code (default ``en``).
      - ``min_score``         drop matches below this confidence
        (default ``0.4``).
      - ``severity``          default ``high``.
      - ``suggested_action``  override the per-hook default. When unset
        the guardrail suggests ``redact`` for ``post_llm`` and ``block``
        for ``pre_llm`` (the plan baseline); other hooks default to
        ``redact``.
      - ``analyzer``          (testing hook) inject a pre-built analyzer
        implementing :class:`PiiAnalyzer`, bypassing backend selection.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        backend = str(config.get("backend", "auto")).lower()
        if backend not in _VALID_BACKENDS:
            raise GuardrailConfigError(
                f"pii guardrail 'backend' must be one of {_VALID_BACKENDS}, got {backend!r}."
            )
        self._backend = backend

        raw_entities = config.get("entities")
        if raw_entities is not None:
            if not isinstance(raw_entities, list) or not all(
                isinstance(e, str) for e in raw_entities
            ):
                raise GuardrailConfigError("pii guardrail 'entities' must be a list of strings.")
            self._entities: list[str] | None = [str(e).upper() for e in raw_entities]
        else:
            self._entities = None

        self._language = str(config.get("language", "en"))
        try:
            self._min_score = float(config.get("min_score", 0.4))
        except (TypeError, ValueError) as exc:
            raise GuardrailConfigError("pii guardrail 'min_score' must be numeric.") from exc

        self._severity = _coerce_severity(config.get("severity"))
        self._suggested_override = _coerce_action(config.get("suggested_action"))

        # Optional injected analyzer (tests / advanced hosts). When set it
        # wins over backend selection but must satisfy the Protocol.
        injected = config.get("analyzer")
        if injected is not None and not isinstance(injected, PiiAnalyzer):
            raise GuardrailConfigError(
                "pii guardrail 'analyzer' must implement the PiiAnalyzer protocol."
            )
        self._injected: PiiAnalyzer | None = injected

        # Lazily built per-instance; resolved on first check (so import of
        # this module never instantiates Presidio).
        self._analyzer: PiiAnalyzer | None = injected
        self._regex_fallback = RegexPiiAnalyzer()
        # Marks that the configured backend could not be loaded.
        self._unavailable_reason: str | None = None

    # -- backend resolution ------------------------------------------------- #

    def _resolve_analyzer(self) -> PiiAnalyzer | None:
        """Return the analyzer to use, or ``None`` when unavailable.

        Caches the decision. For ``presidio`` (strict) a failure to load
        records an ``_unavailable_reason`` and returns ``None`` so the
        check degrades to a typed unavailable result. For ``auto`` a
        Presidio failure silently falls back to the regex detector.
        """
        if self._analyzer is not None:
            return self._analyzer

        if self._backend == "regex":
            self._analyzer = self._regex_fallback
            return self._analyzer

        if self._backend == "presidio":
            try:
                self._analyzer = PresidioPiiAnalyzer(language=self._language)
            except PresidioUnavailableError as exc:
                self._unavailable_reason = str(exc)
                return None
            return self._analyzer

        # auto: prefer Presidio, fall back to regex.
        if presidio_available():
            try:
                self._analyzer = PresidioPiiAnalyzer(language=self._language)
                return self._analyzer
            except PresidioUnavailableError:
                pass  # model missing -> regex fallback below
        self._analyzer = self._regex_fallback
        return self._analyzer

    # -- suggested action --------------------------------------------------- #

    def _suggested_action(self, context: GuardrailContext) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        if context.hook == "pre_llm":
            return Action.BLOCK
        # post_llm / pre_tool / post_tool default to masking the PII.
        return Action.REDACT

    # -- check -------------------------------------------------------------- #

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        if not text:
            return GuardrailResult.ok()

        analyzer = self._resolve_analyzer()
        if analyzer is None:
            # Strict 'presidio' backend with the dep/model missing: do NOT
            # crash and do NOT silently pass — surface a typed, non-blocking
            # 'unavailable' result the host can log / alert on.
            return GuardrailResult(
                triggered=False,
                severity=Severity.LOW,
                detail=(
                    "PII guardrail unavailable: "
                    f"{self._unavailable_reason or 'Presidio backend could not load.'}"
                ),
                suggested_action=None,
                payload={
                    "available": False,
                    "backend": self._backend,
                    "reason": self._unavailable_reason,
                },
            )

        matches = [m for m in analyzer.analyze(text, self._entities) if m.score >= self._min_score]
        backend_name = self._backend_name(analyzer)
        if not matches:
            return GuardrailResult(
                triggered=False,
                payload={"available": True, "backend": backend_name},
            )

        entity_types = sorted({m.entity_type for m in matches})
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=(
                f"Detected {len(matches)} PII span(s) "
                f"[{', '.join(entity_types)}] in {context.hook} text."
            ),
            suggested_action=self._suggested_action(context),
            payload={
                "available": True,
                "backend": backend_name,
                "entity_types": entity_types,
                "spans": [
                    {
                        "entity_type": m.entity_type,
                        "text": m.text,
                        "start": m.start,
                        "end": m.end,
                        "score": m.score,
                    }
                    for m in matches
                ],
            },
        )

    def _backend_name(self, analyzer: PiiAnalyzer) -> str:
        if isinstance(analyzer, RegexPiiAnalyzer):
            return "regex"
        if isinstance(analyzer, PresidioPiiAnalyzer):
            return "presidio"
        return self._backend


def detected_entity_types(matches: Iterable[PiiMatch]) -> list[str]:
    """Sorted unique entity types from a set of matches (host convenience)."""
    return sorted({m.entity_type for m in matches})


@register_guardrail("pii")
def _build_pii(config: dict[str, Any]) -> PiiGuardrail:
    return PiiGuardrail(config)


__all__ = [
    "PiiAnalyzer",
    "PiiGuardrail",
    "PiiMatch",
    "PresidioPiiAnalyzer",
    "PresidioUnavailableError",
    "RegexPiiAnalyzer",
    "detected_entity_types",
    "presidio_available",
]
