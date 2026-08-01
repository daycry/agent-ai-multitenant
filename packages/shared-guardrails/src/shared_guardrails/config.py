"""Declarative pipeline config model + YAML/dict parsing (Plan 11).

The pipeline is configured declaratively — a YAML document (or the dict
it parses into) describes, per hook point, an ordered list of guardrails
to run. Each guardrail entry is:

```yaml
guardrails:
  pre_llm:
    - type: keyword
      action: block          # one of the 6 actions; overrides the
                             # guardrail's own suggested_action
      locked: true           # platform-only: lower layers can't override
      config:
        keywords: [ignore previous instructions]
  post_llm:
    - type: regex
      action: redact
      config:
        pattern: "sk-[a-z0-9]{20,}"
```

Phase A parses this into typed objects and the pipeline runs it. The
``locked`` flag is parsed and preserved here so the layered-config merge
(task_11_02) can enforce that a platform-locked guardrail cannot be
overridden by a tenant / project layer. An empty / absent config is a
valid no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.types import HOOK_POINTS, Action, HookPoint


def _coerce_action(value: Any) -> Action | None:
    if value is None:
        return None
    if isinstance(value, Action):
        return value
    try:
        return Action(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(
            f"Invalid action {value!r}; valid: {[a.value for a in Action]}"
        ) from exc


@dataclass
class GuardrailSpec:
    """One declarative guardrail entry within a hook's ordered list.

    ``type`` names a registered guardrail. ``config`` is the per-instance
    config passed to the guardrail's factory. ``action`` is the action
    the engine applies when this guardrail triggers — it overrides the
    guardrail's own ``suggested_action``. ``locked`` marks a
    platform-mandated guardrail that lower layers may not override
    (enforced by the layered merge in task_11_02).
    """

    type: str
    config: dict[str, Any] = field(default_factory=dict)
    action: Action | None = None
    locked: bool = False
    # Optional stable id so layered merge / observability can address a
    # specific guardrail entry; defaults to the type when absent.
    id: str | None = None
    # ADR 0102 D5: qué hacer cuando el check NO emite veredicto — porque
    # revienta, o porque se declara indisponible (`available: False`). "warn" =
    # fail-open, se registra sin bloquear; "block" = fail-closed, cuenta como
    # disparo con acción block.
    #
    # `None` significa «el operador no lo escribió», y NO es lo mismo que
    # `"warn"`: el default se deriva de `locked` (ver `effective_on_error`).
    # Guardar la ausencia es lo que permite que un `locked` herede fail-closed
    # sin quitarle al operador la opción de bajarlo a warn a propósito.
    on_error: str | None = None

    @property
    def key(self) -> str:
        return self.id or self.type

    @property
    def effective_on_error(self) -> str:
        """La política de fallo que aplica de verdad (ADR 0102 D5, opción c).

        Default `block` para los guardrails que la plataforma marcó `locked` y
        `warn` para el resto; lo que el operador escriba gana siempre. Un
        candado que se abre solo cuando el check revienta no es un candado —
        ése era el fail-open que la opción (c) del ADR se eligió para cerrar.
        """
        if self.on_error is not None:
            return self.on_error
        return "block" if self.locked else "warn"

    def to_dict(self) -> dict[str, Any]:
        """Inverso serializable de :func:`_parse_spec` (ADR 0102 D3) — el
        worker transporta la config RESUELTA al runtime como dict JSON."""
        out: dict[str, Any] = {"type": self.type}
        if self.config:
            out["config"] = dict(self.config)
        if self.action is not None:
            out["action"] = self.action.value
        if self.locked:
            out["locked"] = True
        if self.id is not None:
            out["id"] = self.id
        # Solo viaja lo que el operador escribió: `locked` viaja igualmente, así
        # que el receptor recalcula el MISMO default. Serializar el default
        # calculado congelaría la política del emisor en el receptor.
        if self.on_error is not None:
            out["on_error"] = self.on_error
        return out


@dataclass
class PipelineConfig:
    """The full declarative config: hook point -> ordered guardrail list.

    Hooks not present in the source map to an empty list (no-op). The
    constructor normalizes so every hook point is always a key.
    """

    hooks: dict[HookPoint, list[GuardrailSpec]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure every hook point exists so callers can index safely.
        for hp in HOOK_POINTS:
            self.hooks.setdefault(hp, [])

    def specs_for(self, hook: HookPoint) -> list[GuardrailSpec]:
        return self.hooks.get(hook, [])

    @property
    def is_empty(self) -> bool:
        return all(not specs for specs in self.hooks.values())

    def to_dict(self) -> dict[str, Any]:
        """Serialización JSON-friendly (ADR 0102 D3): ``{"guardrails": {hook:
        [spec…]}}`` con solo los hooks no vacíos — apta para ``parse_config``
        (roundtrip) y para viajar en el task spec del runtime."""
        hooks = {
            hook: [spec.to_dict() for spec in specs] for hook, specs in self.hooks.items() if specs
        }
        return {"guardrails": hooks}


def _parse_spec(raw: Any, *, hook: HookPoint, index: int) -> GuardrailSpec:
    if not isinstance(raw, dict):
        raise GuardrailConfigError(
            f"Guardrail entry #{index} at hook {hook!r} must be a mapping, "
            f"got {type(raw).__name__}."
        )
    gtype = raw.get("type")
    if not isinstance(gtype, str) or not gtype:
        raise GuardrailConfigError(
            f"Guardrail entry #{index} at hook {hook!r} is missing a 'type'."
        )
    cfg = raw.get("config", {})
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise GuardrailConfigError(
            f"Guardrail {gtype!r} at hook {hook!r}: 'config' must be a mapping."
        )
    spec_id = raw.get("id")
    if spec_id is not None and not isinstance(spec_id, str):
        raise GuardrailConfigError(f"Guardrail {gtype!r} at hook {hook!r}: 'id' must be a string.")
    raw_on_error = raw.get("on_error")
    on_error = None if raw_on_error is None else str(raw_on_error).lower()
    if on_error is not None and on_error not in ("warn", "block"):
        raise GuardrailConfigError(
            f"Guardrail {gtype!r} at hook {hook!r}: 'on_error' must be 'warn' or 'block'."
        )
    return GuardrailSpec(
        type=gtype,
        config=dict(cfg),
        action=_coerce_action(raw.get("action")),
        locked=bool(raw.get("locked", False)),
        id=spec_id,
        on_error=on_error,
    )


def parse_config(source: dict[str, Any] | None) -> PipelineConfig:
    """Build a :class:`PipelineConfig` from a parsed dict.

    Accepts either a top-level mapping with a ``guardrails`` key, or the
    hook->list mapping directly. ``None`` / empty maps to an empty
    (no-op) config. Unknown hook points are rejected.
    """
    if not source:
        return PipelineConfig()
    if not isinstance(source, dict):
        raise GuardrailConfigError("Config root must be a mapping.")

    # Allow either {"guardrails": {...}} or the hook mapping directly.
    hooks_src = source.get("guardrails", source)
    if hooks_src is None:
        return PipelineConfig()
    if not isinstance(hooks_src, dict):
        raise GuardrailConfigError("'guardrails' must be a mapping of hook -> list.")

    hooks: dict[HookPoint, list[GuardrailSpec]] = {}
    for hook, entries in hooks_src.items():
        if hook not in HOOK_POINTS:
            raise GuardrailConfigError(f"Unknown hook point {hook!r}; valid: {list(HOOK_POINTS)}")
        # The membership check above narrows `hook` to the HookPoint Literal.
        hp: HookPoint = hook
        if entries is None:
            hooks[hp] = []
            continue
        if not isinstance(entries, list):
            raise GuardrailConfigError(f"Hook {hook!r} must map to a list of guardrails.")
        hooks[hp] = [_parse_spec(raw, hook=hp, index=i) for i, raw in enumerate(entries)]
    return PipelineConfig(hooks=hooks)


def load_config(yaml_text: str) -> PipelineConfig:
    """Parse a YAML string into a :class:`PipelineConfig`."""
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise GuardrailConfigError(f"Invalid YAML: {exc}") from exc
    if data is None:
        return PipelineConfig()
    if not isinstance(data, dict):
        raise GuardrailConfigError("Top-level YAML must be a mapping.")
    return parse_config(data)


__all__ = [
    "GuardrailSpec",
    "PipelineConfig",
    "load_config",
    "parse_config",
]
