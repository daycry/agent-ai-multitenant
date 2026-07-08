"""task_prod12_mkt_01 — análisis estático en la PRIMERA instalación.

El endpoint `POST /marketplace/installations` corre el MISMO gate de
análisis estático (bandit/semgrep vía `InstallOrchestrator`) que el update
ya corría, cuando el artefacto del listing existe en disco:

  - hallazgos por encima de la política de confianza → 422 + audit row de
    aborto (`static_analysis_blocked`) y NINGUNA installation persistida;
  - análisis limpio → instala respetando el gate de consentimiento
    (community/experimental nacen DISABLED) y el audit detail lleva el
    informe (`gates.static_analysis`);
  - artefacto AUSENTE en disco → instala registrando el skip con honestidad
    (`skipped_reason=no_artifact`) — bloquear aquí cerraría en falso TODAS
    las instalaciones pre-registry (la regresión H4 que documenta ADR 0081);
  - install y update comparten el MISMO pipeline: el analyzer inyectado ve
    ambas llamadas.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_SKILL_MD = """---
name: sa-skill
version: 1.0.0
description: skill de prueba para el gate de análisis
---

# sa-skill

Cuerpo del skill.
"""


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "admin": uuid4(),
        "source": uuid4(),
        "community": uuid4(),
        "community_2": uuid4(),
        "community_v2": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources,"
            " projects, agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant"],
            "Tenant SA",
            "tenant-sa-mkt",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-sa-mkt",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            ids["admin"],
            "admin@sa-mkt.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type)"
            " VALUES ($1, 'official-catalog', 'official')",
            ids["source"],
        )
        # Tres listings community del catálogo global: dos 1.0.0 (una por test
        # destructivo) y una 1.1.0 del MISMO nombre que community_2 para el update.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level,"
            "  requested_permissions, signature)"
            " VALUES"
            " ($1, $2, NULL, 'skill', 'sa-skill', '1.0.0', 'community', '[]'::jsonb, NULL),"
            " ($3, $2, NULL, 'skill', 'sa-skill-pipeline', '1.0.0', 'community',"
            "  '[]'::jsonb, NULL),"
            " ($4, $2, NULL, 'skill', 'sa-skill-pipeline', '1.1.0', 'community',"
            "  '[]'::jsonb, NULL)",
            ids["community"],
            ids["source"],
            ids["community_2"],
            ids["community_v2"],
        )
    finally:
        await conn.close()
    return ids


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


class _SpyAnalyzer:
    """Analyzer determinista: devuelve findings fijos y registra cada llamada
    (source_dir + trust_level) — la prueba de que install y update pasan por
    el MISMO pipeline es que AMBOS acaban aquí."""

    def __init__(self, findings: list[Any] | None = None) -> None:
        self.findings = findings or []
        self.calls: list[tuple[str, str]] = []

    def analyze(self, source_dir: str, trust_level: str) -> Any:
        from api_server.marketplace.static_analysis import StaticAnalysisReport
        from api_server.marketplace.trust import trust_policy

        self.calls.append((source_dir, str(trust_level)))
        return StaticAnalysisReport(
            findings=tuple(self.findings),
            policy=trust_policy(trust_level),
            ran=("bandit", "semgrep"),
        )


def _high_finding() -> Any:
    from api_server.marketplace.static_analysis import Finding, Severity

    return Finding(
        severity=Severity.HIGH,
        rule="B602",
        file="src/run.py",
        line=3,
        msg="subprocess with shell=True",
        scanner="bandit",
    )


class _FakeSandbox:
    """Sandbox que siempre pasa — el gate de sandbox del UPDATE (community)
    no es el objeto de estos tests y el arnés no tiene daemon utilizable."""

    def run(self, spec: Any) -> Any:
        from api_server.marketplace.sandbox import SandboxResult

        return SandboxResult(
            smoke_command="true", exit_code=0, stdout="", stderr="", timed_out=False
        )


def _wire_orchestrator(app, root: Path, analyzer: Any) -> None:
    from api_server.marketplace.install import InstallOrchestrator, LocalArtifactFetcher
    from api_server.routers.marketplace import get_install_orchestrator

    def _factory() -> InstallOrchestrator:
        return InstallOrchestrator(
            fetcher=LocalArtifactFetcher(root_dir=str(root)),
            analyzer=analyzer,
            sandbox=_FakeSandbox(),
        )

    app.dependency_overrides[get_install_orchestrator] = _factory


def _write_artifact(root: Path, listing_id: UUID, body: str = _SKILL_MD) -> None:
    listing_dir = root / str(listing_id)
    listing_dir.mkdir(parents=True, exist_ok=True)
    (listing_dir / "SKILL.md").write_text(body, encoding="utf-8")


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _fetch_audit_rows(dsn: str, tenant_id: UUID) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT action, detail FROM marketplace_audit_entries"
            " WHERE tenant_id=$1 ORDER BY created_at",
            tenant_id,
        )
        return [{"action": r["action"], "detail": json.loads(r["detail"])} for r in rows]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_install_blocks_on_findings_above_policy(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _write_artifact(tmp_path, seeded["community"])
    _wire_orchestrator(configured_app, tmp_path, _SpyAnalyzer(findings=[_high_finding()]))
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["community"])},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert "static analysis" in resp.json()["detail"].lower()

        # Ninguna installation sobrevive; el aborto queda auditado.
        installs = await client.get("/marketplace/installations", headers=headers)
        assert installs.json() == []

    audits = await _fetch_audit_rows(migrations_pg_dsn, seeded["tenant"])
    assert len(audits) == 1
    assert audits[0]["action"] == "install"
    assert audits[0]["detail"]["aborted"] is True
    assert audits[0]["detail"]["reason"] == "static_analysis_blocked"


@pytest.mark.asyncio
async def test_clean_install_records_report_and_keeps_consent_gate(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _write_artifact(tmp_path, seeded["community"])
    _wire_orchestrator(configured_app, tmp_path, _SpyAnalyzer())
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["community"])},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        # El gate de consentimiento sigue intacto: community nace DISABLED.
        assert resp.json()["status"] == "disabled"

    audits = await _fetch_audit_rows(migrations_pg_dsn, seeded["tenant"])
    assert len(audits) == 1
    gates = audits[0]["detail"]["gates"]
    assert gates["static_analysis"]["blocked"] is False
    assert gates["static_analysis"]["max_severity"] == "NONE"
    assert "bandit" in gates["static_analysis"]["ran"]


@pytest.mark.asyncio
async def test_install_without_artifact_records_honest_skip(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # SIN _write_artifact: el listing no tiene artefacto en disco.
    _wire_orchestrator(configured_app, tmp_path, _SpyAnalyzer())
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["community"])},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    audits = await _fetch_audit_rows(migrations_pg_dsn, seeded["tenant"])
    gates = audits[0]["detail"]["gates"]
    assert gates["static_analysis"]["skipped_reason"] == "no_artifact"


@pytest.mark.asyncio
async def test_install_and_update_share_the_analysis_pipeline(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _write_artifact(tmp_path, seeded["community_2"])
    _write_artifact(tmp_path, seeded["community_v2"])
    spy = _SpyAnalyzer()
    _wire_orchestrator(configured_app, tmp_path, spy)
    token = await _mint_token(seeded["admin"], seeded["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        installed = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(seeded["community_2"])},
            headers=headers,
        )
        assert installed.status_code == 201, installed.text
        assert len(spy.calls) == 1

        updated = await client.post(
            f"/marketplace/installations/{installed.json()['id']}/update",
            json={},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text

    # El MISMO analyzer vio ambas pasadas — un solo pipeline de análisis.
    assert len(spy.calls) == 2
    assert all(level == "community" for _dir, level in spy.calls)
