"""prod-07 `task_prod07_13` (llm-1) — el coste facturable deja de ser $0.

El hallazgo: `_openai_compat.py` sólo rellena `usage.cost_usd` si el endpoint
añade `cost`, y Ollama/Copilot **nunca** lo añaden (APIM sólo con policy). Ese
0 se persistía tal cual en `executions.total_cost_usd` y lo sumaban los budgets:
tres de los cuatro proveedores del catálogo cerrado gastaban gratis.

`task_prod07_12` ya garantiza el dato de entrada — el paso `model_call` lleva
`provider` y su clave casa con el catálogo, así que cada llamada tiene un
`price_snapshot` congelado con su `cost_usd`. Lo que falta es **dónde
aterriza**: aquí se fija que, cuando el runtime reporta 0 y hay llamadas
preciadas, `total_cost_usd` recibe la SUMA de los snapshots.

Las tres reglas que estos tests clavan, y por qué cada una:

1. runtime reporta 0 + snapshots preciados → se persiste la suma del catálogo
   (si no, budgets sigue sumando $0);
2. runtime reporta coste real → **no se toca** (claude_sdk es hoy el único que
   lo reporta; pisarlo sería la regresión del riesgo 3 del plan);
3. el catálogo no sabe el precio → se queda en 0, no se inventa un número
   (la integridad de facturación que ya defiende `price_snapshot.available`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Project, Task
from api_server.db.execution_repo import (
    create_running_execution,
    finalize_execution,
    get_execution,
    record_execution,
)
from api_server.db.models import Organization
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# El precio sembrado es 3 USD/1M in y 15 USD/1M out (el `unit` cae a su default
# `per_1m_tokens`), así que una llamada de 1000/500 tokens cuesta
# 1000/1e6*3 + 500/1e6*15 = 0.003 + 0.0075 = 0.0105 USD.
_CALL_COST = 0.0105
_CATALOG_FAMILY = "ollama"
_CATALOG_MODEL = "ollama/llama3.1"
_NATIVE_MODEL = "llama3.1"


@dataclass
class _Result:
    """Un `agent_runtime.ExecutionResult` duck-typed (`ExecutionResultLike`)."""

    steps: list[dict[str, Any]]
    usage: dict[str, Any]
    status: str = "done"
    abort_code: str | None = None
    output: str | None = "listo"
    iterations: int = 1
    finish_status: str | None = "success"
    prompt_version: str | None = None
    runtime_image_digest: str | None = None


def _model_call(*, native_model: str = _NATIVE_MODEL, cost_usd: float = 0.0) -> dict[str, Any]:
    """Exactamente lo que emite `agent_runtime.steps.model_call_step`."""
    from agent_runtime.steps import model_call_step

    return model_call_step(
        0,
        "act",
        model=native_model,
        tokens_in=1000,
        tokens_out=500,
        cost_usd=cost_usd,
        summary="llamada del agente",
        provider="ollama",
    )


def _result(*, calls: int, reported_cost: float, native_model: str = _NATIVE_MODEL) -> _Result:
    steps = [_model_call(native_model=native_model) for _ in range(calls)]
    return _Result(
        steps=steps,
        usage={
            "total_tokens": 1500 * calls,
            "cost_usd": reported_cost,
            "tool_calls": 0,
            "model_calls": calls,
        },
    )


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


_INSERT_PRICE = text(
    "INSERT INTO model_prices"
    " (id, provider, model_id, modality, input_price, output_price, source, effective_from)"
    " VALUES (:id, :provider, :model_id, 'text', 3.0, 15.0, 'manual', now())"
)


async def _seed(session: async_sessionmaker, *, with_price: bool = True) -> dict[str, UUID]:
    """Un tenant/proyecto/tarea limpios + (opcional) el precio de catálogo.

    SQL crudo para `model_prices` a propósito: cargar su ORM arrastra la FK a
    `llm_providers`, cuyo modelo este fichero no importa.
    """
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects, organizations,"
                " model_prices RESTART IDENTITY CASCADE"
            )
        )
        if with_price:
            await s.execute(
                _INSERT_PRICE,
                {"id": uuid4(), "provider": _CATALOG_FAMILY, "model_id": _CATALOG_MODEL},
            )
        s.add(Organization(id=ids["tenant"], name="Coste tenant", slug="coste-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Coste project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Coste task",
                status="backlog",
                priority="medium",
            )
        )
    return ids


async def _finalize(sm: async_sessionmaker, ids: dict[str, UUID], result: _Result) -> Any:
    async with sm() as s, s.begin():
        row = await create_running_execution(s, tenant_id=ids["tenant"], task_id=ids["task"])
        execution_id = row.id
    async with sm() as s, s.begin():
        await finalize_execution(s, execution_id, result=result)
    async with sm() as s:
        return await get_execution(s, execution_id)


@pytest.mark.asyncio
async def test_zero_reported_cost_becomes_the_catalog_sum(
    _migrated: None, admin_database_url: str
) -> None:
    """El caso Ollama/Copilot/Azure: el runtime dice 0, el catálogo sí sabe."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        loaded = await _finalize(sm, ids, _result(calls=3, reported_cost=0.0))

        assert loaded is not None
        assert float(loaded.total_cost_usd) == pytest.approx(_CALL_COST * 3), (
            "el coste facturable siguió siendo el 0 del runtime: los budgets "
            "vuelven a sumar $0 para tres de los cuatro proveedores (llm-1)"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_cost_reported_by_the_runtime_is_never_overwritten(
    _migrated: None, admin_database_url: str
) -> None:
    """No-regresión de claude_sdk: el único kind que hoy reporta coste real.

    El snapshot de catálogo daría 0.0315 para estas tres llamadas; el runtime
    dice 0.0042. Gana el runtime — es la cifra que el proveedor facturó de
    verdad, y el snapshot es sólo una estimación.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        loaded = await _finalize(sm, ids, _result(calls=3, reported_cost=0.0042))

        assert loaded is not None
        assert float(loaded.total_cost_usd) == pytest.approx(0.0042), (
            "la estimación de catálogo pisó el coste real del proveedor "
            "(riesgo 3 del plan prod-07)"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_unpriced_call_stays_zero_instead_of_inventing_a_number(
    _migrated: None, admin_database_url: str
) -> None:
    """Sin precio en el catálogo, `available=False`: 0 honesto, no un 0 fingido.

    Es la misma integridad que ya defiende `price_snapshot`: «no lo sé» no se
    puede convertir en una factura.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_price=False)

        loaded = await _finalize(sm, ids, _result(calls=3, reported_cost=0.0))

        assert loaded is not None
        assert float(loaded.total_cost_usd) == 0.0
        snapshots = [
            step["price_snapshot"] for step in loaded.steps_log if step.get("kind") == "model_call"
        ]
        assert snapshots and all(snap["available"] is False for snap in snapshots)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_record_execution_applies_the_same_rule(
    _migrated: None, admin_database_url: str
) -> None:
    """La OTRA vía de escritura. Si sólo se arregla `finalize_execution`, el
    camino de un solo paso (orquestador/tests) sigue facturando $0."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        async with sm() as s, s.begin():
            execution = await record_execution(
                s,
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                result=_result(calls=2, reported_cost=0.0),
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)

        assert loaded is not None
        assert float(loaded.total_cost_usd) == pytest.approx(_CALL_COST * 2)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_per_call_provenance_survives_the_estimate(
    _migrated: None, admin_database_url: str
) -> None:
    """La trazabilidad que sustituye a la columna nueva.

    Sin `cost_estimated_usd` (crear columnas exige migración, fuera del alcance
    de esta tarea), la distinción «reportado por el runtime» vs «estimado por
    catálogo» se lee en los pasos: cada llamada preciada conserva su
    `price_snapshot` con `source`, `price_id` y el `cost_usd` congelado, y el
    `cost_usd` que reportó el runtime sigue ahí al lado, en 0.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        loaded = await _finalize(sm, ids, _result(calls=1, reported_cost=0.0))

        assert loaded is not None
        call = next(step for step in loaded.steps_log if step.get("kind") == "model_call")
        assert call["cost_usd"] == 0.0, "el dato crudo del runtime debe seguir visible"
        snapshot = call["price_snapshot"]
        assert snapshot["available"] is True
        assert float(snapshot["cost_usd"]) == pytest.approx(_CALL_COST)
        assert snapshot["price_id"] is not None
        assert float(loaded.total_cost_usd) == pytest.approx(_CALL_COST)
    finally:
        await engine.dispose()
