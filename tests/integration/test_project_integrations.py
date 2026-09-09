"""`PUT /projects/{id}` con `integrations` (`task_mk_20`, MK-05).

El viaje completo por HTTP: guardar las anclas, leerlas en el GET, que `None`
sea «sin cambio» (PATCH), que `{}` las borre y que una clave desconocida sea un
422 y NO toque lo guardado. Reutiliza el arranque (`configured_app`, seed y
token) del test de avisos de egress, que ya monta un proyecto del tenant.
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_project_mcp_egress_warning import (
    _mint_token,
    _seed,
)

_ANCHORS = {
    "jira": {"project_key": "PLAT", "parent_issue_key": "PLAT-120"},
    "confluence": {"space_key": "ENG", "root_page_id": "123456"},
}


async def _stored(dsn: str, project_id: UUID) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        raw = await conn.fetchval(
            "SELECT integrations::text FROM projects WHERE id = $1", project_id
        )
        return dict(json.loads(raw))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_integrations_roundtrip_patch_semantics_and_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint_token(ids['user'], ids['tenant'])}"}
    project = ids["project"]

    async with AsyncClient(transport=ASGITransport(app=configured_app), base_url="http://t") as c:
        # Nace sin anclas: `{}`, no null ni ausente.
        got = await c.get(f"/projects/{project}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["integrations"] == {}

        # Guardar.
        put = await c.put(f"/projects/{project}", json={"integrations": _ANCHORS}, headers=headers)
        assert put.status_code == 200, put.text
        assert put.json()["integrations"] == _ANCHORS
        assert await _stored(migrations_pg_dsn, project) == _ANCHORS

        # PATCH: tocar otro campo NO borra las anclas.
        other = await c.put(f"/projects/{project}", json={"description": "x"}, headers=headers)
        assert other.status_code == 200, other.text
        assert other.json()["integrations"] == _ANCHORS

        # 422 por clave desconocida, y lo guardado sigue intacto.
        bad = await c.put(
            f"/projects/{project}",
            json={"integrations": {"jira": {"project_key": "PLAT", "epic": "PLAT-1"}}},
            headers=headers,
        )
        assert bad.status_code == 422, bad.text
        assert "jira.epic" in bad.text
        assert await _stored(migrations_pg_dsn, project) == _ANCHORS

        # `{}` explícito las borra.
        cleared = await c.put(f"/projects/{project}", json={"integrations": {}}, headers=headers)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["integrations"] == {}
    assert await _stored(migrations_pg_dsn, project) == {}
