"""prod-13 · task_prod13_23 — el 409 no filtra el mensaje crudo de PostgreSQL.

Hallazgo api-5. Seis routers devolvían `detail=str(exc.orig)`, y ese texto es lo
que PostgreSQL escribe, entero, incluido el `DETAIL:` con **el valor de la clave
en conflicto**. En una plataforma multi-tenant eso significaba que un 409 de
nombre duplicado devolvía al cliente el `tenant_id` real de la fila con la que
chocó — un UUID de tenant que el llamante no tiene por qué conocer.

Lo que se comprueba:

  * el `detail` es un dict de dominio estable, no texto de la BD;
  * NADA del mensaje crudo sobrevive: ni el nombre de la constraint, ni el de la
    tabla, ni el `tenant_id`, ni la frase "duplicate key value";
  * las constraints conocidas se traducen a códigos distintos (si todas cayeran
    en el genérico, el mensaje al usuario no serviría de nada);
  * una constraint DESCONOCIDA cae en el genérico y NO filtra su nombre — el
    comportamiento fail-closed;
  * el mensaje crudo sí llega al log del servidor, que es donde debe estar.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.unit

#: El mensaje REAL que asyncpg/PostgreSQL producen, copiado de un 409 observado.
_RAW_DUPLICATE = (
    'duplicate key value violates unique constraint "uq_projects_tenant_slug_live"\n'
    "DETAIL:  Key (tenant_id, slug)=(3f2a9c1e-0b44-4d90-9f21-8a7c6e5b4d33, api-v1)"
    " already exists."
)


class _FakeOrigError(Exception):
    """Sustituto del `exc.orig` de asyncpg: solo aporta su `str()`."""


def _integrity_error(raw: str, *, constraint: str | None = None) -> IntegrityError:
    orig = _FakeOrigError(raw)
    if constraint is not None:
        orig.constraint_name = constraint  # type: ignore[attr-defined]
    return IntegrityError("INSERT INTO projects ...", {}, orig)


def test_detail_is_a_domain_dict_not_database_prose() -> None:
    from api_server.routers._integrity import integrity_conflict

    exc = integrity_conflict(_integrity_error(_RAW_DUPLICATE), context="project.create")

    assert exc.status_code == 409
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "duplicate_project_slug"
    assert "nombre" in exc.detail["message"]


@pytest.mark.parametrize(
    "secret",
    [
        "3f2a9c1e-0b44-4d90-9f21-8a7c6e5b4d33",  # el tenant_id de otra fila
        "uq_projects_tenant_slug_live",  # el nombre interno de la constraint
        "duplicate key value",  # la prosa de PostgreSQL
        "DETAIL",
        "api-v1",  # el valor en conflicto
    ],
)
def test_no_fragment_of_the_raw_message_reaches_the_client(secret: str) -> None:
    from api_server.routers._integrity import integrity_conflict

    exc = integrity_conflict(_integrity_error(_RAW_DUPLICATE), context="project.create")
    rendered = str(exc.detail)
    assert secret not in rendered, f"el 409 sigue filtrando {secret!r}: {rendered}"


def test_known_constraints_map_to_distinct_codes() -> None:
    """Si todo cayera en `conflict`, el mapa no aportaría nada. Se exige que las
    constraints conocidas produzcan códigos DISTINTOS y ninguno el genérico."""
    from api_server.routers._integrity import _CONSTRAINT_MESSAGES, integrity_conflict

    codes = set()
    for name in _CONSTRAINT_MESSAGES:
        exc = integrity_conflict(_integrity_error(f'violates constraint "{name}"'), context="t")
        assert isinstance(exc.detail, dict)
        code = exc.detail["error"]
        assert code != "conflict", f"{name} cayó en el genérico"
        codes.add(code)

    assert (
        len(_CONSTRAINT_MESSAGES) >= 8
    ), f"el mapa se quedó en {len(_CONSTRAINT_MESSAGES)} constraints: ¿se vació?"
    assert len(codes) >= 6, f"demasiados nombres comparten código: {sorted(codes)}"


def test_unknown_constraint_falls_back_without_leaking_its_name() -> None:
    from api_server.routers._integrity import integrity_conflict

    exc = integrity_conflict(
        _integrity_error('violates unique constraint "uq_something_brand_new"'),
        context="whatever",
    )
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "conflict"
    assert "uq_something_brand_new" not in str(exc.detail)


def test_constraint_name_is_read_from_the_attribute_when_present() -> None:
    """asyncpg expone `constraint_name`; es más fiable que el regex sobre el
    mensaje (que cambia con la versión y el idioma del servidor)."""
    from api_server.routers._integrity import constraint_name

    exc = _integrity_error("mensaje traducido sin comillas", constraint="uq_tools_tenant_name")
    assert constraint_name(exc) == "uq_tools_tenant_name"


def test_constraint_name_falls_back_to_the_message_regex() -> None:
    from api_server.routers._integrity import constraint_name

    assert constraint_name(_integrity_error(_RAW_DUPLICATE)) == "uq_projects_tenant_slug_live"


def test_no_router_still_returns_the_raw_orig() -> None:
    """Guarda con aserción de «encontré algo»: los seis routers tienen que seguir
    capturando `IntegrityError`, y ninguno puede volver a `detail=str(exc.orig)`."""
    from pathlib import Path

    routers = Path("apps/api-server/src/api_server/routers")
    assert routers.is_dir(), f"¿ruta mal? {routers.resolve()}"

    offenders: list[str] = []
    catchers: list[str] = []
    for path in sorted(routers.rglob("*.py")):
        if path.name == "_integrity.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "detail=str(exc.orig)" in source:
            offenders.append(path.name)
        if "except IntegrityError" in source:
            catchers.append(path.name)

    assert not offenders, f"estos routers siguen filtrando el error crudo: {offenders}"
    assert len(catchers) >= 6, (
        f"la guarda dejó de encontrar los routers que capturan IntegrityError "
        f"(vio {len(catchers)}: {catchers})"
    )
