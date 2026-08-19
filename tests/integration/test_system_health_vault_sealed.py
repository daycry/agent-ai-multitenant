"""`/admin/system-health` con Vault SELLADO: degradado, ni 500 ni «ok».

Plan prod-10 `task_prod10_09` (hallazgos secrets-5 y deploy-8), test declarado
`auto_prod10_09_b`. El hermano `tests/unit/test_vault_seal_probe.py` cubre la
sonda en aislamiento y su cableado por AST; lo que faltaba es la vuelta entera:
petición HTTP autenticada -> router -> sonda -> Vault -> cuerpo de la respuesta.

## Por qué hace falta el camino completo

El fallo que motiva la tarea NO es que la sonda calcule mal, sino que **tres
capas daban por bueno un Vault inutilizable**: el healthcheck del compose traduce
sellado (503) a 200 a propósito (si no, Vault se reiniciaría en bucle antes de
que nadie pueda desellarlo), el compose del instalador arranca todo detrás de ese
200, y el watchdog acepta cualquier estado «running». Tras un reinicio del host,
el operador veía un panel verde con la plataforma incapaz de resolver un secreto.

De ahí las tres cosas que se fijan aquí y que un unit test de la sonda no ve:

1. **No es un 500.** Un Vault sellado contesta HTTP; si el endpoint reventara, el
   panel entero se caería por la dependencia que precisamente intenta reportar.
2. **No es un 200 que dice «ok».** El agregado tiene que bajar a `degraded`, que
   es lo que el operador mira de un vistazo. Antes de prod-10 decía `ok` y había
   que abrir la lista de servicios para enterarse (hallazgo secrets-5).
3. **El veredicto lo causa el sello**, no una constante: con el MISMO montaje y
   `sealed: false` el agregado vuelve a `ok`. Sin ese contraste, un endpoint que
   devolviera `degraded` siempre pasaría el primer test.

## El montaje

Un servidor HTTP de verdad en `127.0.0.1` haciendo de Vault, apuntado por
`API_SERVER_VAULT_URL`. No se parchea `probe_vault_seal`: el cliente httpx real,
el timeout real y el `asyncio.gather` de las ocho sondas forman parte de lo que
se está probando.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

from tests.integration._user_seeding import seed_user

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Vault de mentira: contesta `/v1/sys/seal-status` con la carga que le pongan.
# ---------------------------------------------------------------------------
class _FakeVault:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {"initialized": True, "sealed": False}
        self.paths_seen: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._stopped = False

    @property
    def url(self) -> str:
        host, port = self._server.server_address[0], self._server.server_address[1]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            # `do_GET` en mayúsculas no es un capricho: es el nombre que
            # `BaseHTTPRequestHandler` despacha por reflexión.
            def do_GET(self) -> None:
                outer.paths_seen.append(self.path)
                body = json.dumps(outer.payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                """Silencio: si no, cada sonda escupe una línea en stderr."""

        return _Handler


@pytest.fixture()
def fake_vault() -> Iterator[_FakeVault]:
    vault = _FakeVault()
    try:
        yield vault
    finally:
        with contextlib.suppress(Exception):
            vault.stop()


# ---------------------------------------------------------------------------
# App configurada - mismo molde que `tests/integration/test_admin_rbac.py`, con
# `API_SERVER_VAULT_URL` apuntando al Vault de mentira.
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    fake_vault: _FakeVault,
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
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "100")
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("API_SERVER_VAULT_URL", fake_vault.url)

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


async def _promote_to_system_admin(dsn: str, email: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET is_system_admin = true WHERE email = $1", email)
    finally:
        await conn.close()


async def _system_health(configured_app, migrations_dsn: str) -> Any:
    """Autentica como System Admin y devuelve la respuesta de `/admin/system-health`."""
    email, password = "vault-probe@example.com", "longenoughpw"
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        await seed_user(migrations_dsn, email, password)
        await _promote_to_system_admin(migrations_dsn, email)
        login = await client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        return await client.get(
            "/admin/system-health",
            headers={"Authorization": f"Bearer {token}"},
        )


def _service(body: dict[str, Any], name: str) -> dict[str, Any]:
    for svc in body["services"]:
        if svc["name"] == name:
            return svc
    raise AssertionError(f"`/admin/system-health` dejó de reportar `{name}`: {body}")


# ---------------------------------------------------------------------------
# Vault sellado
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_sealed_vault_degrades_the_dashboard(
    configured_app, migrations_pg_dsn: str, fake_vault: _FakeVault
) -> None:
    fake_vault.payload = {"initialized": True, "sealed": True}

    resp = await _system_health(configured_app, migrations_pg_dsn)

    # (1) Ni un 500: la dependencia rota es justo la que hay que reportar.
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # (2) Ni un 200 que dice «ok»: el agregado baja a degradado (secrets-5).
    assert body["status"] == "degraded", body

    vault = _service(body, "vault")
    assert vault["status"] == "degraded", body
    # El detalle nombra el sello y el runbook: un «degraded / HTTP 503» genérico
    # no le dice al operador que lo que toca es desellar.
    assert "SEALED" in (vault["detail"] or ""), vault
    assert "docs/06-runbooks/restart-services.md" in (vault["detail"] or ""), vault

    # Y el veredicto es atribuible a Vault, no a una base de datos caída: si
    # postgres no estuviera `ok`, el agregado sería `down` por otra razón.
    assert _service(body, "postgres")["status"] == "ok", body

    # Se preguntó al endpoint que dice la verdad. `/v1/sys/health` responde 200
    # con el sellado escondido tras `sealedcode`, así que sondearlo daría verde.
    assert any(p.startswith("/v1/sys/seal-status") for p in fake_vault.paths_seen), (
        f"la sonda no consultó /v1/sys/seal-status (vio {fake_vault.paths_seen})"
    )


@pytest.mark.asyncio
async def test_an_uninitialised_vault_also_degrades(
    configured_app, migrations_pg_dsn: str, fake_vault: _FakeVault
) -> None:
    """Sin inicializar tampoco sirve secretos, y el compose también lo pinta 200."""
    fake_vault.payload = {"initialized": False, "sealed": True}

    resp = await _system_health(configured_app, migrations_pg_dsn)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "degraded", body
    vault = _service(body, "vault")
    assert vault["status"] == "degraded", body
    # El remedio es OTRO que el del sellado, y el detalle tiene que decirlo.
    assert "init-vault.sh" in (vault["detail"] or ""), vault


# ---------------------------------------------------------------------------
# El contraste: sin el sello, el mismo montaje vuelve a verde
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_open_vault_leaves_the_dashboard_ok(
    configured_app, migrations_pg_dsn: str, fake_vault: _FakeVault
) -> None:
    """Sin este caso, un endpoint que devolviera `degraded` SIEMPRE pasaría el
    test de arriba, y el panel mentiría en la otra dirección."""
    fake_vault.payload = {"initialized": True, "sealed": False}

    resp = await _system_health(configured_app, migrations_pg_dsn)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    vault = _service(body, "vault")
    assert vault["status"] == "ok", body
    assert vault["detail"] is None, body
    # El agregado sólo puede ser `ok` si además postgres responde; las demás
    # sondas (minio, clamav, docling, ollama, egress) pueden estar caídas en el
    # entorno de test sin arrastrarlo: es el diseño documentado del router.
    assert body["status"] == "ok", body


# ---------------------------------------------------------------------------
# «No responde» NO es «sellado»
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unreachable_vault_is_down_not_sealed(
    configured_app, migrations_pg_dsn: str, fake_vault: _FakeVault
) -> None:
    """Un Vault caído se reporta `down`, con un detalle genérico.

    Confundirlo con `sealed` mandaría al operador a desellar un contenedor que no
    está sellado; de un servicio que no contesta se ocupa la regla `ServiceDown`
    (`up == 0`). Y el detalle viaja a un panel, así que no puede llevar el texto
    crudo de la excepción: eso filtra la topología interna (error-obs-logging-6).

    Nota deliberada: este caso NO afirma nada sobre `body["status"]`. Hoy el
    agregado sólo baja a `degraded` por el sello, así que un Vault inalcanzable
    lo deja en `ok` - algo que puede ser correcto (lo cubre `ServiceDown`) o un
    hueco, pero es una decisión del operador y no la fija este test.
    """
    host_port = fake_vault.url.removeprefix("http://")
    fake_vault.stop()

    resp = await _system_health(configured_app, migrations_pg_dsn)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    vault = _service(body, "vault")
    assert vault["status"] == "down", body
    assert vault["status"] != "degraded", (
        "un Vault que no contesta NO es un Vault sellado: el remedio es otro"
    )
    assert vault["detail"] in {"connection failed", "timeout"}, vault
    assert host_port not in json.dumps(body), (
        "el detalle filtra la URL interna de Vault a un panel; sólo debe salir "
        "la CLASE del fallo, con el diagnóstico en los logs del servidor"
    )
