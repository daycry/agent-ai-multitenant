"""Wire contract of one agent execution (refactor P2).

The DTOs the orchestrator and the worker exchange over Celery: the request
payload (`ExecutionRequest.as_dict`/`from_dict` — the shape IS the contract,
do not change it without both sides), the stored outcome, and the tenant
boundary error. Pure module — no DB, no docker, no api_server imports — so
the pre-commit mypy gate covers it.

`workers.execution` re-exports everything here (its historical home); import
from either place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CrossTenantExecutionError(RuntimeError):
    """An ExecutionRequest's `task_id` does not belong to its declared
    `tenant_id`.

    The worker connects with the BYPASSRLS `migrations_user` role
    (workers/config.py) because it legitimately writes `executions` rows
    for many tenants — so RLS cannot catch a tampered or buggy Celery
    payload that pairs one tenant with another tenant's task. We validate
    the task↔tenant ownership explicitly at the worker boundary instead
    (Plan 06.14 task_06_14_02 / multi-tenancy-rls-1, multi-tenancy-rls-5).
    """


@dataclass(frozen=True)
class ExecutionRequest:
    """Everything the worker needs to conduct one execution.

    The orchestrator (task_02_31) builds this from a task event. `task`
    and `model` are the spec dicts the agent-runtime entrypoint expects
    (`AGENT_TASK_SPEC`); `task_id` / `tenant_id` are the real DB ids the
    `executions` row is keyed on.
    """

    tenant_id: str
    task_id: str
    agent_id: str | None
    task: dict[str, Any]
    model: dict[str, Any]
    budgets: dict[str, Any] | None = None
    # The active chat mode's tool whitelist (`ChatModeConfig.allowed_tools`,
    # task_06_14_07). ``None`` = no restriction (every registered tool
    # callable). A list — including an empty one — installs the allowlist;
    # the agent-runtime's ToolRegistry then rejects any tool outside it at
    # call time. We keep ``None`` distinct from ``[]`` so the "block every
    # tool" discussion mode is expressible.
    allowed_tools: list[str] | None = None
    # The project's shell-command allowlist (`projects.allowed_commands`,
    # Plan 06.16 task_06_16_02). The orchestrator threads it from the task's
    # project; the worker forwards it into the spec so the runtime can build a
    # per-project `shell_exec` bound to exactly these program basenames
    # (deny-by-default). ``None`` = no key (shell_exec not registered, e.g. a
    # bare run); a list — including ``[]`` — registers shell_exec, with the
    # empty list meaning deny-all (every command rejected).
    allowed_commands: list[str] | None = None
    # The project's HTTP-tools domain allowlist (`projects.allowed_domains`,
    # prod-12 Fase B / gap4-2). The orchestrator threads it from the task's
    # project; the worker forwards it into the spec so the runtime binds
    # `http_request` + the http_endpoint tools to exactly these FQDNs. ``None``
    # = no key (legacy payloads); a list — including ``[]`` — is forwarded, the
    # empty list meaning deny-all (the historical accidental default, now
    # explicit). GATED: only wired once the runtime's ssrf_guard (Fase A) is in
    # both tools — see tests/unit/test_ssrf_wiring_sentinel.py.
    allowed_domains: list[str] | None = None
    # The project's stack runtime (`projects.default_runtime_template`, Plan
    # 06.16 task_06_16_03). The orchestrator threads it from the task's project;
    # the worker forwards it so the runtime's `run_*` docker_command tools
    # (`run_pytest`/`run_lint`/`run_typecheck`/`run_build`) resolve their
    # RuntimeTemplate from the project stack — a PHP project with `php-phpunit`
    # runs `run_pytest` there, not in `python-pytest`. `None` (the default, and
    # what a project that pinned no stack carries) keeps each tool's own default
    # runtime (backward-compatible — no behaviour change for Python projects).
    default_runtime_template: str | None = None
    # The agent's assigned tools serialised as executable ToolSpec dicts
    # (`serialize_agent_tool_specs`, Plan 06.18 task_06_18_05). The orchestrator
    # builds it from the agent's `agent_tools` rows; the worker forwards it into
    # the agent spec so `__main__.run_task` registers the real executors under
    # canonical names. `None` = no key (no assignments) → the runtime keeps the
    # pre-06.18 echo/noop behaviour (06.15 backward-compat). Before forwarding,
    # the worker resolves any `docker_command` spec's `runtime_template` to a
    # concrete image (the worker owns the runtime catalog; the sandboxed
    # runtime must not import `shared_test_runtimes`).
    tool_specs: list[dict[str, Any]] | None = None
    # The project's MCP server declarations (`projects.mcp_servers`, JSONB; Plan
    # 06.18 task_06_18_12 / ADR 0052). The orchestrator threads it from the
    # task's project; the worker forwards it into the agent spec so
    # `__main__.run_task` starts an `MCPToolRunner`, connects each server (auth
    # via Vault) and registers its `<server>.<tool>` tools before the graph.
    # `None` = no key (no MCP servers declared) -> the runtime opens no MCP
    # session, the pre-06.18 behaviour (feature-safe). Each entry mirrors
    # `shared_mcp.MCPServerConfig` / `api_server.mcp.config.MCPServerConfigModel`.
    mcp_servers: list[dict[str, Any]] | None = None
    # The agent's assigned skills' `prompt_fragment` list (Plan 06.18
    # task_06_18_13 / ADR 0050). The orchestrator resolves it from the agent's
    # `agent_skills` rows; the worker forwards it into the agent spec so
    # `__main__.run_task` prepends it to the system prompt EFECTIVO. `None` = no
    # key (no skills assigned) -> the runtime keeps the current prompt untouched
    # (backward-compatible).
    skill_prompt_fragments: list[str] | None = None
    # prod-17 (bucle del AI reviewer): when True, this run is a REVIEW of the task
    # by its reviewer agent. On finish the worker applies the parsed verdict
    # (parse_reviewer_output → apply_reviewer_verdict) instead of the normal
    # done/failed task transition (dag_01). `review_context` carries the review
    # input (acceptance criteria + the implementer's prior output) the runtime
    # injects into the reviewer's prompt. Default False/None = a normal run.
    review: bool = False
    review_context: dict[str, Any] | None = None
    # Inter-run reviewer feedback (A2): the AI reviewer's prior rejection payloads
    # for THIS task, threaded by the orchestrator when the task was rejected on an
    # earlier pass and re-dispatched to the implementer (in_review → backlog →
    # ready). Each entry is `{failed_criterion, what_to_fix, testreport_evidence}`.
    # The runtime folds them into a corrective preamble so the IMPLEMENTER knows
    # what to fix. `None` = no key (no prior rejection) → identical to the current
    # behaviour for a first dispatch (backward-compat). Distinct from `review` /
    # `review_context`, which drive the REVIEWER run, not the implementer.
    prior_review_feedback: list[dict[str, Any]] | None = None
    # Feature C: human comments on this task/plan (added in the Kanban/plan UI),
    # threaded by the orchestrator. Each entry `{scope, content}`. The runtime folds
    # them into a contextual preamble so the agent takes them into account. `None` =
    # no key (no comments) → backward-compat.
    task_comments: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe dict — the Celery payload the orchestrator sends."""
        return {
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "task": self.task,
            "model": self.model,
            "budgets": self.budgets,
            "allowed_tools": self.allowed_tools,
            "allowed_commands": self.allowed_commands,
            "allowed_domains": self.allowed_domains,
            "default_runtime_template": self.default_runtime_template,
            "tool_specs": self.tool_specs,
            "mcp_servers": self.mcp_servers,
            "skill_prompt_fragments": self.skill_prompt_fragments,
            "review": self.review,
            "review_context": self.review_context,
            "prior_review_feedback": self.prior_review_feedback,
            "task_comments": self.task_comments,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExecutionRequest:
        """Rebuild a request from the Celery payload (worker side)."""
        return cls(
            tenant_id=raw["tenant_id"],
            task_id=raw["task_id"],
            agent_id=raw.get("agent_id"),
            task=raw["task"],
            model=raw["model"],
            budgets=raw.get("budgets"),
            allowed_tools=raw.get("allowed_tools"),
            allowed_commands=raw.get("allowed_commands"),
            allowed_domains=raw.get("allowed_domains"),
            default_runtime_template=raw.get("default_runtime_template"),
            tool_specs=raw.get("tool_specs"),
            mcp_servers=raw.get("mcp_servers"),
            skill_prompt_fragments=raw.get("skill_prompt_fragments"),
            review=bool(raw.get("review", False)),
            review_context=raw.get("review_context"),
            prior_review_feedback=raw.get("prior_review_feedback"),
            task_comments=raw.get("task_comments"),
        )


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one conducted execution.

    ``pending_task_event`` is the deferred ``(task, old_status, new_status)``
    finish event that the CALLER must publish only after releasing the per-task
    run-lock (H1): the orchestrator reacts to it in milliseconds and a dispatch
    that lands while the lock is still held is dropped as
    ``concurrent_run_locked``. Process-local — never serialized.
    """

    execution_id: str
    status: str
    abort_code: str | None
    pending_task_event: tuple[Any, str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary — what the Celery result backend stores."""
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "abort_code": self.abort_code,
        }
