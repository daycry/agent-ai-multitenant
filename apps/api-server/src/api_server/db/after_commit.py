"""Callbacks que deben correr DESPUÉS del commit, y la sesión que los drena.

Por qué existe este módulo
--------------------------
Un evento de dominio no se puede publicar antes de que su fila sea durable: el
consumidor (el orquestador) llega a leerla y se la salta en silencio. De ahí
:func:`schedule_after_commit`, que aparca una corrutina hasta que la transacción
comitea.

Lo que estaba roto era la otra mitad — **quién la ejecuta**. El registro vivía en
``auth.deps`` y sólo lo drenaba ``open_tenant_session``, así que todo lo que
abriese su propia sesión (cualquier ruta de System Admin, la CLI, el bootstrap)
registraba callbacks que **no corría nadie**. Con una consecuencia concreta:
``set_platform_setting`` agenda ahí su segunda invalidación de caché, la que
existe porque entre el ``flush`` y el commit un lector concurrente todavía ve el
valor viejo en la BD y puede recachearlo otros 30 s. Como TODAS las rutas que
escriben platform settings son System-Admin only, y por tanto usan sesión admin,
esa segunda invalidación **no se ejecutó nunca en producción**: el kill-switch de
egress del córtex (``PUT /owner/cortex/autonomy``) podía tardar hasta medio minuto
en apagar de verdad, y lo mismo ``max_review_retries`` y los budgets.

Y no era sólo la caché. La décima ruta afectada no escribe settings:
``POST /review/{session_id}/verdict`` (``routers/review.py``) también abre sesión
admin a mano, y por ahí pasa ``move_plan`` → ``publish_plan_transition_after_commit``.
O sea que el evento ``plan_status_changed`` **no salía** cuando un humano aprobaba
o rechazaba un plan desde el enlace de revisión: el tablero gerencial dejaba de
moverse justo en la transición que más importa. Mismo silencio, misma causa.

El arreglo, y por qué va aquí
-----------------------------
El contrato es «quien abre una sesión la drena al cerrarla», así que se
implementa **en la sesión**, no en cada llamador: parchear ruta por ruta se
olvida en la ruta número once, y aquí ya iban diez. Las dos factorías del
api-server (:func:`api_server.db.session.get_sessionmaker` y
:func:`~api_server.db.session.get_admin_sessionmaker`) construyen
:class:`AfterCommitSession`, que:

  * marca los callbacks como ejecutables cuando SQLAlchemy emite ``after_commit``
    — o sea, sólo si la transacción llegó a comitear, y
  * los ejecuta en ``close()``, que es el único punto por el que pasan las dos
    formas de cerrar una sesión en esta base (``async with sm() as s, s.begin():``
    y el ``await s.commit()`` explícito).

No se ejecutan dentro del propio ``after_commit`` porque ese hook es SÍNCRONO
—corre dentro del greenlet de SQLAlchemy— y los callbacks son corrutinas: sacarlos
de ahí exigiría ``await_only`` y ataría el arreglo a un detalle interno de la
librería.

Un rollback no promociona nada, así que sus callbacks mueren con la sesión: es
justo lo que debe pasar cuando la fila que los justificaba no existe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

_log = structlog.get_logger("api_server.db.after_commit")

#: Callbacks registrados cuya transacción todavía no ha comiteado.
PENDING_KEY = "_after_commit"
#: Callbacks cuya transacción YA comiteó: los que hay que ejecutar.
READY_KEY = "_after_commit_ready"

AfterCommitFactory = Callable[[], Awaitable[None]]


class _AfterCommitSyncSession(Session):
    """La ``Session`` síncrona que envuelve :class:`AfterCommitSession`.

    Existe sólo para colgar de ella el listener de ``after_commit``: registrarlo
    sobre ``sqlalchemy.orm.Session`` a secas lo aplicaría a TODA sesión del
    proceso, incluidas las de los workers, que ni usan este mecanismo ni deben
    pagar su coste.

    Al revés SÍ propaga, y conviene saberlo antes de asustarse: los listeners que
    :mod:`api_server.cache.membership` registra sobre la ``Session`` base siguen
    disparando para esta subclase (comprobado en los dos órdenes de import). Meter
    una subclase por medio no deja sin invalidar la caché de membresías.
    """


@event.listens_for(_AfterCommitSyncSession, "after_commit")
def _promote_pending_hooks(session: Session) -> None:
    """Marca como ejecutables los callbacks de la transacción recién comiteada.

    Es un hook SÍNCRONO: mueve la lista de un hueco de ``session.info`` a otro y
    nada más. Ejecutar aquí las corrutinas exigiría ``await_only`` dentro del
    greenlet; se hacen en :meth:`AfterCommitSession.close`.
    """
    pending = session.info.pop(PENDING_KEY, None)
    if pending:
        session.info.setdefault(READY_KEY, []).extend(pending)


def schedule_after_commit(session: Any, factory: AfterCommitFactory) -> None:
    """Registra una corrutina para después de que ESTA sesión comitee.

    Publicar en línea —antes del commit— deja que un consumidor rápido lea la
    fila que aún no es durable y la ignore en silencio; ése fue el origen del
    síntoma «el consumidor se atasca». Registrarla aquí garantiza que sale
    post-commit.

    Sólo se ejecuta si la sesión es una :class:`AfterCommitSession` (las dos
    factorías del api-server lo son). Con cualquier otra se avisa en vez de
    callar: un callback que no corre nunca es exactamente el fallo que este
    módulo viene a cerrar, y su firma es el silencio.
    """
    if not isinstance(session, AfterCommitSession):
        _log.warning(
            "api_server.after_commit_session_without_drain",
            session_type=type(session).__name__,
        )
    session.info.setdefault(PENDING_KEY, []).append(factory)


async def run_after_commit_hooks(session: Any) -> None:
    """Ejecuta —una sola vez— los callbacks cuya transacción ya comiteó.

    Best-effort por diseño: la transacción ya es durable, así que un fallo al
    publicar no puede tumbar un request que ya ocurrió. Se saca la lista con
    ``pop`` para que una segunda llamada no repita nada.
    """
    ready = session.info.pop(READY_KEY, None)
    if not ready:
        return
    for factory in ready:
        try:
            await factory()
        except Exception as exc:  # - best-effort, never fail the request
            _log.warning("api_server.after_commit_failed", error=str(exc))


class AfterCommitSession(AsyncSession):
    """La ``AsyncSession`` del api-server: drena sus callbacks al cerrarse.

    Se cierra en ``close()`` y no en el ``after_commit`` de SQLAlchemy porque
    aquél es síncrono (ver el docstring del módulo), y ``close()`` es el punto
    común de las dos formas de cerrar sesión que usa esta base: el
    ``async with sm() as s, s.begin():`` de las rutas admin y el
    ``await s.commit()`` explícito.
    """

    sync_session_class = _AfterCommitSyncSession

    async def close(self) -> None:
        try:
            await run_after_commit_hooks(self)
        finally:
            await super().close()


__all__ = [
    "PENDING_KEY",
    "READY_KEY",
    "AfterCommitFactory",
    "AfterCommitSession",
    "run_after_commit_hooks",
    "schedule_after_commit",
]
