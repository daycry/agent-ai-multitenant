"""Purga física de filas soft-borradas vencidas — ``workers.purge_soft_deleted``.

prod-13 · task_prod13_14 (hallazgo db-4).

El problema
-----------
Borrar una KB o un proyecto solo ponía ``deleted_at``. Las filas seguían ahí para
siempre: los ``chunks`` con su ``vector(768)`` (lo más caro por fila de todo el
esquema) y los ``steps_log`` de sus runs incluidos. La promesa que sí está
escrita en los docstrings del borrado —«recuperable durante la ventana de
gracia»— tiene una segunda mitad implícita, **pasada la gracia se borra de
verdad**, que nunca se implementó. El resultado es una base que solo crece.

El alcance es una decisión, no un barrido
-----------------------------------------
Hay **35 tablas** con ``deleted_at``. Purgarlas todas por el hecho de tener la
columna sería un barrido a ciegas sobre cosas como ``organizations`` (dar de baja
un tenant entero) o ``users`` (global desde el ADR 0137). Así que la purga tiene
una **allowlist corta** (:data:`PURGABLE_ROOTS`) y una lista de exclusiones **con
el motivo escrito** (:data:`EXCLUDED_SOFT_DELETE_TABLES`); un test comprueba que
entre las dos cubren el universo, de forma que una tabla nueva con ``deleted_at``
obligue a decidir en vez de colarse en cualquiera de los dos sentidos.

Las dos raíces son las que el plan nombra y las que de verdad ocupan disco:

* ``knowledge_bases`` → ``documents`` → ``chunks`` (cascada por FK), más los
  **blobs** de MinIO de esos documentos;
* ``projects`` → ``plans`` / ``tasks`` / ``executions`` (y el resto de
  dependientes con ``ON DELETE CASCADE``).

Por qué el default es dry-run
-----------------------------
Riesgo 3 del plan: «la purga borra datos que un tenant quería recuperar». El
borrado es irreversible y no hay forma de probarlo en producción sin arriesgar,
así que la task **cuenta y no borra** mientras nadie diga lo contrario. Encender
el borrado real es una decisión del operador, y tiene dos vías:

* el platform setting ``purge.soft_deleted_enabled`` (System Admin), o
* una llamada puntual con ``dry_run=False`` para una primera pasada vigilada.

Nunca rompe el beat: como el resto de tareas de mantenimiento, un fallo se anota
y la siguiente pasada lo reintenta (la purga es idempotente por construcción —
solo mira filas que siguen soft-borradas).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.celery_app import app
from workers.db import worker_engine
from workers.maintenance.singleton import beat_singleton

_log = structlog.get_logger("workers.maintenance")

_Sessionmaker = async_sessionmaker[AsyncSession]

#: Ventana de gracia por defecto. Coincide con la del GC de conocimiento (G-03)
#: a propósito: dos ventanas distintas para «lo soft-borrado» serían imposibles
#: de explicar en la UI.
DEFAULT_GRACE_DAYS = 30

#: Palancas de plataforma. Se leen genéricamente con ``get_platform_setting``
#: (sin getter propio) para no tocar módulos de otros carriles; exponerlas en
#: ``platform_settings_registry`` para que salgan en el panel es el follow-up.
PURGE_ENABLED_KEY = "purge.soft_deleted_enabled"
PURGE_GRACE_DAYS_KEY = "purge.soft_deleted_grace_days"

#: Cota por pasada y por raíz. Una purga que se encuentra 200.000 filas vencidas
#: la primera vez no puede monopolizar la cola ni hacer un DELETE gigante: se
#: lleva un mordisco y el resto cae en la siguiente pasada del beat.
_MAX_ROOTS_PER_PASS = 500

#: Las dos raíces que se purgan de verdad. Cortas a propósito (ver módulo).
PURGABLE_ROOTS: tuple[str, ...] = ("knowledge_bases", "projects")

#: Tablas con ``deleted_at`` que NO se purgan, y por qué. El motivo es
#: obligatorio: una exclusión sin justificación es un olvido disfrazado de
#: decisión, y un test mide que el texto exista.
EXCLUDED_SOFT_DELETE_TABLES: dict[str, str] = {
    "organizations": (
        "Dar de baja un tenant es una operación de negocio con su propio "
        "procedimiento (backup, export, facturación); no puede ser el efecto "
        "colateral de un beat nocturno."
    ),
    "users": (
        "La tabla es GLOBAL (ADR 0137): un usuario soft-borrado en un tenant "
        "puede seguir siendo miembro activo de otro. Borrar la fila rompería "
        "esa membresía."
    ),
    "user_org_memberships": (
        "Es el rastro de que alguien tuvo acceso a un tenant. Se conserva por "
        "auditoría; su retención la decide el ADR de retención (task 15)."
    ),
    "documents": (
        "Ya la purga el GC de conocimiento (G-03, `knowledge_gc.py`), que "
        "además borra su blob. Dos jobs sobre la misma tabla se pisarían."
    ),
    "memory_entries": (
        "La memoria es el activo con más valor acumulado del sistema y su "
        "retención es una decisión de producto, no de disco. Va al ADR de "
        "retención (task_prod13_15)."
    ),
    "plans": (
        "`tasks.plan_id` es ON DELETE SET NULL: purgar un plan dejaría sus "
        "tareas VIVAS y huérfanas. Se purgan con su proyecto, que sí cascadea."
    ),
    "conversations": (
        "El historial de chat de un proyecto se va con el proyecto (cascada). "
        "Suelto, es conversación que el usuario puede querer recuperar."
    ),
    "assistant_conversations": (
        "Hilos del asistente de un usuario: mismo criterio que las "
        "conversaciones de proyecto, y su volumen es texto, no vectores."
    ),
    "assistant_turns": (
        "Cuelgan de `assistant_conversations`; se irían con ellas el día que "
        "esa raíz entre. Sueltas no significan nada."
    ),
    "cortex_conversations": (
        "Hilos del córtex, sin tenant y con un único dueño humano (ADR 0074). "
        "Su retención es decisión del System Owner, no de un beat."
    ),
    "review_sessions": (
        "Cuelgan de `plans` con ON DELETE CASCADE: se van con el proyecto. "
        "Purgarlas aparte rompería el histórico de una review de un plan vivo."
    ),
    "plan_comments": (
        "Cascada desde `plans`. Suelto, un comentario borrado es contexto de "
        "una discusión que sigue viva."
    ),
    # --- Catálogo: filas pequeñas cuya identidad se referencia por nombre ----
    "agents": (
        "Catálogo referenciado por nombre desde seeds, plantillas y planes. "
        "Purgar un agente borrado rompería referencias históricas y libera "
        "unos pocos KB: el coste no compensa el riesgo."
    ),
    "teams": "Mismo criterio que `agents`: catálogo pequeño referenciado por nombre.",
    "skills": "Mismo criterio que `agents`: catálogo pequeño referenciado por nombre.",
    "tools": "Mismo criterio que `agents`: catálogo pequeño referenciado por nombre.",
    "kb_categories": "Catálogo de taxonomía; filas mínimas y referenciadas por slug.",
    "approval_policy_templates": (
        "Plantillas de política de aprobación: define QUÉ se aprobó en su "
        "momento. Borrarlas dejaría aprobaciones históricas sin explicar."
    ),
    "custom_chat_modes": "Configuración de tenant, filas mínimas; su borrado no libera disco.",
    "guardrail_alert_rules": "Reglas de alerta: configuración, no datos; volumen despreciable.",
    "outlier_alert_rules": "Reglas de alerta: configuración, no datos; volumen despreciable.",
    "notification_channels": (
        "Configuración de entrega. Un canal borrado sigue explicando envíos "
        "históricos del log de notificaciones."
    ),
    "notification_preferences": "Configuración por usuario; volumen despreciable.",
    "notification_templates": (
        "Override de plantilla del tenant: explica el texto de notificaciones ya enviadas."
    ),
    "sso_configurations": (
        "Configuración de identidad. Un proveedor retirado explica sesiones y "
        "provisiones JIT históricas; borrarlo es perder ese rastro."
    ),
    "incoming_webhook_configs": (
        "Cascada desde `projects`. Suelto, explica los eventos entrantes ya "
        "recibidos que siguen en la tabla de eventos."
    ),
    "eval_datasets": (
        "Datasets de evaluación: curaduría humana cara de reconstruir. Su "
        "retención la decide el operador, no el disco."
    ),
    "eval_dataset_items": "Cuelgan de `eval_datasets`; se irían con ellos si esa raíz entrara.",
    "eval_criteria": "Definición de qué se midió; borrarla deja resultados sin interpretar.",
    "marketplace_sources": (
        "Origen de paquetes instalados: sin la fila no se puede explicar de "
        "dónde salió una instalación viva."
    ),
    "marketplace_listings": (
        "Un listing retirado sigue explicando instalaciones vivas y su consentimiento de permisos."
    ),
    "marketplace_installations": (
        "Registro de qué se instaló y qué permisos se concedieron; es la "
        "evidencia del consentimiento (ADR 0142)."
    ),
    "marketplace_shares": (
        "Compartición cross-tenant: el rastro de que un tenant expuso algo a "
        "otro es exactamente lo que una auditoría busca."
    ),
}

__all__ = [
    "DEFAULT_GRACE_DAYS",
    "EXCLUDED_SOFT_DELETE_TABLES",
    "PURGABLE_ROOTS",
    "PURGE_ENABLED_KEY",
    "PURGE_GRACE_DAYS_KEY",
    "purge_soft_deleted",
    "purge_soft_deleted_task",
]


async def _expired_ids(session: AsyncSession, table: str, *, cutoff: datetime) -> list[UUID]:
    """Ids de ``table`` soft-borrados ANTES del corte, acotados por pasada.

    ``deleted_at IS NOT NULL`` es redundante con ``deleted_at < :cutoff`` en SQL
    (NULL nunca compara verdadero), pero se escribe igual: hace explícito en el
    propio texto de la consulta que las filas vivas están fuera, que es la
    invariante que más caro sale romper.
    """
    rows = await session.execute(
        text(
            # `table` sale SIEMPRE de PURGABLE_ROOTS, nunca de una request.
            f"SELECT id FROM {table}"
            " WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
            " ORDER BY deleted_at"
            " LIMIT :cap"
        ),
        {"cutoff": cutoff, "cap": _MAX_ROOTS_PER_PASS},
    )
    return [row[0] for row in rows.all()]


async def _count(session: AsyncSession, sql: str, ids: list[UUID]) -> int:
    result = await session.execute(text(sql), {"ids": ids})
    return int(result.scalar_one() or 0)


async def _delete(session: AsyncSession, table: str, ids: list[UUID]) -> int:
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            text(f"DELETE FROM {table} WHERE id = ANY(:ids)"),  # `table` de la allowlist
            {"ids": ids},
        ),
    )
    return int(result.rowcount or 0)


async def _purge_knowledge_bases(
    sessionmaker: _Sessionmaker, *, cutoff: datetime, dry_run: bool
) -> tuple[dict[str, int], list[str]]:
    """KBs vencidas + su cascada. Devuelve ``(recuentos, claves de blob)``.

    Las claves de los blobs se leen ANTES del DELETE: después de la cascada las
    filas ``documents`` ya no existen y no habría forma de saber qué borrar en
    MinIO. Ese orden es justo el hallazgo db-3 que prod-06 arregla en el borrado
    interactivo; aquí se hace bien desde el principio.
    """
    async with sessionmaker() as session:
        ids = await _expired_ids(session, "knowledge_bases", cutoff=cutoff)
        if not ids:
            return {}, []
        documents = await _count(
            session, "SELECT count(*) FROM documents WHERE kb_id = ANY(:ids)", ids
        )
        chunks = await _count(
            session,
            "SELECT count(*) FROM chunks WHERE document_id IN"
            " (SELECT id FROM documents WHERE kb_id = ANY(:ids))",
            ids,
        )
        keys = [
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT source_storage_key FROM documents"
                        " WHERE kb_id = ANY(:ids) AND source_storage_key IS NOT NULL"
                    ),
                    {"ids": ids},
                )
            ).all()
        ]

    counts = {"knowledge_bases": len(ids), "documents": documents, "chunks": chunks}
    if dry_run:
        return counts, []

    async with sessionmaker() as session, session.begin():
        counts["knowledge_bases"] = await _delete(session, "knowledge_bases", ids)
    return counts, keys


async def _purge_projects(
    sessionmaker: _Sessionmaker, *, cutoff: datetime, dry_run: bool
) -> dict[str, int]:
    """Proyectos vencidos + su cascada (plans / tasks / executions)."""
    async with sessionmaker() as session:
        ids = await _expired_ids(session, "projects", cutoff=cutoff)
        if not ids:
            return {}
        plans = await _count(
            session, "SELECT count(*) FROM plans WHERE project_id = ANY(:ids)", ids
        )
        tasks = await _count(
            session, "SELECT count(*) FROM tasks WHERE project_id = ANY(:ids)", ids
        )
        executions = await _count(
            session,
            "SELECT count(*) FROM executions WHERE task_id IN"
            " (SELECT id FROM tasks WHERE project_id = ANY(:ids))",
            ids,
        )

    counts = {
        "projects": len(ids),
        "plans": plans,
        "tasks": tasks,
        "executions": executions,
    }
    if dry_run:
        return counts

    async with sessionmaker() as session, session.begin():
        counts["projects"] = await _delete(session, "projects", ids)
    return counts


async def _delete_blobs(storage: Any, keys: list[str]) -> tuple[int, int]:
    """Borra los blobs de los documentos purgados. ``(borrados, fallos)``.

    Best-effort por blob: un MinIO caído NO puede impedir que las filas se
    purguen (ya lo están cuando llegamos aquí) ni tumbar la pasada entera. Los
    que fallen los recoge el barrido de huérfanos del GC de conocimiento, que
    borra blobs ``kb/**`` sin fila ``documents``.
    """
    deleted = 0
    failures = 0
    for key in keys:
        try:
            await storage.delete_object(key=key)
            deleted += 1
        except Exception:
            failures += 1
            _log.warning("purge_soft_deleted.blob_delete_failed", key=key)
    return deleted, failures


async def purge_soft_deleted(
    sessionmaker: _Sessionmaker,
    storage: Any,
    *,
    grace_days: int = DEFAULT_GRACE_DAYS,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Purga las filas soft-borradas anteriores a la ventana de gracia.

    Args:
        sessionmaker: sesiones sobre el engine BYPASSRLS del worker (la purga es
            cross-tenant por definición).
        storage: almacén de objetos para los blobs de los documentos purgados.
        grace_days: días de gracia. ``0`` significa «el corte es ahora», y aun
            así una fila viva (``deleted_at IS NULL``) nunca entra.
        dry_run: cuenta sin borrar. **Default a propósito**: el borrado es
            irreversible (riesgo 3 del plan).
        now: reloj inyectable para los tests.

    Devuelve ``{"dry_run", "grace_days", "cutoff", "by_table", "blobs_deleted",
    "blob_failures"}``. ``by_table`` va vacío cuando no había nada vencido, que
    es el caso normal y no genera ruido en el log.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=grace_days)

    kb_counts, blob_keys = await _purge_knowledge_bases(
        sessionmaker, cutoff=cutoff, dry_run=dry_run
    )
    project_counts = await _purge_projects(sessionmaker, cutoff=cutoff, dry_run=dry_run)
    blobs_deleted, blob_failures = await _delete_blobs(storage, blob_keys)

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "grace_days": grace_days,
        "cutoff": cutoff.isoformat(),
        "by_table": {**kb_counts, **project_counts},
        "blobs_deleted": blobs_deleted,
        "blob_failures": blob_failures,
    }
    if report["by_table"]:
        _log.info("purge_soft_deleted.done", **report)
    return report


@app.task(name="workers.purge_soft_deleted")  # type: ignore[untyped-decorator]
@beat_singleton("purge_soft_deleted", ttl_s=3600)
def purge_soft_deleted_task(
    dry_run: bool | None = None, grace_days: int | None = None
) -> dict[str, Any]:
    """Entrada Celery (beat diario). Best-effort — nunca crashea beat.

    Los argumentos permiten una pasada puntual vigilada
    (``celery call workers.purge_soft_deleted --kwargs '{"dry_run": false}'``)
    sin tocar el platform setting; cuando van a ``None`` mandan los settings.
    """
    try:
        return asyncio.run(_run(dry_run=dry_run, grace_days=grace_days))
    except Exception as exc:  # pragma: no cover - la red del beat
        _log.warning("purge_soft_deleted.failed", error=str(exc))
        return {"error": str(exc)}


async def _resolve_knobs(
    session: AsyncSession, *, dry_run: bool | None, grace_days: int | None
) -> tuple[bool, int]:
    """Las dos palancas: el argumento manda, si no el setting, si no el default."""
    from api_server.db.platform_settings import get_platform_setting

    if dry_run is None:
        enabled = await get_platform_setting(session, PURGE_ENABLED_KEY, default=False)
        dry_run = not bool(enabled)
    if grace_days is None:
        raw = await get_platform_setting(session, PURGE_GRACE_DAYS_KEY, default=DEFAULT_GRACE_DAYS)
        try:
            grace_days = int(raw)
        except (TypeError, ValueError):
            grace_days = DEFAULT_GRACE_DAYS
    # Una gracia negativa purgaría el futuro; una de 0 días se permite (es
    # explícita y sigue respetando `deleted_at IS NOT NULL`).
    return dry_run, max(0, grace_days)


async def _run(*, dry_run: bool | None, grace_days: int | None) -> dict[str, Any]:
    """Engine + storage propios, ``dispose`` garantizado (patrón de G-03)."""
    from api_server.storage import get_object_storage

    from workers.config import get_settings

    settings = get_settings()
    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            resolved_dry_run, resolved_grace = await _resolve_knobs(
                session, dry_run=dry_run, grace_days=grace_days
            )
        return await purge_soft_deleted(
            sessionmaker,
            get_object_storage(),
            grace_days=resolved_grace,
            dry_run=resolved_dry_run,
        )
    finally:
        await engine.dispose()
