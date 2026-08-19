"""Córtex F3.3 — ``POST /owner/cortex/identity/onboarding`` (onboarding co-diseñado).

El hueco que cierra esto: ``propose_identity`` existía y estaba probada desde F3,
pero **no la llamaba nadie**. No había endpoint que generase el turno en el que el
córtex se autonombra, así que el «co-diseñado» del plan se resolvía con el owner
rellenando un formulario (``PUT /identity``) — el patrón nº5 de
``verificar-antes-de-implementar.md``: mecanismo entregado, cero llamantes.

Lo que se ejercita end-to-end sobre el app real (BD + Redis):

  * **paso 1, propuesta**: el primer POST corre UN turno del córtex con el grafo de
    F1 (modelo scripted) y devuelve nombre/valores propuestos + el ``diff`` contra
    el estado vigente, **sin persistir nada** (``onboarded_at`` sigue NULL);
  * **paso 2, confirmación**: un POST con ``confirm`` persiste el ``identity_state``
    con ``updated_by='onboarding'``, marca ``onboarded_at`` y versiona en
    ``cortex_identity_history``;
  * **idempotencia**: un tercer POST NO re-onboarda, NO reescribe la identidad y
    **no gasta un turno de LLM** (lo que costaría dinero cada vez que la UI se
    recargase);
  * **guardrail ADR 0074**: ni el modelo ni el owner mueven ``traits`` /
    ``mood_baseline`` por esta puerta;
  * **gate + aislamiento**: non-owner → 403 (DB-authoritative) y la identidad de
    otro usuario nunca se cruza (las tablas del córtex son tenant-less: el
    aislamiento es el filtro ``owner_user_id`` explícito, ADR 0074/0156).

Fixtures espejo de ``test_cortex_f3_identity_endpoints.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


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


async def _seed_two_owners(dsn: str, *, owner_is_owner: bool = True) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_identity_history, cortex_identity, cortex_turns,"
            " cortex_conversations, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Onboarding Tenant",
            "cortex-onboarding-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, false)",
            owner_id,
            "owner@onboarding.test",
            "h",
            owner_is_owner,
            other_id,
            "other@onboarding.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID, *, owner_claim: bool = True) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(
        user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner_claim
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _CountingModel:
    """Modelo scripted que además CUENTA cuántos turnos se le pidieron.

    El contador es la prueba de la idempotencia que importa: no basta con que un
    segundo POST no reescriba la fila; tiene que no gastar una llamada al LLM."""

    def __init__(self, content: str) -> None:
        from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel

        self._inner = ScriptedAssistantModel(turns=[ModelTurn(content=content)])
        self.calls = 0

    async def decide(self, state: Any):
        self.calls += 1
        return await self._inner.decide(state)


_PROPOSAL = (
    'Lo he pensado: {"name": "Atlas", "core_values": ["honestidad", "curiosidad"], '
    '"narrative": "Soy Atlas, el córtex de mi owner.", '
    '"learning_goals": ["entender su forma de trabajar"], "language": "es"}'
)


def _install_model(app, content: str = _PROPOSAL) -> _CountingModel:
    from api_server.routers.cortex import get_cortex_model

    model = _CountingModel(content)
    app.dependency_overrides[get_cortex_model] = lambda: model
    return model


async def _identity_row(dsn: str, owner_id: UUID) -> dict[str, Any] | None:
    """La fila persistida del owner, con el JSONB ya decodificado.

    ``asyncpg`` sin codec de ``jsonb`` devuelve el blob como ``str``; indexarlo por
    clave da un ``TypeError`` que se lee como un fallo del endpoint y no lo es."""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT identity_state, version, updated_by, onboarded_at"
            " FROM cortex_identity WHERE owner_user_id = $1",
            owner_id,
        )
    finally:
        await conn.close()
    if row is None:
        return None
    return {**dict(row), "identity_state": json.loads(row["identity_state"])}


# ===========================================================================
# Paso 1 — el córtex se propone una identidad (nada se persiste)
# ===========================================================================
@pytest.mark.asyncio
async def test_first_post_proposes_name_and_values_without_persisting(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    model = _install_model(configured_app)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/owner/cortex/identity/onboarding", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert model.calls == 1, "el turno de propuesta lo genera el grafo de F1"
    assert body["already_onboarded"] is False
    assert body["applied"] is False
    assert body["identity"]["name"] == "Atlas"
    assert body["identity"]["core_values"] == ["honestidad", "curiosidad"]
    assert body["identity"]["onboarded_at"] is None
    # El diff es lo que la UI enseña al owner antes de que confirme.
    assert body["diff"]["name"]["after"] == "Atlas"
    # El texto literal del turno viaja para poder enseñarlo tal cual.
    assert "Atlas" in body["proposal"]
    # Copy honesto, en los DOS idiomas (principio rector 12).
    assert body["honesty"]["note_es"] and body["honesty"]["note_en"]
    assert body["honesty"]["note_es"] != body["honesty"]["note_en"]

    # NADA se ha persistido: la propuesta es una propuesta.
    row = await _identity_row(migrations_pg_dsn, seed["owner_id"])
    assert row is not None
    assert row["onboarded_at"] is None
    assert row["version"] == 0
    assert row["identity_state"]["name"] != "Atlas"


@pytest.mark.asyncio
async def test_the_proposal_never_moves_the_derived_fields(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Guardrail ADR 0074: el córtex no elige sus rasgos ni su set-point de mood."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    _install_model(
        configured_app,
        '{"name": "Atlas", "traits": {"openness": 1.0, "conscientiousness": 0.0,'
        ' "extraversion": 1.0, "agreeableness": 0.0, "neuroticism": 1.0},'
        ' "mood_baseline": {"valence": 1.0, "arousal": 1.0, "dominance": 1.0}}',
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/owner/cortex/identity/onboarding", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["identity"]["traits"] == {
        "openness": 0.5,
        "conscientiousness": 0.5,
        "extraversion": 0.5,
        "agreeableness": 0.5,
        "neuroticism": 0.5,
    }
    assert "traits" not in body["diff"]
    assert "mood_baseline" not in body["diff"]


# ===========================================================================
# Paso 2 — el owner confirma: se persiste, se versiona y se marca onboarded
# ===========================================================================
@pytest.mark.asyncio
async def test_confirm_persists_versions_and_marks_onboarded(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    _install_model(configured_app)
    token = await _mint(owner_id, seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        await client.post("/owner/cortex/identity/onboarding", headers=headers)
        resp = await client.post(
            "/owner/cortex/identity/onboarding",
            json={
                "confirm": True,
                "name": "Atlas",
                "core_values": ["honestidad", "curiosidad"],
                "narrative": "Soy Atlas, el córtex de mi owner.",
                "learning_goals": ["entender su forma de trabajar"],
                "language": "es",
            },
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is True
    assert body["already_onboarded"] is False
    assert body["identity"]["name"] == "Atlas"
    assert body["identity"]["onboarded_at"] is not None
    assert body["identity"]["version"] == 1
    assert body["identity"]["updated_by"] == "onboarding"

    row = await _identity_row(migrations_pg_dsn, owner_id)
    assert row is not None
    assert row["identity_state"]["name"] == "Atlas"
    assert row["identity_state"]["core_values"] == ["honestidad", "curiosidad"]
    assert row["onboarded_at"] is not None
    assert row["updated_by"] == "onboarding"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        history = await conn.fetch(
            "SELECT version, updated_by, diff FROM cortex_identity_history"
            " WHERE owner_user_id = $1 ORDER BY version ASC",
            owner_id,
        )
    finally:
        await conn.close()
    assert [r["version"] for r in history] == [1]
    assert history[0]["updated_by"] == "onboarding"
    assert "name" in json.loads(history[0]["diff"])


@pytest.mark.asyncio
async def test_confirm_cannot_smuggle_the_derived_fields(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tampoco por la puerta del OWNER: ``extra=forbid`` rechaza traits/mood_baseline."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    _install_model(configured_app)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/owner/cortex/identity/onboarding",
            json={"confirm": True, "name": "Atlas", "traits": {"openness": 1.0}},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        resp2 = await client.post(
            "/owner/cortex/identity/onboarding",
            json={"confirm": True, "mood_baseline": {"valence": 1.0}},
            headers=headers,
        )
        assert resp2.status_code == 422, resp2.text


# ===========================================================================
# Idempotencia — un segundo onboarding no re-onboarda NI gasta un turno
# ===========================================================================
@pytest.mark.asyncio
async def test_a_second_onboarding_is_idempotent_and_costs_nothing(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    model = _install_model(configured_app)
    token = await _mint(owner_id, seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        await client.post(
            "/owner/cortex/identity/onboarding",
            json={"confirm": True, "name": "Atlas", "core_values": ["honestidad"]},
            headers=headers,
        )
        calls_after_onboarding = model.calls
        # Segundo POST de PROPUESTA: ni turno ni reescritura.
        resp = await client.post("/owner/cortex/identity/onboarding", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["already_onboarded"] is True
        assert body["applied"] is False
        assert body["identity"]["name"] == "Atlas"
        assert model.calls == calls_after_onboarding, "un re-onboarding no gasta LLM"

        # Y un segundo CONFIRM con otro nombre tampoco reescribe la identidad:
        # el camino de edición posterior es ``PUT /identity`` (owner_override).
        resp2 = await client.post(
            "/owner/cortex/identity/onboarding",
            json={"confirm": True, "name": "Otro"},
            headers=headers,
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["already_onboarded"] is True
        assert resp2.json()["applied"] is False

    row = await _identity_row(migrations_pg_dsn, owner_id)
    assert row is not None
    assert row["identity_state"]["name"] == "Atlas"
    assert row["version"] == 1  # sigue habiendo UNA sola reescritura

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        versions = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity_history WHERE owner_user_id = $1",
            owner_id,
        )
    finally:
        await conn.close()
    assert versions == 1


@pytest.mark.asyncio
async def test_apply_onboarding_is_idempotent_on_its_own(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La idempotencia vive DENTRO de ``apply_onboarding``, no sólo en el endpoint.

    El endpoint corta antes por lo barato (no gastar un turno de LLM), así que su
    guarda tapa la de la función y un fallo de ésta no se vería desde HTTP. Se
    ejercita la función directamente: es la invariante que la casilla pide
    («onboarding crea la identidad una sola vez»), y dejarla sólo en el llamante la
    haría depender de que todos los llamantes futuros se acuerden."""
    from api_server.cortex.onboarding import apply_onboarding
    from api_server.db.session import get_admin_sessionmaker

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    sessionmaker = get_admin_sessionmaker()

    async with sessionmaker() as session, session.begin():
        identity, applied = await apply_onboarding(session, owner_id, {"name": "Atlas"})
        assert applied is True
        assert identity.version == 1
        assert identity.onboarded_at is not None

    async with sessionmaker() as session, session.begin():
        identity, applied = await apply_onboarding(session, owner_id, {"name": "Otro"})
        assert applied is False, "un segundo onboarding NO re-onboarda"
        assert identity.identity_state["name"] == "Atlas"
        assert identity.version == 1

    row = await _identity_row(migrations_pg_dsn, owner_id)
    assert row is not None
    assert row["identity_state"]["name"] == "Atlas"
    assert row["version"] == 1


# ===========================================================================
# Gate DB-authoritative + aislamiento cross-owner
# ===========================================================================
@pytest.mark.asyncio
async def test_onboarding_non_owner_gets_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn, owner_is_owner=False)
    model = _install_model(configured_app)
    # Forja el claim `own`; el gate re-lee la BD y debe rechazar igualmente.
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner_claim=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/owner/cortex/identity/onboarding", headers=headers)
        assert resp.status_code == 403, resp.text
        resp2 = await client.post(
            "/owner/cortex/identity/onboarding",
            json={"confirm": True, "name": "Hack"},
            headers=headers,
        )
        assert resp2.status_code == 403, resp2.text

    assert model.calls == 0, "un rechazado no debe llegar a gastar un turno"


@pytest.mark.asyncio
async def test_onboarding_never_touches_another_owners_identity(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Las tablas del córtex son tenant-less: el aislamiento es el filtro explícito."""
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_identity"
            " (id, owner_user_id, identity_state, version, updated_by, onboarded_at)"
            " VALUES ($1, $2, $3::jsonb, 5, 'reflection', now())",
            uuid4(),
            other_id,
            '{"name": "Eco", "core_values": ["secreto"], "narrative": "no tuya"}',
        )
    finally:
        await conn.close()

    _install_model(configured_app)
    token = await _mint(owner_id, seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/owner/cortex/identity/onboarding", headers=headers)
        assert resp.status_code == 200, resp.text
        # El owner NO hereda el onboarding de Eco: el suyo sigue pendiente.
        assert resp.json()["already_onboarded"] is False
        assert resp.json()["identity"]["name"] != "Eco"
        await client.post(
            "/owner/cortex/identity/onboarding",
            json={"confirm": True, "name": "Atlas"},
            headers=headers,
        )

    other = await _identity_row(migrations_pg_dsn, other_id)
    assert other is not None
    assert other["identity_state"]["name"] == "Eco"
    assert other["version"] == 5

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        foreign_history = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity_history WHERE owner_user_id = $1",
            other_id,
        )
    finally:
        await conn.close()
    assert foreign_history == 0
