"""Particiones futuras de las tablas append-only — ``workers.ensure_partitions``.

part-01 · task_part01_02 (ADR 0151, opción C).

El modo de fallo que este módulo existe para impedir
-----------------------------------------------------
Una tabla ``PARTITION BY RANGE (created_at)`` **rechaza** una fila cuya fecha no
cae en ninguna partición existente::

    no partition of relation "guardrail_events" found for row

Es decir: el día 1 del mes que viene, a las 00:00, la primera escritura falla —y
con ella el run que la produjo— si nadie creó la partición de ese mes. El ADR 0151
lo dice con todas las letras: *«hace falta el job que cree la partición del mes
siguiente. Sin él, la primera inserción del mes que viene falla. Es el modo de
fallo que convierte esta decisión en un incidente, y tiene que llevar su propia
alerta»*.

Tres decisiones de diseño que conviene leer antes de tocar nada
--------------------------------------------------------------
1. **Tres meses de colchón, no uno.** El beat corre a diario, así que con un solo
   mes por delante bastaría… mientras el beat funcione. :data:`PARTITION_HEADROOM_MONTHS`
   pone tres para que el sistema aguante un job parado varias semanas sin que
   nadie pierda una escritura. Cuesta tres tablas vacías por tabla convertida.

2. **NO hay partición ``DEFAULT``, a propósito.** Una ``DEFAULT`` capturaría las
   filas sin partición y evitaría el error… convirtiéndolo en algo peor: las filas
   quedan en el cajón de sastre y después **impiden crear la partición correcta**
   (PostgreSQL escanea la ``DEFAULT`` al hacer ``ATTACH`` y rechaza el enganche si
   alguna fila pertenecería a la nueva). Un fallo ruidoso e inmediato que se
   arregla en un minuto es mejor que un fallo silencioso que hay que desenredar a
   mano. Lo que sustituye a la ``DEFAULT`` es el colchón + la alerta.

3. **La alerta salta en cuanto falta M+1, no a mitad de mes.** El ADR pide que
   «si la partición de M+1 no existe a mitad de mes alguien debe enterarse»; esto
   es estrictamente más pronto, y la razón es que esperar al día 15 gasta media
   ventana de seguridad sin comprar nada. Como el job crea M+1 en su primera
   pasada, la única forma de que falte al terminar es que la creación fallara: eso
   ya es la señal, no hay que esperar a la fecha.

Cada partición nace con su RLS
------------------------------
La RLS de una tabla particionada **se declara por partición** (ADR 0151, coste
operativo nº 4). Al acceder por el padre se aplica la policy del padre, pero una
consulta directa contra ``guardrail_events_2026_09`` solo pasa por las policies de
*esa* relación: una partición sin policy sería una puerta lateral al aislamiento
entre tenants. Por eso :meth:`SqlPartitionStore.create` hace ``ENABLE`` + ``FORCE``
+ ``CREATE POLICY`` **en la misma transacción** que el ``CREATE TABLE``: o nace
entera y protegida, o no nace.

Los índices, en cambio, NO hay que repetirlos: PostgreSQL crea en cada partición
nueva el índice equivalente de cada índice del padre (``CREATE TABLE … PARTITION
OF`` los propaga). Los declara la migración sobre el padre y ya está.

Por qué conecta con el DSN de backup y no con el del worker
-----------------------------------------------------------
``settings.database_url`` es ``service_user``: **BYPASSRLS pero SIN DDL**
(prod-14 task_05 / tenancy-2). No puede crear una tabla. El DSN admin ya existe
para el ``pg_dump`` (``backup_database_url``, rol ``migrations_user``, dueño del
esquema) y se **reutiliza** aquí, igual que hace el restore por tenant con su
copiado cross-tenant: *una credencial reutilizada, nunca una segunda*. De ahí
también que la entrada de beat vaya a la cola ``privileged``, la única cuyo pool
(``workers-backup``) tiene ese DSN configurado.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

import structlog

from workers.celery_app import app

_log = structlog.get_logger("workers.maintenance")

__all__ = [
    "ALERT_NAME",
    "PARTITIONED_TABLES",
    "PARTITION_HEADROOM_MONTHS",
    "PartitionSpec",
    "PartitionStore",
    "SqlPartitionStore",
    "add_months",
    "alert_event",
    "coverage_gap",
    "ensure_partitions",
    "ensure_partitions_task",
    "missing_partitions",
    "month_start",
    "partition_name",
    "partition_statements",
    "required_partitions",
]

#: Meses creados por delante del actual. Ver decisión 1 del docstring.
PARTITION_HEADROOM_MONTHS = 3

#: Las tablas ya convertidas a ``PARTITION BY RANGE (created_at)``. Crece **una
#: por ola** del plan part-01, en el mismo commit que su migración. La guarda
#: `tests/unit/test_partition_planner.py::test_every_partitioned_model_is_in_the_job_registry`
#: compara esta tupla con lo que declara el modelo ORM, así que una conversión
#: que se olvide de registrarse aquí rompe la suite en vez de romper el mes que
#: viene en producción.
PARTITIONED_TABLES: tuple[str, ...] = (
    "guardrail_events",  # migración 0131 (ola 1)
    "notification_logs",  # migración 0134 (ola 2)
    "llm_usage_events",  # migración 0135 (ola 3)
    "audit_log",  # migración 0136 (ola 4)
    "executions",  # migración 0137 (ola 5) — las CINCO del ADR 0151
)

#: `event_type` REUTILIZADO: `infra_alert` ya está registrado en el dispatcher
#: (`event_mapping.py`), es platform-scoped, va por la lane PRIORITY y sale por
#: in-app + telegram, con plantillas ES/EN. Una partición que falta es
#: infraestructura, igual que el disco lleno o el backup caído; inventar un
#: `event_type` nuevo habría exigido plantillas nuevas en los dos idiomas para
#: decir lo mismo.
ALERT_EVENT_TYPE = "infra_alert"
ALERT_NAME = "PartitionCoverageMissing"

#: El predicado de aislamiento por tenant, LITERALMENTE el de la migración 0052
#: (y el de 0001 / 0041 / 0045). El `NULLIF(..., '')` convierte en NULL la cadena
#: vacía que devuelve un GUC sin fijar, así que una sesión sin `app.tenant_id`
#: casa con cero filas — por defecto seguro.
_TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

#: Identificador SQL admisible. Todo lo que se interpola en el DDL de abajo sale
#: de :data:`PARTITIONED_TABLES` o de :func:`partition_name`, nunca de una
#: request; esta guarda está para que siga siendo verdad si alguien cambia eso.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,55}$")


@dataclass(frozen=True)
class PartitionSpec:
    """Una partición mensual: su nombre y su rango medio-abierto ``[start, end)``."""

    table: str
    name: str
    start: date
    end: date


class PartitionStore(Protocol):
    """Lo único que :func:`ensure_partitions` necesita de la base de datos."""

    async def existing(self, table: str) -> set[str]: ...

    async def create(self, spec: PartitionSpec) -> None: ...


class PartitionNotifier(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


# ---------------------------------------------------------------------------
# Núcleo puro: aritmética de calendario y qué falta
# ---------------------------------------------------------------------------
def month_start(moment: datetime | date) -> date:
    """El día 1 del mes de ``moment``."""
    return date(moment.year, moment.month, 1)


def add_months(start: date, months: int) -> date:
    """``start`` desplazado ``months`` meses, sobre el día 1.

    Se hace con aritmética de meses absolutos (``year*12 + month``) en vez de
    sumar días: diciembre + 1 mes es enero del año siguiente, y un ``timedelta``
    no sabe eso.
    """
    total = (start.year * 12 + (start.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def partition_name(table: str, first_of_month: date) -> str:
    """``guardrail_events`` + 2026-08-01 → ``guardrail_events_2026_08``."""
    return f"{table}_{first_of_month.year:04d}_{first_of_month.month:02d}"


def required_partitions(
    table: str,
    *,
    today: datetime | date,
    headroom: int = PARTITION_HEADROOM_MONTHS,
) -> list[PartitionSpec]:
    """Las particiones que deben existir hoy: el mes en curso y ``headroom`` más."""
    current = month_start(today)
    specs: list[PartitionSpec] = []
    for offset in range(headroom + 1):
        start = add_months(current, offset)
        end = add_months(current, offset + 1)
        specs.append(
            PartitionSpec(table=table, name=partition_name(table, start), start=start, end=end)
        )
    return specs


def missing_partitions(
    specs: Iterable[PartitionSpec], existing: Iterable[str]
) -> list[PartitionSpec]:
    """Las de ``specs`` que no están en ``existing``, en orden cronológico."""
    known = set(existing)
    return [spec for spec in specs if spec.name not in known]


def coverage_gap(table: str, *, today: datetime | date, existing: Iterable[str]) -> str | None:
    """El nombre de la partición de **M+1** si falta; ``None`` si está.

    Se vigila M+1 y no el mes en curso a propósito: si falta el mes en curso ya
    no hay alerta que dar, hay un incidente en marcha, y lo delata el propio
    error de la inserción. Lo que una alerta puede todavía evitar es el del mes
    que viene.
    """
    following = add_months(month_start(today), 1)
    name = partition_name(table, following)
    return None if name in set(existing) else name


def alert_event(gaps: dict[str, str]) -> dict[str, Any]:
    """El `infra_alert` platform-scoped que describe los huecos de cobertura.

    UNA notificación con todos los huecos, no una por tabla: con cinco tablas
    convertidas, el fallo típico (la credencial admin caducada, el disco lleno)
    las tumba a la vez, y cinco avisos idénticos son cuatro de ruido.
    """
    detalle = ", ".join(f"{table} → {name}" for table, name in sorted(gaps.items()))
    return {
        "event_type": ALERT_EVENT_TYPE,
        # Platform-scoped: el esquema es de la plataforma, no de un tenant.
        "tenant_id": None,
        "context": {
            "alertname": ALERT_NAME,
            "severity": "critical",
            "status": "firing",
            "instance": "postgres",
            "summary": (
                f"Falta la partición del mes que viene en {len(gaps)} tabla(s) append-only"
            ),
            "description": (
                f"No existe la partición del mes siguiente para: {detalle}. "
                "La primera inserción de ese mes fallará con «no partition of "
                "relation found for row». Crea la partición a mano (ver el runbook "
                "de particionado) y averigua por qué falló workers.ensure_partitions."
            ),
        },
    }


async def ensure_partitions(
    store: PartitionStore,
    notifier: PartitionNotifier,
    *,
    tables: Sequence[str] = PARTITIONED_TABLES,
    now: datetime | None = None,
    headroom: int = PARTITION_HEADROOM_MONTHS,
) -> dict[str, Any]:
    """Una pasada: crea lo que falte en cada tabla y avisa si M+1 sigue sin estar.

    Best-effort **por partición y por tabla**: con cinco tablas convertidas, que
    una reviente (un lock, una carrera con otra pasada) no puede dejar a las otras
    cuatro sin cobertura. Lo que no se crea acaba contado en ``gaps``, que es lo
    que dispara la alerta — así un fallo nunca se queda callado.
    """
    moment = now or datetime.now(UTC)
    created: list[str] = []
    gaps: dict[str, str] = {}

    for table in tables:
        try:
            present = await store.existing(table)
        except Exception as exc:
            _log.warning("ensure_partitions.introspection_failed", table=table, error=str(exc))
            # Sin saber qué hay no se puede decidir nada: se anota como hueco
            # para que la alerta lo diga en vez de fingir cobertura.
            gaps[table] = partition_name(table, add_months(month_start(moment), 1))
            continue

        specs = required_partitions(table, today=moment, headroom=headroom)
        for spec in missing_partitions(specs, present):
            try:
                await store.create(spec)
            except Exception as exc:
                _log.warning(
                    "ensure_partitions.create_failed",
                    table=table,
                    partition=spec.name,
                    error=str(exc),
                )
                continue
            created.append(spec.name)
            present.add(spec.name)

        gap = coverage_gap(table, today=moment, existing=present)
        if gap is not None:
            gaps[table] = gap

    if gaps:
        notifier.publish(alert_event(gaps))
        _log.error("ensure_partitions.coverage_gap", gaps=gaps)

    report: dict[str, Any] = {
        "tables": len(tables),
        "created": created,
        "gaps": gaps,
        "alerted": bool(gaps),
    }
    if created or gaps:
        _log.info("ensure_partitions.done", **report)
    return report


# ---------------------------------------------------------------------------
# Cableado real: el DDL
# ---------------------------------------------------------------------------
def _checked(identifier: str) -> str:
    if not _IDENTIFIER.match(identifier):
        raise ValueError(f"identificador SQL no admisible: {identifier!r}")
    return identifier


def partition_statements(spec: PartitionSpec) -> tuple[str, ...]:
    """El DDL de una partición: crearla y dejarla protegida, en ese orden.

    Se devuelve como tupla de sentencias (en vez de ejecutarlas aquí) para que el
    orden sea inspeccionable y el llamante las pueda meter en UNA transacción: una
    partición a medio hacer —creada pero sin policy— sería visible sin aislamiento
    entre tenants durante la ventana.

    ``DROP POLICY IF EXISTS`` antes del ``CREATE`` porque PostgreSQL no tiene
    ``CREATE POLICY IF NOT EXISTS``; sobre una partición ya existente eso deja una
    ventana sin policy que es **fail-closed** (RLS activa y sin ninguna policy
    niega todo), no fail-open.
    """
    table = _checked(spec.table)
    name = _checked(spec.name)
    policy = f"{name}_tenant_isolation"
    return (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table}"
        f" FOR VALUES FROM ('{spec.start.isoformat()}') TO ('{spec.end.isoformat()}')",
        f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {policy} ON {name}",
        f"CREATE POLICY {policy} ON {name} FOR ALL"
        f" USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})",
    )


_EXISTING_SQL = """
SELECT child.relname
  FROM pg_inherits
  JOIN pg_class child ON child.oid = pg_inherits.inhrelid
  JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
 WHERE parent.relname = :table
   AND parent.relnamespace = 'public'::regnamespace
"""


class SqlPartitionStore:
    """:class:`PartitionStore` sobre un engine SQLAlchemy async.

    El engine tiene que llevar el DSN **admin** (dueño del esquema): crear una
    partición es DDL, y el rol del worker no lo tiene (ver el docstring del
    módulo).
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def existing(self, table: str) -> set[str]:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            rows = await conn.execute(text(_EXISTING_SQL), {"table": _checked(table)})
            return {str(row[0]) for row in rows.all()}

    async def create(self, spec: PartitionSpec) -> None:
        """Crea la partición y su RLS en UNA transacción (o nada)."""
        async with self._engine.begin() as conn:
            for statement in partition_statements(spec):
                await conn.exec_driver_sql(statement)


def _admin_database_url(settings: Any) -> str:
    """El DSN admin en forma SQLAlchemy.

    ``backup_database_url`` es libpq (``postgresql://``) porque lo consume
    ``pg_dump``; SQLAlchemy necesita el driver explícito. Misma conversión, y por
    el mismo motivo, que hace el restore por tenant.
    """
    raw = str(settings.backup_database_url)
    if raw.startswith("postgresql+"):
        return raw
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


@app.task(name="workers.ensure_partitions")  # type: ignore[untyped-decorator]
def ensure_partitions_task() -> dict[str, Any]:
    """Entrada Celery (beat diario). Best-effort: nunca rompe el beat.

    Un fallo TOTAL de la pasada (la BD no responde, el DSN admin no vale) sale por
    el log y devuelve el reporte vacío. No se convierte en alerta desde aquí a
    propósito: si no hemos podido ni conectar, no sabemos si falta la partición, y
    una alerta que dice «falta» cuando en realidad dice «no lo sé» enseña a
    ignorarla.
    """
    from workers.config import get_settings

    settings = get_settings()

    async def _main() -> dict[str, Any]:
        from workers.db import worker_engine
        from workers.standup import CeleryStandupNotifier

        engine = worker_engine(url=_admin_database_url(settings))
        try:
            return await ensure_partitions(
                SqlPartitionStore(engine),
                CeleryStandupNotifier(broker_url=settings.broker_url),
                tables=PARTITIONED_TABLES,
                now=datetime.now(tz=UTC),
            )
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_main())
    except Exception as exc:
        _log.exception("ensure_partitions.run_failed", error=str(exc))
        return {"tables": 0, "created": [], "gaps": {}, "alerted": False}
