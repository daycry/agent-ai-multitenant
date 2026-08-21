"""prod-12 `human_prod12_02` — «cada uso de 'open' aparece en el audit log».

`network_policy` es uno de los permisos del catálogo de consentimiento del
marketplace, y `open` es su valor peligroso (egress). El checklist humano exige
que **cada uso** de `open` quede en el audit log. Estaba implementado por las
dos vías por las que un `open` puede entrar en un tenant, y ninguna tenía
assert:

  * un listing **verified** se instala ENABLED honrando los
    `granted_permissions` de la petición verbatim → la fila `install` debe
    nombrar el permiso concedido **con su valor**;
  * un listing **community** se instala DISABLED sin nada concedido, y el
    `open` solo puede entrar por `POST .../consent` → la fila `consent` debe
    nombrar la decisión y el valor.

En ambos casos se comprueba también el ACTOR (`user:<uuid>`), porque un audit
que dice «se concedió open» sin decir quién no sirve para lo que existe.

## Lo que este fichero NO puede acreditar (hueco real, no omisión)

`SandboxResult.proxied_egress` — la prueba de que el egress de `open` salió
proxificado por el registry-proxy y no por NAT crudo — **no llega a ninguna
fila de audit**:

  * `InstallOrchestrator._gate_sandbox` solo copia `exit_code` / `timed_out` /
    `passed` a `gate_report`, no `network_policy` ni `proxied_egress`;
  * y el sondeo del install fija `network_policy=NetworkPolicy.NONE` a
    propósito («the first run is the most locked-down»), así que un `open` no
    aparecería ahí ni añadiendo el campo;
  * además, en la ruta REAL del router el gate de sandbox está DIFERIDO
    (ADR 0081 fases B/C: el api-server no tiene socket Docker), de modo que
    hoy ningún install ejecuta sandbox alguno.

`test_install_sandbox.py` afirma `SandboxResult.proxied_egress` a nivel de
objeto, que es todo lo que hay. Correlacionar el `open` consentido con el
egress proxificado en la misma fila de audit exige código nuevo, no un test.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_OPEN_PERMISSION = {"type": "network_policy", "value": "open"}


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "admin": uuid4(),
        "source": uuid4(),
        "verified_open": uuid4(),
        "community_open": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources,"
            " tools, skills, projects, agents, teams, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-npaudit')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'admin@npaudit.test', 'h')",
            ids["admin"],
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
        # Los dos listings piden EL MISMO permiso peligroso (network_policy=open)
        # y se diferencian solo en el trust level, que es lo que decide por qué
        # vía entra el `open`.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level,"
            "  requested_permissions, signature, manifest)"
            " VALUES"
            " ($1, $2, NULL, 'tool', 'open-egress-tool', '1.0.0', 'verified',"
            '  \'[{"type": "network_policy", "value": "open"}]\'::jsonb, \'sig\','
            '  \'{"implementation_type": "http_endpoint",'
            '     "implementation_ref": "https://api.z.com/tool/{q}"}\'::jsonb),'
            " ($3, $2, NULL, 'tool', 'community-open-tool', '1.0.0', 'community',"
            '  \'[{"type": "network_policy", "value": "open"}]\'::jsonb, NULL,'
            '  \'{"implementation_type": "http_endpoint",'
            '     "implementation_ref": "https://api.w.com/tool/{q}"}\'::jsonb)',
            ids["verified_open"],
            ids["source"],
            ids["community_open"],
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
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _audit_rows(dsn: str, tenant_id: UUID, action: str) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT actor, detail FROM marketplace_audit_entries"
            " WHERE tenant_id = $1 AND action = $2 ORDER BY created_at",
            tenant_id,
            action,
        )
    finally:
        await conn.close()
    out = []
    for row in rows:
        detail = row["detail"]
        out.append(
            {
                "actor": row["actor"],
                "detail": json.loads(detail) if isinstance(detail, str) else detail,
            }
        )
    return out


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# Vía 1 — listing VERIFIED: el `open` entra en el install y queda en su fila
# ===========================================================================
@pytest.mark.asyncio
async def test_install_granting_open_records_it_in_the_audit_row(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/marketplace/installations",
            json={
                "listing_id": str(ids["verified_open"]),
                "granted_permissions": [_OPEN_PERMISSION],
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Un verified no pide consentimiento por permiso: nace ENABLED con el
        # `open` ya concedido.
        assert body["status"] == "enabled"
        assert body["granted_permissions"] == [_OPEN_PERMISSION]

    rows = await _audit_rows(migrations_pg_dsn, ids["tenant"], "install")
    assert len(rows) == 1, f"esperaba 1 fila de install, hubo {len(rows)}"
    entry = rows[0]
    # QUIÉN.
    assert entry["actor"] == f"user:{ids['admin']}"
    # QUÉ: el permiso con su VALOR — 'open', no solo el tipo 'network_policy'.
    granted = {p["type"]: p["value"] for p in entry["detail"]["granted_permissions"]}
    assert granted == {"network_policy": "open"}
    assert entry["detail"]["status"] == "enabled"
    assert entry["detail"]["consent_required"] is False


# ===========================================================================
# Vía 2 — listing COMMUNITY: el `open` solo puede entrar por el consent, y
# la fila `install` NO puede afirmar que se concedió nada
# ===========================================================================
@pytest.mark.asyncio
async def test_community_open_needs_consent_and_both_rows_are_honest(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Instalar pidiendo el `open` NO lo concede: un community aterriza
        # DISABLED y sin permisos, aunque el cliente los mande.
        install = await client.post(
            "/marketplace/installations",
            json={
                "listing_id": str(ids["community_open"]),
                "granted_permissions": [_OPEN_PERMISSION],
            },
            headers=headers,
        )
        assert install.status_code == 201, install.text
        assert install.json()["status"] == "disabled"
        assert install.json()["granted_permissions"] == []
        install_id = install.json()["id"]

        # Ahora el humano concede el `open` explícitamente.
        consent = await client.post(
            f"/marketplace/installations/{install_id}/consent",
            json={"decisions": [{"type": "network_policy", "decision": "grant"}]},
            headers=headers,
        )
        assert consent.status_code == 200, consent.text
        assert consent.json()["status"] == "enabled"

    # La fila de install es HONESTA: dice que hacía falta consentimiento y que
    # no concedió nada (si dijese que concedió `open`, el audit mentiría).
    install_rows = await _audit_rows(migrations_pg_dsn, ids["tenant"], "install")
    assert len(install_rows) == 1
    assert install_rows[0]["detail"]["consent_required"] is True
    assert install_rows[0]["detail"]["granted_permissions"] == []
    assert install_rows[0]["detail"]["status"] == "disabled"

    # Y la fila de consent es la que registra el uso de `open`: quién y qué.
    consent_rows = await _audit_rows(migrations_pg_dsn, ids["tenant"], "consent")
    assert len(consent_rows) == 1
    entry = consent_rows[0]
    assert entry["actor"] == f"user:{ids['admin']}"
    assert entry["detail"]["decisions"] == {"network_policy": "grant"}
    granted = {p["type"]: p["value"] for p in entry["detail"]["granted_permissions"]}
    assert granted == {"network_policy": "open"}
    assert entry["detail"]["enabled"] is True


# ===========================================================================
# Rechazar el `open` también queda registrado (y la instalación no se habilita)
# ===========================================================================
@pytest.mark.asyncio
async def test_denying_open_is_audited_and_keeps_the_install_disabled(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint(ids["admin"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(ids["community_open"])},
            headers=headers,
        )
        install_id = install.json()["id"]
        consent = await client.post(
            f"/marketplace/installations/{install_id}/consent",
            json={"decisions": [{"type": "network_policy", "decision": "deny"}]},
            headers=headers,
        )
        assert consent.status_code == 200, consent.text
        assert consent.json()["status"] == "disabled"

    denied_rows = await _audit_rows(migrations_pg_dsn, ids["tenant"], "consent_denied")
    assert len(denied_rows) == 1
    entry = denied_rows[0]
    assert entry["actor"] == f"user:{ids['admin']}"
    assert entry["detail"]["decisions"] == {"network_policy": "deny"}
    denied = {p["type"]: p["value"] for p in entry["detail"]["denied_permissions"]}
    assert denied == {"network_policy": "open"}
    assert entry["detail"]["enabled"] is False
