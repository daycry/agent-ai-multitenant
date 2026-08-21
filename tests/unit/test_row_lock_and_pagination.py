"""prod-13 · task_prod13_17 + task_prod13_22 — bloqueo de fila y válvulas de listado.

Dos hallazgos distintos, los dos verificables sin BD porque lo que hay que probar
es **el SQL que se emite** y **la firma que FastAPI publica**:

  * api-10 — `get_writable_or_404(..., for_update=True)` tiene que producir
    `SELECT … FOR UPDATE`, y los endpoints que FIRMAN un plan tienen que pedirlo.
    Un test que solo mirase el parámetro no distinguiría entre "acepta el flag" y
    "lo aplica".
  * api-6 / perf-8 — los tres listados sin cota aceptan `limit`/`offset` con los
    límites compartidos, y el visor de citas ya NO selecciona `Chunk.embedding`
    (un `vector(768)`, ~3 KB por fila).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# api-10 — FOR UPDATE
# ---------------------------------------------------------------------------
class _StatementRecorder:
    """Sesión falsa: guarda el statement y devuelve una fila cualquiera."""

    def __init__(self, row: object) -> None:
        self.statements: list[Any] = []
        self._row = row

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        row = self._row

        class _Result:
            @staticmethod
            def scalar_one_or_none() -> object:
                return row

        return _Result()

    def compiled_sql(self) -> str:
        assert self.statements, "no se emitió ningún statement"
        return str(self.statements[-1].compile(compile_kwargs={"literal_binds": False}))


def _principal() -> Any:
    from api_server.auth.deps import AuthPrincipal

    return AuthPrincipal(user_id=uuid4(), tenant_id=uuid4(), session_id=uuid4())


@pytest.mark.asyncio
async def test_for_update_false_emits_a_plain_select() -> None:
    from api_server.db.domain import Plan
    from api_server.routers._helpers import get_writable_or_404

    session = _StatementRecorder(object())
    await get_writable_or_404(
        session,  # type: ignore[arg-type]
        Plan,
        uuid4(),
        _principal(),
        not_found_detail="x",
    )
    assert "FOR UPDATE" not in session.compiled_sql()


@pytest.mark.asyncio
async def test_for_update_true_emits_select_for_update() -> None:
    from api_server.db.domain import Plan
    from api_server.routers._helpers import get_writable_or_404

    session = _StatementRecorder(object())
    await get_writable_or_404(
        session,  # type: ignore[arg-type]
        Plan,
        uuid4(),
        _principal(),
        not_found_detail="x",
        for_update=True,
    )
    sql = session.compiled_sql()
    assert "FOR UPDATE" in sql, sql
    # El bloqueo no puede haberse comido el filtro de tenant: sería cambiar una
    # carrera por una fuga cross-tenant.
    assert "tenant_id" in sql, sql


def test_the_signing_endpoints_take_the_row_lock() -> None:
    """Guarda con aserción de «encontré algo». Los tres endpoints que mueven el
    estado de un plan (`/approve`, `/approve-and-start`, `/start-execution`)
    tienen que pedir `for_update=True`; el resto de los `get_writable_or_404` de
    `plans.py` (CRUD normal) NO, porque serializar ahí no compra nada."""
    from pathlib import Path

    source = Path("apps/api-server/src/api_server/routers/plans.py").read_text(encoding="utf-8")
    total = source.count("get_writable_or_404(")
    # Se cuentan SITIOS DE LLAMADA, no apariciones del literal: un comentario que
    # mencione `for_update=True` no puede inflar la cuenta.
    locked = source.count('not_found_detail="plan not found", for_update=True')

    assert total >= 8, f"la guarda dejó de encontrar los llamantes (vio {total})"
    assert locked == 3, (
        f"esperaba 3 lookups con bloqueo de fila en plans.py, vi {locked}. "
        "Si se ha añadido una transición nueva, decide explícitamente si firma."
    )


def test_human_action_locks_the_task_row() -> None:
    from pathlib import Path

    source = Path("apps/api-server/src/api_server/routers/task_lifecycle.py").read_text(
        encoding="utf-8"
    )
    assert "select(Task).where(Task.id == task_id).with_for_update()" in source, (
        "apply_human_action volvió a leer la task sin FOR UPDATE (api-10)"
    )
    # El plan ya se leía bloqueado desde antes; que siga así.
    assert "with_for_update=True" in source


# ---------------------------------------------------------------------------
# api-6 — paginación de los tres listados
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("module", "func_name"),
    [
        ("api_server.routers.conversations", "list_conversations"),
        ("api_server.routers.knowledge_bases", "list_documents"),
        ("api_server.routers.knowledge_bases", "get_document_citations"),
    ],
)
def test_listing_endpoints_accept_bounded_limit_and_offset(module: str, func_name: str) -> None:
    """`limit`/`offset` presentes Y con los bounds compartidos: `le=MAX_PAGE_SIZE`
    es lo que impide que un cliente pida `?limit=1000000` y anule la válvula."""
    import importlib
    import inspect

    from api_server.routers._pagination import MAX_PAGE_SIZE

    mod = importlib.import_module(module)
    candidates = [
        name
        for name in dir(mod)
        if name == func_name or name.endswith(f"_{func_name}") or func_name in name
    ]
    fn = getattr(mod, func_name, None)
    assert fn is not None, f"{module} no expone {func_name} (candidatos: {candidates})"

    params = inspect.signature(fn).parameters
    assert "limit" in params, f"{func_name} sigue sin `limit`"
    assert "offset" in params, f"{func_name} sigue sin `offset`"

    # Los bounds de un `Query` de FastAPI viven en `metadata` como anotaciones de
    # `annotated_types` (`Ge(ge=1)` / `Le(le=500)`), no como atributos sueltos.
    def _bounds(query: Any) -> dict[str, Any]:
        return {
            type(item).__name__.lower(): getattr(item, type(item).__name__.lower())
            for item in getattr(query, "metadata", [])
            if type(item).__name__ in {"Ge", "Le"}
        }

    limit_bounds = _bounds(params["limit"].default)
    assert limit_bounds.get("le") == MAX_PAGE_SIZE, (
        f"{func_name}.limit no está acotado por MAX_PAGE_SIZE: {limit_bounds}"
    )
    assert limit_bounds.get("ge") == 1, limit_bounds
    assert _bounds(params["offset"].default).get("ge") == 0


# ---------------------------------------------------------------------------
# perf-8 — el visor de citas no arrastra el vector
# ---------------------------------------------------------------------------
def test_citations_query_does_not_select_the_embedding_vector() -> None:
    """El visor de citas no puede arrastrar el vector: son ~6 MB por PDF de 2.000
    chunks que nadie usa.

    **Esta guarda se reescribió el 2026-08-19 porque no cogía su propia
    regresión.** Antes hacía dos cosas y ninguna interrogaba al router: compilaba
    un `select(...)` construido A MANO en el propio test —o sea, comprobaba que
    SQLAlchemy funciona— y hacía `grep` de la cadena ANTIGUA
    (`select(Chunk).where(...)`). Añadir `Chunk.embedding` a la lista de columnas
    del router dejaba las dos aserciones en verde: se comprobó, y siguió pasando.

    Ahora se lee el AST del router, se localiza la llamada `select(...)` de la
    función de citas y se mira QUÉ COLUMNAS pide de verdad. La diferencia
    importa: el fallo que perf-8 quiere evitar es que alguien amplíe esa lista,
    no que alguien reescriba una cadena concreta.
    """
    import ast
    from pathlib import Path

    ruta = Path("apps/api-server/src/api_server/routers/knowledge_bases.py")
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    objetivo = next(
        (
            n
            for n in ast.walk(arbol)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
            and n.name == "get_document_citations"
        ),
        None,
    )
    assert objetivo is not None, (
        "no encuentro `get_document_citations` en el router: si se renombró, este"
        " test dejó de vigilar lo que dice vigilar"
    )

    # Las columnas de CADA `select(...)` dentro de la función.
    columnas: list[str] = []
    selects = 0
    for nodo in ast.walk(objetivo):
        if not (isinstance(nodo, ast.Call) and getattr(nodo.func, "id", None) == "select"):
            continue
        selects += 1
        for arg in nodo.args:
            if isinstance(arg, ast.Attribute) and getattr(arg.value, "id", None) == "Chunk":
                columnas.append(arg.attr)
            elif isinstance(arg, ast.Name) and arg.id == "Chunk":
                columnas.append("<ENTIDAD COMPLETA>")

    # No-vacuidad: si el walk no encontró nada, las aserciones de abajo pasarían
    # sobre una lista vacía. Es el modo de fallo por el que hubo que reescribir
    # esta guarda.
    assert selects >= 1, "no vi ningún `select(...)` en la función de citas"
    assert len(columnas) >= 3, (
        f"esperaba varias columnas explícitas de Chunk, vi {columnas!r}."
        " Con menos, o el parseo se rompió o el router volvió a pedir la entidad."
    )

    assert "<ENTIDAD COMPLETA>" not in columnas, (
        "el visor de citas volvió a hacer `select(Chunk)`, que trae la entidad"
        " entera y con ella el vector (perf-8)"
    )
    assert "embedding" not in columnas, (
        f"el visor de citas pide `Chunk.embedding` entre sus columnas: {columnas!r}."
        " Son ~6 MB por PDF de 2.000 chunks que la pantalla no usa (perf-8)."
    )


def test_citations_default_page_is_the_hard_maximum_not_the_soft_one() -> None:
    """El visor del admin-panel llama SIN `limit`. Con el default blando (100), un
    PDF de 2.000 chunks habría quedado silenciosamente truncado a las primeras
    páginas: cambiar "pesado" por "incompleto sin avisar" no es una mejora."""
    import inspect

    from api_server.routers._pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
    from api_server.routers.knowledge_bases import get_document_citations

    default = inspect.signature(get_document_citations).parameters["limit"].default.default
    assert default == MAX_PAGE_SIZE, default
    assert default != DEFAULT_PAGE_SIZE, "el default blando trunca el visor en silencio"


def test_citations_payload_makes_truncation_detectable() -> None:
    """`total` + `has_more` en la respuesta: aunque el cliente no pagine todavía,
    la truncación pasa de invisible a detectable."""
    from pathlib import Path

    source = Path("apps/api-server/src/api_server/routers/knowledge_bases.py").read_text(
        encoding="utf-8"
    )
    assert '"has_more": offset + len(chunks) < total' in source
    # `total` tiene que ser un COUNT real, no `len(chunks)` — que siempre sería
    # igual al tamaño de página y haría que `has_more` fuese siempre False.
    assert "select(func.count()).select_from(Chunk)" in source
