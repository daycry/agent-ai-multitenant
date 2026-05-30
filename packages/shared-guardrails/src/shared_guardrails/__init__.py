"""Declarative guardrails engine (Plan 11).

A :class:`GuardrailPipeline` loads a declarative config (YAML / dict)
describing, per hook point (``pre_llm`` | ``post_llm`` | ``pre_tool`` |
``post_tool``), an ordered list of guardrails to run. Each guardrail is
an object implementing the :class:`Guardrail` Protocol —
``check(context) -> GuardrailResult`` — looked up by ``type`` in a
:class:`GuardrailRegistry`. Running a hook against a
:class:`GuardrailContext` yields an aggregated :class:`PipelineDecision`
carrying the per-guardrail outcomes and the decisive action.

Phase A delivers the engine core (pipeline + config model + actions +
registry) plus two trivial built-ins (``keyword`` / ``regex``) so it is
exercisable. The 12 production guardrail types are Phase B; the layered
platform->tenant->project config merge with lockable fields is
task_11_02.
"""

# Importing the built-ins for their import-time registration side effect
# so the `keyword` / `regex` types are available out of the box.
from shared_guardrails import builtins as _builtins  # noqa: F401
from shared_guardrails.actions import (
    DEFAULT_MAX_RETRIES,
    AppliedAction,
    BlockOutcome,
    EscalateOutcome,
    GuardrailBlocked,
    RedactOutcome,
    RetryOutcome,
    Transformer,
    TransformOutcome,
    WarnOutcome,
    apply_action,
    apply_block,
    apply_escalate,
    apply_redact,
    apply_retry,
    apply_transform,
    apply_warn,
)
from shared_guardrails.config import (
    GuardrailSpec,
    PipelineConfig,
    load_config,
    parse_config,
)
from shared_guardrails.exceptions import (
    GuardrailConfigError,
    GuardrailError,
    UnknownGuardrailTypeError,
)
from shared_guardrails.layers import (
    LAYER_ORDER,
    FieldProvenance,
    LayerConfig,
    LayerName,
    LockedFieldOverrideError,
    RejectedOverride,
    ResolvedConfig,
    resolve_config,
)
from shared_guardrails.pipeline import GuardrailPipeline
from shared_guardrails.registry import (
    Guardrail,
    GuardrailFactory,
    GuardrailRegistry,
    default_registry,
    register_guardrail,
)
from shared_guardrails.types import (
    HOOK_POINTS,
    Action,
    GuardrailContext,
    GuardrailOutcome,
    GuardrailResult,
    HookPoint,
    PipelineDecision,
    Severity,
    most_severe_action,
)

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "HOOK_POINTS",
    "LAYER_ORDER",
    "Action",
    "AppliedAction",
    "BlockOutcome",
    "EscalateOutcome",
    "FieldProvenance",
    "Guardrail",
    "GuardrailBlocked",
    "GuardrailConfigError",
    "GuardrailContext",
    "GuardrailError",
    "GuardrailFactory",
    "GuardrailOutcome",
    "GuardrailPipeline",
    "GuardrailRegistry",
    "GuardrailResult",
    "GuardrailSpec",
    "HookPoint",
    "LayerConfig",
    "LayerName",
    "LockedFieldOverrideError",
    "PipelineConfig",
    "PipelineDecision",
    "RedactOutcome",
    "RejectedOverride",
    "ResolvedConfig",
    "RetryOutcome",
    "Severity",
    "TransformOutcome",
    "Transformer",
    "UnknownGuardrailTypeError",
    "WarnOutcome",
    "apply_action",
    "apply_block",
    "apply_escalate",
    "apply_redact",
    "apply_retry",
    "apply_transform",
    "apply_warn",
    "default_registry",
    "load_config",
    "most_severe_action",
    "parse_config",
    "register_guardrail",
    "resolve_config",
]
