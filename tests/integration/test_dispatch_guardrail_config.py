"""prod-03 task_prod03_11 — el dispatch resuelve y transporta las TRES capas.

La premisa que traía escrita esta tarea —«en `apps/workers` hoy CERO referencias
a guardrails»— ya era falsa cuando se abordó: el ADR 0102 D3 cableó
`_resolve_effective_guardrails`, que fusionaba plataforma + proyecto y lo metía
en `spec["guardrails"]`. Lo que faltaba de verdad eran las dos cosas que el
enunciado pide al final y que dependían de la tabla de `task_prod03_07`:

  * **la capa TENANT**, que sencillamente no existía en ninguna parte, así que un
    tenant no podía endurecer sus guardrails sin ir proyecto por proyecto;
  * **la versión para invalidación**, para que la config que viajó a un run sea
    identificable y comparable sin re-derivarla.

Este fichero prueba el resolvedor del worker contra PostgreSQL de verdad, con
las tres capas escritas en la tabla, porque el punto entero es que el worker lee
la tabla nueva: los tests unitarios que había antes monkeypatcheaban la capa de
plataforma y por tanto no podían ver ni la tabla ni la capa tenant.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


_PLATFORM = {
    "guardrails": {
        "post_tool": [
            {
                "type": "prompt_injection",
                "id": "platform_prompt_injection",
                "action": "warn",
                "locked": True,
            }
        ]
    }
}
_TENANT = {
    "guardrails": {
        "pre_tool": [{"type": "allowed_domains", "config": {"allowed_domains": ["tenant.test"]}}]
    }
}
_PROJECT = {"guardrails": {"pre_llm": [{"type": "keyword", "config": {"keywords": ["nope"]}}]}}


async def _seed(sm: async_sessionmaker) -> dict[str, UUID]:
    from api_server.db.domain import Project
    from api_server.db.guardrail_config import set_layer_config
    from api_server.db.models import Organization
    from sqlalchemy import text

    ids = {"tenant": uuid4(), "project": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text("TRUNCATE guardrail_configs, projects, organizations RESTART IDENTITY CASCADE")
        )
        s.add(Organization(id=ids["tenant"], name="Disp", slug=f"disp-{ids['tenant'].hex[:8]}"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Disp P",
                status="active",
                is_template=False,
            )
        )
    async with sm() as s, s.begin():
        await set_layer_config(s, "platform", _PLATFORM)
        await set_layer_config(s, "tenant", _TENANT, tenant_id=ids["tenant"])
        await set_layer_config(
            s, "project", _PROJECT, tenant_id=ids["tenant"], project_id=ids["project"]
        )
    return ids


async def _resolve(sm: async_sessionmaker, project_id: UUID) -> dict[str, Any] | None:
    """Lo que el dispatch resolvería para ese proyecto, por su camino real."""
    from api_server.db.domain import Project
    from workers.execution import _resolve_effective_guardrails

    async with sm() as s:
        project = await s.get(Project, project_id)
        return await _resolve_effective_guardrails(s, project)


@pytest.mark.asyncio
async def test_the_dispatch_carries_all_three_layers(
    _migrated: None, admin_database_url: str
) -> None:
    """Plataforma + tenant + proyecto, las tres en el spec del run."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        resolved = await _resolve(sm, ids["project"])

        assert resolved is not None
        hooks = resolved["guardrails"]
        assert hooks["post_tool"][0]["type"] == "prompt_injection"
        assert hooks["post_tool"][0]["locked"] is True
        assert hooks["pre_tool"][0]["type"] == "allowed_domains"  # ← la capa TENANT
        assert hooks["pre_llm"][0]["type"] == "keyword"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_config_travels_with_a_version(_migrated: None, admin_database_url: str) -> None:
    """La versión es la que permite invalidar sin re-derivar la config.

    Va como hermana de ``guardrails`` en el mismo dict, no dentro: `parse_config`
    solo mira la clave ``guardrails``, así que el runtime la ignora sin
    enterarse y el worker puede compararla.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        first = await _resolve(sm, ids["project"])
        assert first is not None
        assert first["version"]

        # Reescribir una capa cambia la versión — sin eso, un caché no sabría.
        from api_server.db.guardrail_config import set_layer_config

        async with sm() as s, s.begin():
            await set_layer_config(
                s,
                "tenant",
                {
                    "guardrails": {
                        "pre_tool": [
                            {
                                "type": "allowed_domains",
                                "config": {"allowed_domains": ["otro.test"]},
                            }
                        ]
                    }
                },
                tenant_id=ids["tenant"],
            )

        second = await _resolve(sm, ids["project"])
        assert second is not None
        assert second["version"] != first["version"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_tenant_layer_cannot_relax_a_platform_lock_even_in_the_dispatch(
    _migrated: None, admin_database_url: str
) -> None:
    """El candado vale también aquí, y no por la validación del CRUD.

    El CRUD rechaza el intento con 422, pero una fila puede haber llegado por
    otra vía (un seed, una restauración, un `psql`). La resolución del dispatch
    es no-estricta a propósito —un run no puede morir porque una config esté
    mal— y el candado tiene que seguir ganando ahí: se ignora el override, no el
    guardrail.
    """
    from api_server.db.guardrail_config import GuardrailConfig

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        # Inyectada a mano, saltándose el CRUD: es el caso que importa.
        async with sm() as s, s.begin():
            row = await s.get(GuardrailConfig, (await _tenant_row_id(sm, ids["tenant"])))
            assert row is not None
            row.config = {
                "guardrails": {
                    "post_tool": [
                        {
                            "type": "prompt_injection",
                            "id": "platform_prompt_injection",
                            "action": "warn",
                            "config": {"remove": True},
                        }
                    ]
                }
            }
        from api_server.db.guardrail_config import invalidate_effective_config_cache

        await invalidate_effective_config_cache()

        resolved = await _resolve(sm, ids["project"])

        assert resolved is not None
        post_tool = resolved["guardrails"]["post_tool"]
        assert [g["id"] for g in post_tool] == ["platform_prompt_injection"]
        assert post_tool[0]["locked"] is True
    finally:
        await engine.dispose()


async def _tenant_row_id(sm: async_sessionmaker, tenant_id: UUID) -> UUID:
    from api_server.db.guardrail_config import get_layer_config

    async with sm() as s:
        row = await get_layer_config(s, "tenant", tenant_id=tenant_id)
        assert row is not None
        return row.id


@pytest.mark.asyncio
async def test_no_layers_means_no_key_in_the_spec(_migrated: None, admin_database_url: str) -> None:
    """Sin capas, el runtime cae a su baseline: no se emite la clave."""
    from api_server.db.domain import Project
    from api_server.db.guardrail_config import invalidate_effective_config_cache
    from api_server.db.models import Organization
    from sqlalchemy import text
    from workers.execution import _agent_spec, _resolve_effective_guardrails

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = {"tenant": uuid4(), "project": uuid4()}
        async with sm() as s, s.begin():
            await s.execute(
                text("TRUNCATE guardrail_configs, projects, organizations RESTART IDENTITY CASCADE")
            )
            s.add(
                Organization(id=ids["tenant"], name="Vacio", slug=f"vacio-{ids['tenant'].hex[:8]}")
            )
            await s.flush()
            s.add(
                Project(
                    id=ids["project"],
                    tenant_id=ids["tenant"],
                    name="Vacio P",
                    status="active",
                    is_template=False,
                )
            )
        await invalidate_effective_config_cache()

        async with sm() as s:
            project = await s.get(Project, ids["project"])
            resolved = await _resolve_effective_guardrails(s, project)

        assert resolved is None
        from workers.execution import ExecutionRequest

        request = ExecutionRequest(
            tenant_id=str(ids["tenant"]),
            task_id=str(uuid4()),
            agent_id=str(uuid4()),
            task={"id": "t-1", "title": "x", "description": ""},
            model={"kind": "ollama"},
        )
        assert "guardrails" not in _agent_spec(request, None, guardrails=resolved)
    finally:
        await engine.dispose()
