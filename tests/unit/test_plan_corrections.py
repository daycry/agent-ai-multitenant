"""ADR 0107: helper puro de mutación del spec para el ciclo de correcciones.

`mark_corrections_accepted` es la única pieza que reescribe
`specification.corrections` al aceptar tareas correctivas. Reglas:

  - devuelve un dict NUEVO (JSONB se persiste reemplazando el dict
    completo; mutar in-place no marca dirty la columna), sin tocar el
    original;
  - una entrada `proposed` cuyo `task_ids` interseca la selección pasa a
    `accepted` y registra la intersección en `accepted_task_ids`;
  - entradas sin intersección quedan intactas;
  - re-aceptar (entrada ya `accepted`) une las selecciones en
    `accepted_task_ids` sin duplicar.
"""

from __future__ import annotations

import pytest
from api_server.chat.plan_corrections import (
    append_corrections,
    find_correction_for_session,
    mark_corrections_accepted,
)

pytestmark = pytest.mark.unit


def _spec_with(corrections: list[dict]) -> dict:
    return {
        "tasks": [
            {"id": "t1", "title": "Original"},
            {"id": "fix-1", "title": "Corrección A", "origin": "correction"},
            {"id": "fix-2", "title": "Corrección B", "origin": "correction"},
        ],
        "corrections": corrections,
    }


def test_marks_matching_proposed_entry_accepted() -> None:
    spec = _spec_with(
        [
            {
                "session_id": "sess-1",
                "reason": "filtro global",
                "task_ids": ["fix-1", "fix-2"],
                "status": "proposed",
            }
        ]
    )
    out = mark_corrections_accepted(spec, ["fix-2", "fix-1"])
    entry = out["corrections"][0]
    assert entry["status"] == "accepted"
    assert entry["accepted_task_ids"] == ["fix-1", "fix-2"]


def test_partial_selection_records_only_the_intersection() -> None:
    spec = _spec_with(
        [
            {
                "session_id": "sess-1",
                "reason": "r",
                "task_ids": ["fix-1", "fix-2"],
                "status": "proposed",
            }
        ]
    )
    out = mark_corrections_accepted(spec, ["fix-1"])
    entry = out["corrections"][0]
    assert entry["status"] == "accepted"
    assert entry["accepted_task_ids"] == ["fix-1"]


def test_non_matching_entries_stay_proposed() -> None:
    spec = _spec_with(
        [
            {"session_id": "s1", "reason": "r", "task_ids": ["fix-1"], "status": "proposed"},
            {"session_id": "s2", "reason": "r2", "task_ids": ["fix-2"], "status": "proposed"},
        ]
    )
    out = mark_corrections_accepted(spec, ["fix-1"])
    assert out["corrections"][0]["status"] == "accepted"
    assert out["corrections"][1]["status"] == "proposed"
    assert "accepted_task_ids" not in out["corrections"][1]


def test_returns_new_dict_without_mutating_the_input() -> None:
    corrections = [{"session_id": "s1", "reason": "r", "task_ids": ["fix-1"], "status": "proposed"}]
    spec = _spec_with(corrections)
    out = mark_corrections_accepted(spec, ["fix-1"])
    assert out is not spec
    assert spec["corrections"][0]["status"] == "proposed"
    assert corrections[0]["status"] == "proposed"


def test_reaccept_unions_accepted_ids_and_stays_accepted() -> None:
    spec = _spec_with(
        [
            {
                "session_id": "s1",
                "reason": "r",
                "task_ids": ["fix-1", "fix-2"],
                "status": "accepted",
                "accepted_task_ids": ["fix-1"],
            }
        ]
    )
    out = mark_corrections_accepted(spec, ["fix-2"])
    entry = out["corrections"][0]
    assert entry["status"] == "accepted"
    assert entry["accepted_task_ids"] == ["fix-1", "fix-2"]


def test_spec_without_corrections_is_a_noop_copy() -> None:
    spec = {"tasks": [{"id": "t1", "title": "x"}]}
    out = mark_corrections_accepted(spec, ["t1"])
    assert out["corrections"] == []
    assert out is not spec


# ---------------------------------------------------------------------------
# append_corrections + find_correction_for_session (ADR 0107, generate)
# ---------------------------------------------------------------------------
def test_append_corrections_adds_tasks_and_proposed_entry() -> None:
    spec = {"tasks": [{"id": "t1", "title": "Original"}]}
    fixes = [
        {"id": "fix-1", "title": "Corrección A", "origin": "correction"},
        {"id": "fix-2", "title": "Corrección B", "origin": "correction"},
    ]
    out = append_corrections(
        spec,
        session_id="sess-1",
        reason="filtro global",
        tasks=fixes,
        created_at="2026-07-08T00:00:00+00:00",
    )
    assert [t["id"] for t in out["tasks"]] == ["t1", "fix-1", "fix-2"]
    entry = out["corrections"][0]
    assert entry == {
        "session_id": "sess-1",
        "reason": "filtro global",
        "task_ids": ["fix-1", "fix-2"],
        "created_at": "2026-07-08T00:00:00+00:00",
        "status": "proposed",
    }
    # No muta el original.
    assert len(spec["tasks"]) == 1
    assert "corrections" not in spec


def test_find_correction_for_session_matches_by_id() -> None:
    spec = {
        "corrections": [
            {"session_id": "sess-1", "task_ids": ["fix-1"], "status": "proposed"},
            {"session_id": "sess-2", "task_ids": ["fix-2"], "status": "accepted"},
        ]
    }
    hit = find_correction_for_session(spec, "sess-2")
    assert hit is not None and hit["task_ids"] == ["fix-2"]
    assert find_correction_for_session(spec, "sess-9") is None
    assert find_correction_for_session({}, "sess-1") is None
