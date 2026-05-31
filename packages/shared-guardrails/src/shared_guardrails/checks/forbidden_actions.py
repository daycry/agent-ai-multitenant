"""Forbidden-actions guardrail (Plan 11, Phase B — task_11_09).

Registers the ``forbidden_actions`` guardrail type: a configured allowlist /
denylist of tool / action names, enforced at ``pre_tool``. A tool that is on
the denylist, or that is not on the allowlist, is blocked before it runs.

This is the ``allowed_tools`` enforcement the 06.14 audit deferred to Plan 11
(finding ``guardrails-1``): chat modes declare ``allowed_tools`` that were
documented as "enforced in the pre_tool guardrail layer", but no such hook
existed. This guardrail is that hook.

Hooks
-----
Primary hook is ``pre_tool`` (validate the tool about to run). It also works at
``post_tool`` (defence in depth — flag a forbidden tool that somehow ran). At
``pre_llm`` / ``post_llm`` there is no tool name, so it is a no-op.

Allowlist resolution (config + per-call metadata)
-------------------------------------------------
The allowlist can come from two places, intersected when both are present:

  * the guardrail's own ``allowed`` config (a static policy), and
  * the per-call ``metadata`` key ``allowed_tools`` (e.g. the active chat
    mode's allowlist, resolved by the host) — read via ``allowed_metadata_key``
    (default ``"allowed_tools"``).

When neither an allowlist nor a denylist is configured, the guardrail does
nothing (it never blocks every tool by accident). Name matching is exact and
case-sensitive by default (tool names are identifiers); set
``case_insensitive: true`` to fold case.

No heavy dependency — pure set membership.

The detection is side-effect-free: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``block`` (a disallowed tool
must not run).
"""

from __future__ import annotations

from typing import Any

from shared_guardrails.checks._common import coerce_action, coerce_severity, coerce_str_list
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity


class ForbiddenActionsGuardrail:
    """Blocks tool calls on a denylist or absent from an allowlist.

    Config (configure at least one of allowed/denied, or rely on metadata):
      - ``denied``               list[str] — tool names that are always
        blocked.
      - ``allowed``              list[str] — when set, ONLY these tools may
        run (anything else is blocked). Intersected with the per-call
        ``allowed_tools`` metadata when that is present.
      - ``allowed_metadata_key`` str       — metadata key carrying the
        per-call allowlist. Default ``"allowed_tools"``. Set to ``""`` /
        ``None`` to ignore metadata.
      - ``case_insensitive``     bool      — fold case when matching. Default
        ``False``.
      - ``severity``             str       — default ``high``.
      - ``suggested_action``     str       — override the default action.
        When unset the guardrail suggests ``block``.

    The result ``payload`` carries the ``tool`` name, the ``reason``
    (``"denylisted"`` | ``"not_in_allowlist"``) and the effective
    ``allowlist`` that was applied.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        denied_raw = config.get("denied")
        allowed_raw = config.get("allowed")
        self._denied = (
            coerce_str_list(denied_raw, field="denied", guardrail="forbidden_actions")
            if denied_raw is not None
            else []
        )
        self._allowed: list[str] | None = (
            coerce_str_list(allowed_raw, field="allowed", guardrail="forbidden_actions")
            if allowed_raw is not None
            else None
        )
        self._case_insensitive = bool(config.get("case_insensitive", False))

        key = config.get("allowed_metadata_key", "allowed_tools")
        self._allowed_meta_key = str(key) if key else None

        if not self._denied and self._allowed is None and self._allowed_meta_key is None:
            raise GuardrailConfigError(
                "forbidden_actions guardrail requires 'denied', 'allowed', or a metadata "
                "allowlist key."
            )

        self._severity = coerce_severity(config.get("severity"), default=Severity.HIGH)
        self._suggested_override = coerce_action(config.get("suggested_action"))

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.BLOCK

    def _fold(self, name: str) -> str:
        return name.lower() if self._case_insensitive else name

    def _meta_allowlist(self, context: GuardrailContext) -> list[str] | None:
        if self._allowed_meta_key is None:
            return None
        raw = context.metadata.get(self._allowed_meta_key)
        if raw is None:
            return None
        if not isinstance(raw, list | tuple | set) or not all(isinstance(t, str) for t in raw):
            return None
        return [str(t) for t in raw]

    def check(self, context: GuardrailContext) -> GuardrailResult:
        # Only tool hooks carry a tool name; everything else is a no-op.
        if context.hook not in ("pre_tool", "post_tool"):
            return GuardrailResult.ok()
        tool = context.tool_name
        if not tool:
            return GuardrailResult.ok()

        folded = self._fold(tool)

        # 1) Denylist always blocks.
        if any(folded == self._fold(d) for d in self._denied):
            return self._blocked(tool, "denylisted", allowlist=None)

        # 2) Allowlist: config ∩ metadata (whichever are present).
        allowlists: list[list[str]] = []
        if self._allowed is not None:
            allowlists.append(self._allowed)
        meta = self._meta_allowlist(context)
        if meta is not None:
            allowlists.append(meta)

        if allowlists:
            effective = {self._fold(t) for lst in allowlists for t in lst}
            # Intersect when more than one source contributes.
            for lst in allowlists:
                effective &= {self._fold(t) for t in lst}
            if folded not in effective:
                return self._blocked(
                    tool,
                    "not_in_allowlist",
                    allowlist=sorted({t for lst in allowlists for t in lst}),
                )

        return GuardrailResult(triggered=False)

    def _blocked(self, tool: str, reason: str, *, allowlist: list[str] | None) -> GuardrailResult:
        detail = (
            f"Tool {tool!r} is denylisted."
            if reason == "denylisted"
            else f"Tool {tool!r} is not in the allowed-tools list."
        )
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=detail,
            suggested_action=self._suggested_action(),
            payload={"tool": tool, "reason": reason, "allowlist": allowlist},
        )


@register_guardrail("forbidden_actions")
def _build_forbidden_actions(config: dict[str, Any]) -> ForbiddenActionsGuardrail:
    return ForbiddenActionsGuardrail(config)


__all__ = ["ForbiddenActionsGuardrail"]
