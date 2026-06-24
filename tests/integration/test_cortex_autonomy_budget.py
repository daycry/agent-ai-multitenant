"""Córtex F4 — budget gate + circuit-breaker contra el Redis de test (DB 15).

Salvaguardas del MVP del bucle (ADR 0078):

  * **Budget cap diario** de búsquedas: bajo el cap → ``allowed``; al alcanzarlo →
    ``not allowed`` con reason; ``record_searches`` acumula y fija TTL; un cap ≤ 0
    nunca permite (curiosidad apagada de facto); **cross-owner** (un owner no
    consume el budget de otro).
  * **Circuit-breaker**: N fallos consecutivos lo ABREN con TTL=cooldown; un éxito
    intermedio resetea el contador; cross-owner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from api_server.cortex.autonomy import (
    CURIOSITY_KIND,
    check_searches_budget,
    circuit_key,
    daily_budget_key,
    is_circuit_open,
    record_failure,
    record_searches,
    record_success,
)
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture()
async def redis_client(test_redis_url: str):
    client: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Budget cap
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_budget_allows_under_cap_then_blocks_at_cap(redis_client: Redis) -> None:
    owner = str(uuid4())
    cap = 5

    # Empieza vacío → permitido.
    d = await check_searches_budget(redis_client, owner_user_id=owner, cap=cap, now=_NOW)
    assert d.allowed is True
    assert d.used == 0

    # Consume 5 búsquedas (== cap) repartidas en pasadas.
    assert await record_searches(redis_client, owner_user_id=owner, count=3, now=_NOW) == 3
    assert await record_searches(redis_client, owner_user_id=owner, count=2, now=_NOW) == 5

    # La 6ª está bloqueada: used >= cap.
    d = await check_searches_budget(redis_client, owner_user_id=owner, cap=cap, now=_NOW)
    assert d.allowed is False
    assert d.reason == "budget_exhausted"
    assert d.used == 5


@pytest.mark.asyncio
async def test_budget_sets_ttl_until_midnight(redis_client: Redis) -> None:
    owner = str(uuid4())
    await record_searches(redis_client, owner_user_id=owner, count=1, now=_NOW)
    key = daily_budget_key(owner, CURIOSITY_KIND, now=_NOW)
    ttl = await redis_client.ttl(key)
    # 12:00 UTC → 12h hasta medianoche; TTL positivo y ≤ 12h.
    assert 0 < ttl <= 12 * 3600


@pytest.mark.asyncio
async def test_budget_cap_zero_never_allows(redis_client: Redis) -> None:
    owner = str(uuid4())
    d = await check_searches_budget(redis_client, owner_user_id=owner, cap=0, now=_NOW)
    assert d.allowed is False
    assert d.reason == "cap_zero"


@pytest.mark.asyncio
async def test_budget_is_per_owner(redis_client: Redis) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    # A agota su budget.
    await record_searches(redis_client, owner_user_id=owner_a, count=5, now=_NOW)
    da = await check_searches_budget(redis_client, owner_user_id=owner_a, cap=5, now=_NOW)
    db = await check_searches_budget(redis_client, owner_user_id=owner_b, cap=5, now=_NOW)
    assert da.allowed is False
    # B sigue con su budget intacto (aislamiento por owner).
    assert db.allowed is True
    assert db.used == 0


# ---------------------------------------------------------------------------
# Circuit-breaker
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_circuit_opens_after_n_consecutive_failures(redis_client: Redis) -> None:
    owner = str(uuid4())
    threshold = 3
    cooldown = 1800

    assert await is_circuit_open(redis_client, owner_user_id=owner) is False

    # Dos fallos: aún cerrado.
    assert (
        await record_failure(
            redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=cooldown
        )
        is False
    )
    assert (
        await record_failure(
            redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=cooldown
        )
        is False
    )
    assert await is_circuit_open(redis_client, owner_user_id=owner) is False

    # Tercer fallo: ABRE.
    opened = await record_failure(
        redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=cooldown
    )
    assert opened is True
    assert await is_circuit_open(redis_client, owner_user_id=owner) is True

    # El TTL del breaker abierto es el cooldown.
    ttl = await redis_client.ttl(circuit_key(owner, CURIOSITY_KIND))
    assert 0 < ttl <= cooldown


@pytest.mark.asyncio
async def test_success_resets_failure_streak(redis_client: Redis) -> None:
    owner = str(uuid4())
    threshold = 3

    # Dos fallos, luego un éxito → la racha se reinicia.
    await record_failure(redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=60)
    await record_failure(redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=60)
    await record_success(redis_client, owner_user_id=owner)

    # Dos fallos más NO abren (la racha venía de 0 tras el éxito).
    await record_failure(redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=60)
    opened = await record_failure(
        redis_client, owner_user_id=owner, threshold=threshold, cooldown_s=60
    )
    assert opened is False
    assert await is_circuit_open(redis_client, owner_user_id=owner) is False


@pytest.mark.asyncio
async def test_circuit_breaker_is_per_owner(redis_client: Redis) -> None:
    owner_a = str(uuid4())
    owner_b = str(uuid4())
    for _ in range(3):
        await record_failure(redis_client, owner_user_id=owner_a, threshold=3, cooldown_s=60)
    assert await is_circuit_open(redis_client, owner_user_id=owner_a) is True
    # B no se ve afectado por los fallos de A.
    assert await is_circuit_open(redis_client, owner_user_id=owner_b) is False
