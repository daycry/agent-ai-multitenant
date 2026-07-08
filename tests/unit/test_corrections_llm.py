"""ADR 0107: generación LLM de tareas correctivas desde el motivo de rechazo.

`generate_corrective_tasks` pide al provider el MISMO JSON de tareas que
`pm_plan_draft` y lo normaliza a tareas listas para `specification.tasks`:

  - ids únicos con prefijo ``fix-`` (deduplicados contra el spec y entre sí);
  - ``depends_on`` solo referencia ids finales de la propia tanda o ids ya
    existentes del plan (self-refs y desconocidos se descartan);
  - ``origin: "correction"`` en todas;
  - complejidad válida (fallback ``m``) y criterios limpiados con el mismo
    cleaner del planner.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from api_server.chat.corrections_llm import (
    build_corrections_messages,
    generate_corrective_tasks,
    normalise_corrective_tasks,
)

pytestmark = pytest.mark.unit


class _FakeProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})

        class _Resp:
            content = self.content

        return _Resp()


_EXISTING = [
    {"id": "t1", "title": "Original A", "role": "backend_dev"},
    {"id": "t2", "title": "Original B", "role": "qa"},
]


# ---------------------------------------------------------------------------
# normalise_corrective_tasks (puro)
# ---------------------------------------------------------------------------
def test_normalise_prefixes_ids_and_stamps_origin() -> None:
    raw = {
        "tasks": [
            {
                "id": "c1",
                "title": "Acotar filtro",
                "description": "d",
                "role": "backend_dev",
                "complexity": "s",
                "acceptance_criteria": ["la portada responde text/html"],
            }
        ]
    }
    out = normalise_corrective_tasks(raw, existing_ids=["t1", "t2"])
    assert len(out) == 1
    task = out[0]
    assert task["id"] == "fix-c1"
    assert task["origin"] == "correction"
    assert task["complexity"] == "s"
    assert task["acceptance_criteria"] == ["la portada responde text/html"]


def test_normalise_dedupes_against_existing_and_batch_ids() -> None:
    raw = {
        "tasks": [
            {"id": "fix-1", "title": "A"},
            {"id": "1", "title": "B"},  # también normaliza a fix-1 → sufijo
        ]
    }
    out = normalise_corrective_tasks(raw, existing_ids=["t1", "fix-1"])
    ids = [t["id"] for t in out]
    assert len(set(ids)) == 2
    assert "fix-1" not in ids  # ya existe en el spec
    assert all(i.startswith("fix-1") for i in ids)


def test_normalise_maps_depends_on_through_final_ids() -> None:
    raw = {
        "tasks": [
            {"id": "c1", "title": "A"},
            # depende de la nueva por su id CRUDO, de una existente, de una
            # desconocida y de sí misma: solo las dos primeras sobreviven.
            {"id": "c2", "title": "B", "depends_on": ["c1", "t1", "nope", "c2"]},
        ]
    }
    out = normalise_corrective_tasks(raw, existing_ids=["t1", "t2"])
    by_id = {t["id"]: t for t in out}
    assert by_id["fix-c2"]["depends_on"] == ["fix-c1", "t1"]


def test_normalise_drops_unusable_and_defaults_complexity() -> None:
    raw = {
        "tasks": [
            {"id": "c1", "title": ""},  # sin título → fuera
            "no soy un dict",
            {"id": "c2", "title": "B", "complexity": "gigante"},
        ]
    }
    out = normalise_corrective_tasks(raw, existing_ids=[])
    assert len(out) == 1
    assert out[0]["complexity"] == "m"


def test_normalise_accepts_bare_list_and_rejects_garbage() -> None:
    assert normalise_corrective_tasks([{"id": "x", "title": "T"}], existing_ids=[]) != []
    assert normalise_corrective_tasks("prosa", existing_ids=[]) == []
    assert normalise_corrective_tasks(None, existing_ids=[]) == []


# ---------------------------------------------------------------------------
# build_corrections_messages
# ---------------------------------------------------------------------------
def test_messages_carry_reason_digest_and_roles() -> None:
    messages = build_corrections_messages(
        rejection_reason="El filtro JSON es global y rompe la portada",
        plan_title="Plan CI4",
        plan_summary="API + web",
        existing_tasks=_EXISTING,
    )
    assert messages[0].role == "system"
    user = messages[-1].content
    assert "El filtro JSON es global" in user
    assert "t1" in user and "Original A" in user
    assert "backend_dev" in messages[0].content or "backend_dev" in user


# ---------------------------------------------------------------------------
# generate_corrective_tasks (con provider fake)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_parses_json_with_surrounding_prose() -> None:
    payload = {
        "tasks": [
            {
                "id": "c1",
                "title": "Acotar filtro Content-Type",
                "description": "Aplicar solo a api/v1",
                "role": "backend_dev",
                "complexity": "s",
                "depends_on": [],
                "acceptance_criteria": ["la portada responde text/html"],
            }
        ]
    }
    provider = _FakeProvider("Claro, aquí está el plan:\n```json\n" + json.dumps(payload) + "\n```")
    out = await generate_corrective_tasks(
        provider,
        rejection_reason="filtro global",
        plan_title="Plan CI4",
        plan_summary="",
        existing_tasks=_EXISTING,
        model="m",
    )
    assert [t["id"] for t in out] == ["fix-c1"]
    assert out[0]["origin"] == "correction"
    assert provider.calls[0]["model"] == "m"


@pytest.mark.asyncio
async def test_generate_returns_empty_on_unusable_reply() -> None:
    provider = _FakeProvider("no tengo nada que proponer")
    out = await generate_corrective_tasks(
        provider,
        rejection_reason="r",
        plan_title="t",
        plan_summary="",
        existing_tasks=[],
        model=None,
    )
    assert out == []
