"""Shared config-coercion helpers for the Phase B built-in guardrails.

The earlier Phase B checks (``pii``, ``secret_leakage``, ``prompt_injection``,
``content_safety``, ``code_safety``) each carry private ``_coerce_severity`` /
``_coerce_action`` copies that mirror ``builtins.py``. The task_11_09 bundle
adds seven more guardrail types; centralising the coercion here keeps them
consistent without a sevenfold copy. The behaviour is identical to the inline
copies: a :class:`Severity` / :class:`Action` passes through, a string is
parsed case-insensitively, ``None`` falls back to the default, and an invalid
value raises :class:`GuardrailConfigError`.
"""

from __future__ import annotations

from typing import Any

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.types import Action, Severity

# Ordinal rank so guardrails can floor / compare severities deterministically.
SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def coerce_severity(value: Any, default: Severity = Severity.MEDIUM) -> Severity:
    """Coerce a config value into a :class:`Severity` (``default`` when unset)."""
    if value is None:
        return default
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid severity {value!r}.") from exc


def coerce_action(value: Any) -> Action | None:
    """Coerce a config value into an :class:`Action` (``None`` when unset)."""
    if value is None:
        return None
    if isinstance(value, Action):
        return value
    try:
        return Action(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid action {value!r}.") from exc


def coerce_str_list(value: Any, *, field: str, guardrail: str) -> list[str]:
    """Coerce a config value into a list[str], raising on the wrong shape."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GuardrailConfigError(f"{guardrail} guardrail '{field}' must be a list of strings.")
    return [str(item) for item in value]


__all__ = ["SEVERITY_RANK", "coerce_action", "coerce_severity", "coerce_str_list"]
