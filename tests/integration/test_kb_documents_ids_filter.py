"""Filtro aditivo `ids` de `GET /knowledge-bases/{kb}/documents` (`task_ui_21`, gap 1).

El asistente de subida sondea el lote con UNA petición por ciclo en vez de una
por documento pendiente.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_kb_endpoints import (  # noqa: F401  (fixture)
    _mint_token,
    _seed_two_tenants,
    configured_app,
)

pytestmark = pytest.mark.integration


async def _insert_docs(dsn: str, *, tenant_id: UUID, kb_id: str, n: int) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key, status)"
            " SELECT gen_random_uuid(), $1, $2::uuid, 'd' || g, 'd' || g || '.txt',"
            " 'text/plain', 'k/' || gen_random_uuid(), 'pending'"
            " FROM generate_series(1, $3) g RETURNING id",
            tenant_id,
            kb_id,
            n,
        )
    finally:
        await conn.close()
    return [str(r["id"]) for r in rows]


@pytest.mark.asyncio
async def test_ids_filter_returns_only_requested_docs_of_this_kb(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
) -> None:
    app, _ = configured_app
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    ha = {"Authorization": f"Bearer {await _mint_token(a['user_id'], a['tenant_id'])}"}
    hb = {"Authorization": f"Bearer {await _mint_token(b['user_id'], b['tenant_id'])}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb1 = (await client.post("/knowledge-bases", json={"name": "K1"}, headers=ha)).json()["id"]
        kb2 = (await client.post("/knowledge-bases", json={"name": "K2"}, headers=ha)).json()["id"]
        kb_b = (await client.post("/knowledge-bases", json={"name": "KB"}, headers=hb)).json()["id"]
        d1 = await _insert_docs(migrations_pg_dsn, tenant_id=a["tenant_id"], kb_id=kb1, n=4)
        d2 = await _insert_docs(migrations_pg_dsn, tenant_id=a["tenant_id"], kb_id=kb2, n=2)
        db = await _insert_docs(migrations_pg_dsn, tenant_id=b["tenant_id"], kb_id=kb_b, n=2)

        # Sólo los pedidos; un id de OTRA base del mismo tenant y uno de OTRO
        # tenant no salen.
        wanted = [d1[0], d1[2]]
        qs = [("ids", i) for i in [*wanted, d2[0], db[0], "00000000-0000-4000-8000-000000000000"]]
        got = await client.get(f"/knowledge-bases/{kb1}/documents", params=qs, headers=ha)
        assert got.status_code == 200
        assert sorted(d["id"] for d in got.json()) == sorted(wanted)

        # Sin `ids` el listado no cambia.
        plain = await client.get(f"/knowledge-bases/{kb1}/documents", headers=ha)
        assert len(plain.json()) == 4

        # Tenant B no ve la base de A ni con ids de A.
        denied = await client.get(
            f"/knowledge-bases/{kb1}/documents", params=[("ids", d1[0])], headers=hb
        )
        assert denied.status_code == 404
        # Y en su propia base, un id de A no sale.
        own = await client.get(
            f"/knowledge-bases/{kb_b}/documents",
            params=[("ids", d1[0]), ("ids", db[0])],
            headers=hb,
        )
        assert [d["id"] for d in own.json()] == [db[0]]


@pytest.mark.asyncio
async def test_ids_filter_is_capped_and_validated(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
) -> None:
    app, _ = configured_app
    seeded = await _seed_two_tenants(migrations_pg_dsn)
    a = seeded["a"]
    ha = {"Authorization": f"Bearer {await _mint_token(a['user_id'], a['tenant_id'])}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb = (await client.post("/knowledge-bases", json={"name": "K"}, headers=ha)).json()["id"]
        bad = await client.get(
            f"/knowledge-bases/{kb}/documents", params=[("ids", "no-es-uuid")], headers=ha
        )
        assert bad.status_code == 422
        many = [("ids", f"00000000-0000-4000-8000-{i:012d}") for i in range(201)]
        over = await client.get(f"/knowledge-bases/{kb}/documents", params=many, headers=ha)
        assert over.status_code == 422
