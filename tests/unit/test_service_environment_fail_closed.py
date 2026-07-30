"""`ENVIRONMENT` fail-CLOSED en los tres servicios que no son la api-server.

## El fail-open que sobrevivió al endurecimiento

`prod-09 task_prod09_02` (authz-2) cerró `environment` en `api_server.config` a un
conjunto CERRADO `{dev, staging, prod}` y reescribió el guard anti-defaults como
«todo lo que no sea dev» en vez de «staging o prod». El motivo, textual en ese
fichero: escrito como `in {staging, prod}`, **cualquier valor no reconocido
significaba dev** — un typo (`production`), una variable vacía o un `prod ` con
espacio de más desactivaban en silencio el guard de secretos, el MFA de admin, la
allowlist de IP y la sesión corta. Un error de escritura degradaba la postura de
seguridad entera sin una línea de log.

`workers`, `orchestrator` y `notification-dispatcher` **se quedaron con la forma
vieja**:

```python
if self.environment not in {"staging", "prod"}:
    return self          # ← fail-OPEN
```

y sin validador del conjunto cerrado. O sea: el mismo agujero que se cerró en un
servicio seguía abierto en los otros tres, y en los tres el guard protege
exactamente lo mismo — el DSN con las credenciales BYPASSRLS de la base de datos
(y en el dispatcher, además, la clave con la que se cifran los secretos de los
canales de notificación).

No es hipotético: el enum del instalador vale `production`, y `compose_generator`
lo emitía **en crudo** hasta el 2026-07-30 (ADR 0136, condición 2). Cualquier
stack generado antes de ese arreglo corría los tres servicios con
`ENVIRONMENT=production`, que estos guards leían como «no es staging ni prod, no
compruebes nada».

## Se parametriza por servicio a propósito

Con un solo servicio, el test pasaría el día que alguien arregle uno y olvide los
otros dos — que es literalmente el fallo que este fichero documenta.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _settings_classes() -> dict[str, type]:
    from notification_dispatcher.config import Settings as NotifySettings
    from orchestrator.config import Settings as OrchestratorSettings
    from workers.config import Settings as WorkersSettings

    return {
        "workers": WorkersSettings,
        "orchestrator": OrchestratorSettings,
        "notification-dispatcher": NotifySettings,
    }


#: Un DSN que NO lleva marcadores de dev, para poder aislar el efecto del valor de
#: `environment` sin que el guard salte por la credencial.
_REAL_DSN = "postgresql+asyncpg://service_user:aV3ryR3alP4ssw0rd@db:5432/agentic"
#: Y su contraparte: el default de dev, que fuera de dev debe ser rechazado.
_DEV_DSN = "postgresql+asyncpg://service_user:changeme-service-dev-only@db:5432/agentic"


def _kwargs(settings_cls: type, **overrides: Any) -> dict[str, Any]:
    """Los kwargs mínimos para construir estos Settings sin tocar el entorno."""
    kwargs: dict[str, Any] = {"database_url": _REAL_DSN}
    # El dispatcher tiene un segundo guard (clave de cifrado de canales); dale un
    # valor real para que el test hable SOLO de `environment`.
    if "notification_encryption_key" in settings_cls.model_fields:
        kwargs["notification_encryption_key"] = "a-real-notification-encryption-key"
    kwargs.update(overrides)
    return kwargs


def test_the_guard_covers_the_three_services() -> None:
    """Aserción de descubrimiento: si esto se queda corto, el resto pasa en vacío."""
    classes = _settings_classes()
    assert len(classes) == 3, f"la guarda dejó de cubrir los tres servicios: {sorted(classes)}"
    for name, cls in classes.items():
        assert "environment" in cls.model_fields, f"{name} ya no tiene campo `environment`"
        assert "database_url" in cls.model_fields, f"{name} ya no tiene campo `database_url`"


# ---------------------------------------------------------------------------
# 1. Conjunto cerrado: un valor desconocido NO arranca
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("service", sorted(_settings_classes()))
@pytest.mark.parametrize("bad_value", ["production", "development", "PRODUCCION", "", "prod2"])
def test_an_unknown_environment_fails_startup(service: str, bad_value: str) -> None:
    cls = _settings_classes()[service]
    with pytest.raises(ValidationError) as exc:
        cls(**_kwargs(cls, environment=bad_value))
    assert "environment" in str(exc.value).lower(), (
        f"{service}: el error no menciona `environment`, así que el operador no "
        f"sabría qué arreglar: {exc.value}"
    )


@pytest.mark.parametrize("service", sorted(_settings_classes()))
@pytest.mark.parametrize("good_value", ["dev", "staging", "prod"])
def test_the_three_known_environments_are_accepted(service: str, good_value: str) -> None:
    """Contra-prueba: el validador no puede ser un «rechaza todo» disfrazado."""
    cls = _settings_classes()[service]
    settings = cls(**_kwargs(cls, environment=good_value))
    assert settings.environment == good_value


@pytest.mark.parametrize("service", sorted(_settings_classes()))
def test_whitespace_and_case_are_normalised(service: str) -> None:
    """`" PROD "` es un accidente de un `.env` con salto de línea, no la intención
    de correr sin guardas. Se normaliza; cualquier otra cosa falla."""
    cls = _settings_classes()[service]
    assert cls(**_kwargs(cls, environment=" PROD ")).environment == "prod"


# ---------------------------------------------------------------------------
# 2. El guard anti-defaults es fail-CLOSED: enforce salvo en dev
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("service", sorted(_settings_classes()))
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_dev_dsn_is_rejected_outside_dev(service: str, env: str) -> None:
    cls = _settings_classes()[service]
    with pytest.raises(ValidationError) as exc:
        cls(**_kwargs(cls, environment=env, database_url=_DEV_DSN))
    assert "dev" in str(exc.value).lower()


@pytest.mark.parametrize("service", sorted(_settings_classes()))
def test_dev_dsn_is_allowed_in_dev(service: str) -> None:
    """Lo que hace útil al guard: en dev el stack levanta con sus defaults."""
    cls = _settings_classes()[service]
    settings = cls(**_kwargs(cls, environment="dev", database_url=_DEV_DSN))
    assert settings.database_url == _DEV_DSN


@pytest.mark.parametrize("service", sorted(_settings_classes()))
def test_the_guard_is_written_as_not_dev_not_as_a_staging_prod_list(service: str) -> None:
    """La forma del predicado, no solo su efecto de hoy.

    Los dos test de arriba pasarían igual con `in {staging, prod}`, porque hoy solo
    hay tres entornos. Lo que ese predicado pierde es el FUTURO: añadir un cuarto
    entorno pasaría a saltarse el guard por omisión en vez de aplicarlo. Con el
    conjunto cerrado ya no se puede colar un valor nuevo, pero la forma correcta es
    la defensa en profundidad de las dos, así que se fija leyendo la fuente.
    """
    import inspect

    cls = _settings_classes()[service]
    source = inspect.getsource(cls)
    assert '{"staging", "prod"}' not in source and "{'staging', 'prod'}" not in source, (
        f"{service}: el guard sigue escrito como «está en {{staging, prod}}» "
        "(fail-open ante un valor nuevo). Escribirlo como «no es dev»."
    )


# ---------------------------------------------------------------------------
# 3. El dispatcher: su segundo secreto sigue guardado
# ---------------------------------------------------------------------------
def test_notify_encryption_key_guard_survives_the_rewrite() -> None:
    """Regresión: el dispatcher comprueba DOS cosas fuera de dev (DSN y clave de
    cifrado de canales). Reescribir el predicado no puede perder la segunda."""
    from notification_dispatcher.config import Settings as NotifySettings

    with pytest.raises(ValidationError) as exc:
        NotifySettings(
            environment="prod",
            database_url=_REAL_DSN,
            notification_encryption_key="dev-only-notification-encryption-key-change-me",
        )
    assert "encryption" in str(exc.value).lower()
