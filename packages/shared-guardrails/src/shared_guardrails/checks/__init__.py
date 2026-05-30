"""Production built-in guardrail types (Plan 11, Phase B).

Each module here implements one real guardrail type and registers itself
into the process-wide ``default_registry`` at import time via
``@register_guardrail("<type>")``. Importing this package therefore makes
every Phase B guardrail type available out of the box, exactly like the
trivial Phase A built-ins (``keyword`` / ``regex``).

Heavy / model-dependent backends (Presidio + its spaCy model for ``pii``,
the content-safety classifier for ``content_safety``) are imported
LAZILY behind optional extras so the engine stays importable and CI is
not forced to install a multi-hundred-MB model. When the backend is
absent the guardrail degrades to a typed *unavailable* path (and, where
it makes sense, a lightweight pure-Python fallback) instead of crashing.
"""

from __future__ import annotations

# Importing each module for its import-time registration side effect.
from shared_guardrails.checks import pii as _pii  # noqa: F401
from shared_guardrails.checks import prompt_injection as _prompt_injection  # noqa: F401
from shared_guardrails.checks import secret_leakage as _secret_leakage  # noqa: F401

__all__: list[str] = []
