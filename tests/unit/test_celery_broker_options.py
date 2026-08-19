"""El `visibility_timeout` del broker, coherente con los límites de ejecución.

Plan prod-06 `task_prod06_zombi_03` (hallazgo workers-3). Es el test que el plan
declaraba y que nunca se escribió: `tests/unit/test_hard_limit_registry_validation.py`
—el otro fichero del mismo comando— cubre la app de **workers** y la desigualdad
entre constantes; aquí se cierran los tres huecos que quedaban.

## Qué protege

En el transporte Redis de kombu, `visibility_timeout` es la ventana tras la cual
un mensaje **no confirmado** se considera perdido y se **re-entrega**. Con
`task_acks_late=True` (que estas dos apps usan) el ack llega al TERMINAR, así que
una tarea que dure más que la ventana se re-entrega **mientras sigue viva**:
ejecución duplicada, contenedor duplicado y coste LLM duplicado. El valor por
defecto de kombu es **una hora** — por debajo de cualquier run de agente— así que
una app que no declare la opción no es «neutra», es la vulnerable.

De ahí la cadena que se fija abajo, de la más externa a la más interna:

    registry.max_value  <  EXECUTION_VISIBILITY_TIMEOUT_S  <=  ventana de cada app

## Los tres huecos

1. **La app del notification-dispatcher no la comprobaba nadie**, y su ventana es
   un literal (`25200`) desconectado de la constante: puede bajar sin que nada
   chille. El test recorre las apps CONSUMIDORAS (las que declaran configuración
   completa; las de `send_task` sólo producen y no restauran nada) y exige la
   ventana en todas, con la aserción de que encontró más de una — una guarda cuyo
   descubrimiento se quede vacío pasaría en verde sin mirar nada.

2. **La validación cruzada del registry no se ejercía de verdad.** El test
   hermano compara `max_value < EXECUTION_VISIBILITY_TIMEOUT_S`, que es una
   afirmación sobre dos constantes; lo que le importa al operador es que el
   validador REAL —el que corre cuando alguien guarda el ajuste desde la UI—
   rechace un hard limit que alcance la ventana. Eso se comprueba llamando a
   `validate_platform_setting_value`.

3. **El borde no estaba fijado por ninguno de los dos lados**: que el techo
   permitido se acepte y que un segundo por encima se rechace.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


#: Las apps Celery **consumidoras** del repo: las que arrancan un worker y por
#: tanto restauran mensajes no confirmados. Las demás llamadas a `Celery(...)`
#: del repo (`api_server.celery_client`, `orchestrator.dispatch`, los emisores de
#: `workers/*.py`) sólo producen con `send_task`, y un productor no re-entrega
#: nada. Si aparece una tercera app consumidora, añádela aquí: es la lista que
#: hace que este test no envejezca en silencio.
CONSUMER_APP_FACTORIES: tuple[tuple[str, str], ...] = (
    ("workers", "workers.celery_app"),
    ("notification-dispatcher", "notification_dispatcher.celery_app"),
)


def _build(module_name: str) -> Any:
    import importlib

    module = importlib.import_module(module_name)
    return module.build_celery_app()


def _hard_limit_ceiling() -> int:
    from api_server.platform_settings_registry import PLATFORM_KNOWN_SETTINGS

    hard = PLATFORM_KNOWN_SETTINGS["ejecucion"].settings["execution_hard_time_limit_s"]
    assert hard.max_value is not None, (
        "`execution_hard_time_limit_s` sin `max_value`: el operador podría subir "
        "el límite duro por encima de la ventana del broker desde la UI"
    )
    return int(hard.max_value)


def test_every_consumer_app_pins_a_visibility_window() -> None:
    """Ninguna app consumidora se queda con la hora por defecto de kombu."""
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S

    checked = 0
    for label, module_name in CONSUMER_APP_FACTORIES:
        app = _build(module_name)
        options = app.conf.broker_transport_options or {}
        window = options.get("visibility_timeout")
        assert window is not None, (
            f"la app Celery de {label} no declara `visibility_timeout`: con "
            "`task_acks_late` hereda la hora por defecto de kombu y re-entregaría "
            "cualquier tarea más larga que eso mientras sigue viva"
        )
        # La constante de `workers` es el suelo de plataforma, derivado del run
        # más largo que el operador puede configurar. Una app que pinte una
        # ventana MENOR estaría afirmando que sus tareas están acotadas por
        # debajo, y ninguna de las dos declara `task_time_limit` que lo acote.
        assert window >= EXECUTION_VISIBILITY_TIMEOUT_S, (
            f"la ventana de {label} ({window}s) queda por debajo del suelo de "
            f"plataforma ({EXECUTION_VISIBILITY_TIMEOUT_S}s)"
        )
        checked += 1

    assert checked >= 2, (
        f"la guarda dejó de encontrar las apps consumidoras (vio {checked}): "
        "sin apps que recorrer pasaría en verde sin comprobar nada"
    )


def test_the_platform_floor_clears_the_hard_limit_ceiling() -> None:
    """El suelo de la ventana supera el mayor hard limit alcanzable."""
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S

    assert _hard_limit_ceiling() < EXECUTION_VISIBILITY_TIMEOUT_S


def test_the_registry_refuses_a_hard_limit_that_reaches_the_window() -> None:
    """El validador REAL rechaza el valor que provocaría la re-entrega.

    Es el «rechace valores de hard limit >= visibility_timeout» de la tarea. No
    basta comparar constantes: lo que corre cuando el operador guarda el ajuste
    es esta función.
    """
    from api_server.platform_settings_registry import validate_platform_setting_value
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S

    for value in (EXECUTION_VISIBILITY_TIMEOUT_S, EXECUTION_VISIBILITY_TIMEOUT_S + 1):
        with pytest.raises(ValueError):
            validate_platform_setting_value("execution_hard_time_limit_s", value)


def test_the_registry_draws_the_line_exactly_at_the_ceiling() -> None:
    """El techo se acepta; un segundo por encima, no."""
    from api_server.platform_settings_registry import validate_platform_setting_value

    ceiling = _hard_limit_ceiling()
    assert validate_platform_setting_value("execution_hard_time_limit_s", ceiling) == ceiling
    with pytest.raises(ValueError):
        validate_platform_setting_value("execution_hard_time_limit_s", ceiling + 1)
