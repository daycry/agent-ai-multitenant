"""Reaper de contenedores y redes huérfanos — ``workers.reap_orphans``, cada 10
min (prod-12 task_prod12_reaper_01, sandbox-5). Best-effort: nunca rompe beat.

Coordinación con el sweeper de zombis de prod-06 (``stale_sweeper``, dirigido
por la tabla ``executions``) — un solo criterio de vida compartido para que
nunca haya doble-kill ni criterios divergentes:

  * un contenedor cuya execution está ``running`` NUNCA se toca (misma regla
    que ``_remove_exited_terminal_containers``);
  * el sweeper cierra filas zombis y mata SU contenedor por label; este reaper
    barre el residuo que aquel no ve — contenedores ``managed`` en CUALQUIER
    estado sin fila viva (p. ej. un worker muerto tras el launch cuya fila
    selló el soft-timeout), review-runtimes de sesiones terminales que el sweep
    de expiry no cubre (approved/rejected antes de expirar) y las redes bridge
    per-task de test-runtime que quedaron sin contenedores;
  * margen de edad anti-carrera (10 min) para no barrer provisiones frescas;
    un contenedor managed SIN label de asociación solo cae pasado el hard-limit
    de runs + 25 % (mitigación del riesgo 5 del plan).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")

#: Margen anti-carrera: nada más joven que esto se toca (la fila/el attach
#: pueden estar aún en provisión).
_REAP_GRACE = timedelta(minutes=10)
#: Un contenedor managed SIN label de asociación (ni execution ni review) solo
#: se barre pasado el hard-limit de contenedor (6 h) + 25 %.
_UNTAGGED_REAP_AFTER = timedelta(hours=7, minutes=30)

_EXECUTION_LABEL = "com.agentic-platform.execution-id"
_REVIEW_LABEL = "com.agentic-platform.review-session-id"
_MANAGED_FILTER = {"label": "com.agentic-platform.managed=true"}
_TEST_NETWORK_FILTER = {"label": "com.agentic-platform.component=test-runtime"}
# ADR 0129 fase 2: the per-session review bridges (aux sidecars for the app
# -preview) get their own component label; the reaper sweeps them empty the same
# way it sweeps the per-task test bridges.
_REVIEW_NETWORK_FILTER = {"label": "com.agentic-platform.component=review-runtime"}
_EMPTY_NETWORK_FILTERS = (_TEST_NETWORK_FILTER, _REVIEW_NETWORK_FILTER)

#: Estados de review-session que mantienen su contenedor VIVO.
_LIVE_REVIEW_STATUSES = ("running", "suspended")


def _parse_docker_time(raw: object) -> datetime | None:
    """Parse Docker's ``Created`` timestamps (RFC3339 con nanosegundos)."""
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.rstrip("Z")
    # datetime.fromisoformat no traga nanosegundos — truncar a microsegundos.
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


@app.task(name="workers.reap_orphans")  # type: ignore[untyped-decorator]
def reap_orphans() -> dict[str, Any]:
    """Reap managed containers with no live association + empty test networks."""
    settings = get_settings()
    return asyncio.run(_reap_orphans_async(settings))


async def _live_ids(
    engine: Any, exec_ids: list[UUID], review_ids: list[UUID]
) -> tuple[set[str], set[str]]:
    """Las asociaciones que siguen VIVAS (execution running / review activa)."""
    from api_server.db.domain import Execution, ExecutionStatus
    from api_server.db.models import ReviewSession
    from sqlalchemy import select

    live_exec: set[str] = set()
    live_review: set[str] = set()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as db:
        if exec_ids:
            rows = await db.execute(
                select(Execution.id).where(
                    Execution.id.in_(exec_ids),
                    Execution.status == ExecutionStatus.RUNNING.value,
                )
            )
            live_exec = {str(row[0]) for row in rows}
        if review_ids:
            rows = await db.execute(
                select(ReviewSession.id).where(
                    ReviewSession.id.in_(review_ids),
                    ReviewSession.status.in_(_LIVE_REVIEW_STATUSES),
                    ReviewSession.deleted_at.is_(None),
                )
            )
            live_review = {str(row[0]) for row in rows}
    return live_exec, live_review


def _classify(
    containers: list[Any], moment: datetime
) -> tuple[list[tuple[Any, str]], list[tuple[Any, str]], list[Any]]:
    """Split managed containers by association label, applying the age grace."""
    by_exec: list[tuple[Any, str]] = []
    by_review: list[tuple[Any, str]] = []
    untagged: list[Any] = []
    for container in containers:
        created = _parse_docker_time((getattr(container, "attrs", None) or {}).get("Created"))
        age = None if created is None else moment - created
        if age is not None and age < _REAP_GRACE:
            continue
        labels = getattr(container, "labels", None) or {}
        exec_id = labels.get(_EXECUTION_LABEL, "")
        review_id = labels.get(_REVIEW_LABEL, "")
        if exec_id:
            by_exec.append((container, exec_id))
        elif review_id:
            by_review.append((container, review_id))
        elif age is not None and age >= _UNTAGGED_REAP_AFTER:
            untagged.append(container)
    return by_exec, by_review, untagged


def _reap_empty_networks(client: Any, moment: datetime) -> int:
    """Remove empty, aged-out per-task/per-session bridges (test-runtime + review).

    Sweeps every component filter, deduping by network id so a network never
    falls twice; an occupied or fresh (within the anti-race grace) network is
    left alone. Best-effort per network — a single failure never aborts the
    sweep."""
    removed = 0
    seen_networks: set[str] = set()
    for net_filter in _EMPTY_NETWORK_FILTERS:
        for network in list(client.networks.list(filters=net_filter)):
            net_id = str(getattr(network, "id", None) or getattr(network, "name", ""))
            if net_id in seen_networks:
                continue
            seen_networks.add(net_id)
            with contextlib.suppress(Exception):
                network.reload()
                attrs = getattr(network, "attrs", None) or {}
                if attrs.get("Containers"):
                    continue
                created = _parse_docker_time(attrs.get("Created"))
                if created is not None and (moment - created) < _REAP_GRACE:
                    continue
                network.remove()
                removed += 1
    return removed


async def _reap_orphans_async(
    settings: Settings,
    *,
    client: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Async core. ``client`` (docker SDK) y ``now`` inyectables para tests."""
    from workers.docker_client import get_docker_client

    if client is None:
        client = get_docker_client()
    if client is None:
        return {"containers_removed": 0, "networks_removed": 0, "note": "docker unavailable"}

    moment = now or datetime.now(UTC)
    containers_removed = 0
    networks_removed = 0
    engine = create_async_engine(settings.database_url)
    try:
        managed = list(client.containers.list(all=True, filters=_MANAGED_FILTER))
        by_exec, by_review, untagged = _classify(managed, moment)

        exec_ids: list[UUID] = []
        for _c, raw in by_exec:
            with contextlib.suppress(ValueError):
                exec_ids.append(UUID(raw))
        review_ids: list[UUID] = []
        for _c, raw in by_review:
            with contextlib.suppress(ValueError):
                review_ids.append(UUID(raw))

        live_exec, live_review = await _live_ids(engine, exec_ids, review_ids)

        doomed = [c for c, eid in by_exec if eid not in live_exec]
        doomed += [c for c, sid in by_review if sid not in live_review]
        doomed += untagged
        for container in doomed:
            with contextlib.suppress(Exception):
                container.remove(force=True)
                containers_removed += 1

        # Redes bridge per-task de test-runtime + per-session de review que
        # quedaron vacías (el runner/spawn las borra al terminar, salvo kill -9
        # del worker a mitad de run, o la sesión de review ya reapeada de sus
        # contenedores).
        networks_removed = _reap_empty_networks(client, moment)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.reap_orphans.error", error=str(exc))
        return {
            "containers_removed": containers_removed,
            "networks_removed": networks_removed,
            "error": str(exc),
        }
    finally:
        await engine.dispose()

    if containers_removed or networks_removed:
        _log.info(
            "maintenance.reap_orphans.done",
            containers_removed=containers_removed,
            networks_removed=networks_removed,
        )
    return {"containers_removed": containers_removed, "networks_removed": networks_removed}
