"""The agent loop nudges itself off a research rut (regression 2026-06-27).

A claude_sdk run burned all 25 iterations on reads/searches (list_files 11 times,
with repeated dirs, plus rag_search 9 times) and wrote NOTHING. `reflect` injects guidance
into the working context — on a repeated research call or a long research-only
streak — pushing the model to produce the deliverable.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import (
    _PATH_CHURN_THRESHOLD,
    _REREAD_CHURN_NUDGE_LIMIT,
    _RESEARCH_HARD_LIMIT,
    _SAME_TARGET_HARD_LIMIT,
    STATUS_NEEDS_HUMAN_REVIEW,
    AgentDeps,
    _abort_or_escalate_status,
    _AgentLoop,
    _path_churn_nudge,
    _read_target,
    _repetition_nudge,
    _reread_churn_nudge,
    _research_exhausted,
    _research_nudge,
    _sterile_hard_limit,
)
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.safeguards import Budgets, SafeguardTracker


def _exhausted(**kw: Any) -> bool:
    """Call ``_research_exhausted`` with sensible defaults so each test varies only
    the axis it cares about (semántica por-novedad, plan guardas-research)."""
    kw.setdefault("sterile_streak", 0)
    kw.setdefault("max_same_target_reads", 0)
    kw.setdefault("sterile_limit", _RESEARCH_HARD_LIMIT)
    kw.setdefault("same_target_limit", _SAME_TARGET_HARD_LIMIT)
    kw.setdefault("has_produced", False)
    kw.setdefault("review_retries", 0)
    kw.setdefault("is_review", False)
    return _research_exhausted(**kw)


def test_nudge_on_repeated_research_tool() -> None:
    msg = _research_nudge(tool="list_files", repeat_count=3)
    assert msg is not None and "list_files" in msg and "Do not repeat" in msg


def test_no_nudge_for_normal_research() -> None:
    assert _research_nudge(tool="list_files", repeat_count=1) is None


def test_no_nudge_for_producing_tool() -> None:
    assert _research_nudge(tool="write_file", repeat_count=1) is None


# --- ADR 0095: reviewer-aware safeguards -----------------------------------


def test_review_nudge_says_emit_verdict_not_write_file() -> None:
    # A reviewer is forbidden to write_file; the sterility nudge must push it to
    # FINISH with its <verdict>, not to produce a deliverable.
    msg = _reread_churn_nudge(
        churn_streak=_REREAD_CHURN_NUDGE_LIMIT,
        limit=_REREAD_CHURN_NUDGE_LIMIT,
        has_produced=False,
        is_review=True,
    )
    assert msg is not None
    assert "verdict" in msg.lower()
    assert "write_file" not in msg


def test_review_research_exhausted_cuts_sterile_reviewer() -> None:
    # Un reviewer que re-lee sin novedad se corta por esterilidad (el carve-out
    # antiguo por research_streak bruto castigaba lecturas DISTINTAS legítimas).
    assert _exhausted(sterile_streak=_RESEARCH_HARD_LIMIT, is_review=True) is True
    # Non-review sterile run is still NOT cut (D3 invariant preserved).
    assert _exhausted(sterile_streak=_RESEARCH_HARD_LIMIT, is_review=False) is False


def test_review_safeguard_escalates_not_aborts() -> None:
    # A review run that trips a safeguard escalates to a human (so the worker can
    # converge the task), instead of a silent hard abort.
    assert _abort_or_escalate_status(False, is_review=True) == STATUS_NEEDS_HUMAN_REVIEW


# --- Backstop por novedad (plan guardas-research-por-novedad A3) ---------------
# Trips (when eligible) on ANY of: racha ESTÉRIL (research sin target nuevo) o
# CUALQUIER target leído same_target_limit veces. La amplitud (muchos ficheros
# DISTINTOS) ya no corta nunca: la acota el presupuesto de iteraciones.
def test_research_exhausted_true_on_sterile_streak_after_produced() -> None:
    assert _exhausted(sterile_streak=_RESEARCH_HARD_LIMIT, has_produced=True)


def test_research_exhausted_true_after_failed_review_reread() -> None:
    # Re-read churn AFTER a rejected self-review: no new write, but there IS work.
    assert _exhausted(sterile_streak=_RESEARCH_HARD_LIMIT, review_retries=1)


def test_research_exhausted_true_on_same_target_hammering() -> None:
    # El mismo fichero leído same_target_limit veces (aunque intercalado con
    # lecturas nuevas que resetean la racha estéril) → trip.
    assert _exhausted(
        sterile_streak=0, max_same_target_reads=_SAME_TARGET_HARD_LIMIT, has_produced=True
    )


def test_research_exhausted_false_for_wide_distinct_exploration() -> None:
    # EL FALSO POSITIVO RETIRADO: leer muchos ficheros NUEVOS tras producir era
    # cortado por el techo de 22 distintos; ya no — la amplitud es legítima.
    assert not _exhausted(sterile_streak=0, max_same_target_reads=1, has_produced=True)


def test_research_exhausted_false_for_sterile_analysis_run() -> None:
    # INVARIANT (D3): a sterile analysis-only run (no production, no failed review,
    # not a review) is NOT cut even with huge churn — bounded by max_iterations.
    assert not _exhausted(
        sterile_streak=_RESEARCH_HARD_LIMIT + 5,
        max_same_target_reads=_SAME_TARGET_HARD_LIMIT + 5,
    )


def test_sterile_hard_limit_scales_with_budget() -> None:
    # Relativo al presupuesto: 25 % de max_iterations con suelo en el límite fijo.
    assert _sterile_hard_limit(50) == 12
    assert _sterile_hard_limit(25) == _RESEARCH_HARD_LIMIT
    assert _sterile_hard_limit(8) == _RESEARCH_HARD_LIMIT


def test_research_exhausted_false_below_both_limits() -> None:
    assert not _exhausted(
        sterile_streak=_RESEARCH_HARD_LIMIT - 1,
        max_same_target_reads=_SAME_TARGET_HARD_LIMIT - 1,
        has_produced=True,
    )


# --- B1: the repetition nudge fires by tool class at the detector threshold ----
def test_repetition_nudge_fires_at_threshold_for_mutator() -> None:
    # threshold=3 → a write seen 3 times warns on the turn BEFORE the 4th aborts.
    msg = _repetition_nudge(tool="write_file", repeat_count=3, threshold=3, has_produced=True)
    assert msg is not None
    assert "write_file" in msg and "submit_result" in msg  # producer wording → FINISH


def test_repetition_nudge_not_before_threshold() -> None:
    nudge = _repetition_nudge(tool="write_file", repeat_count=2, threshold=3, has_produced=True)
    assert nudge is None


def test_repetition_nudge_readonly_wording() -> None:
    msg = _repetition_nudge(tool="read_file", repeat_count=3, threshold=3, has_produced=False)
    assert msg is not None
    assert "read_file" in msg and "result you already have" in msg
    assert "submit_result" not in msg  # read-only → reuse, NOT finish


def test_repetition_nudge_namespaced_mutator() -> None:
    # An MCP/custom writer (namespaced) still classifies as a mutator → producer wording.
    msg = _repetition_nudge(tool="fs.write_file", repeat_count=4, threshold=3, has_produced=True)
    assert msg is not None and "write_file" in msg and "submit_result" in msg


def test_repetition_nudge_none_for_no_tool() -> None:
    assert _repetition_nudge(tool=None, repeat_count=9, threshold=3, has_produced=True) is None


def _loop() -> _AgentLoop:
    # reflect() never touches deps.model, so a dummy object is fine.
    return _AgentLoop(AgentDeps(model=object()), SafeguardTracker(Budgets()), LoopDetector())  # type: ignore[arg-type]


def _state(
    tool: str,
    args: dict[str, Any],
    *,
    ok: bool = True,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "last_observation": {"tool": tool, "ok": ok, "output": output or {}},
        "last_decision": {"tool": tool, "tool_args": args},
        "steps": [],
    }


# --- A2 (plan guardas-research): exploración legítima = CERO fricción ----------
def test_exploring_new_files_never_nudges() -> None:
    # 8 lecturas seguidas de ficheros NUEVOS (el caso del operador): ni nudge ni
    # racha estéril — antes el streak ciego de 5 disparaba «STOP researching…
    # produce (e.g. write_file)» sobre exploración normal.
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(8):
        out = loop.reflect(_state("read_file", {"path": f"src/f{i}.php"}))
    assert out.get("guidance_nudge") in (None, "")
    assert loop.read_churn_streak == 0


def test_new_rag_queries_are_exploration_not_churn() -> None:
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(6):
        out = loop.reflect(_state("rag_search", {"query": f"tema {i}"}))
    assert out.get("guidance_nudge") in (None, "")


def test_errored_reads_count_as_sterile_not_novel() -> None:
    # Anti-gaming: paths inexistentes "nuevos" cada turno no son exploración.
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(_REREAD_CHURN_NUDGE_LIMIT):
        out = loop.reflect(_state("read_file", {"path": f"nope{i}.php"}, ok=False))
    assert loop.read_churn_streak == _REREAD_CHURN_NUDGE_LIMIT
    assert (out.get("guidance_nudge") or "") != ""
    assert len(loop.read_targets) == 0  # los fallos no acumulan "novedad"


# --- A1: nudge específico por-target -------------------------------------------
def test_same_target_third_read_nudges_naming_the_file() -> None:
    loop = _loop()
    out: dict[str, Any] = {}
    reads = ["app/Config/Routes.php", "app/Config/Routes.php", "other.php", "app/Config/Routes.php"]
    for path in reads:
        out = loop.reflect(_state("read_file", {"path": path}))
    # 3.ª lectura de Routes.php (intercalada — el churn consecutivo no la ve).
    nudge = out.get("guidance_nudge") or ""
    assert "app/Config/Routes.php" in nudge
    assert loop.read_counts["read_file:app/Config/Routes.php"] == 3


def test_reflect_injects_guidance_on_repeat() -> None:
    loop = _loop()
    action = {"tool": "list_files", "args": {"path": "."}}
    loop.detector.record(action)  # seen twice → count_of == 2 in reflect
    loop.detector.record(action)
    out = loop.reflect(_state("list_files", {"path": "."}))
    assert "Do not repeat" in (out.get("guidance_nudge") or "")


# --- A4: el sticky se limpia con progreso ---------------------------------------
def test_guidance_clears_on_novel_read_after_nudge() -> None:
    loop = _loop()
    for path in ("a.php", "a.php", "a.php"):  # 3ª lectura → nudge per-target
        out = loop.reflect(_state("read_file", {"path": path}))
    assert (out.get("guidance_nudge") or "") != ""
    out = loop.reflect(_state("read_file", {"path": "b.php"}))  # target NUEVO
    assert "guidance_nudge" in out and out["guidance_nudge"] is None


# --- C1: digests de lecturas en PROGRESS ----------------------------------------
def test_progress_includes_read_digests() -> None:
    loop = _loop()
    loop.reflect(
        _state(
            "read_file",
            {"path": "app/A.php"},
            output={"content": "<?php // controlador A\nclass A {}", "size_bytes": 34},
        )
    )
    out = loop.reflect(
        _state(
            "read_file",
            {"path": "app/B.php"},
            output={"content": "<?php class B {}", "size_bytes": 16},
        )
    )
    progress = out.get("progress_summary") or ""
    assert "app/A.php" in progress and "app/B.php" in progress
    assert "controlador A" in progress  # 1.ª línea significativa como digest


def test_read_digest_includes_symbol_signature() -> None:
    # G10 (ADR 0103): además de la 1.ª línea significativa, el digest lleva la
    # 1.ª FIRMA de símbolo (def/class/function) — el modelo recuerda QUÉ define
    # el fichero sin re-leerlo.
    loop = _loop()
    out = loop.reflect(
        _state(
            "read_file",
            {"path": "app/service.py"},
            output={
                "content": (
                    "# servicio de facturación\n"
                    "import os\n\n"
                    "class InvoiceService:\n"
                    "    def issue(self) -> None: ...\n"
                )
            },
        )
    )
    progress = out.get("progress_summary") or ""
    assert "servicio de facturación" in progress  # 1.ª línea significativa
    assert "class InvoiceService" in progress  # firma del primer símbolo


def test_read_digest_without_symbol_keeps_first_line() -> None:
    loop = _loop()
    out = loop.reflect(
        _state(
            "read_file",
            {"path": "README.md"},
            output={"content": "# Título del proyecto\n\nTexto plano sin símbolos.\n"},
        )
    )
    progress = out.get("progress_summary") or ""
    assert "Título del proyecto" in progress


def test_read_digests_are_lru_capped() -> None:
    loop = _loop()
    for i in range(25):
        loop.reflect(_state("read_file", {"path": f"f{i}.php"}, output={"content": f"// {i}"}))
    assert len(loop.read_digests) == 20
    assert "read_file:f24.php" in loop.read_digests  # las últimas sobreviven
    assert "read_file:f0.php" not in loop.read_digests  # las primeras se evictan


# --- F2b.1/2 (auditoría 2026-07-02): resumen de progreso siempre-visible -------
def test_reflect_sets_progress_summary_each_turn() -> None:
    loop = _loop()
    out = loop.reflect(_state("write_file", {"path": "app/Hello.php", "content": "<?php"}))
    progress = out.get("progress_summary") or ""
    assert "iteration" in progress
    assert "app/Hello.php" in progress  # el modelo VE lo que ya escribió


def test_progress_summary_reports_no_files_without_write_bias() -> None:
    # Casuística del operador (2026-07-03): una tarea puede ser SOLO de análisis
    # (p. ej. revisar versiones de vendors) — el PROGRESS no debe presionar a
    # escribir; debe recordar que la respuesta final también es entregable.
    loop = _loop()
    out = loop.reflect(_state("read_file", {"path": "README.md"}))
    progress = out.get("progress_summary") or ""
    assert "no files written yet" in progress
    assert "analysis" in progress  # la salida de análisis es explícita
    assert "no deliverable" not in progress  # el texto sesgado desaparece


def test_progress_summary_warns_near_iteration_budget() -> None:
    loop = _loop()
    # 80% del presupuesto consumido → aviso de cierre (antes el modelo nunca
    # sabía cuánto le quedaba: los límites solo abortaban).
    cap = loop.tracker.budgets.max_iterations
    loop.tracker.usage.iterations = int(cap * 0.8)
    out = loop.reflect(_state("read_file", {"path": "a.md"}))
    progress = out.get("progress_summary") or ""
    assert "FINISH" in progress or "wrap up" in progress


def test_progress_summary_no_warning_far_from_budget() -> None:
    loop = _loop()
    loop.tracker.usage.iterations = 2
    out = loop.reflect(_state("read_file", {"path": "a.md"}))
    progress = out.get("progress_summary") or ""
    assert "wrap up" not in progress


def test_reflect_resets_sterile_streak_on_producing_tool() -> None:
    loop = _loop()
    loop.read_churn_streak = 4
    loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert loop.read_churn_streak == 0


def test_reflect_sets_repetition_warning_scalar_not_context() -> None:
    # A write_file repeated to the threshold sets the SCALAR repetition_warning —
    # never `context` (which operator.add would reorder, burying it / breaking
    # context[0] ordering). Record it threshold times so reflect's count_of == 3.
    loop = _loop()
    action = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    for _ in range(loop.detector.threshold):
        loop.detector.record(action)
    out = loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert out.get("repetition_warning") is not None
    assert "submit_result" in out["repetition_warning"]
    assert "context" not in out  # a producing tool emits no research guidance


def test_reflect_no_repetition_warning_below_threshold() -> None:
    loop = _loop()
    action = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    loop.detector.record(action)  # count_of == 1 in reflect, < threshold
    out = loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert "repetition_warning" not in out


# --- the over-verification trap: once produced, the sterility nudge says FINISH -
def test_finish_nudge_when_already_produced_and_sterile() -> None:
    msg = _reread_churn_nudge(
        churn_streak=_REREAD_CHURN_NUDGE_LIMIT,
        limit=_REREAD_CHURN_NUDGE_LIMIT,
        has_produced=True,
    )
    # C0 (ADR 0087): the nudge must NOT prescribe "NO tool call" — under the
    # structured-finish contract, FINISH on HTTP providers IS a submit_result tool
    # call. The guidance is provider-neutral: report the result and stop.
    assert msg is not None and "FINISH" in msg
    assert "NO tool call" not in msg and "no tool call" not in msg.lower()


# --- ADR 0089: same-path CHURN nudge (varying content, never byte-identical) ----
def test_path_churn_nudge_fires_at_threshold() -> None:
    msg = _path_churn_nudge(
        path="app/Mig.php", write_count=_PATH_CHURN_THRESHOLD, threshold=_PATH_CHURN_THRESHOLD
    )
    assert msg is not None
    assert "app/Mig.php" in msg and "FINISH" in msg and "submit_result" in msg


def test_path_churn_nudge_not_before_threshold() -> None:
    assert (
        _path_churn_nudge(
            path="a.php", write_count=_PATH_CHURN_THRESHOLD - 1, threshold=_PATH_CHURN_THRESHOLD
        )
        is None
    )


def test_path_churn_nudge_none_without_path() -> None:
    assert _path_churn_nudge(path=None, write_count=99, threshold=_PATH_CHURN_THRESHOLD) is None


def test_reflect_churn_nudge_on_repeated_same_path_varying_content() -> None:
    # The model re-writes the SAME path with DIFFERENT content each turn: the loop
    # detector NEVER trips (content-aware fingerprint) and the identical-args nudge
    # never fires (count_of stays 1) — but the path-churn nudge does, pushing it to
    # FINISH. This is exactly the case that burned 50 iterations re-writing a migration.
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(_PATH_CHURN_THRESHOLD):
        out = loop.reflect(
            _state("write_file", {"path": "app/Mig.php", "content": f"<?php // v{i}"})
        )
    assert loop.path_write_counts["app/Mig.php"] == _PATH_CHURN_THRESHOLD
    assert out.get("repetition_warning") is not None
    assert "app/Mig.php" in out["repetition_warning"] and "FINISH" in out["repetition_warning"]
    # The detector did NOT count these as a loop (distinct content → distinct fingerprint).
    assert (
        loop.detector.count_of(
            {"tool": "write_file", "args": {"path": "app/Mig.php", "content": "<?php // v0"}}
        )
        <= 1
    )


def test_finish_nudge_on_repeat_after_producing() -> None:
    msg = _research_nudge(tool="read_file", repeat_count=3, has_produced=True)
    assert msg is not None and "FINISH" in msg


def test_reflect_latches_has_produced_and_nudges_to_finish() -> None:
    loop = _loop()
    # Produce once → latches has_produced (and resets the sterile streak).
    loop.reflect(_state("write_file", {"path": "a.php", "content": "x"}))
    assert loop.has_produced is True and loop.read_churn_streak == 0
    # Then it slips into RE-verifying the SAME dir (sterile); the nudge says FINISH.
    out: dict[str, Any] = {}
    for _ in range(_REREAD_CHURN_NUDGE_LIMIT + 1):
        out = loop.reflect(_state("list_files", {"path": "app"}))
    assert "FINISH" in (out.get("guidance_nudge") or "")


# --- distinct-path exploration vs re-read churn (2026-07-01 hardening) ----------
def test_read_target_ignores_offset_and_limit() -> None:
    # Paging the SAME file must be the same target — else offset-varying re-reads
    # masquerade as exploration and evade the churn streak.
    a = _read_target("read_file", {"path": "x.php", "offset": 0})
    b = _read_target("read_file", {"path": "x.php", "offset": 200, "limit": 50})
    assert a == b == "read_file:x.php"


def test_read_target_shapes_and_none() -> None:
    assert _read_target("list_files", {}) == "list_files:."  # default path
    assert _read_target("fs.read_file", {"path": "y"}) == "read_file:y"  # namespace-stripped
    assert _read_target("rag_search", {"query": " q "}) == "rag_search:q"
    assert _read_target("memory_recall", {"query": ""}) is None  # empty query → untargetable
    assert _read_target("write_file", {"path": "y"}) is None  # not a research tool


def test_reread_churn_nudge_below_limit_is_none() -> None:
    assert (
        _reread_churn_nudge(
            churn_streak=_REREAD_CHURN_NUDGE_LIMIT - 1,
            limit=_REREAD_CHURN_NUDGE_LIMIT,
            has_produced=False,
        )
        is None
    )


def test_reread_churn_nudge_sterile_offers_finish_by_conclusion() -> None:
    msg = _reread_churn_nudge(
        churn_streak=_REREAD_CHURN_NUDGE_LIMIT, limit=_REREAD_CHURN_NUDGE_LIMIT, has_produced=False
    )
    assert msg is not None and "FINISH" in msg and "conclusion" in msg.lower()


def test_reread_churn_nudge_review_says_verdict() -> None:
    msg = _reread_churn_nudge(
        churn_streak=_REREAD_CHURN_NUDGE_LIMIT,
        limit=_REREAD_CHURN_NUDGE_LIMIT,
        has_produced=False,
        is_review=True,
    )
    assert msg is not None and "verdict" in msg.lower() and "write_file" not in msg


def test_reflect_distinct_reads_do_not_build_churn() -> None:
    # Reading 12 NEW files in a row is exploration: churn stays 0, targets accumulate.
    loop = _loop()
    for i in range(12):
        loop.reflect(_state("read_file", {"path": f"app/File{i}.php"}))
    assert loop.read_churn_streak == 0
    assert len(loop.read_targets) == 12


def test_reflect_reread_same_target_builds_churn_even_varying_offset() -> None:
    # Re-reading the SAME file (paging with different offsets) is churn, not exploration.
    loop = _loop()
    for off in range(6):
        loop.reflect(_state("read_file", {"path": "app/Routes.php", "offset": off * 100}))
    assert len(loop.read_targets) == 1
    assert loop.read_churn_streak == 5  # first is new (0), each re-read +1


def test_reflect_producing_tool_resets_churn_streak() -> None:
    loop = _loop()
    loop.read_churn_streak = 7
    loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert loop.read_churn_streak == 0
