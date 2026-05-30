"""Trivial built-in guardrails so the Phase A pipeline is exercisable.

The 12 production guardrail types (PII, secret leakage, prompt injection,
content safety, code safety, output schema, allowed domains, cost
ceiling, ...) land in Phase B. Phase A ships two small, dependency-free
text guardrails — a keyword match and a regex match — so the declarative
pipeline can be wired and tested end to end.

Both register themselves into the process-wide ``default_registry`` at
import time, keyed by ``type``:

  - ``keyword`` — triggers when any configured keyword appears in the
    hook's primary text (case-insensitive by default).
  - ``regex``   — triggers when a configured regex pattern matches.
"""

from __future__ import annotations

import re
from typing import Any

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity


def _coerce_severity(value: Any, default: Severity = Severity.MEDIUM) -> Severity:
    if value is None:
        return default
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid severity {value!r}.") from exc


def _coerce_action(value: Any) -> Action | None:
    """A guardrail's *suggested* action; ``None`` when unset."""
    if value is None:
        return None
    if isinstance(value, Action):
        return value
    try:
        return Action(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid action {value!r}.") from exc


class KeywordGuardrail:
    """Triggers when any configured keyword appears in the payload text.

    Config:
      - ``keywords``       list[str] — required, non-empty.
      - ``case_sensitive`` bool      — default False.
      - ``severity``       str       — default ``medium``.
      - ``suggested_action`` str     — optional default action.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        raw = config.get("keywords")
        if not isinstance(raw, list) or not raw:
            raise GuardrailConfigError("keyword guardrail requires a non-empty 'keywords' list.")
        self._case_sensitive = bool(config.get("case_sensitive", False))
        self._keywords = [str(k) for k in raw]
        self._needles = (
            self._keywords if self._case_sensitive else [k.lower() for k in self._keywords]
        )
        self._severity = _coerce_severity(config.get("severity"))
        self._suggested = _coerce_action(config.get("suggested_action"))

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        haystack = text if self._case_sensitive else text.lower()
        matched = [
            original
            for original, needle in zip(self._keywords, self._needles, strict=True)
            if needle in haystack
        ]
        if not matched:
            return GuardrailResult.ok()
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=f"Matched forbidden keyword(s): {', '.join(matched)}",
            suggested_action=self._suggested,
            payload={"matched": matched},
        )


class RegexGuardrail:
    """Triggers when a configured regex pattern matches the payload text.

    Config:
      - ``pattern``          str  — required.
      - ``flags_ignorecase`` bool — default False (adds re.IGNORECASE).
      - ``severity``         str  — default ``medium``.
      - ``suggested_action`` str  — optional default action.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        pattern = config.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise GuardrailConfigError("regex guardrail requires a 'pattern' string.")
        flags = re.IGNORECASE if config.get("flags_ignorecase", False) else 0
        try:
            self._regex = re.compile(pattern, flags)
        except re.error as exc:
            raise GuardrailConfigError(f"Invalid regex pattern: {exc}") from exc
        self._severity = _coerce_severity(config.get("severity"))
        self._suggested = _coerce_action(config.get("suggested_action"))

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        matches = [m.group(0) for m in self._regex.finditer(text)]
        if not matches:
            return GuardrailResult.ok()
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=f"Pattern matched {len(matches)} time(s).",
            suggested_action=self._suggested,
            payload={"matches": matches},
        )


@register_guardrail("keyword")
def _build_keyword(config: dict[str, Any]) -> KeywordGuardrail:
    return KeywordGuardrail(config)


@register_guardrail("regex")
def _build_regex(config: dict[str, Any]) -> RegexGuardrail:
    return RegexGuardrail(config)


__all__ = ["KeywordGuardrail", "RegexGuardrail"]
