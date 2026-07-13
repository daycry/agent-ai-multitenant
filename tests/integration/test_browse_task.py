"""ADR 0080 — la task que ejecuta una sesión de navegación aprobada, end-to-end.

Ejercita `_run_browse` (el pegamento BD ↔ contenedor) contra una BD real, con
el lanzamiento del contenedor y el kill-switch inyectados. Cubre los caminos
que la lógica pura no toca y donde vivía el bug approved→failed:

  * kill-switch apagado entre aprobar y ejecutar → la sesión acaba `failed` con
    causa, NO colgada en `approved` ni crasheando la task;
  * sesión aprobada + navegación OK → `done` con el resultado saneado;
  * el runtime falla → `failed` con causa (nunca colgada en `running`);
  * una sesión no aprobada no se ejecuta (skipped).

browse_sessions es tenant-less (córtex = plataforma), así que se accede por un
sessionmaker admin/BYPASSRLS — el mismo modo que el worker usa en producción.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_STEPS = [{"action": "goto", "url": "https://example.com"}, {"action": "extract"}]


@pytest.fixture()
def migrated_admin_maker(alembic_config, admin_database_url: str, monkeypatch):
    """BD migrada al head + un sessionmaker admin (BYPASSRLS) para browse_sessions."""
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        asyncio.run(engine.dispose())


async def _seed(maker, *, status: str) -> str:
    from api_server.db.browse_repo import create_pending

    async with maker() as session, session.begin():
        row = await create_pending(
            session, tenant_id=None, owner_user_id=uuid4(), goal="leer el panel", steps=_STEPS
        )
        if status != "pending_approval":
            row.status = status
    return str(row.id)


async def _run(maker, session_id, monkeypatch, *, browser_enabled=True, container=None):
    import api_server.db.platform_settings as ps
    import workers.browse_task as mod
    from workers.config import Settings

    async def _kill(_session):
        return browser_enabled

    monkeypatch.setattr(ps, "get_cortex_browser_enabled", _kill)
    monkeypatch.setattr(
        mod, "run_browse_container", container or (lambda *a, **k: (True, {"extracted": []}))
    )
    return await mod._run_browse(Settings(), session_id, sessionmaker=maker)


async def _status(maker, session_id: str):
    from api_server.db.browse_repo import get_browse_session

    async with maker() as session:
        return await get_browse_session(session, UUID(session_id))


async def test_kill_switch_off_marks_failed_without_crashing(
    migrated_admin_maker, monkeypatch
) -> None:
    sid = await _seed(migrated_admin_maker, status="approved")
    out = await _run(migrated_admin_maker, sid, monkeypatch, browser_enabled=False)
    assert out["skipped"] == "kill_switch_off"
    row = await _status(migrated_admin_maker, sid)
    assert row.status == "failed", "no queda colgada en approved ni crashea la task"
    assert "deshabilitado" in (row.error or "")


async def test_approved_and_ok_ends_done_with_result(migrated_admin_maker, monkeypatch) -> None:
    sid = await _seed(migrated_admin_maker, status="approved")
    out = await _run(
        migrated_admin_maker,
        sid,
        monkeypatch,
        container=lambda *a, **k: (True, {"extracted": [{"text": "hola"}]}),
    )
    assert out["ok"] is True
    row = await _status(migrated_admin_maker, sid)
    assert row.status == "done"
    assert row.result == {"extracted": [{"text": "hola"}]}


async def test_runtime_failure_ends_failed_never_stuck_running(
    migrated_admin_maker, monkeypatch
) -> None:
    sid = await _seed(migrated_admin_maker, status="approved")

    def _boom(*a, **k):
        raise RuntimeError("docker caído")

    out = await _run(migrated_admin_maker, sid, monkeypatch, container=_boom)
    assert out["ok"] is False
    row = await _status(migrated_admin_maker, sid)
    assert row.status == "failed", "un runtime que revienta no deja la sesión en running"
    assert "docker" in (row.error or "")


async def test_an_unapproved_session_is_not_executed(migrated_admin_maker, monkeypatch) -> None:
    sid = await _seed(migrated_admin_maker, status="pending_approval")
    launched: list[bool] = []

    def _spy(*a, **k):
        launched.append(True)
        return True, {}

    out = await _run(migrated_admin_maker, sid, monkeypatch, container=_spy)
    assert out["skipped"] == "pending_approval"
    assert launched == [], "una sesión sin aprobar NUNCA llega al contenedor"
