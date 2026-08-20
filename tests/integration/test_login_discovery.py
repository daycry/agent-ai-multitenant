"""Login discovery: `GET /auth/discover?email=…` (Plan 08 `task_08_12`).

Lo que la página de login pregunta ANTES de saber si a este usuario le toca SSO
o formulario. La casilla `task_08_12` declaraba este fichero desde el 2026-05-30
y **no existía**: lo único que había era `tests/unit/test_sso_router_package.py`,
que comprueba que la RUTA existe y cuelga de `/auth` y no de `/auth/sso`. Que la
ruta exista no dice nada de lo que responde, y lo que responde es la decisión de
seguridad de este endpoint: es **público y sin autenticar**.

Tres propiedades, y las tres son de seguridad antes que de funcionalidad:

1. **Enruta**: si el dominio del email lo reclama una config SSO *habilitada*,
   la respuesta trae el `provider` y el `login_url` per-provider-id. Si no, el
   `password` genérico.
2. **No enumera usuarios**: la respuesta se deriva SOLO del dominio configurado
   —`users` no se consulta nunca—, así que es **byte a byte idéntica** exista o
   no una cuenta con ese email. Se comprueba comparando las dos respuestas, no
   leyendo el código.
3. **Falla al lado seguro**: config deshabilitada, soft-borrada o email
   malformado → `password`. Nunca un error que se pueda sondear, y nunca un
   `login_url` a medio formar.

**`tenant_id` va SIEMPRE a `null`, y eso es la mitad interesante.** El título de
la casilla dice «email → tenant», que era el diseño del Plan 08 (SSO per-tenant,
`/auth/sso/{tenant_id}/…`). El **ADR 0047** lo sustituyó
(`docs/05-architecture-decisions/0047-sso-auth-global-platform-membership-access.md`):
los providers son platform-global y **el acceso a un tenant lo dan las
memberships DESPUÉS del login**, así que el descubrimiento ya no puede —ni debe—
decir a qué tenant pertenece un email. El campo sigue en el modelo de respuesta
por compatibilidad del contrato público; que llegue siempre vacío se pinea aquí
para que nadie vuelva a poblarlo sin releer el ADR.

Precondición: PostgreSQL + Redis del docker-compose sanos (la fixture
`configured_app` crea una BD desechable y limpia Redis 15).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_ISSUER = "https://idp.discovery.test"
_SAML_ENTITY = "https://idp.discovery.test/saml"
_SAML_SSO = "https://idp.discovery.test/saml/sso"
_SAML_CERT = "MIIC-fake-cert-for-discovery"


# ---------------------------------------------------------------------------
# Seeding — `sso_configurations` es PLATFORM-GLOBAL (sin tenant_id desde la
# migración 0076), así que cada test se limpia lo suyo: lo que deje escrito lo
# leería el fichero siguiente del mismo shard.
# ---------------------------------------------------------------------------
async def _truncate_sso(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE sso_configurations RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def _seed_oidc(
    dsn: str,
    *,
    domains: list[str],
    enabled: bool = True,
    deleted: bool = False,
    created_at: datetime | None = None,
) -> UUID:
    from api_server.auth.sso.secrets import encrypt_client_secret

    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, issuer, client_id,
                 client_secret_encrypted, scopes, claim_mappings, email_domains,
                 created_at, deleted_at)
            VALUES ($1, 'oidc', 'Discovery OIDC', $2, $3, 'client-abc', $4,
                    $5::jsonb, '{}'::jsonb, $6::jsonb, coalesce($7, now()), $8)
            """,
            config_id,
            enabled,
            _ISSUER,
            encrypt_client_secret("shhh"),
            json.dumps(["openid", "email"]),
            json.dumps(domains),
            created_at,
            datetime.now(UTC) if deleted else None,
        )
    finally:
        await conn.close()
    return config_id


async def _seed_saml(dsn: str, *, domains: list[str], enabled: bool = True) -> UUID:
    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, idp_entity_id, idp_sso_url,
                 idp_x509_cert, attribute_mappings, email_domains)
            VALUES ($1, 'saml', 'Discovery SAML', $2, $3, $4, $5, '{}'::jsonb, $6::jsonb)
            """,
            config_id,
            enabled,
            _SAML_ENTITY,
            _SAML_SSO,
            _SAML_CERT,
            json.dumps(domains),
        )
    finally:
        await conn.close()
    return config_id


async def _seed_user(dsn: str, *, email: str) -> None:
    """Un usuario LOCAL con ese email, para el test de no-enumeración."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, full_name, password_hash, is_active)"
            " VALUES ($1, $2, 'Someone', 'x', true)"
            " ON CONFLICT (email) DO NOTHING",
            uuid4(),
            email,
        )
    finally:
        await conn.close()


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _discover(app: object, email: str) -> tuple[int, dict]:
    async with _client(app) as client:
        # PÚBLICO: ni una cabecera Authorization.
        resp = await client.get("/auth/discover", params={"email": email})
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# 1. Enruta al provider que reclama el dominio
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_claimed_domain_routes_to_its_oidc_provider(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_sso(migrations_pg_dsn)
    config_id = await _seed_oidc(migrations_pg_dsn, domains=["acme.com", "acme.io"])

    status, body = await _discover(configured_app, "jane@acme.io")

    assert status == 200
    assert body["method"] == "sso"
    assert body["provider"] == "oidc"
    # Per-provider-id (ADR 0047), NO per-tenant.
    assert body["login_url"] == f"/auth/sso/{config_id}/oidc/login"


@pytest.mark.asyncio
async def test_a_saml_config_routes_to_the_saml_login(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_sso(migrations_pg_dsn)
    config_id = await _seed_saml(migrations_pg_dsn, domains=["saml-corp.test"])

    status, body = await _discover(configured_app, "bob@saml-corp.test")

    assert status == 200
    assert body["method"] == "sso"
    assert body["provider"] == "saml"
    assert body["login_url"] == f"/auth/sso/{config_id}/saml/login"


@pytest.mark.asyncio
async def test_the_match_is_case_insensitive(configured_app, migrations_pg_dsn: str) -> None:
    """Los dominios se guardan en minúsculas; el email lo escribe una persona."""
    await _truncate_sso(migrations_pg_dsn)
    config_id = await _seed_oidc(migrations_pg_dsn, domains=["acme.com"])

    status, body = await _discover(configured_app, "  Jane.Doe@ACME.CoM  ")

    assert status == 200
    assert body["method"] == "sso"
    assert body["login_url"] == f"/auth/sso/{config_id}/oidc/login"


# ---------------------------------------------------------------------------
# 2. El tenant ya NO viaja en la respuesta (ADR 0047)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_answer_never_carries_a_tenant_anymore(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El título de `task_08_12` dice «email → tenant»; el ADR 0047 retiró esa
    mitad. El campo sigue en el contrato público, y llega SIEMPRE vacío: el
    acceso a un tenant lo deciden las memberships, después del login.

    Se afirma sobre las dos ramas (sso y password) porque el campo es del
    modelo de respuesta, no de una de las dos.
    """
    await _truncate_sso(migrations_pg_dsn)
    await _seed_oidc(migrations_pg_dsn, domains=["acme.com"])

    _, sso = await _discover(configured_app, "jane@acme.com")
    _, local = await _discover(configured_app, "jane@nowhere.example")

    assert sso["method"] == "sso"
    assert sso["tenant_id"] is None, (
        "el descubrimiento volvió a publicar un tenant: eso es el diseño"
        " per-tenant que el ADR 0047 sustituyó por acceso por membership"
    )
    assert local["tenant_id"] is None


# ---------------------------------------------------------------------------
# 3. Falla al lado seguro: password genérico, nunca un error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unclaimed_domain_falls_back_to_local_login(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_sso(migrations_pg_dsn)
    await _seed_oidc(migrations_pg_dsn, domains=["acme.com"])

    status, body = await _discover(configured_app, "someone@other-company.test")

    assert status == 200
    assert body == {"method": "password", "provider": None, "tenant_id": None, "login_url": None}


@pytest.mark.asyncio
async def test_a_disabled_config_does_not_claim_its_domain(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El interruptor del operador tiene que morder aquí también: si no, apagar
    un provider dejaría a sus usuarios enrutados a un login que ya no arranca."""
    await _truncate_sso(migrations_pg_dsn)
    await _seed_oidc(migrations_pg_dsn, domains=["acme.com"], enabled=False)

    status, body = await _discover(configured_app, "jane@acme.com")

    assert status == 200
    assert body["method"] == "password"
    assert body["login_url"] is None


@pytest.mark.asyncio
async def test_a_soft_deleted_config_does_not_claim_its_domain(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_sso(migrations_pg_dsn)
    await _seed_oidc(migrations_pg_dsn, domains=["acme.com"], deleted=True)

    status, body = await _discover(configured_app, "jane@acme.com")

    assert status == 200
    assert body["method"] == "password"


@pytest.mark.parametrize(
    "bad",
    ["no-at-sign", "two@at@signs.test", "@nodomain", "trailing@", " "],
)
@pytest.mark.asyncio
async def test_a_malformed_email_gets_the_generic_answer_not_an_error(
    configured_app, migrations_pg_dsn: str, bad: str
) -> None:
    """Un 422/500 ante un email raro sería un oráculo: distinguiría formas de
    entrada, y de ahí a distinguir dominios configurados hay un paso."""
    await _truncate_sso(migrations_pg_dsn)
    await _seed_oidc(migrations_pg_dsn, domains=["acme.com"])

    status, body = await _discover(configured_app, bad)

    assert status == 200, f"{bad!r} no debe producir error: {body}"
    assert body["method"] == "password"


# ---------------------------------------------------------------------------
# 4. No enumera usuarios
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_answer_is_identical_whether_the_account_exists(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La propiedad que hace este endpoint publicable: la respuesta depende del
    DOMINIO configurado, no de `users`. Se comprueba comparando los dos cuerpos,
    que es lo que vería un atacante — no leyendo el SQL."""
    await _truncate_sso(migrations_pg_dsn)
    await _seed_oidc(migrations_pg_dsn, domains=["acme.com"])

    # (a) dominio con SSO: la cuenta existe / no existe.
    await _seed_user(migrations_pg_dsn, email="exists@acme.com")
    _, sso_existing = await _discover(configured_app, "exists@acme.com")
    _, sso_absent = await _discover(configured_app, "ghost@acme.com")
    assert sso_existing == sso_absent

    # (b) dominio sin SSO: mismo par, misma igualdad.
    await _seed_user(migrations_pg_dsn, email="exists@plain.test")
    _, local_existing = await _discover(configured_app, "exists@plain.test")
    _, local_absent = await _discover(configured_app, "ghost@plain.test")
    assert local_existing == local_absent
    assert local_existing["method"] == "password"


# ---------------------------------------------------------------------------
# 5. Colisión de dominio: determinista, y sin revelar que hubo colisión
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_two_configs_claiming_the_same_domain_resolve_to_the_oldest(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Los dominios son ATESTIGUADOS por el operador, no verificados, así que dos
    configs pueden reclamar el mismo (la 0115 retiró el único-por-kind). La
    respuesta tiene que ser estable —siempre la más antigua— y no delatar que
    hubo más de una candidata.
    """
    await _truncate_sso(migrations_pg_dsn)
    now = datetime.now(UTC)
    older = await _seed_oidc(
        migrations_pg_dsn, domains=["shared.test"], created_at=now - timedelta(days=2)
    )
    newer = await _seed_oidc(
        migrations_pg_dsn, domains=["shared.test"], created_at=now - timedelta(days=1)
    )

    first = (await _discover(configured_app, "a@shared.test"))[1]
    second = (await _discover(configured_app, "b@shared.test"))[1]

    assert first == second, "misma pregunta, dos respuestas: el orden no es determinista"
    assert first["login_url"] == f"/auth/sso/{older}/oidc/login"
    assert str(newer) not in json.dumps(first)
