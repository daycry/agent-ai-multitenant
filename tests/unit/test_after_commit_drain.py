"""Quien abre una sesión la drena al cerrarla — y no puede volver a olvidarse.

Contexto
--------
``schedule_after_commit`` aparca corrutinas hasta que la transacción comitea.
Hasta 2026-08-20 el ÚNICO sitio que las ejecutaba era ``open_tenant_session``, de
modo que toda ruta que abriese su propia sesión admin registraba callbacks que no
corría nadie. No era teórico: las nueve rutas que escriben ``platform_settings``
son System-Admin only, o sea que **ninguna** ejecutaba la segunda invalidación de
caché que ``set_platform_setting`` agenda ahí. Consecuencia con nombre: el
kill-switch de egress del córtex (``PUT /owner/cortex/autonomy``) podía tardar
hasta 30 s en apagar de verdad.

Qué fija este fichero
---------------------
Dos cosas distintas, y las dos hacen falta:

* el **contrato de la sesión** — comitea y se ejecutan, deshace y no —, que se
  prueba sin base de datos porque no la necesita;
* la **guarda estructural**: que ninguna factoría de sesiones del api-server
  vuelva a nacer sin la clase que drena. Un arreglo por llamador se olvida en el
  llamador número once; éste falla en cuanto alguien añade la factoría número
  tres.

La ventana completa contra PostgreSQL y Redis de verdad vive en
``tests/integration/test_platform_settings_after_commit_invalidation.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from api_server.db.after_commit import (
    AfterCommitSession,
    run_after_commit_hooks,
    schedule_after_commit,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# El contrato de la sesión
# ---------------------------------------------------------------------------
# Una `AfterCommitSession` sin `bind` no toca la red: sirve para probar el
# contrato de commit/rollback/close sin PostgreSQL delante.
@pytest.mark.asyncio
async def test_a_committed_session_runs_its_hooks_when_it_closes() -> None:
    ran: list[str] = []

    async def _hook() -> None:
        ran.append("sí")

    session = AfterCommitSession()
    schedule_after_commit(session, _hook)
    assert ran == [], "el callback no puede correr en el registro, sino tras el commit"

    await session.commit()
    await session.close()

    assert ran == ["sí"]


@pytest.mark.asyncio
async def test_a_rolled_back_session_never_runs_them() -> None:
    """Lo que no se comitea no se publica: la fila que lo justificaba no existe."""
    ran: list[str] = []

    async def _hook() -> None:
        ran.append("sí")

    session = AfterCommitSession()
    schedule_after_commit(session, _hook)
    await session.rollback()
    await session.close()

    assert ran == []


@pytest.mark.asyncio
async def test_the_hooks_run_exactly_once() -> None:
    """Cerrar dos veces no republica.

    Importa porque el drenaje ocurre en ``close()``, y ``close()`` es idempotente
    por contrato de SQLAlchemy: si el drenaje no lo fuese, un cierre repetido
    duplicaría eventos de dominio.
    """
    ran: list[str] = []

    async def _hook() -> None:
        ran.append("sí")

    session = AfterCommitSession()
    schedule_after_commit(session, _hook)
    await session.commit()
    await session.close()
    await session.close()

    assert ran == ["sí"]


@pytest.mark.asyncio
async def test_a_failing_hook_neither_raises_nor_stops_the_others() -> None:
    """Best-effort de verdad: la transacción YA es durable cuando esto corre.

    Un fallo al publicar no puede tumbar un request que ya ocurrió, ni dejar sin
    ejecutar a los callbacks siguientes.
    """
    ran: list[str] = []

    async def _boom() -> None:
        raise RuntimeError("redis caído")

    async def _hook() -> None:
        ran.append("sí")

    session = AfterCommitSession()
    schedule_after_commit(session, _boom)
    schedule_after_commit(session, _hook)
    await session.commit()
    await session.close()

    assert ran == ["sí"]


@pytest.mark.asyncio
async def test_draining_a_session_with_nothing_pending_is_a_no_op() -> None:
    session = AfterCommitSession()
    await session.commit()
    await run_after_commit_hooks(session)  # no debe levantar
    await session.close()


# ---------------------------------------------------------------------------
# La guarda estructural
# ---------------------------------------------------------------------------
_API_SERVER_SRC = Path(__file__).resolve().parents[2] / "apps" / "api-server" / "src" / "api_server"

#: `async_sessionmaker(` **y** `async_sessionmaker[AsyncSession](`: el subíndice
#: explícito es real en `db/session.py` (el genérico es invariante y siete
#: consumidores anotan `async_sessionmaker[AsyncSession]`). Sin admitirlo la
#: guarda pasó a ver CERO factorías — y la aserción de «vio algo» fue lo que lo
#: cazó, en vez de dejar el test en verde vacío.
_SESSIONMAKER_CALL = re.compile(r"\basync_sessionmaker(?:\[[^\]]*\])?\(")


def _call_arguments(source: str, open_paren: int) -> str:
    """El texto de los argumentos de la llamada que abre en ``open_paren``.

    Se cuentan paréntesis en vez de cortar en el primero que aparezca: el primer
    argumento real es ``bind=get_engine()``, así que un ``[^)]*`` se para dentro
    de la llamada anidada y la guarda daría un falso positivo sobre el código
    correcto — que es como se descubrió.

    Se quitan además los comentarios: un ``# class_=AfterCommitSession`` en prosa
    no puede contar como que la clase se pasa de verdad.
    """
    depth = 0
    for index in range(open_paren, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                body = source[open_paren + 1 : index]
                return "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    raise AssertionError("llamada a async_sessionmaker sin paréntesis de cierre")


def test_every_api_server_sessionmaker_builds_the_draining_session() -> None:
    """Ninguna factoría del api-server puede nacer sin la clase que drena.

    Ésta es la guarda que impide que el arreglo se pierda: quien añada una
    factoría de sesiones nueva y se deje ``class_=AfterCommitSession`` obtiene
    sesiones que registran callbacks post-commit y no los ejecutan jamás —
    exactamente el fallo que este trabajo cierra, y que no da ningún error.
    """
    seen: list[str] = []
    offenders: list[str] = []

    for path in sorted(_API_SERVER_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _SESSIONMAKER_CALL.finditer(source):
            line = source[: match.start()].count("\n") + 1
            where = f"{path.relative_to(_API_SERVER_SRC.parents[2])}:{line}"
            seen.append(where)
            if "class_=AfterCommitSession" not in _call_arguments(source, match.end() - 1):
                offenders.append(where)

    assert len(seen) >= 2, (
        "la guarda dejó de encontrar las factorías de sesiones del api-server "
        f"(vio {len(seen)}): o se movieron de sitio o el patrón ya no las casa, "
        "y en ambos casos esta guarda estaba pasando en vacío"
    )
    assert not offenders, (
        "estas factorías construyen sesiones que NO drenan `schedule_after_commit`, "
        "así que sus callbacks post-commit no los ejecuta nadie: " + ", ".join(offenders)
    )


def test_the_production_factories_hand_out_a_draining_session() -> None:
    """La otra mitad de la guarda: no basta con que el fuente lo diga.

    El chequeo estático mira el texto; éste mira el objeto que sale de las dos
    factorías reales, que es lo que usan las rutas.
    """
    from api_server.db.session import get_admin_sessionmaker, get_sessionmaker

    for factory in (get_sessionmaker, get_admin_sessionmaker):
        session = factory()()
        assert isinstance(session, AfterCommitSession), (
            f"{factory.__name__} entrega {type(session).__name__}, que no drena los "
            "callbacks post-commit"
        )
