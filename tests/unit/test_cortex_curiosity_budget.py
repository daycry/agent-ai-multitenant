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

**Dimensión de coste USD** (añadida el 2026-07-30, cerrando la auditoría del
2026-07-27): `check_and_reserve` mira las DOS dimensiones —búsquedas y dólares— y
`record_spend` acumula ambas. Contar búsquedas acota el egress pero no el gasto:
una sola pasada con razonamiento profundo (`claude_sdk` + WebSearch nativa, ADR
0076) puede costar más que veinte búsquedas baratas, así que un tope de nº de
búsquedas NO es un tope de coste. La forma de la clave (una string por día y
dimensión en vez del hash `cortex:budget:{owner}` del plan) es una divergencia
aceptada: el TTL por-día se autolimpia y ya tenía consumidores.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_server.cortex.autonomy import (
    CURIOSITY_KIND,
    CURIOSITY_USD_KIND,
    check_and_reserve,
    check_searches_budget,
    daily_budget_key,
    is_circuit_open,
    read_budget_usage,
    record_failure,
    record_searches,
    record_spend,
    record_success,
    seconds_until_utc_midnight,
)

pytestmark = pytest.mark.unit

_OWNER = "11111111-1111-1111-1111-111111111111"


class _FakeRedis:
    """Redis en memoria con las 5 operaciones del budget, y un log de comandos.

    ``calls`` guarda ``(comando, clave)`` en orden para poder afirmar QUÉ tocó
    cada función (leer vs. escribir), no solo el resultado."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        raw = self.store.get(key)
        return None if raw is None else str(raw)

    async def incrby(self, key: str, amount: int) -> int:
        self.calls.append(("incrby", key))
        self.store[key] = self.store.get(key, 0) + int(amount)
        return int(self.store[key])

    async def incrbyfloat(self, key: str, amount: float) -> str:
        # Redis devuelve el nuevo total como STRING en INCRBYFLOAT: el doble lo
        # imita para que el parseo del código bajo prueba se ejercite de verdad.
        self.calls.append(("incrbyfloat", key))
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return str(self.store[key])

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
    incrbyfloat = _boom
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


# ---------------------------------------------------------------------------
# La dimensión de COSTE (USD) — la que faltaba entera (auditoría 2026-07-27)
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_bajo_los_dos_topes_el_gate_permite_y_reporta_ambos_usos() -> None:
    """Con búsquedas y dólares por debajo del cap, `allowed` y los dos contadores.

    El gate tiene que exponer AMBAS dimensiones en su veredicto: el panel enseña
    "0.12 USD de 0.50" y el bucle registra el porqué del skip. Un `BudgetDecision`
    que solo llevara búsquedas dejaría el coste invisible, que es justamente el
    defecto que se está cerrando (la columna `cost_usd` era siempre 0)."""
    redis = _FakeRedis()
    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.12, searches=2, now=_NOW)

    d = await check_and_reserve(redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=_NOW)
    assert d.allowed is True
    assert d.reason == "ok"
    assert d.used == 2
    assert d.cap == 5
    assert d.used_usd == pytest.approx(0.12)
    assert d.cap_usd == pytest.approx(0.50)


@pytest.mark.asyncio
async def test_el_cap_de_dolares_bloquea_aunque_queden_busquedas() -> None:
    """Gastado el cap en USD, no importa que sobren búsquedas: no se busca.

    Es la razón de existir de esta dimensión. Una pasada con razonamiento
    profundo (claude_sdk + WebSearch nativa, ADR 0076) puede costar más que
    veinte búsquedas baratas: con SOLO el tope de nº de búsquedas, el gasto real
    no tenía techo. El `reason` es propio (`usd_budget_exhausted`) para que el
    pursuit 'skipped' diga la verdad sobre por qué no salió."""
    redis = _FakeRedis()
    # Una sola búsqueda, pero caras: 0.60 USD > cap 0.50.
    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.60, searches=1, now=_NOW)

    d = await check_and_reserve(redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=_NOW)
    assert d.allowed is False
    assert d.reason == "usd_budget_exhausted"
    assert d.used == 1  # quedaban 4 búsquedas de budget…
    assert d.used_usd == pytest.approx(0.60)  # …pero el dinero se acabó


@pytest.mark.asyncio
async def test_el_cap_de_busquedas_sigue_bloqueando_con_dinero_de_sobra() -> None:
    """Simétrico: agotadas las búsquedas, tener saldo no habilita más egress.

    Las dos dimensiones son AND, no OR. Sin este caso, un refactor que mirase
    solo el dinero (más "moderno") retiraría el freno al egress sin que nada
    fallase — el cap de búsquedas es el que protege del abuso del proveedor de
    búsqueda, no del gasto."""
    redis = _FakeRedis()
    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.0, searches=5, now=_NOW)

    d = await check_and_reserve(redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=_NOW)
    assert d.allowed is False
    assert d.reason == "budget_exhausted"
    assert d.used_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_un_cap_de_cero_dolares_apaga_la_curiosidad_de_facto() -> None:
    """`usd_cap <= 0` ⇒ nunca permitido, igual que el cap de búsquedas.

    Se elige la MISMA semántica que la dimensión hermana (`cap_zero`) a
    propósito: "no gastes" se lee como "no actúes", no como "sin tope". El
    reason distingue la dimensión para que el panel pueda explicarlo."""
    redis = _FakeRedis()
    d = await check_and_reserve(redis, owner_user_id=_OWNER, usd_cap=0.0, searches_cap=5, now=_NOW)
    assert d.allowed is False
    assert d.reason == "usd_cap_zero"
    # Y no hizo falta ni consultar Redis para negarlo (fail-safe barato).
    assert redis.calls == []


@pytest.mark.asyncio
async def test_consultar_no_gasta_en_ninguna_de_las_dos_dimensiones() -> None:
    """`check_and_reserve` NO reserva: el plan lo dice y el nombre engaña.

    El nombre viene del plan («check_and_reserve»), pero la reserva real ocurre
    DESPUÉS de la búsqueda con `record_spend` — si consultar incrementase, cada
    pasada que muere más adelante (drive gate, sin tema, approval gate) quemaría
    budget que nunca se usó y la curiosidad se apagaría sola."""
    redis = _FakeRedis()
    for _ in range(5):
        d = await check_and_reserve(
            redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=_NOW
        )
        assert d.allowed is True
    assert {cmd for cmd, _ in redis.calls} == {"get"}
    assert redis.store == {}


@pytest.mark.asyncio
async def test_record_spend_acumula_y_pone_ttl_hasta_medianoche_en_ambas_claves() -> None:
    """El gasto se ACUMULA en la ventana y muere con ella (las dos dimensiones).

    Sin TTL, el gasto de ayer toparía el cap de hoy para siempre; con un TTL
    distinto en cada dimensión, una de las dos ventanas se desalinearía."""
    redis = _FakeRedis()
    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.10, searches=1, now=_NOW)
    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.05, searches=2, now=_NOW)

    searches, usd = await read_budget_usage(redis, owner_user_id=_OWNER, now=_NOW)
    assert searches == 3
    assert usd == pytest.approx(0.15)

    usd_key = daily_budget_key(_OWNER, CURIOSITY_USD_KIND, now=_NOW)
    searches_key = daily_budget_key(_OWNER, CURIOSITY_KIND, now=_NOW)
    assert redis.ttls[usd_key] == seconds_until_utc_midnight(_NOW)
    assert redis.ttls[searches_key] == seconds_until_utc_midnight(_NOW)
    # Claves DISTINTAS: mezclarlas haría que 3 búsquedas contasen como 3 dólares.
    assert usd_key != searches_key


@pytest.mark.asyncio
async def test_record_spend_con_coste_cero_no_crea_la_clave_de_dolares() -> None:
    """Una pasada gratis (Ollama local) no debe dejar una clave de gasto a 0.

    El proveedor local no cuesta nada (ADR 0021: «la factura de la GPU es del
    operador»), así que el camino habitual del stack de desarrollo pasa por aquí:
    debe contar la búsqueda y NO tocar la dimensión de dinero."""
    redis = _FakeRedis()
    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.0, searches=1, now=_NOW)

    assert daily_budget_key(_OWNER, CURIOSITY_USD_KIND, now=_NOW) not in redis.store
    assert redis.store[daily_budget_key(_OWNER, CURIOSITY_KIND, now=_NOW)] == 1


@pytest.mark.asyncio
async def test_la_ventana_de_dolares_tambien_se_resetea_a_medianoche() -> None:
    """El gasto del día D no cuenta contra el cap del día D+1.

    Misma garantía que ya tenía la dimensión de búsquedas, sobre la otra clave:
    si la clave del gasto no llevase el día, un despliegue que gastase su cap una
    vez dejaría la curiosidad muerta para siempre."""
    redis = _FakeRedis()
    ultima_hora = datetime(2026, 6, 24, 23, 59, tzinfo=UTC)
    dia_siguiente = datetime(2026, 6, 25, 0, 1, tzinfo=UTC)

    await record_spend(redis, owner_user_id=_OWNER, cost_usd=0.60, searches=1, now=ultima_hora)
    agotado = await check_and_reserve(
        redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=ultima_hora
    )
    assert agotado.allowed is False

    manana = await check_and_reserve(
        redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=dia_siguiente
    )
    assert manana.allowed is True
    assert manana.used_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_un_contador_de_dolares_corrupto_no_tumba_el_gate() -> None:
    """Un valor no numérico en la clave del gasto se lee como 0, no levanta.

    El bucle corre sin nadie mirando: una clave pisada por otro proceso (o un
    `SET` manual de operador) no puede convertirse en una excepción dentro de
    beat. Se lee 0 —el lado que NO bloquea la curiosidad legítima— porque el cap
    de búsquedas sigue estando ahí como segunda barrera."""
    redis = _FakeRedis()
    redis.store[daily_budget_key(_OWNER, CURIOSITY_USD_KIND, now=_NOW)] = "basura"  # type: ignore[assignment]

    d = await check_and_reserve(redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=_NOW)
    assert d.allowed is True
    assert d.used_usd == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_sin_redis_el_gate_de_dos_dimensiones_niega_el_gasto() -> None:
    """Fail-safe del coste también en el gate nuevo: sin Redis no se gasta."""
    redis = _BrokenRedis()
    d = await check_and_reserve(redis, owner_user_id=_OWNER, usd_cap=0.50, searches_cap=5, now=_NOW)
    assert d.allowed is False
    assert d.reason == "redis_error"


@pytest.mark.asyncio
async def test_sin_redis_record_spend_no_propaga() -> None:
    """La contabilidad del gasto es best-effort: corre DESPUÉS de gastar."""
    assert (
        await record_spend(_BrokenRedis(), owner_user_id=_OWNER, cost_usd=1.0, searches=1, now=_NOW)
        is None
    )
