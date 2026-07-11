"""AGENT_TASK_SPEC construction (refactor P2).

Builds the spec payload the agent-runtime container consumes from an
`ExecutionRequest` (+ the resolved model / policy / budgets the worker
computed). Pure functions — no DB, no docker; the only lazy import is the
worker-side runtime catalog (`workers.test_runtime`) to resolve tool images.

`workers.execution` re-exports everything here (its historical home);
`_build_runtime_env` stays there (it mints the internal token — api_server).
"""

from __future__ import annotations

from typing import Any

from workers.agent_tool_schemas import build_model_tool_schemas
from workers.run_contract import ExecutionRequest

# Track 1 / ADR 0021 addendum: a base shell allowlist for the natively-agentic
# Claude Agent SDK. UNIONed with the project's allowlist for `claude_sdk` runs ONLY,
# so the SDK can reconcile the worktree with file ops instead of being straitjacketed
# by an empty allowlist. Safe inside the sandbox (cap-drop ALL, read-only rootfs except
# /workspace+/tmp, internal network/no egress, no docker socket — ADR 0012/0019/0040):
# every command is confined to the container and the task worktree.
# NOTE (Feature D): `git` is deliberately NOT here. The agent never commits/pushes
# (the worker owns git — principle 2, no credentials in the sandbox), and git is
# BROKEN here anyway: the worktree's `.git` points to the bare repo's worktree
# metadata, which is NOT mounted in the sandbox → every `git` exits 128. Exposing it
# only made the agent waste turns on cryptic failures; the prompt tells it the
# platform persists changes automatically.
_SDK_BASE_SHELL_COMMANDS: frozenset[str] = frozenset(
    {
        "rm",
        "mv",
        "cp",
        "mkdir",
        "rmdir",
        "ls",
        "cat",
        "find",
        "grep",
        "touch",
        "head",
        "tail",
        "wc",
        "diff",
        # Read/text utilities the models reach for naturally to page/inspect
        # files (G6a, audit 2026-07-03: `sed -n` was denied live twice, forcing
        # sterile retries). No fine-grained arg validation: writing to the
        # worktree is ALREADY allowed (rm/mv/cp are here), so these read-oriented
        # tools add no attack surface — the sandbox (internal net, no docker
        # socket, cap-drop ALL, seccomp) is the real boundary, not the allowlist.
        "sed",
        "awk",
        "sort",
        "uniq",
        "cut",
        "tr",
        "echo",
    }
)


def _agent_spec(  # noqa: PLR0912 - secuencia lineal de claves opcionales del spec
    request: ExecutionRequest,
    approval_policy: dict[str, Any] | None,
    *,
    model_spec: dict[str, Any] | None = None,
    acceptance_criteria: list[Any] | None = None,
    wall_clock_budget_s: float | None = None,
    max_iterations_budget: int | None = None,
    max_tokens_budget: int | None = None,
) -> dict[str, Any]:
    """The `AGENT_TASK_SPEC` payload for the container.

    ``model_spec`` is the RESOLVED model (kind + endpoint + credential,
    ADR 0057 F1) the worker computed from ``request.model``; ``None`` keeps
    the request's spec verbatim (pure-function callers / scripted tests).

    ``acceptance_criteria`` (the task's definition of "done") is merged into
    ``spec["task"]`` so the agent's decision prompt can show what completing the
    task means — letting the TASK drive read/write/test behaviour instead of a
    blanket rule. ``None``/empty keeps ``task`` as the request sent it.
    """
    task_payload = (
        {**request.task, "acceptance_criteria": acceptance_criteria}
        if acceptance_criteria
        else request.task
    )
    spec: dict[str, Any] = {"task": task_payload, "model": model_spec or request.model}
    # Agent-loop safeguard budgets. Align the internal wall-clock with the
    # per-provider container budget so a slow claude_sdk run isn't aborted early
    # by the 600s default (max_wall_clock_exceeded). An operator-supplied value in
    # request.budgets always wins (setdefault).
    budgets = dict(request.budgets or {})
    if wall_clock_budget_s is not None:
        budgets.setdefault("max_wall_clock_s", float(wall_clock_budget_s))
    if max_iterations_budget is not None:
        budgets.setdefault("max_iterations", int(max_iterations_budget))
    if max_tokens_budget is not None:
        # Auditoría 2026-07-02: con la contabilidad de usage arreglada (F1.4),
        # el default de 100k del runtime corta runs sanos de claude_sdk a ~23
        # iteraciones — presupuesto por-kind realista, como max_iterations.
        budgets.setdefault("max_tokens", int(max_tokens_budget))
    if budgets:
        spec["budgets"] = budgets
    # With a policy the loop gates sensitive tool calls (task_02_33).
    if approval_policy:
        spec["approval_policy"] = approval_policy
    # Forward the chat mode's tool allowlist (task_06_14_07). Only emit the
    # key when set — `None` means "no key", which the runtime reads as "no
    # restriction". An empty list IS emitted (block every tool).
    if request.allowed_tools is not None:
        spec["allowed_tools"] = request.allowed_tools
    # Forward the project's shell-command allowlist (task_06_16_02). Only emit
    # the key when set — `None` means "no key" (shell_exec not registered). An
    # empty list IS emitted: it registers a deny-all shell_exec. For a natively-
    # agentic `claude_sdk` run, UNION the base VCS/file allowlist so the SDK can
    # reconcile the worktree (Track 1 / ADR 0021) — this also forces shell_exec to
    # register even when the project pinned nothing. Thin providers are unchanged.
    kind = (model_spec or request.model or {}).get("kind")
    allowed_commands = request.allowed_commands
    if kind == "claude_sdk":
        allowed_commands = sorted(_SDK_BASE_SHELL_COMMANDS.union(allowed_commands or []))
    if allowed_commands is not None:
        spec["allowed_commands"] = allowed_commands
    # Forward the project's HTTP-tools domain allowlist (prod-12 Fase B /
    # gap4-2). Only emit the key when set — `None` = legacy payload (the
    # runtime keeps its deny-all default). An empty list IS emitted (explicit
    # deny-all). The runtime re-validates every resolution with the ssrf_guard
    # (Fase A) — see tests/unit/test_ssrf_wiring_sentinel.py.
    if request.allowed_domains is not None:
        spec["allowed_domains"] = [str(d) for d in request.allowed_domains]
    # Forward the project's stack runtime (task_06_16_03). Only emit the key
    # when the project pinned a stack; `None` means "no key", which the runtime
    # reads as "keep each `run_*` tool's own default runtime" (python-pytest) —
    # backward-compatible for existing Python projects.
    if request.default_runtime_template is not None:
        spec["default_runtime_template"] = request.default_runtime_template
    # Forward the serialised executable ToolSpec list (task_06_18_05). Only emit
    # when the agent has assignments — `None` means "no key", which the runtime
    # reads as "register no new families" (pre-06.18 echo/noop behaviour). Before
    # forwarding we resolve each docker_command spec's `runtime_template` to a
    # concrete image (the worker owns the runtime catalog; the sandboxed runtime
    # must not import `shared_test_runtimes`).
    if request.tool_specs is not None:
        spec["tool_specs"] = _resolve_tool_spec_images(
            request.tool_specs, request.default_runtime_template
        )
    # Forward the project's MCP server declarations (task_06_18_12 / ADR 0052).
    # Only emit the key when the project declares servers -- `None` means "no
    # key", which the runtime reads as "open no MCP session" (feature-safe,
    # pre-06.18 behaviour). The runtime starts an `MCPToolRunner`, connects each
    # server and registers its `<server>.<tool>` tools before the graph.
    if request.mcp_servers is not None:
        spec["mcp_servers"] = request.mcp_servers
    # Forward the assigned skills' prompt fragments (task_06_18_13 / ADR 0050).
    # Only emit the key when the agent has skills -- `None` means "no key", which
    # the runtime reads as "keep the system prompt untouched" (backward-compat).
    if request.skill_prompt_fragments is not None:
        spec["skill_prompt_fragments"] = request.skill_prompt_fragments
    # Audit cluster C1 (F51): a REVIEW run MUST carry its review context to the
    # container. The orchestrator builds `review_context` (acceptance criteria +
    # the implementer's prior output + the <test-report>) but until now the worker
    # never forwarded it, so the reviewer ran blind on title+description, produced
    # no <verdict>, and the worker defensively rejected every reviewed task
    # (in_review→backlog→blocked). The runtime reads `review` to build the
    # reviewer's verdict-instruction preamble (`build_review_preamble`).
    if request.review:
        spec["review"] = True
        if request.review_context is not None:
            spec["review_context"] = request.review_context
    # Inter-run reviewer feedback (A2): a re-dispatched IMPLEMENTER run carries the
    # AI reviewer's prior rejection payloads so the runtime can fold them into a
    # corrective preamble (`build_prior_feedback_preamble`). Only emit when present
    # (`None`/absent = no prior rejection) — "no key" is the unchanged behaviour for
    # a first dispatch (backward-compat). Independent of the REVIEW keys above: this
    # is the implementer being told what to fix, not the reviewer judging.
    if request.prior_review_feedback is not None:
        spec["prior_review_feedback"] = request.prior_review_feedback
    # Feature C: human task/plan comments → the runtime folds them into a contextual
    # preamble (`build_comments_preamble`). Only emit when present (backward-compat).
    if request.task_comments is not None:
        spec["task_comments"] = request.task_comments
    # P0-1 (investigación 2026-07-11): the agent's persona → the runtime prepends
    # it as the FIRST system-preamble block (`build_persona_preamble`), so the run
    # carries the agent's domain identity (role, conventions, expertise). Only
    # emit when present (backward-compat).
    if request.agent_persona is not None:
        spec["agent_persona"] = request.agent_persona
    # Agentes #2: advertise the agent's tools to the LLM so it can actually call
    # them (memory_recall/rag_search/read_file/…). Without this the model never
    # sees any tool → it can neither recall memory nor work through tools, for ANY
    # provider. Schemas come from the canonical builtin catalog + custom tool_specs,
    # filtered to the effective allowlist (`allowed_tools`). Set inside `model` so
    # `build_provider_client` reads `spec["tools"]` and passes them to complete().
    # `include_system_tools=True`: the memory + orchestration families are
    # runtime CAPABILITIES (not catalog assignments), so they never reach the
    # allowlist — we advertise them here so every agent can recall/store memory
    # and move the Kanban (H0/H3). An explicit empty allowlist (discussion mode)
    # still suppresses everything inside build_model_tool_schemas.
    model_tools = build_model_tool_schemas(
        request.allowed_tools, request.tool_specs, include_system_tools=True
    )
    if model_tools:
        spec["model"] = {**spec["model"], "tools": model_tools}
    return spec


def _resolve_tool_spec_images(
    tool_specs: list[dict[str, Any]], project_default_runtime: str | None
) -> list[dict[str, Any]]:
    """Pre-resolve each ``docker_command`` ToolSpec's ``runtime_template`` to a
    concrete docker image (Plan 06.18 task_06_18_05).

    The agent-runtime is a separate container with no access to
    ``shared_test_runtimes``; only the worker can map a runtime-template id to
    an image. So we resolve here — honouring the project stack over the tool
    default (Plan 06.16 precedence) — and replace ``runtime_template`` with an
    explicit ``image`` the runtime's ``docker_command`` builder consumes
    directly. Specs that already carry an explicit ``image`` (Plan 05 custom
    tools) are left untouched. An unknown runtime id surfaces as a clear
    ``RuntimeResolutionError`` at dispatch, not a silent boot crash inside the
    container.
    """
    from workers.test_runtime import resolve_run_runtime_image

    resolved: list[dict[str, Any]] = []
    for raw in tool_specs:
        spec = dict(raw)
        if spec.get("implementation_type") == "docker_command":
            config = dict(spec.get("config") or {})
            if not config.get("image"):
                tool_runtime = config.pop("runtime_template", None)
                config["image"] = resolve_run_runtime_image(
                    project_default_runtime,
                    str(tool_runtime) if tool_runtime else None,
                )
            spec["config"] = config
        resolved.append(spec)
    return resolved
