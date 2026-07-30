"""Liveness ≠ readiness (`task_audit14_08`, hallazgo AUD14-06).

Hasta ahora la api-server sólo tenía `/healthz`, que devuelve `{"status": "ok"}`
en cuanto el proceso responde. Eso vale como **liveness** —«¿hay que reiniciar el
contenedor?»— y es exactamente lo que NO debe consultar dependencias externas: si
`/healthz` fallara por un PostgreSQL caído, Docker reiniciaría la api-server en
bucle sin arreglar nada.

Lo que faltaba era la otra pregunta, la de **readiness**: «¿puede este proceso
aceptar tráfico ahora mismo?». Sin ella, un despliegue nuevo empieza a recibir
peticiones antes de poder atenderlas y el operador no tiene forma de distinguir
«arrancado» de «listo».

Este fichero fija las dos mitades del contrato, y la asimetría entre ellas es lo
importante:

- con PostgreSQL inalcanzable, `/healthz` sigue 200 y `/readyz` da 503;
- `/readyz` dice CUÁL dependencia falla (degradación parcial), no un booleano;
- el cuerpo del 503 no lleva secretos — la contraseña del DSN no puede acabar en
  un log de Docker ni en una pantalla de estado;
- un check que se cuelga no cuelga la petición: hay deadline por check.

Sólo se comprueban las dependencias **críticas** (PostgreSQL y Redis).
Vault/Ollama/Docling son opcionales a propósito: meterlas aquí convertiría un
servicio auxiliar caído en un flapping de readiness de la api-server (riesgo 5
del plan).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_DEAD_PORT = 1  # nada escucha aquí; connect falla rápido y sin DNS


def _rewire(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Reapunta la config del proceso y tira los caches que la memorizan.

    Los engines y el cliente de Redis están `lru_cache`-ados; sin limpiarlos el
    proceso seguiría usando la BD buena aunque el env diga otra cosa.
    """
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()


async def _get(app: Any, path: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# Camino feliz: con el stack real arriba, readiness es 200 y dice qué probó.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_readyz_is_200_with_both_dependencies_up(configured_app: Any) -> None:
    resp = await _get(configured_app, "/readyz")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    names = {check["name"] for check in body["checks"]}
    assert names == {"postgresql", "redis"}, (
        "readiness debe declarar QUÉ probó; si el conjunto cambia, es un cambio "
        f"de contrato: {names}"
    )
    assert all(check["ok"] for check in body["checks"])


@pytest.mark.asyncio
async def test_healthz_stays_liveness_only(configured_app: Any) -> None:
    """El contrato viejo no se toca: `/healthz` sigue siendo `{"status": "ok"}`."""
    resp = await _get(configured_app, "/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Degradación: la asimetría liveness/readiness es el corazón de la tarea.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_healthz_survives_a_dead_postgres_but_readyz_does_not(
    configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con la BD caída, reiniciar el proceso no arregla nada: liveness debe
    aguantar (si no, Docker entra en bucle de reinicios) y readiness caer."""
    _rewire(
        monkeypatch,
        API_SERVER_DATABASE_URL=f"postgresql+asyncpg://nobody:nothing@127.0.0.1:{_DEAD_PORT}/nope",
    )

    live = await _get(configured_app, "/healthz")
    ready = await _get(configured_app, "/readyz")

    assert live.status_code == 200, "liveness NO puede depender de PostgreSQL"
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "not_ready"
    failed = {check["name"]: check for check in body["checks"] if not check["ok"]}
    healthy = {check["name"] for check in body["checks"] if check["ok"]}
    assert set(failed) == {"postgresql"}, f"degradación parcial mal reportada: {body}"
    assert healthy == {"redis"}, "Redis sigue vivo: readiness debe decirlo"
    assert failed["postgresql"]["detail"], "un check caído debe explicar por qué"


@pytest.mark.asyncio
async def test_readyz_503_body_carries_no_credentials(
    configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El cuerpo del 503 lo lee un healthcheck de Docker y acaba en logs. Un
    error de driver que arrastre el DSN filtraría la contraseña de la BD.

    Honestidad sobre lo que prueba: se verificó que esta aserción pasa TAMBIÉN con
    el saneado desactivado, porque los drivers de hoy no filtran. Se conserva como
    red de seguridad para el día que uno sí lo haga; el saneado en sí lo prueba
    `tests/unit/test_readiness_scrub.py`, que sí se pone rojo al quitarlo.
    """
    secret = "sup3r-s3cr3t-pw"  # — contraseña de pega, a propósito
    _rewire(
        monkeypatch,
        API_SERVER_DATABASE_URL=(
            f"postgresql+asyncpg://leaky:{secret}@127.0.0.1:{_DEAD_PORT}/nope"
        ),
        API_SERVER_REDIS_URL=f"redis://:{secret}@127.0.0.1:{_DEAD_PORT}/0",
    )

    resp = await _get(configured_app, "/readyz")

    assert resp.status_code == 503
    assert secret not in resp.text, f"credencial filtrada en el cuerpo: {resp.text}"
    assert "leaky" not in resp.text, f"usuario filtrado en el cuerpo: {resp.text}"


# ---------------------------------------------------------------------------
# Deadline: un check colgado no puede colgar la petición de readiness.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_hanging_check_times_out_instead_of_hanging(
    configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.routers import health

    async def _never_answers(_settings: Any) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(health, "_ping_redis", _never_answers)
    monkeypatch.setattr(health, "READINESS_TIMEOUT_SECONDS", 0.2)

    started = asyncio.get_running_loop().time()
    resp = await _get(configured_app, "/readyz")
    elapsed = asyncio.get_running_loop().time() - started

    assert resp.status_code == 503
    assert elapsed < 5, f"el deadline no se respetó: {elapsed:.1f}s"
    redis_check = next(c for c in resp.json()["checks"] if c["name"] == "redis")
    assert redis_check["ok"] is False
    assert "timeout" in redis_check["detail"].lower(), redis_check


@pytest.mark.asyncio
async def test_readyz_recovers_without_a_restart(
    configured_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`human_audit14_03`: al recuperar la dependencia, `/readyz` vuelve a 200 sin
    reiniciar el proceso. Si el resultado se cacheara, esto se quedaría en 503."""
    good_url = (await _get(configured_app, "/readyz")).status_code
    assert good_url == 200

    _rewire(
        monkeypatch,
        API_SERVER_REDIS_URL=f"redis://127.0.0.1:{_DEAD_PORT}/0",
    )
    assert (await _get(configured_app, "/readyz")).status_code == 503

    monkeypatch.undo()
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    assert (await _get(configured_app, "/readyz")).status_code == 200
