"""Leer las rutas de un router/app sin que la versión de FastAPI cambie la respuesta.

## Por qué existe

Hasta FastAPI 0.136, ``include_router()`` **aplanaba**: las rutas del hijo
aparecían directamente en ``padre.routes`` como ``APIRoute``, cada una con su
``.path`` ya prefijado. Desde **0.141** no: el padre recibe un objeto
``_IncludedRouter`` que NO tiene ``.path`` (devuelve ``None``) y tampoco
``.routes`` — el hijo cuelga de ``.original_router``.

O sea que este idioma, repetido por toda la base:

    paths = {getattr(route, "path", None) for route in app.routes}

pasa de ver ~300 rutas a ver cuatro, **sin lanzar ningún error**. Se queda con
las rutas montadas a pelo (``/openapi.json``, ``/docs``…) y pierde todo lo que
entró por un router.

## Por qué esto importa más de lo que parece

El fallo no es que dos tests se pongan rojos. Es que ``main._is_admin_surface``
**decide con esta introspección si un router lleva la guarda
``require_hardened_system_admin``**. Un router administrativo compuesto de
sub-routers presentaría ``path=""`` en todos, la lista de rutas ``/admin`` sería
vacía, la función devolvería ``False`` y el router se montaría **sin la guarda**
— que es exactamente el modo de fallo histórico que esa función existe para
impedir (9 de 10 routers ``/admin/*``, incluido el de restaurar backups, se
publicaron una vez sin ella).

Hoy no ocurre porque ningún router del repo anida sub-routers, pero es una
trampa armada: silenciosa, de seguridad, y que salta el día que alguien
refactorice un router grande partiéndolo en piezas — que es justo lo que este
repo hace a menudo.

## Cómo se descubrió

El ``.venv`` de desarrollo tiene FastAPI 0.136.1 y el ``uv.lock`` pina 0.141.1,
así que la divergencia solo se ve en un entorno instalado desde el lock, que es
lo que hace CI. Con CI caído por facturación, nadie la corría. Verificado en un
venv aislado con 0.141.1 el 2026-08-01.
"""

from __future__ import annotations

from typing import Any

__all__ = ["iter_routes", "iter_routes_with_paths", "route_paths"]


def iter_routes(container: Any) -> list[Any]:
    """Todas las rutas de ``container``, descendiendo por los routers incluidos.

    Devuelve los objetos ruta tal cual. **Ojo**: leer ``route.path`` de lo que
    sale de aquí NO da el camino efectivo, porque descender a un router incluido
    da los ``path`` del hijo **sin el prefijo con el que se montó**. Si necesitas
    el camino usa :func:`route_paths` (conjunto) o
    :func:`iter_routes_with_paths` (ruta + camino, en orden de registro).
    """
    out: list[Any] = []
    _walk(getattr(container, "routes", []) or [], out, None, set())
    return out


def _prefijo_de(route: Any) -> str:
    """El prefijo con el que se incluyó este router, o cadena vacía.

    FastAPI >= 0.141 lo guarda en ``include_context.prefix``. En 0.136 no hace
    falta: el aplanado ya había reescrito los ``path`` de las hijas.
    """
    ctx = getattr(route, "include_context", None)
    prefijo = getattr(ctx, "prefix", "") if ctx is not None else ""
    return prefijo if isinstance(prefijo, str) else ""


def _dependencias_de(route: Any) -> tuple[str, ...]:
    """Nombres de las dependencias con las que se MONTÓ este router.

    La otra mitad de lo mismo que ``_prefijo_de``, y por el mismo motivo. Hasta
    FastAPI 0.136 un ``include_router(dependencies=[...])`` fusionaba esas
    dependencias en el ``dependant`` de cada hija, así que quien caminaba el
    árbol resuelto las veía. Desde 0.141 el include es **perezoso**: la hija se
    queda como estaba y lo que se pasó al montarla vive en
    ``include_context.dependencies`` hasta que FastAPI resuelve la petición.

    Quien introspeccione sin mirar aquí concluirá que una ruta montada con una
    guarda NO la tiene. Es un falso negativo de los caros: hace rojo un test de
    seguridad que dice justo lo contrario de lo que pasa.
    """
    ctx = getattr(route, "include_context", None)
    deps = getattr(ctx, "dependencies", None) if ctx is not None else None
    nombres: list[str] = []
    for dep in deps or []:
        fn = getattr(dep, "dependency", None)
        if fn is not None:
            nombres.append(getattr(fn, "__name__", ""))
    return tuple(nombres)


def _walk(
    routes: Any,
    out: list[Any],
    acc: list[tuple[Any, str]] | None,
    visto: set[int],
    prefijo: str = "",
    deps: list[tuple[Any, tuple[str, ...]]] | None = None,
    heredadas: tuple[str, ...] = (),
) -> None:
    for route in routes:
        if id(route) in visto:  # defensa contra un ciclo, que colgaría el arranque
            continue
        visto.add(id(route))
        out.append(route)
        if deps is not None:
            deps.append((route, heredadas))
        if acc is not None:
            p = getattr(route, "path", None)
            if isinstance(p, str) and p:
                acc.append((route, prefijo + p))
        # FastAPI >= 0.141: el hijo cuelga de `original_router` y su prefijo,
        # de `include_context`. Mounts de Starlette y sub-apps: `.routes` /
        # `.app.routes`, que ya traen el path completo.
        for attr in ("original_router", "routes", "app"):
            sub = getattr(route, attr, None)
            if sub is None or sub is route:
                continue
            hijas = sub if attr == "routes" else getattr(sub, "routes", None)
            if hijas:
                anidado = attr == "original_router"
                hijo_prefijo = prefijo + _prefijo_de(route) if anidado else prefijo
                hijo_deps = heredadas + _dependencias_de(route) if anidado else heredadas
                _walk(hijas, out, acc, visto, hijo_prefijo, deps, hijo_deps)


def iter_routes_with_paths(container: Any) -> list[tuple[Any, str]]:
    """Cada ruta junto a su camino EFECTIVO, en orden de registro.

    Existe porque las dos funciones que ya había dejaban un hueco por el que se
    cae quien introspecciona un router compuesto:

    * :func:`iter_routes` da los objetos ruta, pero su ``.path`` es el del hijo
      **sin prefijo** desde FastAPI 0.141 (antes el aplanado lo reescribía).
    * :func:`route_paths` da los caminos efectivos, pero como ``set``: pierde el
      orden y no permite llegar al ``name`` ni a los ``methods`` de la ruta.

    Quien necesitaba las tres cosas a la vez —camino efectivo, métodos y nombre—
    acababa leyendo ``route.path`` de ``iter_routes``, que es correcto en 0.136 y
    falso en 0.141. Eso hizo rojo en CI a
    ``tests/unit/test_sso_router_package.py`` mientras seguía verde en el ``.venv``
    de desarrollo: el paquete ``routers/sso/`` monta sus sub-routers bajo el
    ``prefix="/auth/sso"`` del padre, así que las rutas salían como ``/providers``
    en vez de ``/auth/sso/providers``.

    El orden es el de registro (recorrido en profundidad, padre antes que hijas),
    que es el que FastAPI usa para casar: por eso esto sirve además para afirmar
    que una ruta literal quedó ANTES que la paramétrica que se la comería.
    """
    acc: list[tuple[Any, str]] = []
    _walk(getattr(container, "routes", []) or [], [], acc, set())
    return acc


def route_paths(container: Any) -> set[str]:
    """Los caminos EFECTIVOS de todas las rutas, incluidas las de routers incluidos.

    Sustituto directo del idioma frágil::

        {getattr(r, "path", None) for r in app.routes}   # miente desde 0.141
        route_paths(app)                                 # no

    «Efectivos» quiere decir **con el prefijo del include aplicado**, y no es un
    detalle: ``main._is_admin_surface`` clasifica un router por si sus rutas
    empiezan por ``/admin``. Un router incluido con ``prefix="/admin"`` presenta
    ``/cosa`` en sus propias rutas, así que devolver el path del hijo a secas
    haría que la guarda no lo reconociera como superficie administrativa y lo
    montara SIN ``require_hardened_system_admin``.

    Ese caso concreto se descubrió verificando la corrección contra FastAPI
    0.141.1 en un venv aislado: cuatro de los cinco casos pasaban y éste no. Es
    la diferencia entre creer que el arreglo funciona y saberlo.
    """
    return {p for _, p in iter_routes_with_paths(container)}


def iter_routes_with_inherited_dependencies(
    container: Any,
) -> list[tuple[Any, tuple[str, ...]]]:
    """Cada ruta con los NOMBRES de las dependencias heredadas de sus montajes.

    El tercer hueco de esta familia, y el más caro de los tres porque el que se
    cae por él obtiene un falso negativo en un test de seguridad.

    Hasta FastAPI 0.136, un ``include_router(dependencies=[...])`` fusionaba esas
    dependencias en el ``dependant`` de cada ruta hija, así que caminar el árbol
    resuelto bastaba. Desde 0.141 el include es perezoso y lo que se pasó al
    montar vive en ``include_context.dependencies``; la hija queda intacta.

    Consecuencia medida el 2026-08-27 al subir el pin de 0.136.1 a 0.141.1:
    ``test_every_admin_route_carries_the_hardening_gate`` declaró que 43 rutas
    ``/admin`` no llevaban ``require_hardened_system_admin``. Las llevaban las 54:
    el gate estaba en el ``include_context`` de los 11 routers, y se aplica en
    cada petición. Lo que había dejado de ser cierto era la introspección.
    """
    deps: list[tuple[Any, tuple[str, ...]]] = []
    _walk(getattr(container, "routes", []) or [], [], None, set(), "", deps)
    return deps
