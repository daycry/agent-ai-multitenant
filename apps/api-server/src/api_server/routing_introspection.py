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

__all__ = ["iter_routes", "route_paths"]


def iter_routes(container: Any) -> list[Any]:
    """Todas las rutas de ``container``, descendiendo por los routers incluidos.

    Devuelve los objetos ruta tal cual. Si lo que necesitas son los caminos
    EFECTIVOS usa :func:`route_paths`: descender a un router incluido da los
    ``path`` del hijo **sin el prefijo con el que se montó**, y esa diferencia
    importa (ver allí).
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


def _walk(
    routes: Any,
    out: list[Any],
    acc: list[tuple[Any, str]] | None,
    visto: set[int],
    prefijo: str = "",
) -> None:
    for route in routes:
        if id(route) in visto:  # defensa contra un ciclo, que colgaría el arranque
            continue
        visto.add(id(route))
        out.append(route)
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
                _walk(hijas, out, acc, visto, hijo_prefijo)


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
    acc: list[tuple[Any, str]] = []
    _walk(getattr(container, "routes", []) or [], [], acc, set())
    return {p for _, p in acc}
