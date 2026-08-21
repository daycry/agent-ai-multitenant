"""El troceo de `routers/sso.py` en paquete no puede mover ni una ruta.

Plan prod-16, ``task_prod16_10``: «separar el router mixto en paquete
``routers/sso/`` con ``oidc.py``, ``saml.py`` y ``common.py``. **Refactor puro**:
mismas rutas, mismos ``response_model``, tests de integración SSO existentes en
verde sin modificarlos.»

"Refactor puro" es una intención, no una propiedad. Lo que la convierte en
propiedad es este fichero: el conjunto de rutas —camino, métodos y nombre de la
función— se capturó del monolito de 1654 líneas ANTES de partirlo y está escrito
abajo literal. Si el troceo pierde un `include_router`, cambia un prefijo o
duplica un camino, esto se pone rojo diciendo exactamente qué sobra y qué falta.

Dos cosas más que fija, y que son las que de verdad muerden en un troceo así:

1. **El ORDEN de registro.** FastAPI casa por orden, así que repartir los
   `@router.get` entre cuatro módulos puede hacer que una ruta paramétrica se
   coma a una literal (el clásico: `/{provider_id}` registrado antes que
   `/providers`). Se comprueba que ninguna ruta de una sola parte sea
   paramétrica, que es la condición bajo la cual el reparto es seguro.

2. **Que la introspección siga viendo las rutas.** Este paquete es el primero
   del repo que anida sub-routers, o sea el primero que arma de verdad la trampa
   de FastAPI 0.141: `include_router` deja de aplanar y `router.routes` presenta
   `_IncludedRouter` sin `.path`. `main._is_admin_surface` decide con eso si un
   router lleva la guarda de System Admin. Aquí se afirma sobre `route_paths` /
   `iter_routes_with_paths`, que es lo que hay que usar — y de paso queda un caso
   REAL de router compuesto sobre el que ese contrato se ejercita.

   Que la trampa era real lo demostró este mismo fichero: comparaba el conjunto
   leyendo `route.path` a pelo, y con 0.141 eso devuelve el camino del hijo SIN
   el `/auth/sso` del padre. Verde en el `.venv` (0.136.1), rojo en CI (0.141.1
   del `uv.lock`). El test que vigilaba la trampa cayó en ella.
"""

from __future__ import annotations

import pytest
from api_server.routing_introspection import iter_routes_with_paths, route_paths

pytestmark = pytest.mark.unit


# Capturado del `routers/sso.py` monolítico el 2026-08-10, justo antes de
# partirlo: (camino, métodos, nombre de la función que lo sirve).
ROUTES_BEFORE_THE_SPLIT: frozenset[tuple[str, tuple[str, ...], str]] = frozenset(
    {
        ("/auth/sso/api-path-prefix", ("GET",), "get_api_path_prefix_endpoint"),
        ("/auth/sso/api-path-prefix", ("PUT",), "put_api_path_prefix"),
        ("/auth/sso/config", ("GET",), "list_sso_configs"),
        ("/auth/sso/config", ("POST",), "create_sso_config"),
        ("/auth/sso/config/{config_id}", ("DELETE",), "delete_sso_config"),
        ("/auth/sso/config/{config_id}", ("PUT",), "update_sso_config"),
        ("/auth/sso/oidc/callback", ("GET",), "oidc_callback"),
        ("/auth/sso/oidc/callback-url", ("GET",), "get_oidc_callback_url"),
        ("/auth/sso/oidc/templates", ("GET",), "list_oidc_templates"),
        ("/auth/sso/providers", ("GET",), "list_public_providers"),
        ("/auth/sso/public-base-url", ("GET",), "get_public_base_url"),
        ("/auth/sso/public-base-url", ("PUT",), "put_public_base_url"),
        ("/auth/sso/saml/acs", ("POST",), "saml_acs"),
        ("/auth/sso/saml/config", ("GET",), "list_saml_configs"),
        ("/auth/sso/saml/config", ("POST",), "create_saml_config"),
        ("/auth/sso/saml/config/{config_id}", ("DELETE",), "delete_saml_config"),
        ("/auth/sso/saml/config/{config_id}", ("PUT",), "update_saml_config"),
        ("/auth/sso/saml/parse-metadata", ("POST",), "parse_saml_idp_metadata"),
        ("/auth/sso/saml/sp-metadata", ("GET",), "get_saml_sp_metadata"),
        ("/auth/sso/{provider_id}/oidc/login", ("GET",), "oidc_login"),
        ("/auth/sso/{provider_id}/saml/login", ("GET",), "saml_login"),
    }
)

DISCOVERY_ROUTE_BEFORE_THE_SPLIT = ("/auth/discover", ("GET",), "discover_login")


def _signature(container: object) -> set[tuple[str, tuple[str, ...], str]]:
    """Camino EFECTIVO + métodos + nombre de cada ruta.

    El camino se toma de :func:`iter_routes_with_paths`, no de ``route.path``, y
    la diferencia no es cosmética: este router monta sus sub-routers bajo el
    ``prefix="/auth/sso"`` del padre, y desde FastAPI 0.141 ``include_router`` ya
    no aplana, así que el ``.path`` de la ruta hija es ``/providers`` a secas. Leer
    ``.path`` daba verde en el ``.venv`` de desarrollo (0.136.1, que sí aplanaba) y
    rojo en CI (0.141.1 del ``uv.lock``) diciendo que faltaban las 21 rutas y
    sobraban las 21 mismas sin prefijo — la trampa que el docstring de este módulo
    anuncia, cerrándose sobre el propio test que la vigila.
    """
    return {
        (path, tuple(sorted(getattr(route, "methods", ()) or ())), route.name)
        for route, path in iter_routes_with_paths(container)
    }


def test_the_package_serves_exactly_the_routes_the_monolith_served() -> None:
    from api_server.routers.sso import router

    got = _signature(router)

    assert got == set(ROUTES_BEFORE_THE_SPLIT), (
        f"sobran {sorted(got - set(ROUTES_BEFORE_THE_SPLIT))}, "
        f"faltan {sorted(set(ROUTES_BEFORE_THE_SPLIT) - got)}"
    )


def test_discovery_still_hangs_one_level_up_at_auth_discover() -> None:
    """`/auth/discover` NO va bajo `/auth/sso`: es lo que la UI pregunta antes."""
    from api_server.routers.sso import discovery_router

    assert _signature(discovery_router) == {DISCOVERY_ROUTE_BEFORE_THE_SPLIT}


def test_route_paths_sees_through_the_composed_router() -> None:
    """El caso que la trampa de FastAPI 0.141 haría desaparecer en silencio.

    Con `{getattr(r, "path") for r in router.routes}` esto daría un conjunto
    casi vacío en 0.141 — y `_is_admin_surface` decide con esa lectura.
    """
    from api_server.routers.sso import router

    paths = route_paths(router)

    assert "/auth/sso/providers" in paths
    assert "/auth/sso/oidc/callback" in paths
    assert "/auth/sso/saml/acs" in paths
    assert len(paths) == len({path for path, _, _ in ROUTES_BEFORE_THE_SPLIT})


def test_no_single_segment_route_is_parametric() -> None:
    """La condición que hace SEGURO repartir las rutas entre cuatro módulos.

    Repartirlas cambia el orden de registro, y FastAPI casa por orden. El único
    modo de que eso rompa algo es que una ruta paramétrica quede antes que una
    literal con la que solape. Las dos paramétricas de este router tienen tres
    partes y literal distinto en la segunda (`/{provider_id}/oidc/login` vs
    `/{provider_id}/saml/login`), así que sólo hay que garantizar que ninguna
    ruta corta sea paramétrica: si alguien añadiese `/auth/sso/{algo}`, se
    comería a `/auth/sso/providers` según en qué módulo cayera.
    """
    from api_server.routers.sso import router

    for path in route_paths(router):
        segments = path.strip("/").split("/")
        parametric = [s for s in segments if s.startswith("{")]
        if parametric:
            assert len(segments) > 3, (
                f"{path} es paramétrica y corta: el reparto entre módulos "
                "puede hacer que se coma a una ruta literal según el orden de montaje"
            )


def test_the_split_actually_split_it() -> None:
    """Que el paquete exista no basta: los cuatro módulos deben tener rutas propias.

    Sin esto, un `sso/__init__.py` que reexportase el monolito entero pasaría
    todos los tests de arriba — el troceo estaría "hecho" sin haber partido nada,
    que es literalmente lo que ya pasó en el panel con `mcp-server-sections.tsx`.
    """
    from api_server.routers.sso import common, discovery, oidc, saml

    assert len(_signature(oidc.router)) >= 5
    assert len(_signature(saml.router)) >= 5
    assert len(_signature(discovery.router)) >= 3
    # `common` es la excepción deliberada: comparte piezas, no publica rutas.
    assert not hasattr(common, "router")
