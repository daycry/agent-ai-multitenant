"""La URL de la Redis de pruebas, resuelta en UN solo sitio.

Por qué existe este módulo
--------------------------
Desde que el plan prod-10 arrancó Redis con ``--requirepass`` («sin
``environment`` explícito no hay secretos default»), una URL de Redis escrita a
mano —``redis://localhost:6379/15``— dejó de funcionar: no lleva credencial, y
el servidor contesta ``NOAUTH Authentication required``.

El modo de fallo es de los caros, porque **no se parece a su causa**:

* el error llega como ``redis.exceptions.AuthenticationError`` desde dentro de
  una fixture, así que pytest lo marca **ERROR y no FAILED** — el test ni
  siquiera llegó a ejecutarse;
* el traceback apunta al parser de ``redis-py``, y no menciona ni la
  contraseña, ni el compose, ni el fichero de test que escribió la URL;
* aparece de golpe en decenas de ficheros a la vez, lo que invita a buscar una
  causa global (¿se cayó Redis? ¿está mal el docker?) en vez del literal.

Está documentado en
``docs/03-guides/gotchas/redis-con-contrasena-rompe-la-integracion.md``.

El arreglo no es «añadir la contraseña al literal» en cada fichero: eso deja 21
copias que vuelven a divergir en cuanto alguien rote el secreto. El arreglo es
que **nadie escriba la URL**: se importa ``TEST_REDIS_URL`` de aquí.

Y al paralelizar, cada proceso necesita SU base de Redis
------------------------------------------------------
``docs/03-guides/gotchas/integration-tests-share-one-database.md`` explica la
otra mitad: dos tandas de pytest simultáneas sobre la misma base de Redis se
pisan las claves y los streams, y el resultado es un rojo intermitente que se
achaca a «flaky». Por eso se puede pasar ``TEST_REDIS_URL`` por entorno para dar
a cada tanda su base (1, 2, 3…).

Ojo con esa vía, que es justo donde reaparece la trampa de arriba: **la URL que
exportes a mano TIENE que llevar la contraseña**. ``redis://localhost:6379/9``
sin credencial vuelve a fallar con ``AuthenticationError``, no con «acceso
denegado», y otra vez dentro de una fixture.

Y con Celery hay que redirigir DOS cosas, no una
------------------------------------------------
``WorkerSettings(broker_url=TEST_REDIS_URL)`` parece completo y no lo es:
``result_backend`` se queda en su default de producción
(``redis://localhost:6379/2``), que ni lleva credencial ni es una base del
arnés. El síntoma tampoco delata la causa —el enqueue muere con «Retry limit
exceeded while trying to reconnect to the Celery result store backend» tras
minutos de reintentos, y el test falla mucho después en una aserción de estado
que parece de negocio—. Por eso todos los ``build_celery_app()`` de la suite
pasan **broker_url y result_backend**.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

__all__ = ["TEST_REDIS_URL", "default_redis_url", "redis_password"]


def redis_password() -> str:
    """La contraseña de la Redis de desarrollo, si la hay.

    Se busca en dos sitios, por orden: la variable de entorno
    (``TEST_REDIS_PASSWORD``, y si no ``REDIS_PASSWORD``), y el ``docker/.env``
    que el propio compose lee.

    Leer el ``.env`` es deliberado y acotado al arnés de tests: es el mismo
    fichero del que sale la contraseña de Postgres en ``conftest.py``, y evita
    que cada invocación de pytest tenga que exportar a mano un secreto que ya
    está en disco. Nunca se imprime.
    """
    directa = os.environ.get("TEST_REDIS_PASSWORD") or os.environ.get("REDIS_PASSWORD")
    if directa:
        return directa
    env = Path(__file__).resolve().parents[2] / "docker" / ".env"
    if env.is_file():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.startswith("REDIS_PASSWORD="):
                return linea.split("=", 1)[1].strip()
    return ""


def default_redis_url() -> str:
    """`redis://[:pwd@]localhost:6379/15` — DB 15 para no pisar la dev (DB 0)."""
    pwd = redis_password()
    credencial = f":{quote(pwd, safe='')}@" if pwd else ""
    return f"redis://{credencial}localhost:6379/15"


# `or` y no `os.environ.get(..., default)` a propósito: así una `TEST_REDIS_URL`
# exportada VACÍA (el caso de `TEST_REDIS_URL=` en la línea de comandos, o de un
# `.env` que la declara sin valor) cae en la resolución por defecto en vez de
# construir un cliente contra la cadena vacía.
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL") or default_redis_url()
