"""Secret-leakage detection guardrail (Plan 11, Phase B — task_11_05).

Registers the ``secret_leakage`` guardrail type. It scans the hook's
primary text for leaked credentials / tokens and, when found, reports
the detected secret *families* together with a **redacted** copy of the
text — the raw secret is never echoed back in the result.

It runs primarily at ``post_llm`` (the model output may hardcode a token
into generated code) and ``post_tool`` (a tool result — e.g. a file read
or a shell command's stdout — may surface a credential), but it works at
any hook since it only reads ``GuardrailContext.primary_text()``.

Detection is **pure Python** (regex + Shannon entropy) — no heavy or
model dependency, so it is importable and runs everywhere including CI.
Two layers of detection:

  * **Well-known patterns.** High-signal regexes for credential families
    that have a recognisable shape: AWS access keys, Google API keys,
    GitHub / GitLab tokens, Slack tokens, PEM private-key blocks, JWTs,
    and connection strings carrying an inline password.
  * **Generic high-entropy assignments.** A ``name = "value"`` /
    ``"name": "value"`` style assignment whose *key* looks secret-ish
    (``secret`` / ``token`` / ``api_key`` / ``password`` / ...) and whose
    *value* is a long, high-entropy string. The entropy gate keeps the
    false-positive rate low so benign config (``name = "production"``)
    is not flagged.

The detection is side-effect-free: the engine applies the action; this
module only *suggests* one — configurable, defaulting to ``redact`` so
the offending span is masked while the rest of the payload survives
(the plan baseline: "the response is redacted, substituting the token
for a marker"). Set ``suggested_action: block`` to drop the payload
outright.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# --------------------------------------------------------------------------- #
# Detected-secret record                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SecretMatch:
    """One detected secret span.

    ``secret_type`` is a stable family identifier (``AWS_ACCESS_KEY``,
    ``GOOGLE_API_KEY``, ``GITHUB_TOKEN``, ``GITLAB_TOKEN``,
    ``SLACK_TOKEN``, ``PRIVATE_KEY``, ``JWT``, ``CONNECTION_STRING``,
    ``GENERIC_SECRET``) so hosts can group / alert by family.
    """

    secret_type: str
    text: str
    start: int
    end: int


# --------------------------------------------------------------------------- #
# Well-known credential patterns (high signal, structurally recognisable)     #
# --------------------------------------------------------------------------- #

# AWS access key id: AKIA / ASIA / AGPA / ... + 16 uppercase alnum.
_AWS_ACCESS_KEY_RE = re.compile(
    r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA)[A-Z0-9]{16}\b"
)
# Google API key: AIza + 35 url-safe chars.
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
# GitHub tokens: ghp_/gho_/ghu_/ghs_/ghr_ (PAT/OAuth/...) + 36+ alnum, and
# the fine-grained github_pat_ form.
_GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")
# GitLab personal access token: glpat- + 20 url-safe chars.
_GITLAB_TOKEN_RE = re.compile(r"\bglpat-[0-9A-Za-z\-_]{20,}\b")
# Slack tokens: xox[baprs]- + segments. Also xapp- app-level tokens.
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b|\bxapp-\d-[0-9A-Za-z-]{10,}\b")
# PEM private-key block (RSA / EC / OPENSSH / generic / PGP).
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"
    r".*?"
    r"-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----",
    re.DOTALL,
)
# JWT: three base64url segments separated by dots; first segment starts
# with eyJ ({" base64-encoded) so we don't match arbitrary dotted tokens.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b")
# Connection string carrying an inline password:
#   scheme://user:password@host/...   (postgres, mysql, mongodb, redis, amqp, ...)
_CONNECTION_STRING_RE = re.compile(
    r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s:/@]+@[^\s/]+",
    re.IGNORECASE,
)

# (secret_type, compiled pattern). Order matters only for reporting; spans
# are de-duplicated by position below.
_WELL_KNOWN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PRIVATE_KEY", _PRIVATE_KEY_RE),
    ("AWS_ACCESS_KEY", _AWS_ACCESS_KEY_RE),
    ("GOOGLE_API_KEY", _GOOGLE_API_KEY_RE),
    ("GITHUB_TOKEN", _GITHUB_TOKEN_RE),
    ("GITLAB_TOKEN", _GITLAB_TOKEN_RE),
    ("SLACK_TOKEN", _SLACK_TOKEN_RE),
    ("JWT", _JWT_RE),
    ("CONNECTION_STRING", _CONNECTION_STRING_RE),
)


# --------------------------------------------------------------------------- #
# Generic high-entropy secret-assignment detector                             #
# --------------------------------------------------------------------------- #

# A secret-ish assignment: a key whose name contains a secret keyword,
# an = / : separator, then a quoted (or bare) value. We capture the value
# span (group "val") so only the secret — not the whole line — is masked.
_SECRET_KEYWORDS = (
    r"secret|token|passwd|password|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|auth"
)
_SECRET_KEY = rf"(?:[A-Za-z0-9_]*(?:{_SECRET_KEYWORDS})[A-Za-z0-9_]*)"
_ASSIGNMENT_RE = re.compile(
    rf"""(?ix)
    \b{_SECRET_KEY}\b            # secret-ish key name
    \s* [:=] \s*                 # = or : separator
    (?P<q>["'])?                 # optional opening quote
    (?P<val>[A-Za-z0-9+/=_\-.~]{{12,}})  # the value (>=12 chars)
    (?(q)["'])                   # matching closing quote if opened
    """,
)


def shannon_entropy(value: str) -> float:
    """Shannon entropy (bits per character) of ``value``.

    Used to separate genuine secrets (random-looking, high entropy) from
    benign config values (``"production"``, ``"true"``, repeated words),
    keeping the generic detector's false-positive rate down.
    """
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


# --------------------------------------------------------------------------- #
# Pure-Python detector                                                        #
# --------------------------------------------------------------------------- #


class SecretScanner:
    """Pure-Python secret detector: text in, matches out (no I/O, no deps).

    ``min_entropy`` gates the generic high-entropy assignment detector
    only; the well-known patterns are reported regardless of entropy
    (their shape is already high-signal).
    """

    def __init__(self, min_entropy: float = 3.5) -> None:
        self._min_entropy = min_entropy

    def scan(self, text: str) -> list[SecretMatch]:
        out: list[SecretMatch] = []

        # 1) Well-known credential shapes.
        for secret_type, pattern in _WELL_KNOWN:
            for m in pattern.finditer(text):
                out.append(SecretMatch(secret_type, m.group(0), m.start(), m.end()))

        # 2) Generic high-entropy secret-ish assignments — only on the
        # value span, and only when the value clears the entropy gate so
        # benign config strings are not flagged.
        for m in _ASSIGNMENT_RE.finditer(text):
            value = m.group("val")
            if shannon_entropy(value) < self._min_entropy:
                continue
            s, e = m.span("val")
            # Skip if this value span is already covered by a well-known
            # match (e.g. a JWT assigned to ``token = "..."``) to avoid
            # double counting.
            if any(other.start <= s and other.end >= e for other in out):
                continue
            out.append(SecretMatch("GENERIC_SECRET", value, s, e))

        out.sort(key=lambda x: x.start)
        return _dedupe_overlaps(out)


def _dedupe_overlaps(matches: list[SecretMatch]) -> list[SecretMatch]:
    """Drop matches whose span is fully contained in an earlier one.

    Keeps the first (well-known patterns are listed before the generic
    detector) so a token reported as both, say, a ``JWT`` and a
    ``GENERIC_SECRET`` is counted once, as the more specific family.
    """
    kept: list[SecretMatch] = []
    for m in matches:
        if any(k.start <= m.start and k.end >= m.end and k is not m for k in kept):
            continue
        kept.append(m)
    return kept


# --------------------------------------------------------------------------- #
# Redaction — mask the secret, never echo it                                  #
# --------------------------------------------------------------------------- #


def redact_secrets(text: str, matches: list[SecretMatch], marker: str = "[REDACTED:{type}]") -> str:
    """Return ``text`` with every matched secret span replaced by a marker.

    The marker carries only the secret *family*, never any character of
    the secret itself — the redacted output is safe to log / surface.
    Replacements are applied right-to-left so earlier spans' offsets stay
    valid.
    """
    redacted = text
    for m in sorted(matches, key=lambda x: x.start, reverse=True):
        redacted = redacted[: m.start] + marker.format(type=m.secret_type) + redacted[m.end :]
    return redacted


# --------------------------------------------------------------------------- #
# Config coercion (mirrors builtins.py / pii.py shape)                        #
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


# --------------------------------------------------------------------------- #
# The guardrail                                                               #
# --------------------------------------------------------------------------- #


class SecretLeakageGuardrail:
    """Detects leaked credentials / tokens in the hook's primary text.

    Config:
      - ``min_entropy``       float — entropy gate for the generic
        high-entropy assignment detector (default ``3.5``). The
        well-known token patterns ignore it.
      - ``redact_marker``     str   — template for masked spans; must
        contain ``{type}`` (default ``"[REDACTED:{type}]"``).
      - ``severity``          str   — default ``high`` (a leaked secret
        is serious).
      - ``suggested_action``  str   — default ``redact`` (mask the span
        and continue); set ``block`` to drop the payload.

    The result ``payload`` carries:
      - ``secret_types``  sorted unique families found,
      - ``count``         number of spans,
      - ``spans``         per-span ``{secret_type, start, end}`` (offsets
        only — the raw secret is never included),
      - ``redacted_text`` the payload with every secret masked.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        try:
            self._min_entropy = float(config.get("min_entropy", 3.5))
        except (TypeError, ValueError) as exc:
            raise GuardrailConfigError(
                "secret_leakage guardrail 'min_entropy' must be numeric."
            ) from exc

        marker = str(config.get("redact_marker", "[REDACTED:{type}]"))
        if "{type}" not in marker:
            raise GuardrailConfigError(
                "secret_leakage guardrail 'redact_marker' must contain '{type}'."
            )
        self._marker = marker

        self._severity = _coerce_severity(config.get("severity"))
        self._suggested_override = _coerce_action(config.get("suggested_action"))
        self._scanner = SecretScanner(min_entropy=self._min_entropy)

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        # Baseline: redact (mask the token, keep the rest of the payload).
        return Action.REDACT

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        if not text:
            return GuardrailResult.ok()

        matches = self._scanner.scan(text)
        if not matches:
            return GuardrailResult(triggered=False)

        secret_types = sorted({m.secret_type for m in matches})
        redacted = redact_secrets(text, matches, self._marker)
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=(
                f"Detected {len(matches)} leaked secret(s) "
                f"[{', '.join(secret_types)}] in {context.hook} text."
            ),
            suggested_action=self._suggested_action(),
            payload={
                "secret_types": secret_types,
                "count": len(matches),
                # Offsets + family only — never the raw secret value.
                "spans": [
                    {"secret_type": m.secret_type, "start": m.start, "end": m.end} for m in matches
                ],
                "redacted_text": redacted,
            },
        )


@register_guardrail("secret_leakage")
def _build_secret_leakage(config: dict[str, Any]) -> SecretLeakageGuardrail:
    return SecretLeakageGuardrail(config)


__all__ = [
    "SecretLeakageGuardrail",
    "SecretMatch",
    "SecretScanner",
    "redact_secrets",
    "shannon_entropy",
]
