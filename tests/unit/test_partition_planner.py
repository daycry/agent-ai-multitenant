"""part-01 · task_part01_02 — el núcleo PURO del job de particiones (ADR 0151).

Por qué este fichero existe separado del de integración: el modo de fallo que el
ADR 0151 nombra como «el que convierte esta decisión en un incidente» —*«sin el
job, la primera inserción del mes que viene falla»*— es aritmética de calendario,
no SQL. Los errores que de verdad se cometen aquí son de fin de año (diciembre + 1
mes) y de colchón (crear solo el mes actual y creerse cubierto). Ninguno necesita
una base de datos para salir a la luz, y con base de datos tardarían un mes en
manifestarse.

Lo que NO cubre este fichero, para que nadie lo lea como cobertura completa: que
la partición nazca con su RLS y que una fila insertada por el padre aterrice en
ella. Eso es `tests/integration/test_partition_guardrail_events.py`.
"""

from __future__ import annotations

import contextlib
import importlib
import pkgutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any

import pytest
from workers.maintenance.partitions import (
    PARTITION_HEADROOM_MONTHS,
    PARTITIONED_TABLES,
    PartitionSpec,
    add_months,
    alert_event,
    coverage_gap,
    ensure_partitions,
    missing_partitions,
    month_start,
    partition_name,
    required_partitions,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Dobles: un almacén en memoria y un notificador que solo apunta lo publicado.
# ---------------------------------------------------------------------------
@dataclass
class FakeStore:
    """Particiones existentes por tabla + registro de lo creado."""

    existing_names: dict[str, set[str]]
    created: list[str]
    fail_on: set[str]

    @classmethod
    def empty(cls, *tables: str) -> FakeStore:
        return cls({t: set() for t in tables}, [], set())

    async def existing(self, table: str) -> set[str]:
        return set(self.existing_names.get(table, set()))

    async def create(self, spec: PartitionSpec) -> None:
        if spec.name in self.fail_on:
            raise RuntimeError(f"boom on {spec.name}")
        self.existing_names.setdefault(spec.table, set()).add(spec.name)
        self.created.append(spec.name)


class FakeNotifier:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, event: dict[str, Any]) -> None:
        self.published.append(event)


_AUGUST = datetime(2026, 8, 14, 3, 40, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Aritmética de calendario
# ---------------------------------------------------------------------------
def test_month_start_floors_to_the_first_of_the_month() -> None:
    assert month_start(datetime(2026, 8, 14, 23, 59, tzinfo=UTC)) == date(2026, 8, 1)
    assert month_start(date(2026, 8, 1)) == date(2026, 8, 1)


def test_add_months_crosses_the_year_boundary() -> None:
    """Diciembre + 1 mes es enero del año siguiente, no el mes 13."""
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert add_months(date(2026, 1, 1), 0) == date(2026, 1, 1)


def test_partition_name_is_table_plus_zero_padded_year_month() -> None:
    """El cero a la izquierda importa: ordena bien y casa con el runbook."""
    assert partition_name("guardrail_events", date(2026, 8, 1)) == "guardrail_events_2026_08"
    assert partition_name("guardrail_events", date(2026, 12, 1)) == "guardrail_events_2026_12"


# ---------------------------------------------------------------------------
# Qué particiones hacen falta
# ---------------------------------------------------------------------------
def test_required_partitions_covers_the_current_month_plus_the_headroom() -> None:
    specs = required_partitions("guardrail_events", today=_AUGUST)
    assert len(specs) == PARTITION_HEADROOM_MONTHS + 1
    names = [s.name for s in specs]
    assert names == [
        "guardrail_events_2026_08",
        "guardrail_events_2026_09",
        "guardrail_events_2026_10",
        "guardrail_events_2026_11",
    ]


def test_required_partition_ranges_are_contiguous_and_half_open() -> None:
    """El `end` de una es el `start` de la siguiente: sin huecos ni solapes."""
    specs = required_partitions("guardrail_events", today=_AUGUST)
    for previous, following in pairwise(specs):
        assert previous.end == following.start
    assert specs[0].start == date(2026, 8, 1)
    assert specs[0].end == date(2026, 9, 1)


def test_missing_partitions_skips_what_already_exists() -> None:
    specs = required_partitions("guardrail_events", today=_AUGUST)
    existing = {"guardrail_events_2026_08", "guardrail_events_2026_09"}
    missing = [s.name for s in missing_partitions(specs, existing)]
    assert missing == ["guardrail_events_2026_10", "guardrail_events_2026_11"]


# ---------------------------------------------------------------------------
# La alerta: sin M+1 hay incidente en camino
# ---------------------------------------------------------------------------
def test_coverage_gap_is_none_when_next_month_exists() -> None:
    covered = {"guardrail_events_2026_09"}
    assert coverage_gap("guardrail_events", today=_AUGUST, existing=covered) is None


def test_coverage_gap_names_the_missing_next_month() -> None:
    """Lo que se vigila es M+1, no el mes en curso: el mes en curso ya falló."""
    gap = coverage_gap("guardrail_events", today=_AUGUST, existing={"guardrail_events_2026_08"})
    assert gap == "guardrail_events_2026_09"


def test_alert_event_is_a_platform_scoped_infra_alert() -> None:
    event = alert_event({"guardrail_events": "guardrail_events_2026_09"})
    assert event["event_type"] == "infra_alert"
    tenant_message = "la cobertura de particiones es de PLATAFORMA: nunca de un tenant"
    assert event["tenant_id"] is None, tenant_message
    context = event["context"]
    assert context["alertname"] == "PartitionCoverageMissing"
    assert context["severity"] == "critical"
    assert "guardrail_events_2026_09" in context["description"]


# ---------------------------------------------------------------------------
# La pasada completa, con dobles
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_partitions_creates_only_what_is_missing() -> None:
    store = FakeStore.empty("guardrail_events")
    store.existing_names["guardrail_events"] = {"guardrail_events_2026_08"}
    notifier = FakeNotifier()

    report = await ensure_partitions(store, notifier, tables=("guardrail_events",), now=_AUGUST)

    assert store.created == [
        "guardrail_events_2026_09",
        "guardrail_events_2026_10",
        "guardrail_events_2026_11",
    ]
    assert report["created"] == store.created
    assert report["gaps"] == {}
    assert notifier.published == []


@pytest.mark.asyncio
async def test_ensure_partitions_is_idempotent() -> None:
    """La segunda pasada del mismo día no crea nada: el beat corre a diario."""
    store = FakeStore.empty("guardrail_events")
    notifier = FakeNotifier()

    first = await ensure_partitions(store, notifier, tables=("guardrail_events",), now=_AUGUST)
    assert len(first["created"]) == PARTITION_HEADROOM_MONTHS + 1

    store.created.clear()
    second = await ensure_partitions(store, notifier, tables=("guardrail_events",), now=_AUGUST)
    assert second["created"] == []
    assert notifier.published == []


@pytest.mark.asyncio
async def test_ensure_partitions_alerts_when_next_month_could_not_be_created() -> None:
    """El caso que el ADR llama incidente: M+1 no existe al terminar la pasada."""
    store = FakeStore.empty("guardrail_events")
    store.fail_on = {"guardrail_events_2026_09"}
    notifier = FakeNotifier()

    report = await ensure_partitions(store, notifier, tables=("guardrail_events",), now=_AUGUST)

    assert report["gaps"] == {"guardrail_events": "guardrail_events_2026_09"}
    assert len(notifier.published) == 1
    assert notifier.published[0]["context"]["alertname"] == "PartitionCoverageMissing"


@pytest.mark.asyncio
async def test_a_table_that_explodes_does_not_stop_the_others() -> None:
    """Best-effort por tabla: con cinco convertidas, una rota no ciega a las otras."""
    store = FakeStore.empty("guardrail_events", "notification_logs")
    store.fail_on = {
        partition_name("guardrail_events", date(2026, month, 1)) for month in (8, 9, 10, 11)
    }
    notifier = FakeNotifier()

    report = await ensure_partitions(
        store, notifier, tables=("guardrail_events", "notification_logs"), now=_AUGUST
    )

    assert all(name.startswith("notification_logs_") for name in report["created"])
    assert len(report["created"]) == PARTITION_HEADROOM_MONTHS + 1
    assert report["gaps"] == {"guardrail_events": "guardrail_events_2026_09"}


@pytest.mark.asyncio
async def test_one_alert_carries_every_gap() -> None:
    """Dos tablas descubiertas = UNA notificación, no una por tabla."""
    store = FakeStore.empty("guardrail_events", "notification_logs")
    store.fail_on = {
        "guardrail_events_2026_09",
        "notification_logs_2026_09",
    }
    notifier = FakeNotifier()

    await ensure_partitions(
        store, notifier, tables=("guardrail_events", "notification_logs"), now=_AUGUST
    )

    assert len(notifier.published) == 1
    description = notifier.published[0]["context"]["description"]
    assert "guardrail_events_2026_09" in description
    assert "notification_logs_2026_09" in description


# ---------------------------------------------------------------------------
# Las dos guardas estructurales
# ---------------------------------------------------------------------------
def test_the_beat_task_is_registered_under_its_wire_name() -> None:
    """Sin registro, beat encola y el worker responde `NotRegistered` en silencio.

    Es exactamente la enfermedad que `_KNOWN_UNREGISTERED_BEAT_TASKS`
    (`test_approval_expiry_beat.py`) documenta para otras seis tasks: seis
    features «entregadas» que no se han ejecutado nunca.
    """
    import workers.maintenance  # noqa: F401  (el import dispara @app.task)
    from workers.celery_app import app

    assert "workers.ensure_partitions" in app.tasks


def test_every_partitioned_model_is_in_the_job_registry() -> None:
    """Convertir una tabla y olvidar registrarla = incidente el mes que viene.

    Descubre en el MODELO qué tablas declaran `postgresql_partition_by` y exige
    que el job las conozca. Es la guarda que hace seguras las olas 2-5 del plan:
    la migración sola no basta, hay que decírselo al job.
    """
    import api_server.db as dbpkg

    for module in pkgutil.iter_modules(dbpkg.__path__):
        with contextlib.suppress(Exception):
            importlib.import_module(f"api_server.db.{module.name}")
    from api_server.db.base import Base

    partitioned = {
        name
        for name, table in Base.metadata.tables.items()
        if table.dialect_kwargs.get("postgresql_partition_by")
    }
    assert partitioned, "el descubrimiento no vio ninguna tabla particionada: guarda vacía"

    unregistered = sorted(partitioned - set(PARTITIONED_TABLES))
    message = (
        f"tablas particionadas en el modelo que el job NO conoce: {unregistered}. "
        "Sin entrada en PARTITIONED_TABLES nadie crea su partición del mes que "
        "viene y la primera inserción de ese mes falla."
    )
    assert not unregistered, message

    stale = sorted(set(PARTITIONED_TABLES) - partitioned)
    stale_message = (
        f"el job vigila tablas que ya no declaran particionado: {stale}. "
        "Una entrada muerta hace creer que algo está cubierto cuando no lo está."
    )
    assert not stale, stale_message
