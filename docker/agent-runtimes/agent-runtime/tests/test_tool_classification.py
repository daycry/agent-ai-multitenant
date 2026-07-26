"""`tool_classification` — batería directa del módulo del que cuelgan todas las
guardas de convergencia (`task_wf_53`).

Este módulo decide tres cosas de las que depende que un run termine bien:

  * qué cuenta como **investigar** (gana novedad, no es progreso entregable),
  * qué cuenta como **producir** (el latch `has_produced`, que cambia el empujón
    de «escribe algo» a «cierra» y evita escalar un run que SÍ entregó),
  * qué es **read-only** (exento del aborto duro por bucle repetitivo),

y además distingue el fallo de tool que es culpa de la PLATAFORMA del que es
culpa del agente — porque el primero no puede acumular esterilidad.

Tenía cero tests. Cada una de esas tres decisiones, mal, produce un fallo caro y
difícil de atribuir: un run que entregó y se escala igual, o un bucle infinito
que nadie corta.
"""

from __future__ import annotations

import pytest
from agent_runtime.tool_classification import (
    _base_tool_name,
    _is_mutating_tool,
    _is_platform_error,
    _is_producing_tool,
    _is_readonly_tool,
    _is_research_tool,
    _read_target,
)


# ---------------------------------------------------------------------------
# Namespace stripping — la razón de ser de la clasificación por VERBO
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("write_file", "write_file"),
        ("filesystem.write_file", "write_file"),
        ("mi_servidor.sub.write_file", "write_file"),
        ("", ""),
        (None, ""),
    ],
)
def test_the_namespace_is_stripped_before_classifying(raw: str | None, expected: str) -> None:
    assert _base_tool_name(raw) == expected


def test_a_writer_wired_by_mcp_counts_as_producing() -> None:
    # C2/F24: la clasificación miraba el nombre desnudo, así que un fichero
    # escrito por un servidor MCP era invisible — `has_produced` no prendía y el
    # self-review escalaba un run que SÍ había producido.
    assert _is_producing_tool("fs.write_file") is True
    assert _is_producing_tool("cualquier_servidor.stack_exec") is True


# ---------------------------------------------------------------------------
# Las tres clases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool", ["list_files", "read_file", "memory_recall", "rag_search", "search_code"]
)
def test_research_tools_are_research_and_read_only(tool: str) -> None:
    assert _is_research_tool(tool) is True
    # La lista read-only ES la de investigación: repetir una lectura desperdicia
    # turnos pero no puede corromper el entregable.
    assert _is_readonly_tool(tool) is True
    assert _is_mutating_tool(tool) is False
    assert _is_producing_tool(tool) is False


@pytest.mark.parametrize(
    "tool", ["write_file", "edit_file", "create_file", "shell_exec", "stack_exec", "apply_patch"]
)
def test_producing_tools_produce_and_can_hard_abort(tool: str) -> None:
    assert _is_producing_tool(tool) is True
    assert _is_research_tool(tool) is False
    # Repetir un mutador sí puede corromper el entregable: aborto duro.
    assert _is_mutating_tool(tool) is True


def test_an_unknown_verb_is_treated_as_a_mutator() -> None:
    # Conservador por diseño: defaultear a read-only dejaría sin aborto duro a
    # cualquier verbo nuevo, y un escritor desbocado es peor que un lector.
    assert _is_mutating_tool("echo") is True
    assert _is_mutating_tool("un_verbo_que_no_existe") is True
    assert _is_readonly_tool("echo") is False
    assert _is_producing_tool("echo") is False


def test_verbs_without_a_builtin_executor_stay_classified() -> None:
    # `search_code` y `apply_patch` no los registra ningún ejecutor built-in del
    # runtime, pero la clasificación es por VERBO, no por tool registrada: un
    # proyecto puede aportarlos por MCP (`patcher.apply_patch`) y entonces la
    # clasificación decide bien o el run se escala habiendo producido. Sacarlos
    # de las tablas «porque no existen» sería una regresión para ese caso — que
    # es justo el que motivó el stripping de namespace.
    assert _is_producing_tool("patcher.apply_patch") is True
    assert _is_research_tool("grepper.search_code") is True


# ---------------------------------------------------------------------------
# `_read_target` — qué distingue explorar de releer lo mismo
# ---------------------------------------------------------------------------
def test_reading_a_new_path_is_a_new_target() -> None:
    assert _read_target("read_file", {"path": "a.py"}) == "read_file:a.py"
    assert _read_target("list_files", {"path": "src"}) == "list_files:src"
    # Sin path, la raíz — dos `list_files` a secas son el MISMO target.
    assert _read_target("list_files", {}) == "list_files:."


def test_paging_the_same_file_is_not_a_new_target() -> None:
    # Si `offset`/`limit` contaran, releer el mismo fichero por trozos
    # simularía exploración indefinidamente y el detector de churn no saltaría.
    first = _read_target("read_file", {"path": "a.py", "offset": 0, "limit": 100})
    second = _read_target("read_file", {"path": "a.py", "offset": 100, "limit": 100})
    assert first == second


def test_a_search_without_a_query_has_no_target() -> None:
    # Una búsqueda vacía no explora nada: no puede ganar novedad.
    assert _read_target("rag_search", {"query": "   "}) is None
    assert _read_target("memory_recall", {}) is None
    assert _read_target("search_code", {"pattern": ""}) is None


def test_search_code_accepts_either_argument_name() -> None:
    assert _read_target("search_code", {"query": "foo"}) == "search_code:foo"
    assert _read_target("search_code", {"pattern": "foo"}) == "search_code:foo"


def test_a_producing_tool_has_no_read_target() -> None:
    assert _read_target("write_file", {"path": "a.py"}) is None


def test_the_read_target_is_namespace_agnostic() -> None:
    # Dos lecturas del mismo fichero, una builtin y otra por MCP, son el MISMO
    # target: si no, alternarlas fabricaría novedad infinita.
    assert _read_target("fs.read_file", {"path": "a.py"}) == _read_target(
        "read_file", {"path": "a.py"}
    )


# ---------------------------------------------------------------------------
# Culpa de la plataforma vs culpa del agente
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error",
    [
        "tool not allowed for this agent",
        "Unknown tool: foo",
        "no executor registered",
        "Permission denied",
        "EACCES: permission denied",
        "read-only file system",
        "worktree is empty",
    ],
)
def test_a_platform_failure_is_not_the_agents_fault(error: str) -> None:
    # Estos no pueden acumular esterilidad: el agente no hizo nada mal y
    # escalarlo por ellos castiga al run por un fallo de la plataforma.
    assert _is_platform_error({"error": error}) is True


def test_a_guessed_path_that_does_not_exist_is_the_agents_churn() -> None:
    # Anti-gaming (r5a): un file-not-found sobre una ruta que el agente se
    # inventó SÍ es churn estéril — si no, bastaría con leer rutas falsas para
    # no agotar nunca el presupuesto.
    assert _is_platform_error({"error": "no such file or directory: /inventado.py"}) is False


def test_no_error_is_not_a_platform_error() -> None:
    assert _is_platform_error({"ok": True, "error": None}) is False
    assert _is_platform_error({}) is False
    assert _is_platform_error(None) is False
    assert _is_platform_error("un string, no una observación") is False


# ---------------------------------------------------------------------------
# El latch `has_produced` — la consecuencia de clasificar bien o mal
# ---------------------------------------------------------------------------
def _loop_for_latch() -> object:
    from agent_runtime.graph import AgentDeps, _AgentLoop
    from agent_runtime.loop_detection import LoopDetector
    from agent_runtime.safeguards import Budgets, SafeguardTracker

    class _NoModel:
        def decide(self, state: dict) -> object:  # noqa: ARG002  # pragma: no cover
            raise AssertionError

        def review(self, state: dict) -> object:  # noqa: ARG002  # pragma: no cover
            raise AssertionError

    class _NoTools:
        def call(self, tool: str, args: dict) -> object:  # noqa: ARG002  # pragma: no cover
            raise AssertionError

    deps = AgentDeps(model=_NoModel(), tools=_NoTools(), guardrails=None)  # type: ignore[arg-type]
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())


def test_a_successful_write_latches_has_produced() -> None:
    loop = _loop_for_latch()
    assert loop.has_produced is False  # type: ignore[attr-defined]
    loop._track_research("write_file", {"tool_args": {"path": "a.py"}}, {"ok": True})  # type: ignore[attr-defined]
    assert loop.has_produced is True  # type: ignore[attr-defined]


def test_a_failed_producing_tool_does_not_latch() -> None:
    # G3/r4: un `shell_exec` denegado o un write que erró no produjeron nada.
    # Prender el latch convertía cada corte de safeguard de ABORTED en
    # `needs_human_review` —contaminando la bandeja del humano con runs
    # estériles— y cambiaba el empujón a «cierra» sin nada que cerrar.
    loop = _loop_for_latch()
    loop._track_research("shell_exec", {}, {"ok": False, "error": "command not allowed"})  # type: ignore[attr-defined]
    assert loop.has_produced is False  # type: ignore[attr-defined]


def test_reading_never_latches_however_much_you_read() -> None:
    loop = _loop_for_latch()
    for path in ("a.py", "b.py", "c.py"):
        loop._track_research("read_file", {"tool_args": {"path": path}}, {"ok": True})  # type: ignore[attr-defined]
    assert loop.has_produced is False  # type: ignore[attr-defined]


def test_the_latch_never_comes_back_off() -> None:
    # Es un LATCH: una vez que el run produjo algo, un turno estéril posterior
    # no puede volver a marcarlo como «no entregó nada».
    loop = _loop_for_latch()
    loop._track_research("write_file", {"tool_args": {"path": "a.py"}}, {"ok": True})  # type: ignore[attr-defined]
    loop._track_research("read_file", {"tool_args": {"path": "a.py"}}, {"ok": True})  # type: ignore[attr-defined]
    loop._track_research("shell_exec", {}, {"ok": False, "error": "boom"})  # type: ignore[attr-defined]
    assert loop.has_produced is True  # type: ignore[attr-defined]
