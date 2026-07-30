"""AUD16-04 (auditoría 2026-07-16): los system prompts solo anuncian tools que
el runtime puede ejecutar.

``search_code`` está en el catálogo y clasificada como read-only (G4a, ADR
0103), pero NO tiene executor en el runtime: g4 la excluye del anuncio de
schemas (``agent_tool_schemas._catalog_by_canonical``), así que nombrarla en el
prompt hace que el modelo la invoque a pelo y queme el turno en
``unknown tool`` (7/7 llamadas fallidas en los 14 días auditados).
"""

from __future__ import annotations

from agent_runtime.providers import _DECIDE_SYSTEM, _REVIEW_RUN_SYSTEM


def test_decide_system_does_not_announce_unwired_search_code() -> None:
    assert "search_code" not in _DECIDE_SYSTEM


def test_review_run_system_does_not_announce_unwired_search_code() -> None:
    assert "search_code" not in _REVIEW_RUN_SYSTEM


def test_review_run_system_bounds_what_to_fix_to_agent_actions() -> None:
    """AUD16-22: un reviewer pidió al agente reintentar el commit/push (acción
    del WORKER, imposible en el sandbox) y la task bucleó hasta agotar retries.
    El contrato del prompt exige que ``what_to_fix`` se limite a acciones que el
    IMPLEMENTADOR puede ejecutar (ficheros del worktree, stack_exec) y prohíbe
    exigir git/commit/push/deploy."""
    lowered = _REVIEW_RUN_SYSTEM.lower()
    assert "what_to_fix" in _REVIEW_RUN_SYSTEM
    # La restricción tiene que ser explícita: qué puede pedir…
    assert "the implementer can perform" in lowered
    # …y qué no (git es del worker, no del agente).
    assert "never ask the implementer to run git" in lowered
