"""prod-07 `task_prod07_15` (llm-10) — la muerte del Memorizer deja de ser muda.

El modo de fallo que cierra este contador: la destilación se llama en
`best-effort` desde el final del run, y **cualquier** excepción suya se traga
para no tumbar el pipeline. Eso es correcto y es justo lo que la vuelve
invisible: el run termina `ok`, no se persiste ni una memoria, y la única
huella es un `memorize_skip_reason` en la fila de esa ejecución — un dato que
hay que ir a buscar sabiendo ya que hay un problema.

El contador vive en Redis (el broker, que el worker ya tiene abierto) por la
misma razón que `task_metrics`: el pool prefork tiene N procesos y un contador
en memoria contaría lo de un hijo al azar. Es **consecutivo**, no acumulado:
lo que hay que alertar no es «han fallado 40 destilaciones desde el lunes»,
es «las últimas 5 seguidas han fallado» — un proveedor caído AHORA. Por eso
un éxito lo borra.
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.memorizer_metrics import (
    DISTILL_FAILURES_KEY,
    is_distillation_failure,
    record_distillation_outcome,
)


class _FakeRedis:
    """El mínimo de la interfaz async de redis que usa el contador."""

    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, int] = {}
        self.broken = broken

    async def incr(self, key: str) -> int:
        if self.broken:
            raise ConnectionError("redis caído")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def delete(self, key: str) -> int:
        if self.broken:
            raise ConnectionError("redis caído")
        return int(self.store.pop(key, None) is not None)

    async def get(self, key: str) -> Any:
        if self.broken:
            raise ConnectionError("redis caído")
        value = self.store.get(key)
        return None if value is None else str(value).encode()


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        ("llm_error", True),
        ("llm_unparseable", True),
        # `llm_empty` NO es un fallo: el proveedor contestó bien y esa ejecución
        # simplemente no tenía nada que memorizar. Contarlo como fallo haría
        # saltar la alerta en un sistema sano y enseñaría a ignorarla.
        ("llm_empty", False),
        (None, False),
    ],
)
def test_only_a_provider_failure_counts(cause: str | None, expected: bool) -> None:
    assert is_distillation_failure(cause) is expected


@pytest.mark.asyncio
async def test_consecutive_failures_accumulate() -> None:
    redis = _FakeRedis()
    for expected in (1, 2, 3):
        assert await record_distillation_outcome(redis, ok=False) == expected
    assert redis.store[DISTILL_FAILURES_KEY] == 3


@pytest.mark.asyncio
async def test_a_success_clears_the_streak() -> None:
    """Consecutivos, no acumulados: si no se borrara, el contador crecería para
    siempre y la alerta se quedaría encendida tras arreglar el proveedor."""
    redis = _FakeRedis()
    await record_distillation_outcome(redis, ok=False)
    await record_distillation_outcome(redis, ok=False)

    assert await record_distillation_outcome(redis, ok=True) == 0
    assert DISTILL_FAILURES_KEY not in redis.store


@pytest.mark.asyncio
async def test_a_broken_redis_never_breaks_the_memorizer() -> None:
    """Emitir una métrica NUNCA puede tumbar el trabajo real: el contador es la
    última pieza de una tarea best-effort, y si él propagase se convertiría en
    la causa del fallo que venía a delatar."""
    redis = _FakeRedis(broken=True)
    assert await record_distillation_outcome(redis, ok=False) is None
    assert await record_distillation_outcome(redis, ok=True) is None
