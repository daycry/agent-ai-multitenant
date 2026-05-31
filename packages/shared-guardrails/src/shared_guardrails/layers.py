"""Layered guardrails config: platform -> tenant -> project (task_11_02).

A guardrails pipeline is configured in three layers, least- to
most-specific:

  - ``platform``  the baseline every tenant inherits. The platform can
                  mark a guardrail *locked* — lower layers must not be
                  able to weaken or remove it. PII / secret-leakage /
                  prompt-injection baselines live here and are mandatory.
  - ``tenant``    a tenant's overrides on top of the platform baseline.
  - ``project``   a project's overrides on top of its tenant's layer.

Resolution merges the layers per hook point. Within a hook, a guardrail
is addressed by its **key** (``id`` if present, else ``type`` — see
:attr:`GuardrailSpec.key`). A more-specific layer replaces a
less-specific layer's guardrail with the same key, EXCEPT when the
platform locked that key: the override is then ignored (default) or
rejected with a typed :class:`LockedFieldOverrideError` (strict mode),
and either way it is surfaced in the resolution record.

A locked platform guardrail is also **mandatory**: a lower layer cannot
remove it. Removal of an *unlocked* lower-priority guardrail is possible
by re-declaring it with ``remove: true`` in the overriding layer.

The whole module is pure: it operates on the parsed
:class:`PipelineConfig` of each layer (or the raw dict / YAML, which it
parses), resolves an effective :class:`PipelineConfig`, and records who
won. Persistence (tenant/project rows, RLS-scoped) lives where the engine
is wired, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from shared_guardrails.config import GuardrailSpec, PipelineConfig, load_config, parse_config
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.types import HOOK_POINTS, HookPoint

# The three layers, ordered least- to most-specific. A later layer
# overrides an earlier one (unless the platform locked the field).
LayerName = Literal["platform", "tenant", "project"]

LAYER_ORDER: tuple[LayerName, ...] = ("platform", "tenant", "project")


class LockedFieldOverrideError(GuardrailConfigError):
    """A lower layer tried to override a platform-locked guardrail.

    Raised only in ``strict`` resolution mode. In the default
    (non-strict) mode the offending override is ignored and recorded in
    the resolution's ``rejected_overrides`` instead of raising.
    """

    def __init__(self, *, hook: HookPoint, key: str, layer: LayerName) -> None:
        super().__init__(
            f"Layer {layer!r} cannot override platform-locked guardrail "
            f"{key!r} at hook {hook!r}."
        )
        self.hook = hook
        self.key = key
        self.layer = layer


@dataclass(frozen=True)
class LayerConfig:
    """One layer's declarative config, tagged with the layer it is.

    Build from a parsed :class:`PipelineConfig` directly, or from a raw
    config dict / YAML string via :meth:`from_dict` / :meth:`from_yaml`.
    """

    layer: LayerName
    config: PipelineConfig

    @classmethod
    def from_dict(cls, layer: LayerName, source: Mapping[str, Any] | None) -> LayerConfig:
        return cls(layer=layer, config=parse_config(dict(source) if source is not None else None))

    @classmethod
    def from_yaml(cls, layer: LayerName, yaml_text: str) -> LayerConfig:
        return cls(layer=layer, config=load_config(yaml_text))


@dataclass(frozen=True)
class FieldProvenance:
    """Which layer's value won for one guardrail key at one hook."""

    hook: HookPoint
    key: str
    type: str
    winning_layer: LayerName
    locked: bool


@dataclass(frozen=True)
class RejectedOverride:
    """A lower layer's override of a locked guardrail that was ignored.

    Surfaced so an admin UI can show "tenant X tried to weaken the
    platform PII guardrail; the attempt was ignored because it is
    locked".
    """

    hook: HookPoint
    key: str
    attempted_by: LayerName
    reason: str


@dataclass
class ResolvedConfig:
    """The effective pipeline config plus a record of how it was built.

    ``config`` is the merged :class:`PipelineConfig` you hand to a
    :class:`~shared_guardrails.pipeline.GuardrailPipeline`. ``provenance``
    records, per hook, which layer won each guardrail key (and whether it
    was locked). ``rejected_overrides`` lists override attempts on locked
    guardrails that were ignored. ``locked_keys`` is a convenience view
    of the locked guardrail keys per hook.
    """

    config: PipelineConfig
    provenance: dict[HookPoint, list[FieldProvenance]] = field(default_factory=dict)
    rejected_overrides: list[RejectedOverride] = field(default_factory=list)

    def __post_init__(self) -> None:
        for hp in HOOK_POINTS:
            self.provenance.setdefault(hp, [])

    @property
    def locked_keys(self) -> dict[HookPoint, list[str]]:
        return {hook: [p.key for p in provs if p.locked] for hook, provs in self.provenance.items()}

    def winning_layer(self, hook: HookPoint, key: str) -> LayerName | None:
        for prov in self.provenance.get(hook, []):
            if prov.key == key:
                return prov.winning_layer
        return None


# The ``remove: true`` marker lets a more-specific layer drop an
# *unlocked* guardrail it inherited from a less-specific layer.
_REMOVE_KEY = "remove"


def _is_removal(spec: GuardrailSpec) -> bool:
    return bool(spec.config.get(_REMOVE_KEY, False))


@dataclass
class _Entry:
    """Mutable accumulator for one guardrail key while merging a hook."""

    spec: GuardrailSpec
    layer: LayerName
    locked: bool
    order: int  # insertion order so output is deterministic


def _merge_hook(
    hook: HookPoint,
    layers: list[LayerConfig],
    *,
    strict: bool,
) -> tuple[list[GuardrailSpec], list[FieldProvenance], list[RejectedOverride]]:
    # Keyed accumulator preserving first-seen order. The platform layer
    # is processed first, so platform-locked keys are known before any
    # lower layer is considered.
    entries: dict[str, _Entry] = {}
    rejected: list[RejectedOverride] = []
    next_order = 0

    for layer_cfg in layers:
        layer = layer_cfg.layer
        for spec in layer_cfg.config.specs_for(hook):
            key = spec.key
            existing = entries.get(key)
            locked_by_platform = existing is not None and existing.locked

            if locked_by_platform:
                # A platform-locked guardrail cannot be overridden,
                # weakened, or removed by a lower layer.
                if strict:
                    raise LockedFieldOverrideError(hook=hook, key=key, layer=layer)
                reason = (
                    "removal of a locked guardrail is not allowed"
                    if _is_removal(spec)
                    else "override of a platform-locked guardrail is not allowed"
                )
                rejected.append(
                    RejectedOverride(
                        hook=hook,
                        key=key,
                        attempted_by=layer,
                        reason=reason,
                    )
                )
                continue

            if _is_removal(spec):
                # Drop an inherited *unlocked* guardrail. A removal that
                # references a key never seen before is a no-op.
                entries.pop(key, None)
                continue

            if existing is None:
                entries[key] = _Entry(
                    spec=spec,
                    layer=layer,
                    locked=spec.locked and layer == "platform",
                    order=next_order,
                )
                next_order += 1
            else:
                # Override in place, keeping the original output position.
                # ``locked`` only ever comes from the platform layer, and
                # an unlocked platform entry stays unlocked here.
                existing.spec = spec
                existing.layer = layer

    ordered = sorted(entries.values(), key=lambda e: e.order)
    specs = [e.spec for e in ordered]
    provenance = [
        FieldProvenance(
            hook=hook,
            key=e.spec.key,
            type=e.spec.type,
            winning_layer=e.layer,
            locked=e.locked,
        )
        for e in ordered
    ]
    return specs, provenance, rejected


def resolve_config(
    platform: LayerConfig | None,
    tenant: LayerConfig | None = None,
    project: LayerConfig | None = None,
    *,
    strict: bool = False,
) -> ResolvedConfig:
    """Merge the three layers into one effective pipeline config.

    Layers are applied least- to most-specific (platform -> tenant ->
    project). A more-specific layer overrides a less-specific one per
    guardrail key, EXCEPT keys the platform marked ``locked``: those are
    mandatory and cannot be overridden or removed by a lower layer.

    A ``None`` layer is treated as empty. Each argument, if given, must
    carry the matching :attr:`LayerConfig.layer` name.

    With ``strict=False`` (default) a locked-field override attempt is
    ignored and recorded in ``rejected_overrides``. With ``strict=True``
    it raises :class:`LockedFieldOverrideError`.
    """
    supplied: dict[LayerName, LayerConfig] = {}
    for arg, expected in ((platform, "platform"), (tenant, "tenant"), (project, "project")):
        if arg is None:
            continue
        if arg.layer != expected:
            raise GuardrailConfigError(
                f"Layer argument for {expected!r} carries layer={arg.layer!r}."
            )
        supplied[expected] = arg  # type: ignore[index]

    # Apply in canonical order regardless of how they were passed.
    ordered_layers = [supplied[name] for name in LAYER_ORDER if name in supplied]

    merged_hooks: dict[HookPoint, list[GuardrailSpec]] = {}
    provenance: dict[HookPoint, list[FieldProvenance]] = {}
    rejected: list[RejectedOverride] = []

    for hook in HOOK_POINTS:
        specs, provs, rej = _merge_hook(hook, ordered_layers, strict=strict)
        merged_hooks[hook] = specs
        provenance[hook] = provs
        rejected.extend(rej)

    return ResolvedConfig(
        config=PipelineConfig(hooks=merged_hooks),
        provenance=provenance,
        rejected_overrides=rejected,
    )


__all__ = [
    "LAYER_ORDER",
    "FieldProvenance",
    "LayerConfig",
    "LayerName",
    "LockedFieldOverrideError",
    "RejectedOverride",
    "ResolvedConfig",
    "resolve_config",
]
