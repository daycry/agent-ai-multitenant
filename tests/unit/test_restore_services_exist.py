"""Los servicios que la restauración para tienen que EXISTIR (ADR 0117 c).

Hallazgo al consolidar el frontend: `restore_app_services` incluía `web-app`, un
servicio que **no está en ningún compose** — ni el versionado ni el que genera
el instalador, porque `apps/web-app/` nunca tuvo código.

Eso no era cosmética. `_stop_app_stack` hace
``docker compose stop <servicios>`` y **eleva si el código de salida no es 0**;
compose devuelve error ante un servicio desconocido. O sea: la restauración
completa abortaba en el paso 3, **antes de restaurar nada**. El simulacro de
recuperación estaba roto y nadie lo sabía porque el fallo solo aparece
ejecutándolo de verdad.

Este test compara la lista contra los servicios que el generador del instalador
declara, que es el compose que corre en producción.
"""

from __future__ import annotations

from workers.config import Settings


def _generated_services() -> set[str]:
    from installer_backend.compose_generator import CORE_SERVICES, MONITORING_SERVICES

    return set(CORE_SERVICES) | set(MONITORING_SERVICES)


def _restore_services() -> list[str]:
    return list(Settings().restore_app_services)


def test_every_service_the_restore_stops_exists_in_the_generated_compose() -> None:
    missing = sorted(set(_restore_services()) - _generated_services())
    assert not missing, (
        f"la restauración pararía servicios inexistentes {missing}: `docker compose "
        "stop` devuelve != 0 y el restore aborta antes de restaurar nada"
    )


def test_the_phantom_frontend_is_gone() -> None:
    # El caso concreto que rompía el restore. Explícito para que un revert
    # cuente una historia legible.
    assert "web-app" not in _restore_services()


def test_the_restore_still_stops_the_real_app_services() -> None:
    # No vacuo: vaciar la lista haría pasar el test de arriba y dejaría la
    # restauración escribiendo bajo servicios vivos.
    services = _restore_services()
    assert {"api-server", "workers", "admin-panel"} <= set(services)


def test_postgres_is_deliberately_absent() -> None:
    # Tiene que seguir alcanzable para `pg_restore`; pararlo aquí rompería la
    # restauración de otra manera.
    assert not {"postgres", "postgresql", "db"} & set(_restore_services())
