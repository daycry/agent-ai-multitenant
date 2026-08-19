"""El troceo de `routers/agents.py` en paquete no puede mover ni una ruta.

Plan prod-16, ``task_prod16_12``: «`routers/agents.py` (1414): extraer sub-módulos
cohesivos (p. ej. CRUD vs diagnóstico)». Es un refactor puro, y "refactor puro" es
una intención hasta que algo la convierte en propiedad. Eso es este fichero: el
conjunto de rutas —camino, métodos y nombre de la función— se capturó del monolito
de 1462 líneas ANTES de partirlo y está escrito abajo literal.

## La trampa concreta de ESTE router, que el de `sso` no tenía

`GET /agents/provider-options` y `GET /agents/{agent_id}` **solapan**: la segunda
es paramétrica de un solo segmento, así que casa con `provider-options` igual de
bien. Hoy no pasa nada porque la literal se declara 35 líneas ANTES en el mismo
fichero —y el comentario de esa línea lo dice: «DEBE ir antes de GET /{agent_id}»—
y FastAPI casa por ORDEN DE REGISTRO.

Repartir los `@router.get` entre módulos cambia ese orden. Si `provider-options`
acaba en un módulo montado después del que trae `/{agent_id}`, el endpoint deja de
existir: la petición cae en `get_agent`, que intenta parsear ``"provider-options"``
como UUID y responde **422**. Ningún test de rutas por conjunto lo vería —el
conjunto sigue teniendo las 18—, ningún import se rompe, y mypy calla. Por eso
aquí hay un test de ORDEN además del de conjunto.

En `test_sso_router_package.py` el equivalente era
``test_no_single_segment_route_is_parametric``: allí la condición se cumplía y
bastaba con congelarla. Aquí NO se cumple, así que hay que afirmar sobre el orden
real de registro.

## Por qué el orden se lee con `iter_routes_with_paths` y no con `route_paths`

`routing_introspection.route_paths` devuelve un `set` — justo lo que no sirve para
afirmar sobre el orden. `iter_routes_with_paths` hace la misma travesía (desciende
por ``original_router`` aplicando el prefijo de ``include_context``) pero devuelve
una LISTA en orden de registro, y funciona igual en FastAPI 0.136 (donde
`include_router` aplana) que en 0.141 (donde no).

Aquí había una copia local de esa travesía, y el fichero hermano
`test_sso_router_package.py` leía `route.path` a pelo — que con 0.141 devuelve el
camino del hijo SIN el prefijo del padre y por eso puso rojo el CI. Ahora los dos
usan la misma función: este router no lleva prefijo y el `.path` crudo coincidía
con el efectivo por casualidad, pero el día que lo lleve no queremos descubrirlo
en CI.
"""

from __future__ import annotations

import pytest
from api_server.routing_introspection import iter_routes_with_paths, route_paths

pytestmark = pytest.mark.unit


# Capturado del `routers/agents.py` monolítico (1462 líneas) el 2026-08-12, justo
# antes de partirlo: (camino, métodos, nombre de la función que lo sirve).
ROUTES_BEFORE_THE_SPLIT: frozenset[tuple[str, tuple[str, ...], str]] = frozenset(
    {
        ("/agents", ("GET",), "list_agents"),
        ("/agents", ("POST",), "create_agent"),
        ("/agents/provider-options", ("GET",), "get_agent_provider_options"),
        ("/agents/{agent_id}", ("GET",), "get_agent"),
        ("/agents/{agent_id}", ("PUT",), "update_agent"),
        ("/agents/{agent_id}", ("DELETE",), "delete_agent"),
        ("/agents/{source_id}/fork", ("POST",), "fork_agent"),
        ("/agents/{fork_id}/diff", ("GET",), "diff_fork_against_source"),
        ("/agents/{fork_id}/merge", ("POST",), "merge_from_source"),
        ("/agents/{agent_id}/knowledge-bases", ("GET",), "list_agent_kbs"),
        ("/agents/{agent_id}/knowledge-bases", ("POST",), "grant_kb_to_agent"),
        ("/agents/{agent_id}/knowledge-bases/{kb_id}", ("DELETE",), "revoke_kb_from_agent"),
        ("/agents/{agent_id}/tools", ("GET",), "list_agent_tools"),
        ("/agents/{agent_id}/tools", ("PUT",), "set_agent_tools"),
        ("/agents/{agent_id}/skills", ("GET",), "list_agent_skills"),
        ("/agents/{agent_id}/skills", ("PUT",), "set_agent_skills"),
        ("/agents/{agent_id}/effective-tools", ("GET",), "get_agent_effective_tools"),
        ("/agents/{agent_id}/capabilities", ("GET",), "get_agent_capabilities"),
    }
)

#: Rutas AÑADIDAS al paquete después del troceo, con la tarea que las trajo.
#:
#: El inventario de arriba se capturó del monolito y su afirmación es «el troceo
#: no movió ni una ruta». Meter una ruta nueva ahí la convertiría en una mentira
#: —diría que el monolito la servía— así que las adiciones van aparte y **cada
#: una con su procedencia escrita**. Lo que la guarda sigue impidiendo es lo que
#: importa: que una ruta DESAPAREZCA en un refactor, o que aparezca una que nadie
#: declaró aquí.
ROUTES_ADDED_AFTER_THE_SPLIT: frozenset[tuple[str, tuple[str, ...], str]] = frozenset(
    {
        # `task_gov_02`: el historial versionado del `system_prompt`. Vive en
        # `.prompt_versions`, y su camino tiene DOS segmentos, así que no entra en
        # la trampa de orden de `provider-options` (el test de abajo lo comprueba
        # para TODA ruta literal de un segmento, no sólo para aquélla).
        ("/agents/{agent_id}/prompt-versions", ("GET",), "list_agent_prompt_versions"),
    }
)

#: El conjunto que el paquete debe servir HOY.
EXPECTED_ROUTES = ROUTES_BEFORE_THE_SPLIT | ROUTES_ADDED_AFTER_THE_SPLIT


def _signature(container: object) -> set[tuple[str, tuple[str, ...], str]]:
    """Camino EFECTIVO + métodos + nombre de cada ruta (ver el docstring del módulo)."""
    return {
        (path, tuple(sorted(getattr(route, "methods", ()) or ())), route.name)
        for route, path in iter_routes_with_paths(container)
    }


def _ordered_effective_paths(container: object) -> list[str]:
    """Los caminos EFECTIVOS en orden de registro (lo que FastAPI usa para casar)."""
    return [path for _, path in iter_routes_with_paths(container)]


def test_the_package_still_serves_every_route_the_monolith_served() -> None:
    """Ninguna ruta del monolito se perdió, y las nuevas están todas declaradas.

    Las dos direcciones importan por motivos distintos: una ruta que falta es una
    regresión silenciosa (el cliente recibe 404 donde recibía 200), y una que
    sobra sin declararse es superficie de API que entró sin que nadie la mirase.
    """
    from api_server.routers.agents import router

    got = _signature(router)

    assert got == set(EXPECTED_ROUTES), (
        f"sobran {sorted(got - set(EXPECTED_ROUTES))} (¿falta declararlas en "
        f"ROUTES_ADDED_AFTER_THE_SPLIT?), faltan {sorted(set(EXPECTED_ROUTES) - got)}"
    )


def test_provider_options_is_still_registered_before_the_agent_id_wildcard() -> None:
    """La trampa real de partir ESTE router (ver el docstring del módulo).

    Si `/agents/provider-options` queda detrás de `/agents/{agent_id}`, el
    endpoint desaparece en silencio: la petición la sirve `get_agent`, que
    responde 422 al no poder parsear ``"provider-options"`` como UUID.
    """
    from api_server.routers.agents import router

    paths = _ordered_effective_paths(router)

    assert "/agents/provider-options" in paths, f"la ruta literal ya no existe: {paths}"
    assert "/agents/{agent_id}" in paths, f"el comodín ya no existe: {paths}"
    assert paths.index("/agents/provider-options") < paths.index("/agents/{agent_id}"), (
        "`/agents/provider-options` quedó registrada DESPUÉS del comodín "
        f"`/agents/{{agent_id}}`, que se la come: {paths}"
    )


def test_no_literal_single_segment_route_hides_behind_the_wildcard() -> None:
    """La versión GENERAL del test de arriba, que no envejece.

    Aquél nombra `provider-options` porque es la ruta que existía. La trampa, en
    cambio, es de la FORMA: cualquier ruta literal de un segmento bajo `/agents`
    la arma otra vez, y quien la añada dentro de un año no va a leer el docstring
    de este fichero. Esta guarda la comprueba para todas.

    Sólo cuentan las que comparten método con el comodín: una literal de un
    segmento con `POST` no colisiona con un `GET /agents/{agent_id}`.
    """
    from api_server.routers.agents import router

    rutas = list(iter_routes_with_paths(router))
    # Una LISTA y no un dict por camino: `/agents/{agent_id}` son TRES rutas
    # (GET, PUT, DELETE) con el mismo camino, y un dict se queda sólo con la
    # última. Con el DELETE como único comodín, la comparación de métodos contra
    # una literal `GET` sale vacía y la guarda pasa en verde con la trampa puesta.
    # Verificado: el primer intento de este test tenía ese fallo exacto y el
    # rojo provocado a mano fue lo que lo destapó.
    comodines = [
        (index, tuple(sorted(getattr(route, "methods", ()) or ())))
        for index, (route, path) in enumerate(rutas)
        if path == "/agents/{agent_id}"
    ]
    assert len(comodines) >= 3, (
        f"esperaba GET/PUT/DELETE sobre `/agents/{{agent_id}}`; vi {comodines}"
    )

    literales = [
        (index, path, tuple(sorted(getattr(route, "methods", ()) or ())))
        for index, (route, path) in enumerate(rutas)
        if path.startswith("/agents/") and "{" not in path and path.count("/") == 2
    ]
    assert literales, (
        "la guarda dejó de encontrar rutas literales de un segmento bajo `/agents`:"
        " estaría pasando en vacío"
    )

    tapadas = [
        f"{path} ({'/'.join(metodos)}) se registra tras el comodín"
        for index, path, metodos in literales
        for comodin_index, comodin_metodos in comodines
        if set(metodos) & set(comodin_metodos) and comodin_index < index
    ]
    assert not tapadas, (
        "estas rutas literales quedan detrás de `/agents/{agent_id}`, que las sirve"
        " y responde 422 al parsear el segmento como UUID: " + "; ".join(tapadas)
    )


def test_route_paths_sees_through_the_composed_router() -> None:
    """Un router compuesto de sub-routers arma la trampa de FastAPI 0.141.

    Con ``{getattr(r, "path") for r in router.routes}`` esto daría casi vacío en
    0.141 — y `main._is_admin_surface` decide con esa lectura si un router lleva
    la guarda de System Admin.
    """
    from api_server.routers.agents import router

    paths = route_paths(router)

    assert "/agents" in paths
    assert "/agents/provider-options" in paths
    assert "/agents/{agent_id}/capabilities" in paths
    assert "/agents/{agent_id}/prompt-versions" in paths
    assert len(paths) == len({path for path, _, _ in EXPECTED_ROUTES})


def test_the_agents_router_is_not_an_admin_surface() -> None:
    """`/agents` es superficie de TENANT: montarla con la guarda de System Admin
    la 403-earía entera. `_is_admin_surface` debe seguir diciendo `False`, y con
    un router compuesto eso ya no es trivial (ver el test de arriba)."""
    from api_server.main import _is_admin_surface
    from api_server.routers.agents import router

    assert _is_admin_surface(router) is False


def test_the_split_actually_split_it() -> None:
    """Que el paquete exista no basta: los sub-módulos deben tener rutas propias.

    Sin esto, un `agents/__init__.py` que reexportase el monolito entero pasaría
    todos los tests de arriba — el troceo estaría "hecho" sin haber partido nada.
    """
    from api_server.routers.agents import capabilities, common, crud, forks, knowledge_bases
    from api_server.routers.agents import skills as skills_mod
    from api_server.routers.agents import tools as tools_mod

    assert len(_signature(crud.router)) >= 5
    assert len(_signature(forks.router)) == 3
    assert len(_signature(knowledge_bases.router)) == 3
    assert len(_signature(tools_mod.router)) == 3
    assert len(_signature(skills_mod.router)) == 2
    assert len(_signature(capabilities.router)) == 1
    # `common` es la excepción deliberada: comparte piezas, no publica rutas.
    assert not hasattr(common, "router")


def test_every_module_stays_under_the_size_that_motivated_the_split() -> None:
    """Trocear repartiendo la deuda en dos piezas de 700 no es trocear.

    El plan prod-16 mide «piezas del troceado > 500 líneas» como métrica propia
    justamente porque ya pasó en el panel.
    """
    from pathlib import Path

    import api_server.routers.agents as pkg

    package_dir = Path(pkg.__file__).parent
    too_big = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(package_dir.glob("*.py"))
        if len(path.read_text(encoding="utf-8").splitlines()) > 500
    }
    assert not too_big, f"piezas del troceo por encima de 500 líneas: {too_big}"


def test_the_symbols_other_modules_import_are_still_on_the_package() -> None:
    """`routers/teams.py` hace `from api_server.routers.agents import
    _clone_agent_capabilities`, y `main.py` importa `router`. Un troceo que los
    mueva a un submódulo sin reexportarlos rompe el ARRANQUE, no un test."""
    import api_server.routers.agents as pkg

    for symbol in ("router", "_clone_agent_capabilities"):
        assert hasattr(pkg, symbol), f"`api_server.routers.agents.{symbol}` desapareció"


def test_the_response_models_of_every_route_survived() -> None:
    """El enunciado del plan pide «mismos `response_model`». Un `include_router`
    perdido o un decorador copiado a medias los cambia sin tocar el camino."""
    from api_server.routers.agents import router

    by_name = {
        route.name: getattr(route, "response_model", None)
        for route, _ in iter_routes_with_paths(router)
    }

    from api_server.capabilities import CapabilitiesResponse
    from api_server.routers.agents.tools import EffectiveToolsResponse
    from api_server.schemas.agents import (
        AgentDiffResponse,
        AgentProviderOptionsResponse,
        AgentResponse,
    )

    assert by_name["get_agent"] is AgentResponse
    assert by_name["create_agent"] is AgentResponse
    assert by_name["update_agent"] is AgentResponse
    assert by_name["merge_from_source"] is AgentResponse
    assert by_name["fork_agent"] is AgentResponse
    assert by_name["list_agents"] == list[AgentResponse]
    assert by_name["get_agent_provider_options"] is AgentProviderOptionsResponse
    assert by_name["diff_fork_against_source"] is AgentDiffResponse
    assert by_name["get_agent_effective_tools"] is EffectiveToolsResponse
    assert by_name["get_agent_capabilities"] is CapabilitiesResponse
    assert by_name["delete_agent"] is None
    assert by_name["revoke_kb_from_agent"] is None


def test_the_status_codes_that_are_not_200_survived() -> None:
    """Tres rutas devuelven 201 y dos 204. Se pierden con un decorador recortado
    y el síntoma llega al frontend, no a la suite."""
    from api_server.routers.agents import router

    codes = {
        route.name: getattr(route, "status_code", None)
        for route, _ in iter_routes_with_paths(router)
    }

    assert codes["create_agent"] == 201
    assert codes["fork_agent"] == 201
    assert codes["grant_kb_to_agent"] == 201
    assert codes["delete_agent"] == 204
    assert codes["revoke_kb_from_agent"] == 204
