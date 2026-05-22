"""The orchestration builtin tools (task_02_18).

kanban_update, task_comment, notify_user and agent_invoke act on
*platform* state — but the agent container has no platform access (Fase
B: dedicated internal network, no DB, no Redis). So these tools do not
reach out; they record a validated **effect** into an `OrchestrationSink`.
The worker — which does have DB access — drains the sink and applies
the effects.

Fase D ships the tools, the validation and the sink. Wiring the
worker-side application of the effects lands with the live Kanban
(Fase E).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.tools import ToolRegistry, ToolResult

# The Kanban statuses a task may be moved to. Kept in lockstep with
# api_server.db.domain.TaskStatus — the two packages deliberately do
# not import one another (the runtime is container-side).
KANBAN_STATUSES = frozenset(
    {"backlog", "ready", "in_progress", "in_review", "blocked", "done", "cancelled"}
)


@dataclass
class OrchestrationSink:
    """Collects the platform-effect requests an agent makes.

    The agent records *intents* here; the worker applies them. A plain
    list keeps it trivially inspectable in tests.
    """

    effects: list[dict[str, object]] = field(default_factory=list)

    def emit(self, effect: dict[str, object]) -> None:
        self.effects.append(effect)


def _require_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass
class OrchestrationTools:
    """kanban_update / task_comment / notify_user / agent_invoke — each
    validates its arguments and emits one effect into the sink."""

    sink: OrchestrationSink

    def _emit(self, effect_type: str, payload: dict[str, object]) -> ToolResult:
        effect: dict[str, object] = {"effect": effect_type, **payload}
        self.sink.emit(effect)
        return ToolResult(ok=True, output=effect)

    def kanban_update(self, args: dict[str, object]) -> ToolResult:
        task_id, status = args.get("task_id"), args.get("status")
        if not _require_str(task_id):
            return ToolResult(ok=False, error="kanban_update requires a 'task_id'")
        if status not in KANBAN_STATUSES:
            return ToolResult(
                ok=False,
                error=f"invalid kanban status: {status!r}",
                output={"valid": sorted(KANBAN_STATUSES)},
            )
        return self._emit("kanban_update", {"task_id": task_id, "status": status})

    def task_comment(self, args: dict[str, object]) -> ToolResult:
        task_id, body = args.get("task_id"), args.get("body")
        if not _require_str(task_id):
            return ToolResult(ok=False, error="task_comment requires a 'task_id'")
        if not _require_str(body):
            return ToolResult(ok=False, error="task_comment requires a non-empty 'body'")
        return self._emit("task_comment", {"task_id": task_id, "body": body})

    def notify_user(self, args: dict[str, object]) -> ToolResult:
        user_id, message = args.get("user_id"), args.get("message")
        if not _require_str(user_id):
            return ToolResult(ok=False, error="notify_user requires a 'user_id'")
        if not _require_str(message):
            return ToolResult(ok=False, error="notify_user requires a non-empty 'message'")
        return self._emit("notify_user", {"user_id": user_id, "message": message})

    def agent_invoke(self, args: dict[str, object]) -> ToolResult:
        agent_id, prompt = args.get("agent_id"), args.get("prompt")
        if not _require_str(agent_id):
            return ToolResult(ok=False, error="agent_invoke requires an 'agent_id'")
        if not _require_str(prompt):
            return ToolResult(ok=False, error="agent_invoke requires a non-empty 'prompt'")
        return self._emit("agent_invoke", {"agent_id": agent_id, "prompt": prompt})


def register_orchestration_tools(registry: ToolRegistry, sink: OrchestrationSink) -> None:
    """Register all four orchestration tools onto `registry`."""
    tools = OrchestrationTools(sink)
    registry.register("kanban_update", tools.kanban_update)
    registry.register("task_comment", tools.task_comment)
    registry.register("notify_user", tools.notify_user)
    registry.register("agent_invoke", tools.agent_invoke)
