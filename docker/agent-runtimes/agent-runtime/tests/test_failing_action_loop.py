"""Una acción que FALLA IDÉNTICAMENTE una y otra vez también dispara la guarda dura.

MEDIDO EN VIVO (2026-08-31, proyecto «Hello World CI4 v3» del tenant mediapro,
ejecución ``01a05881-89d7-79fa-be72-bd0e7c1a9fbb``): el agente hizo 23 llamadas al
modelo, **14** de ellas ``list_files {}`` devolviendo SIEMPRE el mismo error
``a non-empty 'path' is required``. ``repetitive_loop_detected`` no disparó NUNCA y
el run murió por ``max_tokens`` tras producir 2.149 tokens de salida en total.

La causa está escrita en el propio ``graph.py``: la guarda dura sólo salta con una
tool MUTANTE (Tema C). Esa exención sigue siendo correcta para una lectura que
FUNCIONA —repetirla es exploración cara pero informativa—, y NO se revierte aquí
(la fija ``test_readonly_call_that_succeeds_repeatedly_still_does_not_trip``). Lo
que no distinguía el código es repetir una acción que FALLA IDÉNTICAMENTE: ahí no
hay información nueva de ningún tipo, sea la tool de lectura o no.

Tres afirmaciones del código que la primera tanda dejó SIN guarda, y que cierran
los tres últimos tests del fichero (verificación adversarial del 2026-08-31 — las
tres sobrevivían a su mutación con los 547 tests del runtime en verde):

* el LOTE (ADR 0111): la acción se guarda verbatim entre ``plan`` y ``reflect``
  porque su huella incluye el batch;
* el resumen del corte NOMBRA el error que se repetía (``failing_error``);
* ``note_outcome`` va DESPUÉS de ``note_progress``, no antes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from agent_runtime.graph import AgentDeps, _AgentLoop, run_agent
from agent_runtime.loop_detection import DEFAULT_LOOP_THRESHOLD, LoopDetector
from agent_runtime.model import DecisionKind, ModelDecision, ModelResponse, ScriptedModelClient
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import (
    STATUS_ABORTED,
    STATUS_NEEDS_HUMAN_REVIEW,
    AgentState,
    AgentTask,
    initial_state,
)
from agent_runtime.tools import ToolFn, ToolRegistry, ToolResult

_TASK: AgentTask = {
    "id": "t-fail-loop",
    "title": "Failing loop",
    "description": "misma acción, mismo error",
}

# El error exacto que devolvió `list_files {}` las 14 veces de la ejecución medida.
_LIST_FILES_ERROR = "a non-empty 'path' is required"


def _action(tool: str = "list_files", **args: object) -> dict[str, Any]:
    return {"tool": tool, "args": dict(args)}


# ---------------------------------------------------------------------------
# LoopDetector: la racha de fallos IDÉNTICOS
# ---------------------------------------------------------------------------
def test_identical_failures_reach_the_threshold() -> None:
    detector = LoopDetector()
    action = _action()
    for _ in range(DEFAULT_LOOP_THRESHOLD):
        detector.record(action)
        detector.note_outcome(action, ok=False, error=_LIST_FILES_ERROR)
    assert detector.failure_streak(action) == DEFAULT_LOOP_THRESHOLD
    assert detector.is_failing_identically(action) is True
    assert detector.failure_error(action) == _LIST_FILES_ERROR


def test_below_the_threshold_is_not_yet_a_failing_loop() -> None:
    detector = LoopDetector()
    action = _action()
    for _ in range(DEFAULT_LOOP_THRESHOLD - 1):
        detector.record(action)
        detector.note_outcome(action, ok=False, error=_LIST_FILES_ERROR)
    assert detector.is_failing_identically(action) is False


def test_a_success_clears_the_failure_streak() -> None:
    # Un éxito ES información nueva: la racha se rompe entera, no se decrementa.
    detector = LoopDetector()
    action = _action()
    for _ in range(DEFAULT_LOOP_THRESHOLD):
        detector.note_outcome(action, ok=False, error=_LIST_FILES_ERROR)
    detector.note_outcome(action, ok=True, error=None)
    assert detector.failure_streak(action) == 0
    assert detector.is_failing_identically(action) is False


def test_a_different_error_restarts_the_failure_streak() -> None:
    # Dos errores DISTINTOS sobre la misma acción siguen siendo información.
    detector = LoopDetector()
    action = _action()
    for _ in range(DEFAULT_LOOP_THRESHOLD):
        detector.note_outcome(action, ok=False, error=_LIST_FILES_ERROR)
    detector.note_outcome(action, ok=False, error="permission denied on 'app/'")
    assert detector.failure_streak(action) == 1
    assert detector.is_failing_identically(action) is False


def test_failure_streak_is_per_action() -> None:
    detector = LoopDetector()
    failing, other = _action(path=""), _action(path="app")
    for _ in range(DEFAULT_LOOP_THRESHOLD):
        detector.note_outcome(failing, ok=False, error=_LIST_FILES_ERROR)
    assert detector.is_failing_identically(failing) is True
    assert detector.is_failing_identically(other) is False
    assert detector.failure_error(other) is None


def test_note_progress_clears_the_failure_streak() -> None:
    # `note_progress` es "hubo progreso intermedio, el presupuesto de repetición
    # vuelve a cero": si limpiara los contadores pero no las rachas de fallo, el
    # detector quedaría en un estado incoherente entre sus dos mitades.
    detector = LoopDetector()
    action = _action()
    for _ in range(DEFAULT_LOOP_THRESHOLD):
        detector.note_outcome(action, ok=False, error=_LIST_FILES_ERROR)
    detector.note_progress()
    assert detector.failure_streak(action) == 0


# ---------------------------------------------------------------------------
# El loop completo: `run_agent` de punta a punta
# ---------------------------------------------------------------------------
def _tools_used_by(decisions: list[ModelResponse]) -> list[str]:
    """Las tools que este guion va a pedir: la principal de cada decisión y las
    de su lote read-only (ADR 0111).

    Registrar SÓLO esas mantiene honesto el doble: una tool que el guion no
    nombra sigue siendo «unknown tool», igual que en producción."""
    names: list[str] = []
    for response in decisions:
        decision = response.decision
        batch = (str(extra.get("tool") or "") for extra in decision.batch_calls)
        for name in (decision.tool or "", *batch):
            if name and name not in names:
                names.append(name)
    return names


class _ScriptedTools:
    """Un ``ToolRegistry`` REAL cuyas tools devuelven un guion de resultados.

    ``results`` se consume en orden y la última entrada se repite (igual que
    ``ScriptedModelClient``), así que un único elemento = «siempre lo mismo».

    Se monta sobre el registry de verdad —una función registrada por cada tool
    que el guion vaya a pedir— y no sobre un objeto suelto con un método
    ``call``: así el turno atraviesa la allowlist y la captura de excepciones
    que corren en producción, y ``AgentDeps.tools`` recibe exactamente el tipo
    que declara.
    """

    def __init__(self, decisions: list[ModelResponse], results: list[ToolResult]) -> None:
        self._results = results
        self._cursor = 0
        #: ``(tool, args)`` de cada llamada, en orden. Lo lee el test del lote
        #: para comprobar que los elementos del batch se ejecutaron de verdad.
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.registry = ToolRegistry()
        for name in _tools_used_by(decisions):
            self.registry.register(name, self._runner(name))

    def _runner(self, name: str) -> ToolFn:
        def run(args: dict[str, Any]) -> ToolResult:
            self.calls.append((name, dict(args)))
            index = min(self._cursor, len(self._results) - 1)
            self._cursor += 1
            return self._results[index]

        return run


def _act(tool: str, **args: object) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.ACT, tool=tool, tool_args=dict(args))
    )


def _act_batch(tool: str, batch: Sequence[dict[str, Any]], **args: object) -> ModelResponse:
    """Una decisión con LOTE read-only (ADR 0111): un call principal + extras."""
    return ModelResponse(
        decision=ModelDecision(
            kind=DecisionKind.ACT,
            tool=tool,
            tool_args=dict(args),
            batch_calls=tuple(dict(extra) for extra in batch),
        )
    )


def _recorded(tool: str, batch: Sequence[dict[str, Any]], **args: object) -> dict[str, Any]:
    """La acción tal y como `plan` la registra en el detector: el call principal
    MÁS su lote read-only (ADR 0111). Repetir el mismo batch es la misma acción."""
    return {"tool": tool, "args": dict(args), "batch": [dict(extra) for extra in batch]}


def _deps(decisions: list[ModelResponse], results: list[ToolResult]) -> AgentDeps:
    return AgentDeps(
        model=ScriptedModelClient(decisions=decisions),
        tools=_ScriptedTools(decisions, results).registry,
    )


@pytest.fixture(autouse=True)
def _absent_worktree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Sin worktree en disco: el resumen de escalado cae en la captura de escrituras
    # en memoria de ESTE run, que es determinista.
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "absent"))


def test_readonly_call_failing_identically_trips_the_hard_guard() -> None:
    # LA REGRESIÓN de la ejecución medida: `list_files {}` fallando siempre igual.
    # Con presupuesto de 20 iteraciones, la guarda dura tiene que cortar en la 4ª
    # (umbral 3) en vez de dejar que el run queme el presupuesto entero.
    budgets = Budgets(max_iterations=20)
    result = run_agent(
        _deps([_act("list_files")], [ToolResult(ok=False, error=_LIST_FILES_ERROR)]),
        _TASK,
        budgets=budgets,
    )
    assert result.abort_code == "repetitive_loop_detected"
    assert result.status == STATUS_ABORTED  # run estéril → abort limpio
    assert result.iterations <= DEFAULT_LOOP_THRESHOLD + 1


def test_readonly_call_that_succeeds_repeatedly_still_does_not_trip() -> None:
    # PIN de Tema C (lo que NO cambia): repetir una lectura que FUNCIONA es
    # exploración cara pero informativa — sigue exenta de la guarda dura y la
    # acota el presupuesto de iteraciones, como antes.
    budgets = Budgets(max_iterations=6)
    result = run_agent(
        _deps([_act("read_file", path="a.php")], [ToolResult(ok=True, output="<?php")]),
        _TASK,
        budgets=budgets,
    )
    assert result.abort_code == "max_iterations_exceeded"
    assert result.abort_code != "repetitive_loop_detected"


def test_alternating_errors_do_not_trip_the_hard_guard() -> None:
    # «Idéntica» incluye el RESULTADO: dos errores distintos sobre la misma acción
    # siguen siendo información, así que la racha se rompe y no hay corte duro.
    budgets = Budgets(max_iterations=8)
    results = [ToolResult(ok=False, error=f"no such file or directory: f{i}.php") for i in range(8)]
    result = run_agent(_deps([_act("read_file", path="f.php")], results), _TASK, budgets=budgets)
    assert result.abort_code == "max_iterations_exceeded"
    assert result.abort_code != "repetitive_loop_detected"


def test_platform_error_repeated_identically_also_trips() -> None:
    # Decisión razonada (ver el comentario en `graph.plan`): un error de PLATAFORMA
    # SÍ cuenta aquí. `_is_platform_error` lo excluye de la contabilidad de
    # esterilidad para no culpar al agente de una avería ajena; pero «tool not
    # allowed» no se arregla reintentando, así que es el caso MÁS desesperado, no
    # el más benigno — dejarlo fuera dejaría sin cubrir el peor.
    budgets = Budgets(max_iterations=20)
    result = run_agent(
        _deps([_act("list_files", path=".")], [ToolResult(ok=False, error="tool not allowed")]),
        _TASK,
        budgets=budgets,
    )
    assert result.abort_code == "repetitive_loop_detected"
    assert result.iterations <= DEFAULT_LOOP_THRESHOLD + 1


def test_failing_read_after_production_escalates_preserving_the_work() -> None:
    # El desenlace es el que ya existe (`_abort_or_escalate_status`): si el run YA
    # produjo, el corte ESCALA a humano preservando el trabajo en vez de abortar.
    write = _act("write_file", path="app/A.php", content="<?php class A {}")
    reads = [_act("list_files") for _ in range(6)]
    results = [ToolResult(ok=True, output="written")] + [
        ToolResult(ok=False, error=_LIST_FILES_ERROR) for _ in range(6)
    ]
    result = run_agent(_deps([write, *reads], results), _TASK, budgets=Budgets(max_iterations=20))
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == "repetitive_loop_detected"
    assert "app/A.php" in (result.output or "")


# ---------------------------------------------------------------------------
# El caso del LOTE (ADR 0111): por qué la acción se guarda verbatim
# ---------------------------------------------------------------------------
def test_batch_decision_failing_identically_trips_just_as_soon() -> None:
    """Un LOTE que falla idéntico corta igual de pronto que un call suelto.

    Éste es el caso que hace LOAD-BEARING guardar la acción verbatim entre
    ``plan`` y ``reflect``. La huella del detector incluye el batch; si
    ``reflect`` la reconstruyera desde ``last_decision`` —que no lo lleva—
    anotaría el fallo bajo OTRA huella y la racha del turno con lote se quedaría
    a cero para siempre.

    MEDIDO sobre este mismo escenario (2026-08-31): con el código tal cual corta
    en la iteración 4; reconstruyendo la acción desde ``last_decision`` se queman
    las 20 iteraciones del presupuesto y el run muere por ``max_iterations``.
    """
    read = {"tool": "read_file", "args": {"path": "app/Config/Routes.php"}}
    decisions = [_act_batch("list_files", [read])]
    tools = _ScriptedTools(decisions, [ToolResult(ok=False, error=_LIST_FILES_ERROR)])
    deps = AgentDeps(model=ScriptedModelClient(decisions=decisions), tools=tools.registry)

    result = run_agent(deps, _TASK, budgets=Budgets(max_iterations=20))

    assert result.abort_code == "repetitive_loop_detected"
    assert result.iterations <= DEFAULT_LOOP_THRESHOLD + 1
    # Y el lote se ejecutó de verdad: sin eso el escenario no distinguiría nada,
    # porque la huella con batch y la huella sin él serían la misma.
    assert ("read_file", {"path": "app/Config/Routes.php"}) in tools.calls


# ---------------------------------------------------------------------------
# El resumen del corte NOMBRA el error que se repetía (`failing_error`)
# ---------------------------------------------------------------------------
def test_the_trip_summary_names_the_error_that_kept_repeating() -> None:
    """Sin esto el visor muestra un «repetitive loop» pelado.

    La mitad legible del corte: el operador tiene que leer POR QUÉ se cortó sin
    abrir los steps uno a uno. En la ejecución medida el problema era un
    argumento que faltaba, y el resumen no lo decía.
    """
    result = run_agent(
        _deps([_act("list_files")], [ToolResult(ok=False, error=_LIST_FILES_ERROR)]),
        _TASK,
        budgets=Budgets(max_iterations=20),
    )
    assert result.abort_code == "repetitive_loop_detected"
    trips = [
        step
        for step in result.steps
        if str(step.get("summary", "")).startswith("Repetitive loop detected")
    ]
    assert len(trips) == 1
    summary = str(trips[0]["summary"])
    assert _LIST_FILES_ERROR in summary
    assert "list_files" in summary


def test_a_mutating_loop_that_is_not_failing_names_no_error() -> None:
    """La otra cara: sin racha de fallo idéntico el resumen no inventa un error.

    Un mutador repetido con ÉXITO trip a por contador, no por racha — y entonces
    ``failing_error`` es ``None``. Si el resumen colase ahí el último error visto,
    el operador leería una causa que no es la del corte."""
    write = _act("write_file", path="app/A.php", content="<?php class A {}")
    result = run_agent(
        _deps([write], [ToolResult(ok=True, output="written")]),
        _TASK,
        budgets=Budgets(max_iterations=20),
    )
    assert result.abort_code == "repetitive_loop_detected"
    trips = [
        step
        for step in result.steps
        if str(step.get("summary", "")).startswith("Repetitive loop detected")
    ]
    assert len(trips) == 1
    assert str(trips[0]["summary"]) == "Repetitive loop detected on tool 'write_file'"


# ---------------------------------------------------------------------------
# El ORDEN: `note_outcome` va DESPUÉS de `note_progress`
# ---------------------------------------------------------------------------
def _turn(loop: _AgentLoop, state: AgentState) -> None:
    """Un turno del grafo por sus nodos REALES: plan → act → reflect.

    Se encadenan a mano (en vez de con `run_agent`) porque lo que hay que
    observar es el estado del detector DENTRO del run, y `run_agent` sólo
    devuelve el resultado final."""
    state["last_decision"] = loop.plan(state)["last_decision"]
    state["last_observation"] = loop.act(state)["last_observation"]
    loop.reflect(state)


def test_this_turns_failure_counts_in_the_budget_that_note_progress_reopened() -> None:
    """El resultado del turno se anota DESPUÉS del reset por progreso, no antes.

    ``note_progress`` significa «hubo progreso intermedio, el presupuesto de
    repetición vuelve a cero»; el resultado de ESTE turno es lo primero que
    cuenta en el presupuesto nuevo. Anotarlo antes lo borraría, y la racha
    arrancaría un turno tarde.

    El escenario donde las dos cosas caen en el MISMO turno es real y no
    rebuscado: un call principal que falla mientras un elemento de su lote
    read-only (ADR 0111) lee un target nuevo. Ese turno es «productivo» por el
    lote y su acción difiere de la última productiva —así que resetea—, y a la
    vez tiene un fallo que anotar. Es la única vía: las demás formas de que un
    turno sea productivo exigen que el call principal haya ido BIEN, y un
    ``note_outcome`` con éxito sólo borra, nunca añade.
    """
    read = {"tool": "read_file", "args": {"path": "app/Config/Routes.php"}}
    decisions = [
        _act("write_file", path="app/A.php", content="<?php class A {}"),
        _act_batch("list_files", [read]),
    ]
    results = [
        ToolResult(ok=True, output="written"),  # turno 1: el write va bien
        ToolResult(ok=False, error=_LIST_FILES_ERROR),  # turno 2: el principal falla
        ToolResult(ok=True, output="<?php"),  # turno 2: el lote lee un target NUEVO
    ]
    loop = _AgentLoop(
        _deps(decisions, results),
        SafeguardTracker(Budgets(max_iterations=20)),
        LoopDetector(),
    )
    state = initial_state(_TASK)
    _turn(loop, state)  # fija la «última acción productiva» = el write
    _turn(loop, state)  # el turno que resetea Y falla

    recorded = _recorded("list_files", [read])
    # `note_progress` disparó de verdad — si no, el contador seguiría a 1 y el
    # test pasaría con las DOS ordenaciones, o sea que no fijaría nada.
    assert loop.detector.count_of(recorded) == 0
    # …y el fallo de este turno sobrevive al reset. Con `note_outcome` movido
    # antes de `note_progress`, las dos afirmaciones de abajo valen 0 y None.
    assert loop.detector.failure_streak(recorded) == 1
    assert loop.detector.failure_error(recorded) == _LIST_FILES_ERROR
