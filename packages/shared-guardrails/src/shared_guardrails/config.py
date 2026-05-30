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

    @property
    def key(self) -> str:
        return self.id or self.type


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
    return GuardrailSpec(
        type=gtype,
        config=dict(cfg),
        action=_coerce_action(raw.get("action")),
        locked=bool(raw.get("locked", False)),
        id=spec_id,
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
