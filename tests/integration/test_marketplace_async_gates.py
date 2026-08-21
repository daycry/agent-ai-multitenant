"""prod-13 `task_prod13_01` — el 202 de la instalación y su recurso de estado.

Lo que sólo se puede comprobar con la BD y el router de verdad, y que por tanto
no está en `tests/unit/test_marketplace_no_sync_subprocess_in_async.py`:

* que el endpoint responda **202** con `Location`, y que la fila quede en
  `analyzing` — el recurso de estado consultable que pedía la casilla;
* que ese recurso se pueda LEER por su URL (`GET /installations/{id}`), porque un
  202 cuyo estado sólo se encuentra rebuscando en un listado paginado no es un
  202; y que la instalación de otro tenant sea un 404 y no un 403;
* que el worker cierre la instalación con la MISMA política que el camino
  síncrono (`finalize_installation`), incluida la materialización del ADR 0100;
* que una puerta que rechaza deje `blocked` **y** su audit row ENLAZADO a la
  instalación — sin el enlace, el motivo del rechazo existiría en una fila que
  nadie puede relacionar con la instalación que se quedó parada;
* que un artefacto que existía al aceptar la petición y NO se alcanza desde el
  worker sea un fallo ruidoso y no un `skipped` silencioso. Es la trampa más
  probable de este cambio: el api-server no monta ningún volumen (medido el
  2026-08-19: `docker inspect api-server` → `Mounts: []`), así que trasladar la
  puerta al worker podría apagarla sin que nada se pusiera rojo;
* y que el camino por defecto siga devolviendo **201**: el contrato que ya
  consumen el admin-panel y los scripts del operador no cambia por debajo.
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

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


# ===========================================================================
# Arnés
# ===========================================================================
@pytest.fixture()
def artifact_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un root de artefactos VACÍO, aislado del que pueda haber en la máquina.

    Vacío a propósito: es el estado real del despliegue (`seed_official_catalog`
    no tiene ningún llamante fuera de los tests y el directorio no existe en
    ninguno de los dos contenedores), y hace que el caso por defecto ejercite el
    skip honesto del ADR 0081 en vez de depender de un fixture en disco.
    """
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setenv("MARKETPLACE_ARTIFACT_ROOT", str(root))
    return root


@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    migrations_pg_dsn: str,
    test_redis_url: str,
    artifact_root: Path,
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
    # El worker de esta casilla corre con rol BYPASSRLS: misma URL que el admin.
    monkeypatch.setenv("WORKERS_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("WORKERS_BROKER_URL", test_redis_url)
    monkeypatch.setenv("WORKERS_RESULT_BACKEND", test_redis_url)
    monkeypatch.setenv("WORKERS_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache
    from workers.config import get_settings as get_worker_settings

    get_settings.cache_clear()
    get_worker_settings.cache_clear()
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
        get_worker_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


#: Un manifest de tool de red: materializable sin sandbox (ADR 0100), así que el
#: cierre de la instalación llega hasta el final en vez de quedarse en "diferido".
_TOOL_MANIFEST: dict[str, Any] = {
    "implementation_type": "http_endpoint",
    "implementation_ref": "https://status.example.test/api",
    "targets": ["qa"],
}


async def _seed(dsn: str) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant_a",
            "tenant_b",
            "admin_a",
            "admin_b",
            "source",
            "listing_verified",
            "listing_community",
        )
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_deployments, marketplace_audit_entries,"
            " marketplace_installations, marketplace_listing_versions,"
            " marketplace_listings, marketplace_sources, agent_tools, agent_skills,"
            " tools, skills, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["tenant_a"],
            "Async A",
            "async-a",
            ids["tenant_b"],
            "Async B",
            "async-b",
        )
        for user, email in (
            (ids["admin_a"], "async-admin-a@test.test"),
            (ids["admin_b"], "async-admin-b@test.test"),
        ):
            await conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,'argon2-placeholder')",
                user,
                email,
            )
        for user, tenant in ((ids["admin_a"], ids["tenant_a"]), (ids["admin_b"], ids["tenant_b"])):
            await conn.execute(
                "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
                " VALUES ($1,$2,$3,'tenant_admin')",
                uuid4(),
                tenant,
                user,
            )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-async','official',true)",
            ids["source"],
        )
        for key, name, trust in (
            ("listing_verified", "async-verified", "verified"),
            ("listing_community", "async-community", "community"),
        ):
            await conn.execute(
                "INSERT INTO marketplace_listings"
                " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
                "  requested_permissions)"
                " VALUES ($1,$2,NULL,'tool',$3,'1.0.0',$4,$5::jsonb,'[]'::jsonb)",
                ids[key],
                ids["source"],
                name,
                trust,
                json.dumps(_TOOL_MANIFEST),
            )
    finally:
        await conn.close()
    return ids


async def _row(dsn: str, installation_id: UUID) -> dict[str, Any] | None:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, granted_permissions FROM marketplace_installations WHERE id = $1",
            installation_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def _audit(dsn: str, installation_id: UUID) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT action, actor, detail FROM marketplace_audit_entries"
            " WHERE installation_id = $1 ORDER BY created_at",
            installation_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _install_async(client: AsyncClient, token: str, listing_id: UUID) -> Any:
    return await client.post(
        "/marketplace/installations",
        json={"listing_id": str(listing_id), "async_gates": True},
        headers={"Authorization": f"Bearer {token}"},
    )


def _run_worker(installation_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """La task del worker, por su función async (sin broker de por medio)."""
    from workers.marketplace_gates import _run_install_gates_async

    return asyncio.run(_run_install_gates_async(installation_id, tenant_id))


# ===========================================================================
# 1. El 202 y el recurso de estado
# ===========================================================================
def test_el_endpoint_devuelve_202_y_la_fila_queda_en_analyzing(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Y sin ejecutar ni un subprocess dentro del request."""
    import subprocess

    ids = asyncio.run(_seed(migrations_pg_dsn))
    llamadas: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: llamadas.append((a, k)))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: llamadas.append((a, k)))

    async def _flow() -> None:
        # El productor no debe tocar el broker en este test: se sustituye por el
        # observador, que es lo que un 202 promete (aceptado, encolado).
        publicados: list[dict[str, Any]] = []

        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            publicados.append({"installation_id": installation_id, "tenant_id": tenant_id})
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await _install_async(client, token, ids["listing_verified"])
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "analyzing", body
            installation_id = UUID(body["id"])
            assert resp.headers["location"] == (f"/marketplace/installations/{installation_id}"), (
                "el 202 no dice dónde consultar el estado"
            )

            # El recurso de estado se lee por su URL.
            got = await client.get(
                f"/marketplace/installations/{installation_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert got.status_code == 200, got.text
            assert got.json()["status"] == "analyzing"

        assert not llamadas, (
            f"el request ejecutó {len(llamadas)} subprocess: el análisis sigue dentro del HTTP"
        )
        assert [p["installation_id"] for p in publicados] == [installation_id], (
            "el 202 no encoló las puertas"
        )
        row = await _row(migrations_pg_dsn, installation_id)
        assert row is not None and row["status"] == "analyzing"
        # El rastro de auditoría dice que se ACEPTÓ, no que se instaló.
        acciones = [a["action"] for a in await _audit(migrations_pg_dsn, installation_id)]
        assert acciones == ["gates_queued"], acciones

    asyncio.run(_flow())


def test_el_recurso_de_estado_de_otro_tenant_es_404(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un 403 confirmaría que existe. RLS lo deja invisible."""
    ids = asyncio.run(_seed(migrations_pg_dsn))

    async def _flow() -> None:
        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token_a = await _mint(ids["admin_a"], ids["tenant_a"])
        token_b = await _mint(ids["admin_b"], ids["tenant_b"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await _install_async(client, token_a, ids["listing_verified"])
            assert resp.status_code == 202, resp.text
            installation_id = resp.json()["id"]

            ajeno = await client.get(
                f"/marketplace/installations/{installation_id}",
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert ajeno.status_code == 404, ajeno.text

    asyncio.run(_flow())


def test_el_camino_por_defecto_sigue_devolviendo_201(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Sin `async_gates` el contrato es el de siempre: 201 e instalado."""
    ids = asyncio.run(_seed(migrations_pg_dsn))

    async def _flow() -> None:
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/marketplace/installations",
                json={"listing_id": str(ids["listing_verified"])},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "enabled", resp.text

    asyncio.run(_flow())


# ===========================================================================
# 2. El worker cierra la instalación con la MISMA política
# ===========================================================================
def test_el_worker_cierra_un_verified_como_enabled_y_materializa(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin artefacto en disco: skip honesto (ADR 0081) y la instalación sigue."""
    ids = asyncio.run(_seed(migrations_pg_dsn))

    async def _accept() -> UUID:
        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/marketplace/installations",
                json={
                    "listing_id": str(ids["listing_verified"]),
                    "granted_permissions": ["net:https"],
                    "async_gates": True,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202, resp.text
        return UUID(resp.json()["id"])

    installation_id = asyncio.run(_accept())
    resultado = _run_worker(installation_id, ids["tenant_a"])
    assert resultado["status"] == "enabled", resultado

    row = asyncio.run(_row(migrations_pg_dsn, installation_id))
    assert row is not None and row["status"] == "enabled"
    # Los permisos que pidió el llamante llegaron al OTRO proceso: sin el
    # handoff de `gates_queued` un verified acabaría instalado con la lista vacía.
    concedidos = row["granted_permissions"]
    concedidos = json.loads(concedidos) if isinstance(concedidos, str) else concedidos
    assert concedidos == ["net:https"], concedidos

    filas = asyncio.run(_audit(migrations_pg_dsn, installation_id))
    acciones = [f["action"] for f in filas]
    assert acciones == ["gates_queued", "install"], acciones
    # El actor del install es el humano que lo pidió, no "system": el handoff lo
    # arrastra para que la auditoría no pierda al responsable al cruzar de proceso.
    assert filas[-1]["actor"] == f"user:{ids['admin_a']}", filas[-1]["actor"]
    detalle = filas[-1]["detail"]
    detalle = json.loads(detalle) if isinstance(detalle, str) else detalle
    assert detalle["gates"].get("skipped_reason") == "no_artifact", detalle["gates"]
    assert detalle["materialization"] is not None, "un enabled sin materializar (ADR 0100)"


def test_el_worker_respeta_el_consentimiento_de_un_listing_community(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Community exige consentimiento por permiso: `disabled` y CERO concedidos.

    Es la aserción que demuestra que el worker NO tiene su propia política: llama
    al mismo `finalize_installation` que el router, y por eso ignora los permisos
    pedidos igual que él.
    """
    ids = asyncio.run(_seed(migrations_pg_dsn))

    async def _accept() -> UUID:
        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/marketplace/installations",
                json={
                    "listing_id": str(ids["listing_community"]),
                    "granted_permissions": ["net:https"],
                    "async_gates": True,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202, resp.text
        return UUID(resp.json()["id"])

    installation_id = asyncio.run(_accept())
    assert _run_worker(installation_id, ids["tenant_a"])["status"] == "disabled"
    row = asyncio.run(_row(migrations_pg_dsn, installation_id))
    assert row is not None and row["status"] == "disabled"
    concedidos = row["granted_permissions"]
    concedidos = json.loads(concedidos) if isinstance(concedidos, str) else concedidos
    assert concedidos == [], (
        "un community se instaló con permisos concedidos: el worker se saltó el "
        "gate de consentimiento que el router sí aplica"
    )


def test_el_worker_es_idempotente_ante_una_re_entrega(
    configured_app: Any, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis re-entrega mensajes largos (`task_acks_late` + visibility timeout)."""
    ids = asyncio.run(_seed(migrations_pg_dsn))

    async def _accept() -> UUID:
        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await _install_async(client, token, ids["listing_verified"])
        return UUID(resp.json()["id"])

    installation_id = asyncio.run(_accept())
    _run_worker(installation_id, ids["tenant_a"])
    _run_worker(installation_id, ids["tenant_a"])

    acciones = [f["action"] for f in asyncio.run(_audit(migrations_pg_dsn, installation_id))]
    assert acciones == ["gates_queued", "install"], (
        f"la re-entrega escribió un segundo cierre: {acciones}"
    )


# ===========================================================================
# 3. Las puertas que rechazan, y la que no debe apagarse en silencio
# ===========================================================================
def test_una_puerta_que_rechaza_deja_blocked_con_su_audit_row_enlazado(
    configured_app: Any,
    migrations_pg_dsn: str,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manifest corrupto → `blocked`, y el motivo consultable por instalación."""
    ids = asyncio.run(_seed(migrations_pg_dsn))
    # Artefacto PRESENTE y con el manifest roto: la puerta 2 (parse) aborta. Se
    # usa la puerta real, no un doble — el fetcher y el parser son los de verdad.
    listing_dir = artifact_root / str(ids["listing_verified"])
    listing_dir.mkdir()
    (listing_dir / "tool.yaml").write_text(": esto no es yaml :\n\t- [", encoding="utf-8")

    async def _accept() -> UUID:
        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await _install_async(client, token, ids["listing_verified"])
        assert resp.status_code == 202, resp.text
        return UUID(resp.json()["id"])

    installation_id = asyncio.run(_accept())
    assert _run_worker(installation_id, ids["tenant_a"])["status"] == "blocked"

    row = asyncio.run(_row(migrations_pg_dsn, installation_id))
    assert row is not None and row["status"] == "blocked"

    filas = asyncio.run(_audit(migrations_pg_dsn, installation_id))
    abortos = []
    for f in filas:
        detalle = f["detail"]
        detalle = json.loads(detalle) if isinstance(detalle, str) else detalle
        if detalle.get("aborted"):
            abortos.append(detalle)
    assert len(abortos) == 1, (
        f"se esperaba UN audit row de aborto enlazado a la instalación, hay {len(abortos)}"
    )
    assert abortos[0]["reason"] == "manifest_invalid", abortos[0]


def test_un_artefacto_que_el_worker_no_alcanza_no_se_traga_como_skip(
    configured_app: Any,
    migrations_pg_dsn: str,
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La trampa central de mover la puerta de proceso, medida.

    El artefacto EXISTE cuando se acepta la petición y desaparece antes de que el
    worker lo busque — que es exactamente lo que pasa hoy entre el api-server (sin
    volúmenes montados) y el worker. Si eso se registrase como `skipped`, la
    puerta se habría apagado en silencio: verde en los tests y ningún análisis en
    producción. Tiene que ser `blocked`.
    """
    ids = asyncio.run(_seed(migrations_pg_dsn))
    listing_dir = artifact_root / str(ids["listing_verified"])
    listing_dir.mkdir()
    (listing_dir / "tool.yaml").write_text(
        "implementation_type: http_endpoint\nimplementation_ref: https://status.example.test/api\n",
        encoding="utf-8",
    )

    async def _accept() -> UUID:
        async def _fake(*, installation_id: UUID, tenant_id: UUID) -> bool:
            return True

        monkeypatch.setattr("api_server.celery_client.enqueue_marketplace_install_gates", _fake)
        token = await _mint(ids["admin_a"], ids["tenant_a"])
        transport = ASGITransport(app=configured_app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await _install_async(client, token, ids["listing_verified"])
        assert resp.status_code == 202, resp.text
        return UUID(resp.json()["id"])

    installation_id = asyncio.run(_accept())

    # El artefacto deja de ser alcanzable para el worker: mismo efecto que un
    # root que vive en el sistema de ficheros del OTRO contenedor.
    (listing_dir / "tool.yaml").unlink()
    listing_dir.rmdir()

    assert _run_worker(installation_id, ids["tenant_a"])["status"] == "blocked", (
        "el análisis dejó de correr y la instalación siguió adelante: la puerta se "
        "apagó en silencio"
    )
    filas = asyncio.run(_audit(migrations_pg_dsn, installation_id))
    motivos = []
    for f in filas:
        detalle = f["detail"]
        detalle = json.loads(detalle) if isinstance(detalle, str) else detalle
        if detalle.get("aborted"):
            motivos.append(detalle["reason"])
    assert motivos == ["artifact_fetch_failed"], motivos
