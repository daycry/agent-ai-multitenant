"""The ``Guardrail`` Protocol and the type registry (Plan 11, Phase A).

A guardrail is any object exposing ``check(context) -> GuardrailResult``.
The declarative config names guardrails by a string ``type``; the engine
looks that type up in a registry of *factories* (callables that build a
guardrail instance from its per-instance ``config`` dict) so the YAML can
stay declarative — config authors never import code, they name a type.

Built-in types register themselves at import time via
``@register_guardrail("name")``. The 12 production guardrail types land
in Phase B; Phase A ships a couple of trivial built-ins (regex / keyword)
so the pipeline is exercisable end to end.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from shared_guardrails.exceptions import GuardrailConfigError, UnknownGuardrailTypeError
from shared_guardrails.types import GuardrailContext, GuardrailResult


@runtime_checkable
class Guardrail(Protocol):
    """A single declarative check evaluated against a context.

    Implementations are constructed from their per-instance ``config``
    dict by a registered factory, then ``check`` is called once per hook
    evaluation. ``check`` must be pure and side-effect-free: it inspects
    the context and reports a :class:`GuardrailResult`. The engine (not
    the guardrail) decides what to *do* with a triggered result.
    """

    def check(self, context: GuardrailContext) -> GuardrailResult: ...


# A factory takes the guardrail's per-instance config dict and returns a
# ready-to-use Guardrail.
GuardrailFactory = Callable[[dict[str, Any]], Guardrail]


class GuardrailRegistry:
    """Maps a guardrail ``type`` string to a factory that builds it.

    A class (not a module global) so tests can spin up an isolated
    registry. A process-wide default lives in this module as
    ``default_registry`` and is what the built-ins register into.
    """

    def __init__(self) -> None:
        self._factories: dict[str, GuardrailFactory] = {}

    def register(self, type_name: str, factory: GuardrailFactory) -> None:
        """Register ``factory`` under ``type_name`` (overwrites)."""
        if not type_name:
            raise GuardrailConfigError("Guardrail type name must be non-empty.")
        self._factories[type_name] = factory

    def is_registered(self, type_name: str) -> bool:
        return type_name in self._factories

    def known_types(self) -> list[str]:
        return sorted(self._factories)

    def build(self, type_name: str, config: dict[str, Any]) -> Guardrail:
        """Instantiate the guardrail registered under ``type_name``."""
        try:
            factory = self._factories[type_name]
        except KeyError as exc:
            raise UnknownGuardrailTypeError(type_name) from exc
        return factory(config)


# Process-wide default registry the built-ins register into.
default_registry = GuardrailRegistry()


def register_guardrail(
    type_name: str,
    *,
    registry: GuardrailRegistry | None = None,
) -> Callable[[GuardrailFactory], GuardrailFactory]:
    """Decorator registering a guardrail factory under ``type_name``.

    Defaults to the process-wide ``default_registry``; pass ``registry``
    to target an isolated one (handy in tests).
    """

    target = registry if registry is not None else default_registry

    def _decorate(factory: GuardrailFactory) -> GuardrailFactory:
        target.register(type_name, factory)
        return factory

    return _decorate


__all__ = [
    "Guardrail",
    "GuardrailFactory",
    "GuardrailRegistry",
    "default_registry",
    "register_guardrail",
]
