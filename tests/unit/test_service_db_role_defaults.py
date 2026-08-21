"""Los servicios se conectan como `service_user`, no como `migrations_user`.

Plan prod-14 `task_prod14_05` (hallazgo tenancy-2). Los cuatro servicios de la
plataforma conectaban como `migrations_user`, que es el **propietario del
esquema con `GRANT ALL`**. Un servicio comprometido podía ejecutar

    ALTER TABLE agents DISABLE ROW LEVEL SECURITY;

y apagar el aislamiento multi-tenant de toda la plataforma con una sentencia.
Ese privilegio no lo necesita ningún servicio: solo Alembic.

`service_user` (docker/postgres/init/04-service-role.sql, verificado por
`tests/integration/test_db_roles_service_user.py`) conserva **BYPASSRLS** —es su
razón de ser: un worker procesa el tenant que le toque sin un `app.tenant_id` de
request al que atarse— y pierde el DDL. Lo que esta separación quita no es la
lectura cross-tenant, que es funcional, sino la capacidad de DESMONTAR la
protección para todos los demás.

## Qué fija este fichero, y qué NO

Fija el **default** de cada `Settings`, que es lo que rige cuando el despliegue
no pasa la variable. No comprueba el compose: cambiar los DSN del compose exige
haber aplicado antes los GRANT sobre la base de datos viva
(`docker/postgres/upgrade/20260730-service-user.sh`), y ese orden —primero los
grants, después los servicios— es el riesgo nº 6 del plan. El cambio de compose
queda reportado como paso de despliegue, no escondido en un default.

`api_server.database_url` (rol `app_user`, NOBYPASSRLS) se queda como está: es la
conexión de las peticiones de tenant y la RLS **debe** aplicarse. Solo se mueve
`admin_database_url`, la del engine BYPASSRLS de `/admin/*`.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

#: El rol que ningún servicio debe usar por defecto: propietario + GRANT ALL.
_DDL_ROLE = "migrations_user"
#: El rol de servicio: DML cross-tenant, sin DDL.
_SERVICE_ROLE = "service_user"


def _default_of(settings_cls: type, field: str) -> str:
    default = settings_cls.model_fields[field].default
    assert isinstance(default, str), f"{settings_cls.__name__}.{field} no tiene default de texto"
    return default


def _service_settings() -> dict[str, tuple[type, str]]:
    """Las cuatro conexiones BYPASSRLS de servicio (`clase`, `campo`)."""
    from api_server.config import Settings as ApiServerSettings
    from notification_dispatcher.config import Settings as NotifySettings
    from orchestrator.config import Settings as OrchestratorSettings
    from workers.config import Settings as WorkersSettings

    return {
        "workers": (WorkersSettings, "database_url"),
        "orchestrator": (OrchestratorSettings, "database_url"),
        "notification-dispatcher": (NotifySettings, "database_url"),
        # El engine admin de la api-server, NO su `database_url` de tenant.
        "api-server (admin)": (ApiServerSettings, "admin_database_url"),
    }


def test_the_guard_covers_the_four_services() -> None:
    """Sin esto, un renombrado de campo dejaría la guarda sin objeto y en verde."""
    covered = _service_settings()
    assert len(covered) == 4, f"la guarda dejó de cubrir los cuatro servicios: {sorted(covered)}"
    for name, (cls, field) in covered.items():
        assert field in cls.model_fields, f"{name}: {cls.__name__} ya no tiene el campo {field!r}"


@pytest.mark.parametrize("service", sorted(_service_settings()))
def test_service_connects_as_service_user_not_the_schema_owner(service: str) -> None:
    cls, field = _service_settings()[service]
    default = _default_of(cls, field)

    assert f"{_SERVICE_ROLE}:" in default, (
        f"{service} ({cls.__name__}.{field}) no se conecta como {_SERVICE_ROLE}: {default!r}"
    )
    assert f"{_DDL_ROLE}:" not in default, (
        f"{service} ({cls.__name__}.{field}) sigue conectando como {_DDL_ROLE}, el "
        "propietario del esquema con GRANT ALL: puede ejecutar ALTER TABLE ... "
        "DISABLE ROW LEVEL SECURITY y apagar el aislamiento de toda la plataforma "
        f"(prod-14 tenancy-2). Default actual: {default!r}"
    )


def test_the_api_server_tenant_connection_stays_on_the_rls_role() -> None:
    """Contra-prueba: la conexión de las peticiones de tenant NO se mueve.

    Si `database_url` acabara en un rol BYPASSRLS, cada endpoint de tenant dejaría
    de estar filtrado por RLS — una regresión mucho peor que la que este cambio
    arregla. Este test es lo que impide "unificar" los dos DSN por comodidad.
    """
    from api_server.config import Settings as ApiServerSettings

    default = _default_of(ApiServerSettings, "database_url")
    assert "app_user:" in default, (
        "api_server.database_url debe seguir siendo el rol NOBYPASSRLS `app_user`: "
        f"es la conexión que la RLS filtra por tenant. Default actual: {default!r}"
    )
    for bypassrls_role in (_SERVICE_ROLE, _DDL_ROLE):
        assert f"{bypassrls_role}:" not in default, (
            f"api_server.database_url pasó a un rol BYPASSRLS ({bypassrls_role}): "
            "las peticiones de tenant dejarían de estar filtradas por RLS"
        )


def test_the_dev_default_password_is_still_caught_by_the_anti_default_guard() -> None:
    """Cambiar el rol no puede desactivar el guard anti-defaults de cada servicio.

    Los tres servicios rechazan su `database_url` de dev fuera de dev buscando los
    marcadores `changeme` / `dev-only`. Si el DSN nuevo llevara una contraseña que
    no los contiene, un despliegue de producción arrancaría con la credencial
    pública sin que nada avisara.
    """
    import notification_dispatcher.config as notify_config
    import orchestrator.config as orchestrator_config
    import workers.config as workers_config

    services = _service_settings()
    for name, module in (
        ("workers", workers_config),
        ("orchestrator", orchestrator_config),
        ("notification-dispatcher", notify_config),
    ):
        markers = module._DEV_SECRET_MARKERS
        cls, field = services[name]
        default = _default_of(cls, field).lower()
        assert any(marker in default for marker in markers), (
            f"{name}: el DSN por defecto {default!r} ya no contiene ninguno de los "
            f"marcadores {markers}, así que el guard anti-defaults dejaría pasar la "
            "credencial de dev en producción"
        )
