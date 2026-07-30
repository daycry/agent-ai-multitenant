"""prod-03 task_prod03_05 — el job de caducidad de aprobaciones, contra Postgres.

`expire_stale_requests` estaba implementada y testeada desde el Plan 02 y NADIE
la llamaba. Aquí se prueba al llamante: `workers.expire_stale_approvals`.

Lo que se fija:

  * caduca lo vencido y NO lo que está dentro de la ventana;
  * la ventana sale del platform setting (`approval.timeout_hours`, default 24 h)
    y el sweep tiene un interruptor vivo (`approval_expiry_enabled`);
  * **aislamiento cross-tenant**: el sweep escribe con el rol BYPASSRLS del
    worker, donde RLS no acota NADA. Barrer un tenant no puede tocar las
    solicitudes de otro — y como la firma acepta `tenant_id`, hay que
    demostrarlo, no suponerlo;
  * emite el aviso de timeout con el `tenant_id` de la fila (el fan-out de
    notificaciones es por tenant: un aviso con el tenant equivocado sería una
    fuga de información entre tenants).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.approval_repo import (
    get_approval_timeout_hours,
    tenants_with_stale_approvals,
)
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization, PlatformSetting
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.approval_expiry import (
    APPROVAL_TIMEOUT_EVENT,
    ApprovalTimeoutNotice,
    _expire_stale_approvals,
)

pytestmark = pytest.mark.integration


# Los tests que van por el JOB tienen que sembrar relativo al reloj REAL: el job
# llama a `datetime.now(UTC)` por dentro (no acepta `now=`, y no debería —
# inyectarle el reloj sería probar un job que no es el que corre). `_NOW` fijo
# solo vale para los que pasan `now=` explícito.
def _now() -> datetime:
    return datetime.now(UTC)


_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


class _CapturingNotifier:
    """Seam del notificador: captura los avisos sin broker."""

    def __init__(self) -> None:
        self.notices: list[ApprovalTimeoutNotice] = []

    def notify(self, notice: ApprovalTimeoutNotice) -> None:
        self.notices.append(notice)


class _Settings:
    """Doble mínimo de `workers.config.Settings` (solo se usan estos dos campos)."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.broker_url = "memory://"


async def _seed_tenant(
    sm: async_sessionmaker,
    *,
    slug: str,
    requested_at: datetime,
    truncate: bool = False,
) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task": uuid4(),
        "execution": uuid4(),
        "request": uuid4(),
    }
    async with sm() as s, s.begin():
        if truncate:
            await s.execute(
                text(
                    "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                    " projects, organizations, platform_settings"
                    " RESTART IDENTITY CASCADE"
                )
            )
        s.add(Organization(id=ids["tenant"], name=f"Tenant {slug}", slug=slug))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name=f"Project {slug}",
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
                title=f"Task {slug}",
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
        s.add(
            ApprovalRequest(
                id=ids["request"],
                tenant_id=ids["tenant"],
                execution_id=ids["execution"],
                task_id=ids["task"],
                project_id=ids["project"],
                category="production_deploy",
                status="pending",
                requested_at=requested_at,
            )
        )
    return ids


async def _set_setting(sm: async_sessionmaker, key: str, value: Any) -> None:
    async with sm() as s, s.begin():
        s.add(PlatformSetting(key=key, value=value))


@pytest.mark.asyncio
async def test_the_job_expires_stale_requests_and_notifies(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        stale = await _seed_tenant(
            sm, slug="expiry-stale", requested_at=_now() - timedelta(hours=99), truncate=True
        )
        notifier = _CapturingNotifier()

        result = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=notifier,
        )

        assert result["enabled"] is True and result["ok"] is True
        assert result["expired"] == 1
        assert result["tenants"] == 1
        assert result["failed_tenants"] == 0
        assert result["timeout_hours"] == 24.0

        async with sm() as s:
            request = await s.get(ApprovalRequest, stale["request"])
            execution = await s.get(Execution, stale["execution"])
            task = await s.get(Task, stale["task"])
        assert request is not None and execution is not None and task is not None
        assert request.status == "timed_out"
        assert request.resolved_at is not None
        assert execution.status == "aborted"
        assert execution.abort_code == "approval_timeout_exceeded"
        assert task.status == "blocked"

        # El aviso sale con el tenant de la fila (el fan-out es por tenant).
        assert len(notifier.notices) == 1
        notice = notifier.notices[0]
        assert notice.event_type == APPROVAL_TIMEOUT_EVENT
        assert notice.tenant_id == str(stale["tenant"])
        assert notice.context["task_id"] == str(stale["task"])
        assert notice.context["approval_request_id"] == str(stale["request"])
        assert notice.context["approval_category"] == "production_deploy"
        assert notice.context["task_title"] == "Task expiry-stale"
        assert notice.context["project_name"] == "Project expiry-stale"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_request_inside_the_window_is_untouched(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        fresh = await _seed_tenant(
            sm, slug="expiry-fresh", requested_at=_now() - timedelta(hours=1), truncate=True
        )
        notifier = _CapturingNotifier()

        result = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=notifier,
        )

        # Ni tenants candidatos: la pasada en vacío es un SELECT y nada más.
        assert result["tenants"] == 0
        assert result["expired"] == 0
        assert notifier.notices == []

        async with sm() as s:
            request = await s.get(ApprovalRequest, fresh["request"])
            execution = await s.get(Execution, fresh["execution"])
        assert request is not None and request.status == "pending"
        assert execution is not None and execution.status == "awaiting_human_approval"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_kill_switch_makes_the_run_a_noop(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        stale = await _seed_tenant(
            sm, slug="expiry-off", requested_at=_now() - timedelta(hours=99), truncate=True
        )
        await _set_setting(sm, "approval_expiry_enabled", False)
        notifier = _CapturingNotifier()

        result = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=notifier,
        )

        assert result == {"enabled": False, "skipped": True}
        async with sm() as s:
            request = await s.get(ApprovalRequest, stale["request"])
        assert request is not None and request.status == "pending"
        assert notifier.notices == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_timeout_window_comes_from_the_platform_setting(
    _migrated: None, admin_database_url: str
) -> None:
    """Una solicitud de 2 h: a salvo con 24 h, vencida con la ventana bajada a 1 h."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_tenant(
            sm, slug="expiry-window", requested_at=_now() - timedelta(hours=2), truncate=True
        )

        async with sm() as s:
            assert await get_approval_timeout_hours(s) == 24.0
        untouched = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=None,
        )
        assert untouched["expired"] == 0

        await _set_setting(sm, "approval.timeout_hours", 1)
        async with sm() as s:
            assert await get_approval_timeout_hours(s) == 1.0
        expired = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=None,
        )
        assert expired["expired"] == 1
        assert expired["timeout_hours"] == 1.0

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
        assert request is not None and request.status == "timed_out"
        assert request.reason == "no response within 1 h"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_nonsense_timeout_setting_falls_back_instead_of_expiring_everything(
    _migrated: None, admin_database_url: str
) -> None:
    """Un typo en la UI no puede convertir el sweep en «caduca todo».

    El clamp importa porque caducar ABORTA la ejecución: un `0` mal puesto
    mataría toda solicitud viva de la plataforma en la siguiente pasada.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_tenant(
            sm, slug="expiry-typo", requested_at=_now() - timedelta(minutes=1), truncate=True
        )

        await _set_setting(sm, "approval.timeout_hours", 0)
        async with sm() as s:
            # Clampado al suelo de 15 min, no aplicado a ciegas.
            assert await get_approval_timeout_hours(s) == 0.25
        result = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=None,
        )
        assert result["expired"] == 0

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
        assert request is not None and request.status == "pending"

        async with sm() as s, s.begin():
            await s.execute(
                text("UPDATE platform_settings SET value = '\"gato\"'::jsonb WHERE key = :k"),
                {"k": "approval.timeout_hours"},
            )

        async with sm() as s:
            # Un valor no numérico cae al default documentado, no explota.
            assert await get_approval_timeout_hours(s) == 24.0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweeping_one_tenant_never_touches_another(
    _migrated: None, admin_database_url: str
) -> None:
    """Aislamiento cross-tenant del barrido (Principio nº1).

    El job corre con el rol BYPASSRLS del worker: RLS no le acota nada, así que
    el scope por tenant es responsabilidad del código. Se barre SOLO el tenant A
    y el tenant B —igual de vencido— tiene que quedar intacto.
    """
    from api_server.db.approval_repo import expire_stale_requests

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        a = await _seed_tenant(
            sm, slug="iso-a", requested_at=_NOW - timedelta(hours=99), truncate=True
        )
        b = await _seed_tenant(sm, slug="iso-b", requested_at=_NOW - timedelta(hours=99))

        async with sm() as s:
            tenants = await tenants_with_stale_approvals(s, now=_NOW, timeout_hours=24)
        assert set(tenants) == {a["tenant"], b["tenant"]}

        async with sm() as s, s.begin():
            expired = await expire_stale_requests(
                s, now=_NOW, timeout_hours=24, tenant_id=a["tenant"]
            )
        assert [r.id for r in expired] == [a["request"]]

        async with sm() as s:
            req_a = await s.get(ApprovalRequest, a["request"])
            req_b = await s.get(ApprovalRequest, b["request"])
            exec_b = await s.get(Execution, b["execution"])
            task_b = await s.get(Task, b["task"])
        assert req_a is not None and req_a.status == "timed_out"
        # El vecino, intacto: fila, ejecución y tarea.
        assert req_b is not None and req_b.status == "pending"
        assert exec_b is not None and exec_b.status == "awaiting_human_approval"
        assert task_b is not None and task_b.status == "awaiting_human_approval"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_job_sweeps_every_tenant_in_its_own_transaction(
    _migrated: None, admin_database_url: str
) -> None:
    """El job entero sí cubre a todos los tenants — uno a uno, no de golpe."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        a = await _seed_tenant(
            sm, slug="multi-a", requested_at=_now() - timedelta(hours=99), truncate=True
        )
        b = await _seed_tenant(sm, slug="multi-b", requested_at=_now() - timedelta(hours=99))
        notifier = _CapturingNotifier()

        result = await _expire_stale_approvals(
            _Settings(admin_database_url),  # type: ignore[arg-type]
            notifier=notifier,
        )
        assert result["tenants"] == 2
        assert result["expired"] == 2

        # Un aviso por tenant, cada uno con SU tenant_id.
        assert {n.tenant_id for n in notifier.notices} == {
            str(a["tenant"]),
            str(b["tenant"]),
        }

        async with sm() as s:
            for ids in (a, b):
                request = await s.get(ApprovalRequest, ids["request"])
                assert request is not None and request.status == "timed_out"
    finally:
        await engine.dispose()
