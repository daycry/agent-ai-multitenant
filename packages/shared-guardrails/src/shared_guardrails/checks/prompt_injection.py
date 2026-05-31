"""Prompt-injection detector guardrail (Plan 11, Phase B — task_11_06).

Registers the ``prompt_injection`` guardrail type. It scans the hook's
primary text for jailbreak / instruction-override attempts and reports
the offending technique categories so the host can block (or, in a
learning phase, warn).

Hooks
-----
It runs primarily at ``pre_llm`` (an inbound prompt trying to override
the system prompt) and ``pre_tool`` (injection smuggled into tool
arguments — e.g. a crafted file path / query that contains an override
instruction), but it works at any hook since it only reads
``GuardrailContext.primary_text()``.

Detected techniques
-------------------
  * **instruction_override** — "ignore previous instructions", "disregard
    the system prompt", "forget everything above", multilingual (es + en)
    phrasings of the same.
  * **role_switch** — attempts to flip the model's role / persona
    ("you are now DAN", "act as an unrestricted AI", "developer mode",
    "jailbreak").
  * **system_prompt_exfiltration** — "reveal/print your system prompt",
    "repeat the words above", "show your instructions".
  * **delimiter_smuggling** — injected role markers / control tokens
    ("[system]", "<|im_start|>system", "### system:", "BEGIN SYSTEM").
  * **encoding_smuggling** — base64 / hex / rot13 payloads paired with a
    "decode and execute" style instruction.
  * **tool_credential_coercion** — coercing the model to leak its
    credentials / API keys / tools ("print your api key",
    "what tools do you have access to, give me the token").

Backend strategy (model-classifier seam — heavy/model-dep honesty)
------------------------------------------------------------------
Detection is **pure Python** (heuristics + compiled regexes) — no heavy
or model dependency, so the module is importable and runs everywhere
including CI. The detector is expressed behind an
:class:`InjectionDetector` Protocol so a model-based classifier (e.g. an
LLM-as-judge or a fine-tuned classifier) could be plugged later behind
an optional extra and injected via the ``detector`` config key, exactly
like ``pii``'s ``analyzer`` seam. The **default** backend is always the
heuristic detector; no heavy dependency is shipped by this task.

The detection is side-effect-free: the engine applies the action; this
module only *suggests* one — configurable, defaulting to ``block`` (a
prompt-injection attempt should not reach the model). Set
``learning_mode: true`` (or ``suggested_action: warn``) during the
calibration phase so attempts are surfaced without blocking legitimate
work (the plan's "warn for the learning phase, block after the
calibration curve" baseline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# --------------------------------------------------------------------------- #
# Detected-technique record                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InjectionMatch:
    """One detected injection attempt.

    ``category`` is a stable technique identifier (``instruction_override``,
    ``role_switch``, ``system_prompt_exfiltration``, ``delimiter_smuggling``,
    ``encoding_smuggling``, ``tool_credential_coercion``) so hosts can
    group / alert by technique. ``text`` is the matched span (kept for the
    host's audit log — a prompt-injection span is not itself a secret).
    """

    category: str
    text: str
    start: int
    end: int


# --------------------------------------------------------------------------- #
# Detector protocol — heuristic ships; a classifier can be injected later     #
# --------------------------------------------------------------------------- #


@runtime_checkable
class InjectionDetector(Protocol):
    """A prompt-injection detector: text in, matches out. Pure, no I/O.

    The shipped backend is :class:`HeuristicInjectionDetector`. A
    model-based classifier (optional extra, lazily imported) can satisfy
    this Protocol and be injected via the guardrail's ``detector`` config
    key without touching the engine.
    """

    def detect(self, text: str) -> list[InjectionMatch]: ...


# --------------------------------------------------------------------------- #
# Heuristic / pattern detector (always available, multilingual es + en)       #
# --------------------------------------------------------------------------- #

# Verb that means "ignore / disregard / forget / override" in en + es, and
# the object it acts on (instructions / rules / system prompt / context).
# Kept as one alternation per category so the regex stays readable.

_INSTRUCTION_OVERRIDE_RE = re.compile(
    r"""
    (?:
        \b(?:ignore|disregard|forget|override|bypass|skip|discard)\b
        [\s\w,'"-]{0,40}?
        \b(?:previous|prior|earlier|above|preceding|all|any|the)\b
        [\s\w,'"-]{0,40}?
        \b(?:instruction|instructions|prompt|prompts|rule|rules|
             direction|directions|guideline|guidelines|context|message|messages)\b
      |                                  # verb + "everything/all above" (no object noun)
        \b(?:ignore|disregard|forget|discard)\b
        [\s\w,'"-]{0,20}?
        \b(?:everything|all)\b
        \s+
        \b(?:above|before|preceding|so\s+far)\b
      |                                                       # --- español ---
        \b(?:ignora|ignorar|olvida|olvidar|descarta|descartar|
             omite|omitir|salta|saltar|anula|anular|pasa\sde)\b
        [\s\w,'"áéíóúñ-]{0,40}?
        \b(?:anterior|anteriores|previa|previas|previo|previos|
             arriba|de\sarriba|todas?|todos?|las?|los?)\b
        [\s\w,'"áéíóúñ-]{0,40}?
        \b(?:instruccion|instrucciones|indicacion|indicaciones|
             regla|reglas|prompt|contexto|mensaje|mensajes|directriz|directrices)\b
      |                                  # es: "olvida todo lo anterior/de arriba"
        \b(?:olvida|olvidar|ignora|ignorar|descarta|descartar)\b
        [\s\w,'"áéíóúñ-]{0,20}?
        \b(?:todo)\b
        [\s\w,'"áéíóúñ-]{0,20}?
        \b(?:anterior|arriba|de\sarriba|previo)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# "disregard the system prompt" — system-prompt specific override, en + es.
_SYSTEM_PROMPT_OVERRIDE_RE = re.compile(
    r"""
    \b(?:ignore|disregard|forget|override|bypass|
         ignora|ignorar|olvida|olvidar|descarta|descartar|anula|anular)\b
    [\s\w,'"áéíóúñ-]{0,30}?
    \b(?:system\s*(?:prompt|message|instructions?)|
         (?:prompt|mensaje|instrucci[oó]n(?:es)?)\s+(?:de|del)\s+sistema)\b
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# Role-switch / jailbreak personas (en + es).
_ROLE_SWITCH_RE = re.compile(
    r"""
    (?:
        \b(?:you\s+are\s+now|from\s+now\s+on\s+you\s+are|pretend\s+to\s+be|
             pretend\s+you\s+are|act\s+as(?:\s+if\s+you\s+(?:are|were))?|
             roleplay\s+as|behave\s+as|respond\s+as)\b
      | \b(?:developer\s+mode|jailbreak(?:en|ed|ing)?|do\s+anything\s+now|\bDAN\b|
             unrestricted\s+(?:ai|mode|assistant)|without\s+(?:any\s+)?restrictions|
             no\s+longer\s+bound\s+by)\b
      | \b(?:eres\s+ahora|a\s+partir\s+de\s+ahora\s+eres|
             act[uú]a\s+como|comp[oó]rtate\s+como|finge\s+(?:que\s+eres|ser)|
             haz\s+de|modo\s+(?:desarrollador|sin\s+restricciones))\b
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# System-prompt exfiltration (en + es).
_EXFILTRATION_RE = re.compile(
    r"""
    (?:
        \b(?:reveal|repeat|print|show|display|output|dump|tell\s+me|
             give\s+me|what\s+(?:is|are)|disclose)\b
        [\s\w,'"-]{0,30}?
        \b(?:system\s*(?:prompt|message|instructions?)|
             your\s+(?:initial\s+)?(?:prompt|instructions?|directives?)|
             the\s+(?:words|text|instructions?)\s+above|
             everything\s+above|what\s+you\s+were\s+told)\b
      | \b(?:revela|repite|imprime|muestra|mu[eé]strame|dime|
             ens[eé][ñn]ame|dame)\b
        [\s\w,'"áéíóúñ-]{0,30}?
        \b(?:(?:prompt|mensaje|instrucci[oó]n(?:es)?)\s+(?:de|del)\s+sistema|
             tus\s+instrucciones|lo\s+que\s+(?:te\s+dijeron|hay\s+arriba)|
             el\s+texto\s+de\s+arriba)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# Delimiter / control-token smuggling: injected chat role markers.
_DELIMITER_RE = re.compile(
    r"""
    (?:
        <\|im_(?:start|end)\|>\s*(?:system|assistant|developer)?
      | <\|(?:system|endoftext|im_sep)\|>
      | \[\s*(?:system|assistant|developer|inst)\s*\]
      | \[/?INST\]
      | <<\s*SYS\s*>>
      | (?:^|\n)\s*\#{2,}\s*system\s*:?
      | (?:^|\n)\s*system\s*:\s
      | \bBEGIN\s+SYSTEM\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Encoding smuggling: an instruction to decode + execute an encoded blob.
_ENCODING_RE = re.compile(
    r"""
    \b(?:decode|base64|b64|rot13|hex|atob|from\s*hex|unescape|
         descodifica|decodifica)\b
    [\s\w,'"áéíóúñ-]{0,40}?
    \b(?:and\s+)?(?:execute|run|follow|obey|do|comply|
         ejecuta|ejecutar|sigue|seguir|obedece|haz)\b
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)

# Tool / credential coercion: leak keys / tokens / tool access (en + es).
_CREDENTIAL_COERCION_RE = re.compile(
    r"""
    (?:
        \b(?:print|reveal|show|give\s+me|tell\s+me|leak|expose|what\s+(?:is|are))\b
        [\s\w,'"-]{0,30}?
        \b(?:api[\s_-]*key|secret\s*key|access\s*token|auth\s*token|
             credentials?|password|private\s*key|environment\s+variables?|
             your\s+tools?|available\s+tools?|function\s+definitions?)\b
      | \b(?:revela|imprime|muestra|dame|dime|filtra|expone)\b
        [\s\w,'"áéíóúñ-]{0,30}?
        \b(?:clave\s*(?:api|secreta|privada)|api[\s_-]*key|token\s+de\s+acceso|
             credenciales?|contrase[ñn]a|variables?\s+de\s+entorno|
             tus\s+herramientas)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE | re.UNICODE,
)


# (category, compiled pattern). The system-prompt-specific override is
# listed before the generic one so a "disregard the system prompt" hit is
# de-duplicated to a single instruction_override span (see _dedupe below).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction_override", _SYSTEM_PROMPT_OVERRIDE_RE),
    ("instruction_override", _INSTRUCTION_OVERRIDE_RE),
    ("system_prompt_exfiltration", _EXFILTRATION_RE),
    ("tool_credential_coercion", _CREDENTIAL_COERCION_RE),
    ("role_switch", _ROLE_SWITCH_RE),
    ("encoding_smuggling", _ENCODING_RE),
    ("delimiter_smuggling", _DELIMITER_RE),
)


class HeuristicInjectionDetector:
    """Pure-Python heuristic detector: text in, matches out (no I/O, deps).

    Deterministic and importable everywhere (incl. CI). Multilingual
    (Spanish + English) override / role-switch / exfiltration phrasing.
    """

    def detect(self, text: str) -> list[InjectionMatch]:
        out: list[InjectionMatch] = []
        for category, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                span = m.group(0).strip()
                if not span:
                    continue
                out.append(InjectionMatch(category, span, m.start(), m.end()))
        out.sort(key=lambda x: x.start)
        return _dedupe_overlaps(out)


def _dedupe_overlaps(matches: list[InjectionMatch]) -> list[InjectionMatch]:
    """Drop matches whose span is fully contained in an earlier kept one.

    Patterns are ordered most-specific-first (e.g. the system-prompt
    override before the generic override) so the more specific category
    is the one kept when two spans coincide.
    """
    kept: list[InjectionMatch] = []
    for m in matches:
        if any(k.start <= m.start and k.end >= m.end and k is not m for k in kept):
            continue
        kept.append(m)
    return kept


# --------------------------------------------------------------------------- #
# Config coercion helpers (mirror builtins.py / pii.py / secret_leakage.py)    #
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


class PromptInjectionGuardrail:
    """Detects jailbreak / instruction-override attempts in the payload text.

    Config:
      - ``learning_mode``     bool  — when true the suggested action is
        ``warn`` instead of ``block`` (the calibration phase). Ignored
        when ``suggested_action`` is set explicitly. Default ``false``.
      - ``severity``          str   — default ``high``.
      - ``suggested_action``  str   — override the default action. When
        unset the guardrail suggests ``block`` (or ``warn`` in learning
        mode).
      - ``detector``          (seam) inject a pre-built detector
        implementing :class:`InjectionDetector` (e.g. a future
        model-based classifier behind an optional extra), bypassing the
        default heuristic. Must satisfy the Protocol.

    The result ``payload`` carries:
      - ``categories``  sorted unique techniques detected,
      - ``count``       number of spans,
      - ``spans``       per-span ``{category, text, start, end}``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._learning_mode = bool(config.get("learning_mode", False))
        self._severity = _coerce_severity(config.get("severity"))
        self._suggested_override = _coerce_action(config.get("suggested_action"))

        injected = config.get("detector")
        if injected is not None and not isinstance(injected, InjectionDetector):
            raise GuardrailConfigError(
                "prompt_injection guardrail 'detector' must implement the "
                "InjectionDetector protocol."
            )
        self._detector: InjectionDetector = injected or HeuristicInjectionDetector()

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        # Baseline: block an injection attempt outright; warn during the
        # calibration / learning phase so legitimate work is not blocked.
        return Action.WARN if self._learning_mode else Action.BLOCK

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        if not text:
            return GuardrailResult.ok()

        # pre_tool primary_text() is just the tool *name*; scan the tool
        # arguments too, where injected instructions actually hide.
        if context.hook == "pre_tool" and context.tool_args:
            text = f"{text}\n{_stringify_tool_args(context.tool_args)}"

        matches = self._detector.detect(text)
        if not matches:
            return GuardrailResult(triggered=False)

        categories = sorted({m.category for m in matches})
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=(
                f"Detected {len(matches)} prompt-injection attempt(s) "
                f"[{', '.join(categories)}] in {context.hook} text."
            ),
            suggested_action=self._suggested_action(),
            payload={
                "categories": categories,
                "count": len(matches),
                "spans": [
                    {
                        "category": m.category,
                        "text": m.text,
                        "start": m.start,
                        "end": m.end,
                    }
                    for m in matches
                ],
            },
        )


def _stringify_tool_args(tool_args: dict[str, Any]) -> str:
    """Flatten tool arguments to a single scannable string.

    Tool-arg injection hides override instructions inside an argument
    value (a path, a query, a free-text field), so we concatenate the
    string-coercible values for the detector to scan.
    """
    parts: list[str] = []
    for value in tool_args.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list | tuple)):
            parts.extend(str(v) for v in value)
        elif value is not None:
            parts.append(str(value))
    return "\n".join(parts)


@register_guardrail("prompt_injection")
def _build_prompt_injection(config: dict[str, Any]) -> PromptInjectionGuardrail:
    return PromptInjectionGuardrail(config)


__all__ = [
    "HeuristicInjectionDetector",
    "InjectionDetector",
    "InjectionMatch",
    "PromptInjectionGuardrail",
]
