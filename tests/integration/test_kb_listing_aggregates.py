"""Agregados de las bases en el LISTADO (`task_ui_21`, gap 1 de la revisión).

La pestaña Conocimiento calculaba las cifras en cliente sobre la primera página
(100) de `…/documents` y pedía dos listados por fila. El backend las entrega ya
agregadas en una consulta agrupada por `kb_id`, y los proyectos concedidos por
lotes.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_kb_endpoints import (  # noqa: F401  (fixture)
    _mint_token,
    _seed,
    _seed_two_tenants,
    configured_app,
)

pytestmark = pytest.mark.integration


async def _insert_docs(dsn: str, *, tenant_id: UUID, kb_id: str, status: str, n: int) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key, status)"
            " SELECT gen_random_uuid(), $1, $2::uuid, 'd' || g, 'd' || g || '.txt',"
            " 'text/plain', 'k/' || gen_random_uuid(), $3 FROM generate_series(1, $4) g",
            tenant_id,
            kb_id,
            status,
            n,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_listing_carries_exact_stats_beyond_the_first_page(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        big = (await client.post("/knowledge-bases", json={"name": "Big"}, headers=headers)).json()[
            "id"
        ]
        empty = (
            await client.post("/knowledge-bases", json={"name": "Empty"}, headers=headers)
        ).json()["id"]
        tid = seeded["tenant_id"]
        await _insert_docs(migrations_pg_dsn, tenant_id=tid, kb_id=big, status="indexed", n=130)
        await _insert_docs(migrations_pg_dsn, tenant_id=tid, kb_id=big, status="failed", n=2)
        await _insert_docs(migrations_pg_dsn, tenant_id=tid, kb_id=big, status="pending", n=3)
        await client.post(
            f"/knowledge-bases/{big}/projects",
            json={"project_id": str(seeded["project_id"])},
            headers=headers,
        )

        listed = (await client.get("/knowledge-bases", headers=headers)).json()
        by_id = {k["id"]: k for k in listed}
        stats = by_id[big]["document_stats"]
        assert stats["total"] == 135
        assert stats["by_status"] == {"indexed": 130, "failed": 2, "pending": 3}
        assert stats["last_updated_at"] is not None
        assert by_id[empty]["document_stats"]["total"] == 0
        assert by_id[empty]["document_stats"]["last_updated_at"] is None
        assert [g["project_id"] for g in by_id[big]["granted_projects"]] == [
            str(seeded["project_id"])
        ]
        assert by_id[empty]["granted_projects"] == []

        # El listado por proyecto trae los mismos agregados.
        per_project = (
            await client.get(f"/projects/{seeded['project_id']}/knowledge-bases", headers=headers)
        ).json()
        assert per_project[0]["document_stats"]["total"] == 135


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_listing_stats_never_count_another_tenants_documents(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
) -> None:
    app, _ = configured_app
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    token_a = await _mint_token(a["user_id"], a["tenant_id"])
    token_b = await _mint_token(b["user_id"], b["tenant_id"])
    ha, hb = {"Authorization": f"Bearer {token_a}"}, {"Authorization": f"Bearer {token_b}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb_a = (await client.post("/knowledge-bases", json={"name": "A"}, headers=ha)).json()["id"]
        kb_b = (await client.post("/knowledge-bases", json={"name": "B"}, headers=hb)).json()["id"]
        await _insert_docs(
            migrations_pg_dsn, tenant_id=a["tenant_id"], kb_id=kb_a, status="indexed", n=4
        )
        await _insert_docs(
            migrations_pg_dsn, tenant_id=b["tenant_id"], kb_id=kb_b, status="failed", n=1
        )
        list_b = (await client.get("/knowledge-bases", headers=hb)).json()
        assert [k["id"] for k in list_b] == [kb_b]
        assert list_b[0]["document_stats"]["total"] == 1
        assert list_b[0]["document_stats"]["by_status"] == {"failed": 1}
