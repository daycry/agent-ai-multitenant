"""Content-safety guardrail (Plan 11, Phase B — task_11_07).

Registers the ``content_safety`` guardrail type. It classifies the
hook's primary text into safety categories (violence, hate, sexual,
self-harm, ...) using a *guard model* — LlamaGuard or ShieldGemma —
served through the existing LLM layer (Ollama / provider), and reports
the offending categories so the host can block unsafe content.

Hooks
-----
It runs at ``pre_llm`` (an inbound prompt the user sent) and ``post_llm``
(the model's output before it reaches the user / logs), but it works at
any hook since it only reads ``GuardrailContext.primary_text()``.

Safety categories
-----------------
A stable, provider-agnostic vocabulary the host can group / alert by,
mapped from the guard model's native taxonomy (LlamaGuard's ``S1..S13``,
ShieldGemma's policies):

  * ``violence``        — violent threats / incitement / graphic violence.
  * ``hate``            — hate speech / harassment toward protected groups.
  * ``sexual``          — sexual content, esp. involving minors.
  * ``self_harm``       — self-harm / suicide encouragement.
  * ``weapons``         — illegal weapons / mass-destruction guidance.
  * ``criminal``        — other illegal / criminal planning.
  * ``other``           — any unsafe category the model reports we do not
    map explicitly.

Backend strategy (heavy/model-dep honesty — content-safety classifier)
----------------------------------------------------------------------
The guard model is an LLM (LlamaGuard / ShieldGemma) reached through the
platform's LLM layer (``packages/shared-llm``). That is a heavy, runtime
dependency (a running Ollama / provider serving the guard model), so it
is **never** a base dependency:

  * The classifier lives behind a :class:`SafetyClassifier` Protocol seam
    (``classify(text) -> SafetyVerdict``). The host injects a built
    classifier via the ``classifier`` config key — exactly like ``pii``'s
    ``analyzer`` and ``prompt_injection``'s ``detector`` seams.
  * The optional ``shared-guardrails[content-safety]`` extra pulls
    ``shared-llm`` so an :class:`LLMSafetyClassifier` adapter can drive a
    guard model. It is imported LAZILY; absence of the extra never breaks
    the import or CI.
  * When **no** classifier is configured (and none can be built) the
    guardrail degrades to a typed *unavailable* result
    (``triggered=False``, ``payload={"available": False, ...}``) — it
    NEVER fakes a safe verdict silently. The host can log / alert on the
    unavailable path and decide whether to fail open or closed.

Detection is otherwise pure: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``block`` on unsafe
content (unsafe content must not reach the model nor the user).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# --------------------------------------------------------------------------- #
# Safety taxonomy                                                             #
# --------------------------------------------------------------------------- #

# Stable, provider-agnostic category vocabulary. Guard-model native labels
# map onto these (see ``LLAMAGUARD_CATEGORY_MAP`` below).
SAFETY_CATEGORIES: tuple[str, ...] = (
    "violence",
    "hate",
    "sexual",
    "self_harm",
    "weapons",
    "criminal",
    "other",
)

# LlamaGuard's S-codes -> our vocabulary (the common subset; anything not
# mapped degrades to ``other`` so a new guard-model policy never silently
# disappears).
LLAMAGUARD_CATEGORY_MAP: dict[str, str] = {
    "s1": "violence",  # Violent Crimes
    "s2": "criminal",  # Non-Violent Crimes
    "s3": "sexual",  # Sex-Related Crimes
    "s4": "sexual",  # Child Sexual Exploitation
    "s5": "criminal",  # Defamation
    "s6": "criminal",  # Specialized Advice
    "s7": "criminal",  # Privacy
    "s8": "criminal",  # Intellectual Property
    "s9": "weapons",  # Indiscriminate Weapons
    "s10": "hate",  # Hate
    "s11": "self_harm",  # Suicide & Self-Harm
    "s12": "sexual",  # Sexual Content
    "s13": "criminal",  # Elections
}


def normalize_category(label: str) -> str:
    """Map a guard-model native label onto our stable vocabulary.

    Accepts our own vocabulary verbatim, LlamaGuard ``S1..S13`` codes
    (case-insensitive), and falls back to ``other`` for anything else so
    an unmapped policy is still surfaced as unsafe (never dropped).
    """
    key = label.strip().lower()
    if key in SAFETY_CATEGORIES:
        return key
    if key in LLAMAGUARD_CATEGORY_MAP:
        return LLAMAGUARD_CATEGORY_MAP[key]
    return "other"


@dataclass(frozen=True)
class SafetyVerdict:
    """The outcome of classifying one piece of text.

    ``unsafe`` is the guard model's safe/unsafe verdict. ``categories``
    holds the *normalized* offending categories (our vocabulary) when
    unsafe. ``severity`` lets a classifier signal how serious the worst
    category is; when ``None`` the guardrail uses its configured default.
    ``raw_label`` keeps the model's original label(s) for the audit log.
    """

    unsafe: bool
    categories: tuple[str, ...] = ()
    severity: Severity | None = None
    raw_label: str = ""

    @classmethod
    def safe(cls) -> SafetyVerdict:
        return cls(unsafe=False)


# --------------------------------------------------------------------------- #
# Classifier seam — a guard-model adapter is injected here                    #
# --------------------------------------------------------------------------- #


@runtime_checkable
class SafetyClassifier(Protocol):
    """A content-safety classifier: text in, verdict out.

    The seam is intentionally **synchronous** so it slots into the
    pipeline's synchronous ``check`` exactly like the ``pii`` /
    ``prompt_injection`` seams. A model-backed implementation that drives
    an async :class:`shared_llm.LLMProvider` bridges the async call
    internally (the host configures how — e.g. a dedicated event loop /
    runner) so the guardrail itself stays sync.
    """

    def classify(self, text: str) -> SafetyVerdict: ...


# --------------------------------------------------------------------------- #
# Lazy LLM-backed guard-model classifier (optional extra)                     #
# --------------------------------------------------------------------------- #


class SafetyClassifierUnavailableError(RuntimeError):
    """A guard-model classifier could not be built (extra/model absent)."""


# Default LlamaGuard-style system prompt. The guard model answers with
# ``safe`` or ``unsafe`` optionally followed by a comma-separated list of
# violated category codes on the next line.
_DEFAULT_GUARD_PROMPT = (
    "You are a content-safety classifier. Classify the following content as "
    "'safe' or 'unsafe'. If unsafe, on a second line list the violated safety "
    "categories. Respond with only the verdict and categories."
)


def parse_guard_response(text: str) -> SafetyVerdict:
    """Parse a LlamaGuard-style guard-model response into a verdict.

    LlamaGuard answers ``safe`` or ``unsafe\\nS1,S10`` (verdict on the
    first line, violated category codes on the second). This is pure and
    deterministic so it is unit-testable without a running model.
    """
    stripped = text.strip()
    if not stripped:
        # Empty / unparseable model output: treat as a *non-verdict*. The
        # adapter surfaces this as unavailable rather than a fake "safe".
        raise SafetyClassifierUnavailableError("guard model returned empty output")

    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    verdict_token = lines[0].lower()
    if verdict_token.startswith("safe"):
        return SafetyVerdict.safe()
    if not verdict_token.startswith("unsafe"):
        raise SafetyClassifierUnavailableError(
            f"guard model returned unparseable verdict {lines[0]!r}"
        )

    # Category codes may be on the same line after the verdict, or the next.
    raw_categories = ""
    same_line = verdict_token[len("unsafe") :].strip(" :,")
    if same_line:
        raw_categories = same_line
    elif len(lines) > 1:
        raw_categories = lines[1]

    codes = [c.strip() for c in raw_categories.replace(";", ",").split(",") if c.strip()]
    categories = tuple(dict.fromkeys(normalize_category(c) for c in codes))
    return SafetyVerdict(
        unsafe=True,
        categories=categories or ("other",),
        raw_label=raw_categories or "unsafe",
    )


class LLMSafetyClassifier:
    """Drives a guard model (LlamaGuard / ShieldGemma) via the LLM layer.

    Lazily depends on ``shared-llm`` (the ``shared-guardrails[content-safety]``
    extra). It wraps an injected :class:`shared_llm.LLMProvider` and a
    synchronous ``runner`` that executes the provider's async ``complete``
    coroutine — the host owns the event-loop strategy, keeping this
    adapter (and the :class:`SafetyClassifier` seam) synchronous.

    Constructing it does NOT import ``shared-llm`` unless type-checking;
    the import is only exercised when a provider is actually supplied,
    matching the lazy / optional-extra discipline of the ``pii`` backend.
    """

    def __init__(
        self,
        provider: Any,
        runner: Any,
        *,
        model: str | None = None,
        system_prompt: str = _DEFAULT_GUARD_PROMPT,
    ) -> None:
        # ``provider`` must look like an LLMProvider (async ``complete``);
        # ``runner`` is a callable that runs a coroutine to completion
        # synchronously (e.g. ``asyncio.run`` or a host loop runner).
        if not callable(getattr(provider, "complete", None)):
            raise SafetyClassifierUnavailableError(
                "content-safety LLM classifier needs a provider with an async "
                "complete(); install 'shared-guardrails[content-safety]' and pass "
                "a shared_llm.LLMProvider."
            )
        if not callable(runner):
            raise SafetyClassifierUnavailableError(
                "content-safety LLM classifier needs a synchronous 'runner' "
                "callable to drive the async provider."
            )
        self._provider = provider
        self._runner = runner
        self._model = model
        self._system_prompt = system_prompt

    def classify(self, text: str) -> SafetyVerdict:
        # Build the chat the guard model expects. ``shared_llm.Message`` is
        # imported lazily so the base import never pulls shared-llm.
        from shared_llm import Message

        messages = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=text),
        ]
        response = self._runner(
            self._provider.complete(messages, model=self._model, max_tokens=64, temperature=0.0)
        )
        return parse_guard_response(str(getattr(response, "content", response)))


# --------------------------------------------------------------------------- #
# Config coercion helpers (mirror builtins.py / pii.py / prompt_injection.py)  #
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


# Per-category severity floor: some categories are graver regardless of the
# configured default (e.g. child-sexual / self-harm). The configured default
# is the floor; these raise it for the worst categories.
_CATEGORY_SEVERITY: dict[str, Severity] = {
    "sexual": Severity.CRITICAL,
    "self_harm": Severity.CRITICAL,
    "violence": Severity.HIGH,
    "weapons": Severity.HIGH,
    "hate": Severity.HIGH,
    "criminal": Severity.HIGH,
    "other": Severity.MEDIUM,
}

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


# --------------------------------------------------------------------------- #
# The guardrail                                                               #
# --------------------------------------------------------------------------- #


class ContentSafetyGuardrail:
    """Classifies the hook's primary text into safety categories.

    Config:
      - ``classifier``        (seam) inject a pre-built classifier
        implementing :class:`SafetyClassifier` (e.g. an
        :class:`LLMSafetyClassifier` over a guard model). Must satisfy the
        Protocol. When unset the guardrail degrades to a typed
        *unavailable* result — it NEVER fakes a safe verdict.
      - ``severity``          default ``high`` — the floor; graver
        categories (sexual / self-harm) raise it.
      - ``suggested_action``  override the default action. When unset the
        guardrail suggests ``block`` on unsafe content.
      - ``categories``        optional list[str] restricting which
        (normalized) categories count as a trigger. Unset = all.

    The result ``payload`` carries:
      - ``available``   whether a classifier ran,
      - ``categories``  the offending categories (when unsafe),
      - ``raw_label``   the guard model's native label (audit),
      - ``reason``      why unavailable (when ``available`` is False).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._severity = _coerce_severity(config.get("severity"))
        self._suggested_override = _coerce_action(config.get("suggested_action"))

        raw_categories = config.get("categories")
        if raw_categories is not None:
            if not isinstance(raw_categories, list) or not all(
                isinstance(c, str) for c in raw_categories
            ):
                raise GuardrailConfigError(
                    "content_safety guardrail 'categories' must be a list of strings."
                )
            self._only: set[str] | None = {normalize_category(c) for c in raw_categories}
        else:
            self._only = None

        injected = config.get("classifier")
        if injected is not None and not isinstance(injected, SafetyClassifier):
            raise GuardrailConfigError(
                "content_safety guardrail 'classifier' must implement the "
                "SafetyClassifier protocol."
            )
        self._classifier: SafetyClassifier | None = injected

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.BLOCK

    def _resolve_severity(self, categories: tuple[str, ...], verdict: SafetyVerdict) -> Severity:
        # A classifier may signal its own severity; otherwise raise the
        # configured floor for the gravest offending category.
        best = verdict.severity or self._severity
        for cat in categories:
            cat_sev = _CATEGORY_SEVERITY.get(cat, self._severity)
            if _SEVERITY_RANK[cat_sev] > _SEVERITY_RANK[best]:
                best = cat_sev
        return best

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        if not text:
            return GuardrailResult.ok()

        if self._classifier is None:
            # No guard model configured: surface a typed, non-blocking
            # 'unavailable' result. NEVER fake a safe verdict silently.
            return GuardrailResult(
                triggered=False,
                severity=Severity.LOW,
                detail=(
                    "Content-safety guardrail unavailable: no guard-model classifier "
                    "configured (install 'shared-guardrails[content-safety]' and inject "
                    "a SafetyClassifier)."
                ),
                suggested_action=None,
                payload={"available": False, "reason": "no_classifier"},
            )

        try:
            verdict = self._classifier.classify(text)
        except SafetyClassifierUnavailableError as exc:
            # The guard model ran but produced no usable verdict (down /
            # unparseable). Surface unavailable, do not fake safe.
            return GuardrailResult(
                triggered=False,
                severity=Severity.LOW,
                detail=f"Content-safety guardrail unavailable: {exc}",
                suggested_action=None,
                payload={"available": False, "reason": str(exc)},
            )

        if not verdict.unsafe:
            return GuardrailResult(triggered=False, payload={"available": True})

        # De-duplicate while preserving order; an unsafe verdict with no
        # categories still counts (degrades to 'other', never dropped).
        deduped = tuple(dict.fromkeys(verdict.categories))
        categories: tuple[str, ...] = deduped if deduped else ("other",)
        if self._only is not None:
            categories = tuple(c for c in categories if c in self._only)
            if not categories:
                # Unsafe, but only in categories the host opted out of.
                return GuardrailResult(triggered=False, payload={"available": True})

        return GuardrailResult(
            triggered=True,
            severity=self._resolve_severity(categories, verdict),
            detail=(
                f"Guard model flagged unsafe content "
                f"[{', '.join(sorted(categories))}] in {context.hook} text."
            ),
            suggested_action=self._suggested_action(),
            payload={
                "available": True,
                "categories": sorted(categories),
                "raw_label": verdict.raw_label,
            },
        )


@register_guardrail("content_safety")
def _build_content_safety(config: dict[str, Any]) -> ContentSafetyGuardrail:
    return ContentSafetyGuardrail(config)


__all__ = [
    "LLAMAGUARD_CATEGORY_MAP",
    "SAFETY_CATEGORIES",
    "ContentSafetyGuardrail",
    "LLMSafetyClassifier",
    "SafetyClassifier",
    "SafetyClassifierUnavailableError",
    "SafetyVerdict",
    "normalize_category",
    "parse_guard_response",
]
