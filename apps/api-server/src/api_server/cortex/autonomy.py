"""Córtex F4 — gobierno de los bucles cognitivos autónomos (ADR 0078).

Capa de gobierno **determinista y testeable** que envuelve el comportamiento
autónomo del córtex (curiosidad / reflexión programada / mantenimiento) con tres
salvaguardas NO negociables (ADR 0078, parte del MVP del bucle — no un fast-follow):

  1. **Kill-switch global** — el platform setting ``cortex.autonomy_enabled``
     (default OFF) que cada tarea lee al inicio de la pasada (vive en
     :mod:`api_server.db.platform_settings`, no aquí). Con OFF, ninguna tarea hace
     trabajo.
  2. **Budget caps** por ventana DIARIA en Redis (``cortex:budget:{owner}:{kind}:
     {yyyymmdd}`` con ``INCR`` + cap). Al superar el cap → no-op + log. La clave
     expira a medianoche UTC (ventana diaria que se autolimpia).
  3. **Circuit-breaker** por owner+kind (``cortex:cb:{owner}:{kind}``): tras N
     fallos consecutivos se ABRE durante ``cooldown_s`` y el bucle deja de
     intentar; un éxito resetea el contador.

Aislamiento (excepción consciente al Principio 1, ADR 0074): el córtex es
tenant-less; la **clave-por-owner** es el eje de aislamiento (un owner nunca lee la
clave de budget/breaker de otro). Reusa el namespace Redis de F2 (``cortex:*``).

Todo es **fail-open** en los callers: un fallo de Redis aquí (p.ej. Redis caído)
NUNCA debe romper la plataforma — los bucles capturan y salen no-op. Por eso las
funciones devuelven decisiones simples (``allowed`` / ``is_open``) que el caller
trata como "no autorizado / breaker cerrado" ante la duda.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Namespace Redis (reutiliza el ``cortex:*`` de F2, NO nueva infra).
_BUDGET_PREFIX = "cortex:budget"
_CIRCUIT_PREFIX = "cortex:cb"
# Sufijo del contador de fallos consecutivos del circuit-breaker.
_FAIL_SUFFIX = "fails"

#: Kinds de budget/breaker conocidos (auditoría; un kind nuevo no requiere cambio).
CURIOSITY_KIND = "curiosity"


def _yyyymmdd(now: datetime) -> str:
    """La ventana diaria UTC como ``YYYYMMDD`` (clave del budget)."""
    return now.astimezone(UTC).strftime("%Y%m%d")


def daily_budget_key(owner_user_id: str, kind: str, *, now: datetime) -> str:
    """Clave Redis del budget diario de un owner para un ``kind`` (p.ej. curiosity).

    ``cortex:budget:{owner}:{kind}:{yyyymmdd}`` — una clave por owner+kind+día, de
    modo que la ventana se resetea sola al cambiar de día UTC (la clave nueva nace a
    0) y la vieja expira por TTL."""
    return f"{_BUDGET_PREFIX}:{owner_user_id}:{kind}:{_yyyymmdd(now)}"


def circuit_key(owner_user_id: str, kind: str) -> str:
    """Clave Redis del estado ABIERTO del circuit-breaker (string + TTL=cooldown)."""
    return f"{_CIRCUIT_PREFIX}:{owner_user_id}:{kind}"


def circuit_fails_key(owner_user_id: str, kind: str) -> str:
    """Clave Redis del contador de fallos CONSECUTIVOS (se borra al primer éxito)."""
    return f"{_CIRCUIT_PREFIX}:{owner_user_id}:{kind}:{_FAIL_SUFFIX}"


def seconds_until_utc_midnight(now: datetime) -> int:
    """Segundos desde ``now`` hasta la próxima medianoche UTC (ventana diaria, ≥ 1).

    Puro y determinista. El budget pone este TTL en la clave del día para que la
    ventana se autolimpie; nunca devuelve 0 (un TTL 0 borraría la clave al instante)."""
    now_utc = now.astimezone(UTC)
    tomorrow = (now_utc + timedelta(days=1)).date()
    midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)
    return max(1, int((midnight - now_utc).total_seconds()))


@dataclass(frozen=True)
class BudgetDecision:
    """El veredicto del budget gate: ``allowed`` + la ``reason`` legible + el uso.

    ``used`` es el contador actual de la ventana; ``cap`` el tope; ``allowed`` es
    ``used < cap`` (con cap ≤ 0 ⇒ nunca permitido — curiosidad apagada de facto)."""

    allowed: bool
    reason: str
    used: int
    cap: int


async def check_searches_budget(
    redis: Any,
    *,
    owner_user_id: str,
    cap: int,
    now: datetime,
) -> BudgetDecision:
    """¿Hay budget de búsquedas para una pasada de curiosidad? (NO reserva todavía).

    Lee el contador de la ventana del día (``cortex:budget:{owner}:curiosity:{day}``)
    SIN incrementarlo: el consumo real se registra DESPUÉS de buscar con
    :func:`record_searches`. ``allowed = used < cap`` — un cap de 5 búsquedas bloquea
    la pasada cuando ya se hicieron 5 en la ventana. ``cap <= 0`` ⇒ nunca permitido.

    Best-effort: si Redis falla, devolvemos ``allowed=False`` (fail-safe del coste:
    ante la duda NO gastamos)."""
    if cap <= 0:
        return BudgetDecision(allowed=False, reason="cap_zero", used=0, cap=cap)
    key = daily_budget_key(owner_user_id, CURIOSITY_KIND, now=now)
    try:
        raw = await redis.get(key)
    except Exception:  # Redis caído ⇒ fail-safe del coste (no autorizamos gasto)
        return BudgetDecision(allowed=False, reason="redis_error", used=0, cap=cap)
    used = int(raw) if raw is not None else 0
    if used >= cap:
        return BudgetDecision(allowed=False, reason="budget_exhausted", used=used, cap=cap)
    return BudgetDecision(allowed=True, reason="ok", used=used, cap=cap)


async def record_searches(
    redis: Any,
    *,
    owner_user_id: str,
    count: int,
    now: datetime,
) -> int:
    """Suma ``count`` búsquedas al contador del día y devuelve el nuevo total.

    ``INCRBY`` + ``EXPIRE`` hasta medianoche UTC (la primera escritura del día fija
    el TTL; las siguientes lo refrescan, intrascendente). Best-effort: un fallo de
    Redis devuelve 0 (la métrica de budget es secundaria al trabajo ya hecho)."""
    if count <= 0:
        return 0
    key = daily_budget_key(owner_user_id, CURIOSITY_KIND, now=now)
    try:
        new_total = int(await redis.incrby(key, count))
        await redis.expire(key, seconds_until_utc_midnight(now))
        return new_total
    except Exception:  # contabilidad best-effort; el trabajo ya se hizo
        return 0


# ---------------------------------------------------------------------------
# Circuit-breaker (por owner + kind)
# ---------------------------------------------------------------------------
async def is_circuit_open(redis: Any, *, owner_user_id: str, kind: str = CURIOSITY_KIND) -> bool:
    """¿Está ABIERTO el circuit-breaker de este owner+kind? (existe la clave abierta).

    Mientras está abierto (TTL=cooldown), el bucle NO debe correr. Best-effort: si
    Redis falla devolvemos ``True`` (fail-safe: ante la duda NO actuamos
    autónomamente, que es el lado seguro para coste/egress)."""
    try:
        return bool(await redis.exists(circuit_key(owner_user_id, kind)))
    except Exception:  # Redis caído ⇒ fail-safe: tratamos el breaker como abierto
        return True


async def record_failure(
    redis: Any,
    *,
    owner_user_id: str,
    threshold: int,
    cooldown_s: int,
    kind: str = CURIOSITY_KIND,
) -> bool:
    """Registra un fallo CONSECUTIVO; ABRE el breaker al alcanzar ``threshold``.

    Incrementa el contador de fallos; si llega a ``threshold`` setea la clave ABIERTA
    con TTL=``cooldown_s`` y resetea el contador (el próximo ciclo arranca limpio tras
    el cooldown). Devuelve ``True`` si el breaker quedó abierto en esta llamada.
    Best-effort: un fallo de Redis devuelve ``False`` (no pudimos contabilizar)."""
    fails_key = circuit_fails_key(owner_user_id, kind)
    try:
        fails = int(await redis.incr(fails_key))
        # El contador no debe vivir para siempre: lo atamos a un día (si la curiosidad
        # falla esporádicamente a lo largo de semanas, no queremos que esos fallos
        # cuenten como "consecutivos"; el TTL los olvida).
        await redis.expire(fails_key, 24 * 3600)
        if fails >= max(1, threshold):
            await redis.set(circuit_key(owner_user_id, kind), "open", ex=max(1, cooldown_s))
            await redis.delete(fails_key)
            return True
        return False
    except Exception:  # contabilidad best-effort
        return False


async def record_success(redis: Any, *, owner_user_id: str, kind: str = CURIOSITY_KIND) -> None:
    """Resetea el contador de fallos consecutivos (un éxito limpia la racha).

    NO toca la clave ABIERTA (si el breaker está abierto, sigue abierto hasta que
    expire el cooldown). Best-effort: un fallo de Redis se ignora."""
    try:
        await redis.delete(circuit_fails_key(owner_user_id, kind))
    except Exception:  # best-effort
        return


__all__ = [
    "CURIOSITY_KIND",
    "BudgetDecision",
    "check_searches_budget",
    "circuit_fails_key",
    "circuit_key",
    "daily_budget_key",
    "is_circuit_open",
    "record_failure",
    "record_searches",
    "record_success",
    "seconds_until_utc_midnight",
]
