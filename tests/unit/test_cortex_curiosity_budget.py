"""Córtex F4 — budget gate de curiosidad: la ventana diaria y el fail-safe del coste.

Complementa `tests/integration/test_cortex_autonomy_budget.py` (que ejercita el
cap con el Redis real) cerrando lo que allí NO se afirma y que la aceptación del
plan sí exige:

  * **«la ventana se resetea a medianoche UTC»** — nadie lo probaba. El único
    testigo era `daily_budget_key`, que devuelve la clave del día; pero que un
    budget agotado el día D deje pasar la primera pasada del día D+1 no estaba
    fijado por ningún assert. Con un doble en memoria se afirma sobre el
    comportamiento (permitido/bloqueado), no sobre el nombre de la clave.
  * **El TTL exacto** — el test de integración solo comprueba `0 < ttl <= 12h`,
    banda que también cumpliría un `EXPIRE` de 1 segundo (que perdería la cuenta
    del día) o de 12h clavadas (que la arrastraría al día siguiente en una
    pasada de 00:00). Aquí se ata a `seconds_until_utc_midnight`.
  * **`check_searches_budget` NO consume** — el plan lo dice explícitamente («No
    incrementa coste real aquí»). Si un refactor lo convirtiese en un `INCR`
    (patrón check-and-reserve clásico), cada pasada gastaría budget aunque no
    llegase a buscar y la curiosidad se apagaría sola; el doble registra los
    comandos para atrapar eso.
  * **Fail-safe con Redis caído** — las ramas `except` de `autonomy.py` deciden
    si el córtex gasta dinero cuando no puede contar lo que gasta. Son las
    salvaguardas del ADR 0078 y no las ejecutaba ni un test: el budget debe
    negar (`redis_error`) y el breaker debe darse por ABIERTO.

Todo con dobles en memoria: unitario, sin I/O (el venv no trae `fakeredis`, que
es lo que pedía el plan; el doble cubre las cuatro operaciones que se usan).

**Dimensión de coste USD**: NO se prueba aquí porque no existe en el código
(`usd_cap`, `record_spend(cost_usd)`, `curiosity_cost_usd_today`) — ver la
auditoría 2026-07-27. Añadir el test exigiría implementarla primero.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_server.cortex.autonomy import (
    CURIOSITY_KIND,
    check_searches_budget,
    daily_budget_key,
    is_circuit_open,
    record_failure,
    record_searches,
    record_success,
    seconds_until_utc_midnight,
)

pytestmark = pytest.mark.unit

_OWNER = "11111111-1111-1111-1111-111111111111"


class _FakeRedis:
    """Redis en memoria con las 4 operaciones del budget, y un log de comandos.

    ``calls`` guarda ``(comando, clave)`` en orden para poder afirmar QUÉ tocó
    cada función (leer vs. escribir), no solo el resultado."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        raw = self.store.get(key)
        return None if raw is None else str(raw)

    async def incrby(self, key: str, amount: int) -> int:
        self.calls.append(("incrby", key))
        self.store[key] = self.store.get(key, 0) + int(amount)
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.calls.append(("expire", key))
        self.ttls[key] = int(seconds)
        return True


class _BrokenRedis:
    """Redis caído: TODO comando levanta. Ejercita las ramas ``except``."""

    def __init__(self) -> None:
        self.calls = 0

    async def _boom(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1
        raise ConnectionError("redis down")

    get = _boom
    incr = _boom
    incrby = _boom
    expire = _boom
    exists = _boom
    set = _boom
    delete = _boom


# ---------------------------------------------------------------------------
# La ventana diaria: se resetea a medianoche UTC (aceptación del plan)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_la_ventana_se_resetea_a_medianoche_utc() -> None:
    """Budget agotado a las 23:59 ⇒ la primera pasada de las 00:01 vuelve a pasar.

    Es la segunda mitad de la aceptación («la ventana se resetea a medianoche
    UTC»). Sin este test, un budget con clave global (sin el sufijo del día)
    dejaría la curiosidad apagada para siempre tras el primer día lleno y la
    suite seguiría verde."""
    redis = _FakeRedis()
    ultima_hora = datetime(2026, 6, 24, 23, 59, tzinfo=UTC)
    dia_siguiente = datetime(2026, 6, 25, 0, 1, tzinfo=UTC)

    # Día D: se consume el cap entero.
    assert await record_searches(redis, owner_user_id=_OWNER, count=5, now=ultima_hora) == 5
    agotado = await check_searches_budget(redis, owner_user_id=_OWNER, cap=5, now=ultima_hora)
    assert agotado.allowed is False
    assert agotado.reason == "budget_exhausted"

    # Día D+1, dos minutos después: ventana nueva, contador a 0.
    nuevo_dia = await check_searches_budget(redis, owner_user_id=_OWNER, cap=5, now=dia_siguiente)
    assert nuevo_dia.allowed is True
    assert nuevo_dia.used == 0

    # Y lo que consuma el día D+1 no se mezcla con lo del día D.
    assert await record_searches(redis, owner_user_id=_OWNER, count=1, now=dia_siguiente) == 1
    assert redis.store[daily_budget_key(_OWNER, CURIOSITY_KIND, now=ultima_hora)] == 5


@pytest.mark.asyncio
async def test_el_ttl_del_contador_es_exactamente_hasta_medianoche_utc() -> None:
    """El TTL debe morir CON la ventana, ni antes ni después.

    Un TTL más corto olvidaría búsquedas ya hechas (el cap dejaría de topar);
    uno más largo arrastraría el gasto de ayer al día nuevo (el cap toparía de
    más). El test de integración solo mira que el TTL esté en una banda de 12h,
    que ambos defectos podrían satisfacer."""
    redis = _FakeRedis()
    now = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)
    await record_searches(redis, owner_user_id=_OWNER, count=1, now=now)

    key = daily_budget_key(_OWNER, CURIOSITY_KIND, now=now)
    assert redis.ttls[key] == seconds_until_utc_midnight(now)
    assert redis.ttls[key] == 12 * 3600


# ---------------------------------------------------------------------------
# El check LEE, no consume
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_consultar_el_budget_no_consume_budget() -> None:
    """El gate no es un check-and-reserve: comprobar no gasta.

    El plan es explícito («No incrementa coste real aquí; eso se hace tras la
    búsqueda»). Si consultar incrementase, cada pasada que muere después en el
    drive gate o en la selección de tema quemaría una búsqueda que nunca ocurrió
    y el bucle se auto-silenciaría."""
    redis = _FakeRedis()
    now = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)

    for _ in range(10):
        d = await check_searches_budget(redis, owner_user_id=_OWNER, cap=5, now=now)
        assert d.allowed is True
        assert d.used == 0

    # Ni una escritura: solo lecturas.
    assert {cmd for cmd, _ in redis.calls} == {"get"}
    assert redis.store == {}


@pytest.mark.asyncio
async def test_registrar_cero_busquedas_no_toca_redis() -> None:
    """Una pasada que no buscó nada no debe crear la clave del día.

    Guarda contra el efecto colateral silencioso: un `INCRBY 0` + `EXPIRE`
    dejaría claves vivas por owners que jamás buscaron."""
    redis = _FakeRedis()
    now = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)

    assert await record_searches(redis, owner_user_id=_OWNER, count=0, now=now) == 0
    assert redis.calls == []
    assert redis.store == {}


# ---------------------------------------------------------------------------
# Fail-safe: Redis caído (ramas `except` del ADR 0078)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sin_redis_el_budget_niega_el_gasto() -> None:
    """Si no se puede contar el gasto, no se gasta.

    Es el lado seguro que el docstring de `check_searches_budget` promete
    («ante la duda NO gastamos») y que ningún test ejecutaba. Un fail-OPEN aquí
    dejaría al córtex buscando sin tope cada vez que Redis se cae."""
    redis = _BrokenRedis()
    d = await check_searches_budget(
        redis, owner_user_id=_OWNER, cap=5, now=datetime(2026, 6, 24, 12, 0, tzinfo=UTC)
    )
    assert d.allowed is False
    assert d.reason == "redis_error"
    assert redis.calls == 1  # se intentó, no se saltó la comprobación


@pytest.mark.asyncio
async def test_sin_redis_la_contabilidad_no_propaga_la_excepcion() -> None:
    """La contabilidad es best-effort: el trabajo ya se hizo, no se tira encima.

    `record_searches` corre DESPUÉS de la búsqueda; que Redis se caiga en ese
    momento no puede abortar la pasada ni tumbar el beat."""
    redis = _BrokenRedis()
    assert (
        await record_searches(
            redis, owner_user_id=_OWNER, count=3, now=datetime(2026, 6, 24, 12, 0, tzinfo=UTC)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_sin_redis_el_breaker_se_da_por_abierto() -> None:
    """Breaker fail-safe: sin Redis el bucle NO corre (no actuamos a ciegas).

    Si `is_circuit_open` devolviese False ante el error, una caída de Redis
    borraría de golpe las tres salvaguardas: sin breaker, sin budget contable y
    sin freno al egress."""
    assert await is_circuit_open(_BrokenRedis(), owner_user_id=_OWNER) is True


@pytest.mark.asyncio
async def test_sin_redis_el_registro_del_breaker_es_best_effort() -> None:
    """Registrar fallo/éxito con Redis caído no levanta: solo informa que no contó."""
    assert (
        await record_failure(_BrokenRedis(), owner_user_id=_OWNER, threshold=3, cooldown_s=60)
        is False
    )
    # No devuelve nada; lo que se afirma es que NO propaga.
    assert await record_success(_BrokenRedis(), owner_user_id=_OWNER) is None
