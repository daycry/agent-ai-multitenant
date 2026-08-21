"""Apuntar al Redis del arnés TODO lo que el proceso de test va a usar.

Por qué existe este módulo
--------------------------
``_redis_url.py`` resuelve *cuál* es la URL del Redis de pruebas. Este resuelve
la otra mitad: *quién* tiene que enterarse. Los tests de worker/pipeline no
construyen la app FastAPI, así que nadie ejecuta el cableado de
``conftest.configured_app`` — y las tres piezas de abajo se quedan con sus
**defaults de producción**, que en el arnés no son inofensivos:

1. ``API_SERVER_REDIS_URL`` — su default apunta a la base 0 del loopback SIN
   credencial. Desde prod-10 Redis exige contraseña, así que cada operación de
   la caché de ``platform_settings`` contesta ``NOAUTH``, la conexión se
   descarta y la siguiente vuelve a pagar el peaje de `localhost` (`::1`
   primero, ver el gotcha ``localhost-ipv6-primero-cuesta-dos-segundos.md``).
   Medido en este repo: **2,0 s por operación**, y son dos (lectura + escritura)
   por ``get_platform_setting``. ``_prepare_run`` llama a tres → **13 s de reloj
   en CADA run**, dentro de la transacción abierta.
2. ``WORKERS_BROKER_URL`` / ``WORKERS_RESULT_BACKEND`` — sus defaults apuntan a
   las bases 1 y 2 del loopback, también sin credencial: el enqueue del
   memorizer al terminar el run muere con «Retry limit exceeded while trying to
   reconnect to the Celery result store backend» tras **~80 s** de reintentos.
   La trampa de las DOS cosas que ya documenta ``_redis_url.py``: apuntar solo
   el broker deja el backend en su default y el síntoma es idéntico.
3. ``API_SERVER_BROKER_URL`` / ``API_SERVER_RESULT_BACKEND``: lo mismo para
   ``api_server.celery_client``, que es quien despacha los eventos de dominio
   (otros ~80 s por run).

Ninguno de los tres da un fallo visible: los tres degradan en silencio y solo se
notan en el reloj. Sumados hacían que ``test_autonomous_cycle`` —dos runs
completos— superase su ``timeout(300)`` sin que nada en la salida dijese por qué.

Lo que este módulo NO tapa
--------------------------
Que ``API_SERVER_REDIS_URL`` no esté en el entorno de los servicios ``workers*``
del compose es un defecto de PRODUCCIÓN, no del arnés: allí la caché de
``platform_settings`` (prod-13, hallazgo perf-10) nunca acierta y, peor, la
invalidación que escribe el api-server jamás llega al proceso que lee. Está
anotado como hallazgo; este módulo solo arregla el arnés.
"""

from __future__ import annotations

import os
from typing import Any

from ._redis_url import TEST_REDIS_URL

__all__ = ["point_everything_at_the_test_redis"]

#: Las variables que el arnés fija. Todas al MISMO Redis de test: el broker y el
#: backend de Celery pueden compartir base con los streams (es lo que ya hace
#: ``_pipeline_helpers.consume_and_take_job``).
_ENV_KEYS: tuple[str, ...] = (
    "API_SERVER_REDIS_URL",
    "API_SERVER_BROKER_URL",
    "API_SERVER_RESULT_BACKEND",
    "WORKERS_BROKER_URL",
    "WORKERS_RESULT_BACKEND",
)


def point_everything_at_the_test_redis(url: str = TEST_REDIS_URL) -> None:
    """Fija las env vars y tira las cachés de proceso que ya las hubieran leído.

    Idempotente. Se llama por env vars Y por ``conf`` del app Celery ya
    construido porque ``workers.celery_app.app`` se instancia AL IMPORTAR el
    módulo: cuando un test llega aquí, el objeto puede llevar rato existiendo
    con el broker de producción dentro, y entonces la env var sola no basta.
    """
    for key in _ENV_KEYS:
        os.environ[key] = url

    from api_server.auth.deps import reset_redis_cache
    from api_server.celery_client import reset_celery_client_cache
    from api_server.config import get_settings as api_get_settings
    from api_server.db.platform_settings import reset_platform_setting_cache_binding
    from workers.config import reset_settings_cache

    api_get_settings.cache_clear()
    reset_redis_cache()
    reset_platform_setting_cache_binding()
    reset_celery_client_cache()
    reset_settings_cache()

    from workers.celery_app import app as workers_celery_app

    conf: Any = workers_celery_app.conf
    conf.broker_url = url
    conf.result_backend = url
