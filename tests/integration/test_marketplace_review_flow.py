"""Publicar pasa por revisión — `task_mkt2_09` / `task_mkt2_10` (ADR 0142 D6).

Va **por HTTP**, no llamando a `marketplace/review.py`: el modo de fallo nº1 de
esta base es «mecanismo entregado, cero llamantes»
(`docs/03-guides/verificar-antes-de-implementar.md` §5). Llamar a
`approve_listing` a mano no descubriría que el endpoint no está montado, ni que
el catálogo sigue enseñando lo que está en la cola.

Los nodos que el plan declara irrenunciables, y están los tres:

1. **Un `tenant_user` NO revisa** — ni un `tenant_admin`: la cola es
   `system_admin` y nada menos.
2. **Un `pending_review` NO aparece en el catálogo de otro** — que es la mitad
   de D6 que no vive en la máquina de estados sino en el `WHERE` del browse.
3. **Un rechazo sin motivo es un 422** — un rechazo mudo es indistinguible de
   un borrado y no se puede recurrir.

Más lo que el propio flujo exige para ser útil: publicar deja `pending_review`
(y lo dice), el autor SÍ ve lo suyo en cualquier estado, aprobar lo mete en el
catálogo de todos, y el histórico de versiones nace con la publicación.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
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


async def _mint(user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


async def _sysadmin_headers(sysadmin_id: UUID) -> dict[str, str]:
    """La cabecera del System Admin, que la cola de revisión pide en cada paso.

    Estaba copiada en cinco sitios idénticos; aquí una vez. Sin `tenant_id` a
    propósito: la cola corre sobre la sesión BYPASSRLS porque un
    `pending_review` es invisible para todo el que no sea su autor.
    """
    token = await _mint(sysadmin_id, None, is_system_admin=True)
    return {"Authorization": f"Bearer {token}"}


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_TOOL_YAML = """
name: acme-checker
version: 1.0.0
description: Comprueba el estado de un servicio.
kind: tool
entrypoint: acme_checker.main:run
implementation:
  runtime: python
  module: acme_checker.main
  reference: git+https://status.acme.test/tools/checker@v1.0.0
permissions:
  allowed_domains: [status.acme.test]
  network_policy: restricted
""".strip()


async def _seed(dsn: str) -> dict[str, UUID]:
    """Dos tenants (autor y ajeno), un system admin, y un listing global publicado."""
    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant_a",
            "tenant_b",
            "author",
            "member_a",
            "outsider",
            "sysadmin",
            "source",
            "global_listing",
        )
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_deployments, marketplace_audit_entries,"
            " marketplace_installations, marketplace_listing_versions,"
            " marketplace_listings, marketplace_sources, audit_log,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        for key, name, slug in (
            ("tenant_a", "Autora", "autora"),
            ("tenant_b", "Ajena", "ajena"),
        ):
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3)",
                ids[key],
                name,
                slug,
            )
        for key, email, is_admin in (
            ("author", "author@test.test", False),
            ("member_a", "member@test.test", False),
            ("outsider", "outsider@test.test", False),
            ("sysadmin", "sys@test.test", True),
        ):
            await conn.execute(
                "INSERT INTO users (id, email, password_hash, is_system_admin)"
                " VALUES ($1,$2,'argon2-placeholder',$3)",
                ids[key],
                email,
                is_admin,
            )
        for user_key, tenant_key, role in (
            ("author", "tenant_a", "tenant_admin"),
            ("member_a", "tenant_a", "tenant_user"),
            ("outsider", "tenant_b", "tenant_admin"),
        ):
            await conn.execute(
                "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
                " VALUES ($1,$2,$3,$4)",
                uuid4(),
                ids[tenant_key],
                ids[user_key],
                role,
            )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-review','official',true)",
            ids["source"],
        )
        # Un listing global YA publicado: el catálogo vivo que el backfill de la
        # 0129 deja intacto. Sin `review_status` explícito a propósito — así
        # este seed comprueba de paso que el `server_default` es 'published'.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " VALUES ($1,$2,NULL,'tool','catalogo-vivo','1.0.0','verified',"
            "         '{}'::jsonb,'[]'::jsonb)",
            ids["global_listing"],
            ids["source"],
        )
    finally:
        await conn.close()
    return ids


async def _publish(client: AsyncClient, token: str, *, manifest: str = _TOOL_YAML) -> dict:
    resp = await client.post(
        "/marketplace/private/listings",
        json={"kind": "tool", "manifest": manifest, "changelog": "Primera versión."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Publicar deja el listing EN LA COLA, y lo dice
# ---------------------------------------------------------------------------
def test_publishing_leaves_the_listing_pending_review(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            token = await _mint(ids["author"], ids["tenant_a"])
            body = await _publish(client, token)

            assert body["review_status"] == "pending_review", (
                "publicar NO publica (D6): si esto sale 'published', el catálogo "
                "vuelve a aceptar cualquier cosa sin que nadie la mire"
            )
            assert body["reviewed_at"] is None
            assert body["rejection_reason"] is None

    asyncio.run(scenario())


def test_publishing_opens_the_version_history(configured_app: Any, migrations_pg_dsn: str) -> None:
    """El histórico nace al publicar: es a donde un rollback puede volver."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            token = await _mint(ids["author"], ids["tenant_a"])
            body = await _publish(client, token)

            sys_token = await _mint(ids["sysadmin"], None, is_system_admin=True)
            resp = await client.get(
                f"/admin/marketplace/listings/{body['id']}/versions",
                headers={"Authorization": f"Bearer {sys_token}"},
            )
            assert resp.status_code == 200, resp.text
            versions = resp.json()
            assert [v["version"] for v in versions] == ["1.0.0"]
            assert versions[0]["changelog"] == "Primera versión."
            assert versions[0]["published_by"] == str(ids["author"])
            # Nadie lo ha revisado todavía, y el histórico no finge lo contrario.
            assert versions[0]["reviewed_by"] is None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# La visibilidad: el nodo que el plan llama irrenunciable
# ---------------------------------------------------------------------------
def test_pending_review_is_invisible_to_another_tenant(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)

            # Comparte el listing privado con el tenant B: aunque la RLS se lo
            # enseñe, el filtro de revisión NO. Es la comprobación que separa
            # «no lo ve porque es privado» de «no lo ve porque no está aprobado».
            share = await client.post(
                "/marketplace/shares",
                json={
                    "listing_id": published["id"],
                    "target_tenant_id": str(ids["tenant_b"]),
                },
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert share.status_code == 201, share.text

            outsider = await _mint(ids["outsider"], ids["tenant_b"])
            listed = await client.get(
                "/marketplace/listings", headers={"Authorization": f"Bearer {outsider}"}
            )
            assert listed.status_code == 200
            names = {row["name"] for row in listed.json()}
            assert "acme-checker" not in names, (
                "un listing compartido pero SIN aprobar no es catálogo: se ve "
                "porque hay un share, y aun así no debe aparecer"
            )
            # El catálogo vivo sí sigue ahí (el backfill no rompió nada).
            assert "catalogo-vivo" in names

            detail = await client.get(
                f"/marketplace/listings/{published['id']}",
                headers={"Authorization": f"Bearer {outsider}"},
            )
            assert detail.status_code == 404, "404, no 403: un 403 confirmaría que existe"

    asyncio.run(scenario())


def test_the_author_sees_their_own_listing_in_any_state(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)

            listed = await client.get(
                "/marketplace/listings", headers={"Authorization": f"Bearer {author_token}"}
            )
            assert "acme-checker" in {row["name"] for row in listed.json()}

            # Y un compañero de su MISMO tenant también: la unidad de autoría es
            # el tenant, no el usuario.
            member = await _mint(ids["member_a"], ids["tenant_a"])
            detail = await client.get(
                f"/marketplace/listings/{published['id']}",
                headers={"Authorization": f"Bearer {member}"},
            )
            assert detail.status_code == 200

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Quién revisa: nadie por debajo de system_admin
# ---------------------------------------------------------------------------
def test_a_tenant_admin_cannot_review(configured_app: Any, migrations_pg_dsn: str) -> None:
    """Ni siquiera el autor puede aprobarse a sí mismo. Ése es el punto de D6."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)

            for path, payload in (
                (f"/admin/marketplace/listings/{published['id']}/approve", {"promote": True}),
                (f"/admin/marketplace/listings/{published['id']}/reject", {"reason": "no"}),
                (f"/admin/marketplace/listings/{published['id']}/promote", {}),
            ):
                resp = await client.post(
                    path, json=payload, headers={"Authorization": f"Bearer {author_token}"}
                )
                assert resp.status_code == 403, f"{path} -> {resp.status_code}: {resp.text}"

            queue = await client.get(
                "/admin/marketplace/review-queue",
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert queue.status_code == 403

            # Y el listing sigue exactamente donde estaba.
            detail = await client.get(
                f"/marketplace/listings/{published['id']}",
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert detail.json()["review_status"] == "pending_review"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# El viaje del admin: cola → aprobar → catálogo
# ---------------------------------------------------------------------------
def test_approving_puts_it_in_everyones_catalog(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)
            sys_headers = await _sysadmin_headers(ids["sysadmin"])

            queue = await client.get("/admin/marketplace/review-queue", headers=sys_headers)
            assert queue.status_code == 200, queue.text
            assert [row["id"] for row in queue.json()] == [published["id"]], (
                "la cola tiene que ver el listing privado de OTRO tenant: para eso "
                "corre sobre la sesión BYPASSRLS"
            )

            approved = await client.post(
                f"/admin/marketplace/listings/{published['id']}/approve",
                json={"promote": False},
                headers=sys_headers,
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["review_status"] == "published"
            assert approved.json()["reviewed_at"] is not None
            # Aprobar no promociona: la confianza es una decisión aparte.
            assert approved.json()["trust_level"] == "community"

            # Y ahora el share sí lo enseña.
            share = await client.post(
                "/marketplace/shares",
                json={
                    "listing_id": published["id"],
                    "target_tenant_id": str(ids["tenant_b"]),
                },
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert share.status_code == 201, share.text
            outsider = await _mint(ids["outsider"], ids["tenant_b"])
            listed = await client.get(
                "/marketplace/listings", headers={"Authorization": f"Bearer {outsider}"}
            )
            assert "acme-checker" in {row["name"] for row in listed.json()}

            # La cola queda vacía: lo aprobado deja de esperar.
            queue2 = await client.get("/admin/marketplace/review-queue", headers=sys_headers)
            assert queue2.json() == []

    asyncio.run(scenario())


def test_promote_after_approval_lifts_the_trust_level(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)
            sys_headers = await _sysadmin_headers(ids["sysadmin"])

            # Promocionar ANTES de aprobar es un 409: daría por buena una
            # revisión que no ha ocurrido.
            early = await client.post(
                f"/admin/marketplace/listings/{published['id']}/promote",
                json={},
                headers=sys_headers,
            )
            assert early.status_code == 409, early.text

            await client.post(
                f"/admin/marketplace/listings/{published['id']}/approve",
                json={},
                headers=sys_headers,
            )
            promoted = await client.post(
                f"/admin/marketplace/listings/{published['id']}/promote",
                json={"trust_level": "verified"},
                headers=sys_headers,
            )
            assert promoted.status_code == 200, promoted.text
            assert promoted.json()["trust_level"] == "verified"

            # Y baja igual de bien: degradar sin despublicar.
            demoted = await client.post(
                f"/admin/marketplace/listings/{published['id']}/promote",
                json={"trust_level": "community"},
                headers=sys_headers,
            )
            assert demoted.json()["trust_level"] == "community"
            assert demoted.json()["review_status"] == "published"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Rechazar: con motivo, o no hay rechazo
# ---------------------------------------------------------------------------
def test_rejection_without_a_reason_is_422(configured_app: Any, migrations_pg_dsn: str) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)
            sys_headers = await _sysadmin_headers(ids["sysadmin"])

            for payload in ({}, {"reason": ""}, {"reason": "    "}):
                resp = await client.post(
                    f"/admin/marketplace/listings/{published['id']}/reject",
                    json=payload,
                    headers=sys_headers,
                )
                assert resp.status_code == 422, f"{payload} -> {resp.status_code}: {resp.text}"

            detail = await client.get(
                f"/marketplace/listings/{published['id']}",
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert detail.json()["review_status"] == "pending_review"

    asyncio.run(scenario())


def test_rejection_reaches_the_author_and_can_be_fixed(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El ciclo completo del rechazo: motivo visible → corregir → vuelve a la cola."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)
            sys_headers = await _sysadmin_headers(ids["sysadmin"])

            rejected = await client.post(
                f"/admin/marketplace/listings/{published['id']}/reject",
                json={"reason": "El endpoint no usa TLS."},
                headers=sys_headers,
            )
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["review_status"] == "rejected"

            # El autor lee el motivo…
            detail = await client.get(
                f"/marketplace/listings/{published['id']}",
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert detail.status_code == 200
            assert detail.json()["rejection_reason"] == "El endpoint no usa TLS."

            # …y nadie más ve el listing rechazado.
            outsider = await _mint(ids["outsider"], ids["tenant_b"])
            listed = await client.get(
                "/marketplace/listings", headers={"Authorization": f"Bearer {outsider}"}
            )
            assert "acme-checker" not in {row["name"] for row in listed.json()}

            # Corrige y reenvía: el veredicto viejo se borra con la corrección.
            fixed = _TOOL_YAML.replace("version: 1.0.0", "version: 1.0.1")
            updated = await client.put(
                f"/marketplace/private/listings/{published['id']}",
                json={"manifest": fixed, "changelog": "TLS."},
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["review_status"] == "pending_review"
            assert (
                updated.json()["rejection_reason"] is None
            ), "una acusación caducada no puede sobrevivir a su corrección"

    asyncio.run(scenario())


def test_republishing_an_approved_listing_sends_it_back_to_the_queue(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Una versión nueva NO hereda la aprobación de la anterior.

    Si la heredase, el primer listing aprobado sería un pase permanente para
    publicar cualquier cosa después — el agujero más obvio de D6.
    """

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            author_token = await _mint(ids["author"], ids["tenant_a"])
            published = await _publish(client, author_token)
            sys_headers = await _sysadmin_headers(ids["sysadmin"])
            await client.post(
                f"/admin/marketplace/listings/{published['id']}/approve",
                json={},
                headers=sys_headers,
            )

            v2 = _TOOL_YAML.replace("version: 1.0.0", "version: 2.0.0")
            updated = await client.put(
                f"/marketplace/private/listings/{published['id']}",
                json={"manifest": v2, "changelog": "Cambio gordo."},
                headers={"Authorization": f"Bearer {author_token}"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["review_status"] == "pending_review"

            # Y desaparece del catálogo ajeno mientras espera.
            outsider = await _mint(ids["outsider"], ids["tenant_b"])
            listed = await client.get(
                "/marketplace/listings", headers={"Authorization": f"Bearer {outsider}"}
            )
            assert "acme-checker" not in {row["name"] for row in listed.json()}

            # El histórico tiene AHORA dos filas: la aprobada y la que espera.
            versions = await client.get(
                f"/admin/marketplace/listings/{published['id']}/versions",
                headers=sys_headers,
            )
            assert sorted(v["version"] for v in versions.json()) == ["1.0.0", "2.0.0"]

    asyncio.run(scenario())
