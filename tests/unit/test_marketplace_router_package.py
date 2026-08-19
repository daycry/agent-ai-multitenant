"""El troceo de `routers/marketplace.py` en paquete no puede mover ni una ruta.

Plan prod-16, ``task_prod16_12``: «`routers/marketplace.py` (1380): extraer
sub-módulos cohesivos». Al abrirlo eran **1852** líneas, no 1380 — creció 472
desde que se midió el plan, que es justo el argumento del hallazgo quality-7.

Es la tercera vez que se hace este movimiento en el repo, y la red se escribe
igual que las dos anteriores (``test_sso_router_package.py`` para
``task_prod16_10``, ``test_agents_router_package.py`` para las otras dos piezas
de ``task_prod16_12``): el conjunto de rutas se captura del monolito ANTES de
tocarlo y se escribe abajo literal.

## Las tres cosas que este fichero tiene y los otros dos no

1. **Dos routers, y uno es administrativo.** `router` sirve `/marketplace/*` con
   sesión de tenant; `admin_router` sirve `/admin/marketplace/*` sobre la sesión
   BYPASSRLS del System Admin. `main._is_admin_surface` **decide si un router
   lleva `require_hardened_system_admin` leyendo sus caminos**, y **lanza** si un
   router mezcla `/admin` con no-`/admin`. O sea que meter por descuido una ruta
   de tenant en el router de admin no da un test rojo cualquiera: da un arranque
   caído, o —si la guarda se leyera mal— una superficie de System Admin sin
   endurecer, que es el modo de fallo histórico que esa función existe para
   impedir. Aquí se afirma la partición de los dos conjuntos.

2. **`get_install_orchestrator` es una diana de `dependency_overrides`.**
   `tests/integration/test_marketplace_install_static_analysis.py` y
   `test_marketplace_versioning.py` hacen
   ``app.dependency_overrides[get_install_orchestrator] = _factory`` para no
   bajarse artefactos ni levantar un sandbox Docker. FastAPI casa los overrides
   **por identidad del objeto función**. Si el troceo dejara la definición en un
   módulo y el `Depends(...)` de la ruta apuntara a otra copia, el override
   dejaría de aplicarse: los tests no fallarían por «no encuentro la
   dependencia», seguirían adelante **usando el orquestador de verdad**. Por eso
   hay abajo una comprobación de identidad, no sólo de existencia.

3. **El orden de registro.** En `routers/agents` el troceo podía hacer
   desaparecer `GET /agents/provider-options` en silencio, porque solapa con
   `GET /agents/{agent_id}` y FastAPI casa por orden. Aquí ese solape **no
   existe** hoy, y en vez de darlo por hecho se comprueba: si alguien añade un
   `GET /marketplace/listings/featured` junto a `GET /listings/{listing_id}`, el
   test de abajo exige que el orden lo resuelva.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from api_server.routing_introspection import iter_routes_with_paths

pytestmark = pytest.mark.unit


def _describe(container: object) -> list[tuple[str, tuple[str, ...], str, str | None, int | None]]:
    """(camino efectivo, métodos, nombre de la función, response_model, status_code)."""
    out = []
    for route, path in iter_routes_with_paths(container):
        methods = tuple(sorted(m for m in route.methods if m != "HEAD"))
        model = getattr(route, "response_model", None)
        if model is None:
            rendered = None
        else:
            rendered = getattr(model, "__name__", None)
            if rendered is None or rendered == "list":
                rendered = str(model).replace("api_server.schemas.marketplace.", "")
        out.append((path, methods, route.name, rendered, route.status_code))
    return out


#: Capturado del `routers/marketplace.py` monolítico (1852 líneas) el 2026-08-19,
#: justo antes de partirlo. En ORDEN DE REGISTRO, que es como FastAPI casa.
ROUTES_BEFORE_THE_SPLIT: tuple[tuple[str, tuple[str, ...], str, str | None, int | None], ...] = (
    ("/marketplace/listings", ("GET",), "list_listings", "list[MarketplaceListingResponse]", None),
    (
        "/marketplace/listings/{listing_id}",
        ("GET",),
        "get_listing",
        "MarketplaceListingResponse",
        None,
    ),
    (
        "/marketplace/private/listings",
        ("POST",),
        "publish_private_listing",
        "MarketplaceListingResponse",
        201,
    ),
    (
        "/marketplace/private/listings/{listing_id}",
        ("PUT",),
        "update_private_listing",
        "MarketplaceListingResponse",
        None,
    ),
    (
        "/marketplace/private/listings/{listing_id}",
        ("DELETE",),
        "unpublish_private_listing",
        None,
        204,
    ),
    ("/marketplace/shares", ("POST",), "create_share", "MarketplaceShareResponse", 201),
    ("/marketplace/shares", ("GET",), "list_shares", "list[MarketplaceShareResponse]", None),
    ("/marketplace/shares/{share_id}", ("DELETE",), "revoke_share", None, 204),
    (
        "/marketplace/installations",
        ("POST",),
        "install_listing",
        "MarketplaceInstallationResponse",
        201,
    ),
    (
        "/marketplace/installations/{installation_id}/permissions",
        ("GET",),
        "get_installation_permissions",
        "InstallationPermissionsResponse",
        None,
    ),
    (
        "/marketplace/installations/{installation_id}/consent",
        ("POST",),
        "decide_consent",
        "InstallationPermissionsResponse",
        None,
    ),
    (
        "/marketplace/installations/{installation_id}/update-check",
        ("GET",),
        "check_installation_update",
        "InstallationUpdateCheckResponse",
        None,
    ),
    (
        "/marketplace/installations/{installation_id}/update",
        ("POST",),
        "perform_installation_update",
        "InstallationUpdateResponse",
        None,
    ),
    ("/marketplace/installations/{installation_id}", ("DELETE",), "uninstall_listing", None, 204),
    (
        "/marketplace/installations/{installation_id}/revoke",
        ("POST",),
        "revoke_installation",
        "MarketplaceInstallationResponse",
        None,
    ),
    (
        "/marketplace/installations",
        ("GET",),
        "list_installed",
        "list[MarketplaceInstallationResponse]",
        None,
    ),
)

#: Ídem para el router de System Admin. Estas seis son las que
#: `main._is_admin_surface` tiene que ver TODAS bajo `/admin` para engancharle
#: `require_hardened_system_admin` al montarlo.
ADMIN_ROUTES_BEFORE_THE_SPLIT: tuple[
    tuple[str, tuple[str, ...], str, str | None, int | None], ...
] = (
    (
        "/admin/marketplace/shares",
        ("GET",),
        "admin_list_all_shares",
        "list[MarketplaceShareResponse]",
        None,
    ),
    (
        "/admin/marketplace/review-queue",
        ("GET",),
        "admin_review_queue",
        "list[MarketplaceListingResponse]",
        None,
    ),
    (
        "/admin/marketplace/listings/{listing_id}/versions",
        ("GET",),
        "admin_listing_versions",
        "list[ListingVersionResponse]",
        None,
    ),
    (
        "/admin/marketplace/listings/{listing_id}/approve",
        ("POST",),
        "admin_approve_listing",
        "MarketplaceListingResponse",
        None,
    ),
    (
        "/admin/marketplace/listings/{listing_id}/reject",
        ("POST",),
        "admin_reject_listing",
        "MarketplaceListingResponse",
        None,
    ),
    (
        "/admin/marketplace/listings/{listing_id}/promote",
        ("POST",),
        "admin_promote_listing",
        "MarketplaceListingResponse",
        None,
    ),
)


def test_the_tenant_router_serves_exactly_the_same_routes() -> None:
    """Mismo conjunto: camino, métodos, función, `response_model` y `status_code`.

    El `response_model` y el `status_code` están dentro a propósito: un troceo
    que reconstruya un decorador a mano y se deje el `status_code=201` cambia el
    contrato HTTP sin mover una ruta de sitio.
    """
    from api_server.routers.marketplace import router

    assert set(_describe(router)) == set(ROUTES_BEFORE_THE_SPLIT)


def test_the_admin_router_serves_exactly_the_same_routes() -> None:
    from api_server.routers.marketplace import admin_router

    assert set(_describe(admin_router)) == set(ADMIN_ROUTES_BEFORE_THE_SPLIT)


def test_the_two_surfaces_do_not_bleed_into_each_other() -> None:
    """Ni una ruta de tenant en el router de admin, ni al revés.

    `main._is_admin_surface` **lanza** ante un router que mezcla, así que un
    descuido aquí es un arranque caído. Y la dirección contraria —una ruta
    `/admin` que se cuele en el router de tenant— es peor: se montaría sobre la
    sesión de tenant y SIN `require_hardened_system_admin`.
    """
    from api_server.routers.marketplace import admin_router, router

    tenant_paths = {path for path, *_ in _describe(router)}
    admin_paths = {path for path, *_ in _describe(admin_router)}

    leaked_admin = sorted(p for p in tenant_paths if p.startswith("/admin"))
    leaked_tenant = sorted(p for p in admin_paths if not p.startswith("/admin/"))
    assert not leaked_admin, f"rutas /admin en el router de tenant: {leaked_admin}"
    assert not leaked_tenant, f"rutas no-admin en el router de admin: {leaked_tenant}"
    assert not tenant_paths & admin_paths
    assert len(admin_paths) >= 5, "la guarda dejó de encontrar la superficie de admin"


def test_no_route_pair_depends_on_the_registration_order() -> None:
    """Ninguna pareja de rutas del paquete se resuelve por quién se registró antes.

    Es la lección de `routers/agents`, comprobada en vez de supuesta: allí
    `GET /agents/provider-options` y `GET /agents/{agent_id}` solapaban, así que
    repartirlas entre módulos podía hacer desaparecer la literal **en silencio**
    (la servía la paramétrica y devolvía 422 al parsear el literal como UUID).

    Aquí ese solape no existe: ningún par comparte método y forma. Mientras se
    cumpla, repartir las rutas entre módulos es indiferente al orden — y el día
    que alguien añada `GET /marketplace/listings/featured`, este test se pone
    rojo y obliga a decidir el orden a mano.
    """
    from api_server.routers.marketplace import admin_router, router

    def segments(path: str) -> list[str]:
        return [s for s in path.split("/") if s]

    def overlaps(a: str, b: str) -> bool:
        sa, sb = segments(a), segments(b)
        if len(sa) != len(sb):
            return False
        return all(
            x == y or x.startswith("{") or y.startswith("{") for x, y in zip(sa, sb, strict=True)
        )

    routes = [*_describe(router), *_describe(admin_router)]
    ambiguous = [
        (a[0], b[0], method)
        for i, a in enumerate(routes)
        for b in routes[i + 1 :]
        for method in set(a[1]) & set(b[1])
        if a[0] != b[0] and overlaps(a[0], b[0])
    ]
    assert not ambiguous, (
        "estas parejas se resuelven por ORDEN DE REGISTRO, así que repartirlas "
        f"entre módulos puede hacer desaparecer una en silencio: {ambiguous}"
    )
    assert len(routes) >= 20, f"la guarda sólo vio {len(routes)} rutas"


def test_marketplace_is_a_package_split_by_responsibility() -> None:
    """`routers/marketplace` es un paquete, no un fichero de 1852 líneas.

    Es el test que estaba ROJO antes de esta pieza de `task_prod16_12`; los
    otros cuatro pasaban ya contra el monolito, porque afirman **invariancia**
    y el monolito era la referencia.
    """
    import api_server.routers.marketplace as package

    assert hasattr(package, "__path__"), (
        "routers/marketplace sigue siendo un módulo suelto: task_prod16_12 sin hacer"
    )
    modules = sorted(
        path.stem
        for path in Path(package.__path__[0]).glob("*.py")
        if path.stem != "__init__" and not path.stem.startswith("_")
    )
    assert len(modules) >= 5, f"un paquete de {len(modules)} módulo(s) no es un troceo"
    assert "common" in modules and "admin" in modules, (
        f"faltan los dos módulos con nombre obligado (common sin rutas, admin dueño "
        f"de admin_router): {modules}"
    )


def test_no_module_publishes_on_both_routers() -> None:
    """Ningún módulo cuelga rutas del router de tenant Y del de admin.

    Es la forma estructural de lo que el test anterior comprueba sobre el
    resultado. Un módulo que sirva las dos superficies es el sitio exacto donde
    alguien pega la ruta en el router equivocado: los dos nombres están a mano,
    se diferencian en un prefijo, y el error no da síntoma hasta el arranque
    —o, en el peor caso, publica una ruta administrativa sin endurecer—.
    """
    import api_server.routers.marketplace as package

    package_dir = Path(package.__path__[0])
    offenders = []
    for path in sorted(package_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        source = path.read_text(encoding="utf-8")
        tenant = "@router." in source
        admin = "@admin_router." in source
        if tenant and admin:
            offenders.append(path.name)
    assert not offenders, (
        f"estos módulos publican en los dos routers a la vez: {offenders}. "
        "La superficie de admin va entera en admin.py."
    )
    assert any(
        "@admin_router." in (package_dir / f"{name}.py").read_text(encoding="utf-8")
        for name in ("admin",)
    ), "la guarda dejó de encontrar el router de admin"


def test_the_install_orchestrator_dependency_is_one_single_object() -> None:
    """El `Depends(get_install_orchestrator)` de la ruta y el nombre público son EL MISMO.

    FastAPI casa `dependency_overrides` por identidad. Dos integraciones lo
    sobrescriben para no bajarse artefactos ni levantar Docker; si el troceo
    dejase el `Depends` apuntando a otra copia de la función, esos tests
    seguirían en verde **usando el orquestador real**, que es el peor resultado
    posible: pasan y no prueban lo que dicen.
    """
    from api_server.routers.marketplace import get_install_orchestrator, router

    #: (camino, objeto) de cada dependencia que SE LLAMA como el orquestador.
    #: No basta con «alguna ruta usa el objeto bueno»: la primera versión de
    #: este test afirmaba eso y se quedó VERDE con una copia local puesta a
    #: propósito en `installations.py`, porque la ruta de `updates.py` seguía
    #: usando la buena. Un override que sólo alcanza a la mitad de las rutas es
    #: peor que ninguno: la mitad tapada pasa por probada.
    seen: list[tuple[str, object]] = []
    for route, path in iter_routes_with_paths(router):
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for sub in dependant.dependencies:
            if sub.call is not None and getattr(sub.call, "__name__", "") == (
                "get_install_orchestrator"
            ):
                seen.append((path, sub.call))

    assert seen, (
        "ninguna ruta depende de `get_install_orchestrator`: los "
        "`dependency_overrides` de las integraciones ya no alcanzan a nada y "
        "correrían contra el orquestador de verdad"
    )
    impostors = [path for path, call in seen if call is not get_install_orchestrator]
    assert not impostors, (
        "estas rutas dependen de OTRO objeto llamado `get_install_orchestrator`, "
        f"así que el override no las alcanza: {impostors}"
    )
    assert len(seen) >= 2, f"la guarda sólo vio {len(seen)} ruta(s) con el orquestador"
