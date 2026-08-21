"""Las tools del asistente abren su propia sesión corta — y siguen aisladas
por tenant (prod-13 `task_prod13_07`).

El riesgo nº 1 de esta tarea, escrito en el propio plan
-------------------------------------------------------
Para sacar el turno LLM de la transacción del request hay que quitarle a las
tools la sesión viva que recibían y darles una **fábrica** de sesiones. Ahí está
el peligro: si esa fábrica no repite el ``set_config`` de ``app.tenant_id`` que
hace ``open_tenant_session``, las tools dejan de estar bajo RLS y el asistente de
un tenant puede leer datos de otro. Es la única parte de prod-13 cuyo fallo no se
manifiesta como lentitud sino como **fuga entre tenants**.

Por eso la fábrica es ``open_tenant_session`` TAL CUAL —no una segunda forma de
abrir sesión que pueda olvidarse del binding— y por eso este fichero lo
comprueba en las dos direcciones, que es lo que distingue una guarda de un
adorno:

  * el tenant B **ve lo suyo** — si la sesión corta se abriera sin
    ``app.tenant_id``, la política ``tenant_id = NULLIF(current_setting(...))``
    no casaría con nada y la tool devolvería cero filas: verde vacío;
  * el tenant B **no ve lo de A** — si la sesión corta se abriera contra el
    engine BYPASSRLS, la tool las devolvería todas.

Sólo fallando en ambas direcciones el test dice algo. Comprobado rompiendo la
implementación en los dos sentidos (ver el informe de la tarea).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: dos tenants con datos propios que el otro no debe ver
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a, tenant_b = uuid4(), uuid4()
    admin_a, admin_b = uuid4(), uuid4()
    project_a, project_b = uuid4(), uuid4()
    plan_a, plan_b = uuid4(), uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, messages, conversations, projects, agents,"
            " memory_entries, tenant_settings, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled)"
            " VALUES ($1, $2, $3, true), ($4, $5, $6, true)",
            tenant_a,
            "Tenant A factory",
            "tenant-a-factory",
            tenant_b,
            "Tenant B factory",
            "tenant-b-factory",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            admin_a,
            "admin-a@factory.test",
            "argon2-placeholder",
            admin_b,
            "admin-b@factory.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_a,
            admin_a,
            uuid4(),
            tenant_b,
            admin_b,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, $3, 'active', false), ($4, $5, $6, 'active', false)",
            project_a,
            tenant_a,
            "Proyecto de A",
            project_b,
            tenant_b,
            "Proyecto de B (secreto)",
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, created_by)"
            " VALUES ($1, $2, $3, $4, 'pending_approval', $5),"
            "        ($6, $7, $8, $9, 'pending_approval', $10)",
            plan_a,
            tenant_a,
            project_a,
            "Plan de A",
            admin_a,
            plan_b,
            tenant_b,
            project_b,
            "Plan de B (secreto)",
            admin_b,
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "admin_b": admin_b,
        "project_a": project_a,
        "project_b": project_b,
        "plan_a": plan_a,
        "plan_b": plan_b,
    }


def _ctx_for(seeded: dict[str, UUID], tenant_key: str, admin_key: str) -> tuple[Any, list[int]]:
    """Un ``AssistantToolScope`` — el alcance SIN sesión viva, con la fábrica que
    el endpoint le pasa en producción. Devuelve también un contador de aperturas
    para poder afirmar que cada tool abre la SUYA y la suelta."""
    from api_server.assistant.tools import AssistantToolScope
    from api_server.auth.deps import AuthPrincipal, open_tenant_session

    principal = AuthPrincipal(
        user_id=seeded[admin_key],
        session_id=uuid4(),
        tenant_id=seeded[tenant_key],
    )
    opened: list[int] = []

    @asynccontextmanager
    async def _factory() -> Any:
        opened.append(1)
        async with open_tenant_session(principal) as session:
            yield session

    ctx = AssistantToolScope(
        tenant_id=seeded[tenant_key],
        user_id=seeded[admin_key],
        session_factory=_factory,
    )
    return ctx, opened


# ===========================================================================
# La sesión corta: una por llamada, y devuelta al pool
# ===========================================================================
@pytest.mark.asyncio
async def test_each_tool_call_opens_its_own_session_and_gives_it_back(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import get_engine

    ctx, opened = _ctx_for(seeded, "tenant_a", "admin_a")
    projects = await run_assistant_tool("tenant_projects_status", ctx)
    plans = await run_assistant_tool("tenant_plans_summary", ctx)

    assert projects["total"] == 1
    assert plans["total"] == 1
    # Una sesión por llamada — no una compartida que sobreviva al turno.
    assert opened == [1, 1], opened
    # Y ninguna se quedó retenida: el pool está en cero al volver.
    assert get_engine().pool.checkedout() == 0
    # El alcance no guarda sesión: la corta vive y muere dentro del despacho.
    assert not hasattr(ctx, "session")


# ===========================================================================
# El riesgo nº 1: aislamiento por tenant a través de la fábrica
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_the_factory_path_keeps_the_tenants_apart(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool

    ctx_a, _ = _ctx_for(seeded, "tenant_a", "admin_a")
    ctx_b, _ = _ctx_for(seeded, "tenant_b", "admin_b")

    projects_a = await run_assistant_tool("tenant_projects_status", ctx_a)
    plans_a = await run_assistant_tool("tenant_plans_summary", ctx_a)
    projects_b = await run_assistant_tool("tenant_projects_status", ctx_b)
    plans_b = await run_assistant_tool("tenant_plans_summary", ctx_b)

    ids_a = {p["id"] for p in projects_a["projects"]}
    ids_b = {p["id"] for p in projects_b["projects"]}
    plan_ids_a = {p["id"] for p in plans_a["plans"]}
    plan_ids_b = {p["id"] for p in plans_b["plans"]}

    # Cada uno ve lo suyo (si la fábrica no pusiera `app.tenant_id`, esto sería
    # el conjunto vacío y el test caería aquí, no en la aserción de fuga).
    assert ids_a == {str(seeded["project_a"])}, projects_a
    assert ids_b == {str(seeded["project_b"])}, projects_b
    assert plan_ids_a == {str(seeded["plan_a"])}, plans_a
    assert plan_ids_b == {str(seeded["plan_b"])}, plans_b

    # Y ninguno ve lo del otro (si la fábrica usara el engine BYPASSRLS, esto
    # sería el conjunto de los dos y el test caería aquí).
    assert str(seeded["project_b"]) not in ids_a
    assert str(seeded["project_a"]) not in ids_b
    assert str(seeded["plan_b"]) not in plan_ids_a
    assert str(seeded["plan_a"]) not in plan_ids_b


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_the_write_tool_commits_in_its_own_session_and_belongs_to_its_tenant(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """``remember_about_me`` es la ÚNICA tool que escribe. Antes su INSERT se
    commiteaba con la transacción del request; ahora tiene que commitear en su
    sesión corta —si no, el asistente diría «lo he recordado» y no habría fila—
    y quedar bajo el tenant del que preguntó."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool

    ctx_b, opened = _ctx_for(seeded, "tenant_b", "admin_b")
    stored = await run_assistant_tool(
        "remember_about_me", ctx_b, {"content": "Prefiere las reuniones cortas"}
    )
    assert stored["stored"] is True, stored
    assert opened == [1]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT tenant_id, user_id, scope FROM memory_entries WHERE deleted_at IS NULL"
        )
    finally:
        await conn.close()

    assert len(rows) == 1, rows
    assert rows[0]["tenant_id"] == seeded["tenant_b"]
    assert rows[0]["user_id"] == seeded["admin_b"]
    assert rows[0]["scope"] == "private"

    # Y el dedup sigue funcionando entre sesiones cortas distintas (antes las dos
    # llamadas compartían transacción; ahora la primera ya está commiteada).
    again = await run_assistant_tool(
        "remember_about_me", ctx_b, {"content": "Prefiere las reuniones cortas"}
    )
    assert again["stored"] is False
    assert again["deduped"] is True


# ===========================================================================
# Contratos del contexto
# ===========================================================================
def test_binding_a_session_keeps_the_tenant_and_the_user() -> None:
    """``bind`` es el punto donde el alcance se convierte en el contexto que ve
    la tool. Si ahí se perdiera el ``tenant_id``, las tools que filtran por
    ``ctx.tenant_id`` en vez de fiarse sólo de RLS —``tenant_budget_status``,
    las dos de humanos, la de memoria— pasarían a mirar otro tenant sin que
    ninguna consulta fallara."""
    from api_server.assistant.tools import AssistantToolScope

    tenant_id, user_id = uuid4(), uuid4()
    sentinel = object()
    scope = AssistantToolScope(
        tenant_id=tenant_id,
        user_id=user_id,
        session_factory=lambda: (_ for _ in ()),  # nunca se llama en este test
    )
    bound = scope.bind(sentinel)  # type: ignore[arg-type]

    assert bound.tenant_id == tenant_id
    assert bound.user_id == user_id
    assert bound.session is sentinel


@pytest.mark.asyncio
async def test_a_context_with_a_bound_session_still_works(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El camino con sesión ya enlazada se conserva a propósito: es el que usan
    las llamadas que YA están dentro de una transacción abierta (y toda la suite
    de integración previa del asistente). Lo que no puede haber es una segunda
    forma de ENLAZAR el tenant — por eso la fábrica es ``open_tenant_session``."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import AssistantToolContext, run_assistant_tool
    from api_server.auth.deps import AuthPrincipal, open_tenant_session

    principal = AuthPrincipal(
        user_id=seeded["admin_a"], session_id=uuid4(), tenant_id=seeded["tenant_a"]
    )
    async with open_tenant_session(principal) as session:
        ctx = AssistantToolContext(
            session=session, tenant_id=seeded["tenant_a"], user_id=seeded["admin_a"]
        )
        projects = await run_assistant_tool("tenant_projects_status", ctx)

    assert {p["id"] for p in projects["projects"]} == {str(seeded["project_a"])}
