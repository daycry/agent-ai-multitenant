"""Typed errors the guardrails engine raises (Plan 11, Phase A).

GuardrailError                  — base for everything this layer raises
 ├── GuardrailConfigError       — the declarative config is malformed
 └── UnknownGuardrailTypeError  — config references an unregistered type
"""

from __future__ import annotations


class GuardrailError(Exception):
    """Base class for every error the guardrails layer raises."""


class GuardrailConfigError(GuardrailError):
    """The declarative pipeline config is malformed or invalid."""


class UnknownGuardrailTypeError(GuardrailConfigError):
    """A guardrail config references a ``type`` that is not registered."""

    def __init__(self, guardrail_type: str) -> None:
        super().__init__(
            f"Unknown guardrail type {guardrail_type!r}. "
            "Register it with @register_guardrail or check the config."
        )
        self.guardrail_type = guardrail_type


__all__ = [
    "GuardrailConfigError",
    "GuardrailError",
    "UnknownGuardrailTypeError",
]
