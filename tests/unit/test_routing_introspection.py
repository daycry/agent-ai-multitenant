"""La introspección de rutas no puede depender de la versión de FastAPI.

## El fallo que estos tests impiden

``main._is_admin_surface`` decide **si un router administrativo recibe la guarda
``require_hardened_system_admin``** mirando los ``path`` de sus rutas. Existe
porque una vez 9 de los 10 routers ``/admin/*`` —incluido el de restaurar
backups, que es destructivo— se publicaron sin esa dependencia: la idea es que
montar un router bajo ``/admin`` baste para que quede protegido, sin que nadie
tenga que acordarse.

Desde **FastAPI 0.141** ``include_router()`` ya no aplana: el padre recibe un
``_IncludedRouter`` **sin** ``.path``. Un router administrativo compuesto de
sub-routers presentaría entonces cero rutas ``/admin``, ``_is_admin_surface``
devolvería ``False``, y el router se montaría **sin guarda**. Sin error, sin
aviso: exactamente el fallo que la función existe para impedir, reintroducido
por una actualización de dependencia.

Hoy ningún router del repo anida sub-routers, así que la trampa está armada pero
no ha saltado. Estos tests son el pestillo.

## Por qué no se veía

El ``.venv`` de desarrollo tiene FastAPI 0.136.1; el ``uv.lock`` pina 0.141.1.
Solo diverge en un entorno instalado desde el lock — o sea, en CI, que lleva
caído por facturación. Verificado en un venv aislado con 0.141.1 el 2026-08-01.
"""

from __future__ import annotations

import pytest
from api_server.routing_introspection import route_paths
from fastapi import APIRouter, FastAPI

pytestmark = pytest.mark.unit


def test_route_paths_sees_routes_that_came_from_include_router() -> None:
    """El caso base: lo incluido se ve. Es lo que el idioma frágil pierde."""
    hijo = APIRouter()

    @hijo.get("/inbox/metrics")
    def _m() -> dict[str, str]:
        return {}

    app = FastAPI()
    app.include_router(hijo)

    assert "/inbox/metrics" in route_paths(app)


def test_route_paths_applies_the_prefix() -> None:
    """Con prefijo, el path que sale es el EFECTIVO, no el del hijo."""
    hijo = APIRouter()

    @hijo.get("/cosa")
    def _c() -> dict[str, str]:
        return {}

    app = FastAPI()
    app.include_router(hijo, prefix="/admin")

    paths = route_paths(app)
    assert "/admin/cosa" in paths, (
        "el prefijo del include se perdió: una guarda que decide por prefijo "
        "clasificaría mal el router"
    )


def test_route_paths_sees_routes_hidden_from_the_openapi_schema() -> None:
    """No vale resolver esto leyendo ``app.openapi()``.

    El esquema es la alternativa obvia y es INSUFICIENTE: una ruta con
    ``include_in_schema=False`` —``/metrics`` lo es a propósito, para no
    ensuciar los SDK generados— no aparece ahí. Una introspección basada en el
    OpenAPI daría por inexistente justo lo que no se publica, que suele ser lo
    operativo.
    """
    oculto = APIRouter()

    @oculto.get("/interna", include_in_schema=False)
    def _i() -> dict[str, str]:
        return {}

    app = FastAPI()
    app.include_router(oculto)

    assert "/interna" in route_paths(app)
    assert "/interna" not in app.openapi().get("paths", {})


def test_a_router_built_from_subrouters_still_reports_its_admin_paths() -> None:
    """**El test que importa**: la trampa de seguridad, cerrada.

    Un router administrativo compuesto de sub-routers tiene que seguir
    presentando sus rutas ``/admin/*``. Si esto se rompe,
    ``_is_admin_surface`` devuelve ``False`` y el router se monta sin la
    guarda de System Admin.
    """
    hijo = APIRouter()

    @hijo.get("/admin/backup/restore")
    def _r() -> dict[str, str]:
        return {}

    padre = APIRouter()
    padre.include_router(hijo)

    paths = route_paths(padre)
    assert "/admin/backup/restore" in paths
    assert all(p.startswith("/admin") for p in paths), (
        "todas las rutas son /admin: _is_admin_surface debe verlo como superficie "
        "administrativa y colgarle la guarda"
    )


def test_is_admin_surface_hardens_a_router_composed_of_subrouters() -> None:
    """La guarda de verdad, no su sustituto: se llama a la función real."""
    from api_server.main import _is_admin_surface

    hijo = APIRouter()

    @hijo.get("/admin/algo")
    def _a() -> dict[str, str]:
        return {}

    padre = APIRouter()
    padre.include_router(hijo)

    assert _is_admin_surface(padre) is True, (
        "un router admin hecho de sub-routers se montaría SIN "
        "require_hardened_system_admin — el fallo histórico, reintroducido"
    )


def test_is_admin_surface_still_refuses_a_mixed_router() -> None:
    """Y lo que ya rechazaba lo sigue rechazando: mezclar es error de cableado.

    Sin este caso, «arreglar» la introspección podría haber ablandado la
    comprobación hasta que un router mixto pasara — y un router mixto endurecido
    devuelve 403 a las rutas de tenant, mientras que sin endurecer deja
    abiertas las de admin. Por eso revienta al importar, a gritos.
    """
    from api_server.main import _is_admin_surface

    mixto = APIRouter()

    @mixto.get("/admin/uno")
    def _u() -> dict[str, str]:
        return {}

    @mixto.get("/publico")
    def _p() -> dict[str, str]:
        return {}

    with pytest.raises(RuntimeError, match="mixes /admin paths"):
        _is_admin_surface(mixto)


def test_iter_routes_survives_a_cycle() -> None:
    """Un ciclo no puede colgar el arranque del servidor.

    La función corre en `create_app()`: un bucle infinito aquí no da un error,
    da un proceso que no arranca nunca y un healthcheck que agota su plazo.
    """

    class Ciclico:
        path = "/raiz"

        @property
        def routes(self) -> list[object]:
            return [self]

    assert route_paths(type("C", (), {"routes": [Ciclico()]})()) == {"/raiz"}
