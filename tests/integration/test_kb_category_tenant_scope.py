"""Las categorías embebidas en los listados de KBs llevan predicado de tenant.

Revisión adversarial (intento 1): la consulta batch de categorías de `GET /knowledge-bases`
y `GET /projects/{id}/knowledge-bases` sólo filtraba por id y `deleted_at` (principio 1:
nunca una query sin tenant, además de la RLS). Las built-in viven bajo PLATFORM_TENANT_ID con
`is_builtin` (migración 0030), así que el predicado correcto es «del tenant O built-in».
"""

from __future__ import annotations

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.integration.test_kb_endpoints import (  # noqa: F401  (fixture)
    _mint_token,
    _seed,
    configured_app,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_embedded_category_query_is_tenant_scoped_and_keeps_builtins(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
) -> None:
    app, _ = configured_app
    seeded = await _seed(migrations_pg_dsn)
    headers = {
        "Authorization": f"Bearer {await _mint_token(seeded['user_id'], seeded['tenant_id'])}"
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        kb = (
            await client.post("/knowledge-bases", json={"name": "Con categoría"}, headers=headers)
        ).json()
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            platform = "00000000-0000-0000-0000-000000000001"
            cat = await conn.fetchval(
                "INSERT INTO kb_categories (id, tenant_id, slug, name, color, is_builtin)"
                " VALUES (gen_random_uuid(), $1::uuid, 'builtin-x', 'Built-in X', '#111111', true)"
                " RETURNING id",
                platform,
            )
            await conn.execute(
                "UPDATE knowledge_bases SET category_id = $1 WHERE id = $2::uuid", cat, kb["id"]
            )
        finally:
            await conn.close()

        seen: list[str] = []

        def capture(conn_, cursor, statement, *args):  # type: ignore[no-untyped-def]
            if "FROM kb_categories" in statement:
                seen.append(statement)

        event.listen(Engine, "before_cursor_execute", capture)
        try:
            listed = (await client.get("/knowledge-bases", headers=headers)).json()
        finally:
            event.remove(Engine, "before_cursor_execute", capture)

    assert listed[0]["category"]["slug"] == "builtin-x", "la built-in sigue embebida"
    assert seen, "no se vio la consulta de categorías"
    assert all("kb_categories.tenant_id" in q for q in seen), seen
