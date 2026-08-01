"""La guarda canónica `verify_project_visible` (plan prod-14, quality-8).

El valor de este test no es la función (son ocho líneas): es el CONTRATO que las
cuatro copias que sustituye habían empezado a perder de vista.

  * el predicado incluye `deleted_at IS NULL` — un proyecto soft-borrado no
    acepta tareas/planes/conversaciones/webhooks nuevos;
  * el fallo es 404 y NO 403 — distinguirlos confirmaría que el proyecto existe
    en otro tenant;
  * el `detail` es idéntico en las dos variantes — un mensaje distinto por
    router es un canal lateral.

Sin base de datos: lo que se comprueba es la forma de la query y la del error.
El aislamiento real (que la RLS devuelva 0 filas para otro tenant) lo cubren los
tests de integración; aquí se fija que un 0-filas se traduce SIEMPRE en 404.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from api_server.routers import _guards
from api_server.routers._guards import (
    PROJECT_NOT_FOUND_DETAIL,
    verify_project_visible,
    verify_project_visible_id,
)
from fastapi import HTTPException

pytestmark = pytest.mark.unit

_ROUTERS_DIR = Path(_guards.__file__).parent

# Los cuatro routers de los que salió la guarda (prod-14 task_prod14_11). Si
# alguno deja de importarla, ha vuelto a tener su propia copia y el predicado
# puede divergir sin que se vea en el diff de los otros tres.
_CALLERS = ("tasks", "plans", "conversations", "incoming_webhook_configs")


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """Captura el SELECT compilado y devuelve lo que se le diga."""

    def __init__(self, value: Any) -> None:
        self._value = value
        self.statements: list[str] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": False})))
        return _Result(self._value)


@pytest.mark.asyncio
async def test_returns_the_project_when_visible() -> None:
    sentinel = object()
    session = _FakeSession(sentinel)

    got = await verify_project_visible(session, uuid4())  # type: ignore[arg-type]

    assert got is sentinel


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", [verify_project_visible, verify_project_visible_id])
async def test_zero_rows_becomes_404_with_the_shared_detail(guard: Any) -> None:
    session = _FakeSession(None)

    with pytest.raises(HTTPException) as exc:
        await guard(session, uuid4())

    assert exc.value.status_code == 404, (
        "debe ser 404 y no 403: un 403 confirmaría que el proyecto existe en otro"
        " tenant, que es justo lo que la RLS oculta"
    )
    assert exc.value.detail == PROJECT_NOT_FOUND_DETAIL


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", [verify_project_visible, verify_project_visible_id])
async def test_query_filters_soft_deleted_projects(guard: Any) -> None:
    session = _FakeSession(None)

    with pytest.raises(HTTPException):
        await guard(session, uuid4())

    assert len(session.statements) == 1
    sql = session.statements[0].lower()
    assert "projects.deleted_at is null" in sql, (
        "la guarda perdió el filtro de soft-delete: un proyecto borrado volvería a"
        f" aceptar hijos nuevos. SQL emitido: {sql}"
    )
    assert "projects.id = " in sql


# ---------------------------------------------------------------------------
# La otra mitad: que el módulo TENGA llamantes.
#
# Un helper canónico que nadie importa no unifica nada — es el patrón «mecanismo
# entregado, cero llamantes» del apartado 5 de `verificar-antes-de-implementar.md`.
# Estos dos tests son la guarda estática de que las cuatro copias no vuelven.
# ---------------------------------------------------------------------------
def _router_sources() -> list[tuple[str, str]]:
    return [
        (path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(_ROUTERS_DIR.glob("*.py"))
        if path.stem != "_guards"
    ]


def test_the_four_routers_call_the_canonical_guard() -> None:
    sources = dict(_router_sources())
    missing = [name for name in _CALLERS if name not in sources]
    assert not missing, f"el descubrimiento dejó de encontrar routers: {missing}"

    offenders = [
        name
        for name in _CALLERS
        if "from api_server.routers._guards import" not in sources[name]
        and "from ._guards import" not in sources[name]
    ]
    assert not offenders, (
        "estos routers ya no importan la guarda canónica de `_guards.py`, así que"
        f" volvieron a tener copia propia: {offenders}"
    )


def test_no_router_redefines_the_project_visibility_guard() -> None:
    sources = _router_sources()
    assert len(sources) >= 20, (
        f"el descubrimiento de routers dejó de encontrar ficheros (vio {len(sources)}):"
        " esta guarda estaría pasando vacíamente"
    )

    offenders: list[str] = []
    for name, source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name.endswith(
                "verify_project_visible"
            ):
                offenders.append(f"{name}.py:{node.lineno}:{node.name}")

    assert not offenders, (
        "la guarda de visibilidad de proyecto volvió a estar duplicada fuera de"
        f" `routers/_guards.py`: {offenders}. Cada copia puede perder el filtro"
        " `deleted_at` o cambiar el `detail` por su cuenta, y eso es un hueco de"
        " tenancy que no se ve en el diff de las otras."
    )
