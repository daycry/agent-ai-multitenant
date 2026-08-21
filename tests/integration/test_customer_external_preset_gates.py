"""prod-03 task_prod03_15 — el test de regresión del titular de la auditoría.

El titular era: **«ni siquiera el preset Cliente Externo detiene una sola
tool»**. La causa (hallazgo g6) fue de vocabulario: los 4 presets sembrados
decidían sobre 13 categorías canónicas y el gate del runtime emitía 4 distintas
—intersección VACÍA—, así que `requires_human` caía siempre en `auto`. Un
proyecto con la política más estricta del catálogo ejecutaba `http_post` sin que
ningún humano lo viera.

Esto recorre la cadena entera con el preset REAL leído del seed, no con un dict
escrito a mano en el test: si alguien cambia el preset y deja de gatear algo,
esto se pone rojo. Cuatro tramos, que son los del enunciado:

  1. **se aparca**: la primera tool sensible bajo `customer-external` devuelve
     categoría (el run se detiene antes de ejecutarla);
  2. **el rechazo aborta**: no hay autorización, la acción se vuelve a aparcar;
  3. **la aprobación continúa SIN re-aparcar la MISMA acción** — el bucle del
     ADR 0020 que `task_prod03_06` cerró (ADR 0135);
  4. **lo no atendido caduca**: el barrido de `task_prod03_05` la pasa a
     `timed_out` y aborta la ejecución.

Sobre la ubicación: el plan lo nombra `tests/e2e/test_customer_external_preset_gates.py`.
Vive en `tests/integration/` por lo mismo que
`test_planning_guardrails_route.py`: necesita Postgres y el gate, no un stack
Docker, y `tests/e2e/` no lo corre CI — un test que no se ejecuta no vigila.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from agent_runtime.approval import ApprovalGate
from alembic import command
from api_server.db.approval_repo import (
    read_approved_actions,
    request_approval_if_needed,
    resolve_approval,
)
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization, User
from api_server.seeds.builtin_approval_policies import BUILTIN_POLICIES
from shared_domain.approval_categories import APPROVAL_CATEGORIES
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def _preset(slug: str) -> dict[str, Any]:
    """El preset TAL COMO SE SIEMBRA. No una copia escrita en el test.

    Copiarlo aquí sería el modo de fallo clásico: el test seguiría verde
    mientras el preset real se relaja.
    """
    policy = next(p for p in BUILTIN_POLICIES if p.slug == slug)
    return {"preset": slug, "categories": dict(policy.decisions)}


_CUSTOMER_EXTERNAL = _preset("customer-external")
_SANDBOX = _preset("sandbox")

# La acción sensible del enunciado.
_TOOL = "http_post"
_ARGS = {"url": "https://cliente.example/api/pedidos", "body": {"id": 7}}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


async def _seed(sm: async_sessionmaker) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task": uuid4(),
        "execution": uuid4(),
        "request": uuid4(),
        "reviewer": uuid4(),
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                " projects, organizations, users RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Cliente", slug=f"cli-{ids['tenant'].hex[:8]}"))
        s.add(
            User(
                id=ids["reviewer"],
                email=f"rev-{ids['reviewer'].hex[:8]}@cli.test",
                password_hash="h",
            )
        )
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Proyecto cliente",
                status="active",
                is_template=False,
                human_approval_policy=_CUSTOMER_EXTERNAL,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Publicar el pedido",
                status="awaiting_human_approval",
                priority="medium",
            )
        )
        await s.flush()
        s.add(
            Execution(
                id=ids["execution"],
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="awaiting_human_approval",
            )
        )
        await s.flush()
    # La solicitud se crea por el camino REAL —el mismo que usa el worker al
    # recoger un run aparcado—, no con un INSERT a mano: así el test también
    # comprueba que `request_approval_if_needed` acepta esta política.
    async with sm() as s, s.begin():
        execution = await s.get(Execution, ids["execution"])
        project = await s.get(Project, ids["project"])
        assert execution is not None and project is not None
        request = await request_approval_if_needed(
            s,
            execution=execution,
            project=project,
            category="external_http_post",
            action={"tool": _TOOL, "args": dict(_ARGS)},
        )
        assert request is not None, "customer-external tiene que aparcar esta acción"
        ids["request"] = request.id
    return ids


async def _resolve(
    sm: async_sessionmaker, request_id: UUID, reviewer_id: UUID, *, approved: bool
) -> None:
    async with sm() as s, s.begin():
        request = await s.get(ApprovalRequest, request_id)
        assert request is not None
        resolved = await resolve_approval(
            s, request, approved=approved, resolver_id=reviewer_id, reason="decidido"
        )
        assert resolved is not None


# ---------------------------------------------------------------------------
# 0. El vocabulario, que es donde estuvo el agujero
# ---------------------------------------------------------------------------
def test_the_preset_decides_on_the_canonical_vocabulary() -> None:
    """Si el preset y el gate vuelven a hablar idiomas distintos, esto se rompe.

    Es la comprobación que faltaba en 2026-06: la intersección VACÍA entre las
    13 categorías del preset y las 4 que emitía el runtime no la vio nadie
    porque nadie la miró.
    """
    assert set(_CUSTOMER_EXTERNAL["categories"]) == set(APPROVAL_CATEGORIES)
    assert _CUSTOMER_EXTERNAL["categories"]["external_http_post"] == "human_required"


# ---------------------------------------------------------------------------
# 1. Se aparca (y con `sandbox` NO, que es el control)
# ---------------------------------------------------------------------------
def test_customer_external_stops_the_first_sensitive_tool() -> None:
    gate = ApprovalGate(_CUSTOMER_EXTERNAL)

    assert gate.review(_TOOL, _ARGS) == "external_http_post"


def test_sandbox_lets_the_same_call_through() -> None:
    """El control del experimento: sin él, un gate roto en «siempre aparca»
    pasaría el test de arriba y no gatearía nada de verdad."""
    assert ApprovalGate(_SANDBOX).review(_TOOL, _ARGS) is None


# ---------------------------------------------------------------------------
# 2 y 3. Rechazar vs aprobar
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rejecting_leaves_the_action_unauthorised(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        await _resolve(sm, ids["request"], ids["reviewer"], approved=False)
        async with sm() as s:
            actions = await read_approved_actions(s, task_id=ids["task"], tenant_id=ids["tenant"])
        assert actions == []
        # Y el gate la vuelve a aparcar: rechazar no autoriza nada.
        assert ApprovalGate(_CUSTOMER_EXTERNAL, approved_actions=actions).review(_TOOL, _ARGS) == (
            "external_http_post"
        )

        async with sm() as s:
            execution = await s.get(Execution, ids["execution"])
        assert execution is not None and execution.status == "aborted"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approving_lets_the_same_action_through_exactly_once(
    _migrated: None, admin_database_url: str
) -> None:
    """El tramo que la auditoría llamaba el bucle aprobar→re-aparcar.

    Aprobar autoriza ESA acción exacta, en ESA task, UNA vez (ADR 0135, T1). La
    segunda vez vuelve a preguntar: la autorización se canjea, no se acumula.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        await _resolve(sm, ids["request"], ids["reviewer"], approved=True)
        async with sm() as s:
            actions = await read_approved_actions(s, task_id=ids["task"], tenant_id=ids["tenant"])
        assert actions, "aprobar tiene que dejar la acción autorizada en el spec del run"

        gate = ApprovalGate(_CUSTOMER_EXTERNAL, approved_actions=actions)
        assert gate.review(_TOOL, _ARGS) is None  # continúa, SIN re-aparcar
        assert gate.review(_TOOL, _ARGS) == "external_http_post"  # y solo una vez
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_different_action_of_the_same_category_is_still_parked(
    _migrated: None, admin_database_url: str
) -> None:
    """Aprobar un `http_post` no es firmar un permiso de `http_post`."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        await _resolve(sm, ids["request"], ids["reviewer"], approved=True)
        async with sm() as s:
            actions = await read_approved_actions(s, task_id=ids["task"], tenant_id=ids["tenant"])

        gate = ApprovalGate(_CUSTOMER_EXTERNAL, approved_actions=actions)
        otra = {"url": "https://otro.example/borrar", "body": {}}
        assert gate.review(_TOOL, otra) == "external_http_post"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 4. Lo no atendido caduca
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unattended_request_expires_and_aborts_the_run(
    _migrated: None, admin_database_url: str
) -> None:
    from api_server.db.approval_repo import expire_stale_requests

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        async with sm() as s, s.begin():
            request = await s.get(ApprovalRequest, ids["request"])
            assert request is not None
            request.requested_at = datetime.now(UTC) - timedelta(hours=99)

        async with sm() as s, s.begin():
            expired = await expire_stale_requests(s, timeout_hours=24, tenant_id=ids["tenant"])
        assert [r.id for r in expired] == [ids["request"]]

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert request is not None and request.status == "timed_out"
        assert execution is not None and execution.status == "aborted"
        assert task is not None and task.status == "blocked"
    finally:
        await engine.dispose()
