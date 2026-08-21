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
a cada tanda su base.

Pero **no cualquier base**: las 0, 1 y 2 son del STACK VIVO
----------------------------------------------------------
El compose reparte así las bases de Redis, y los contenedores están levantados
mientras uno corre los tests en la máquina de desarrollo:

* **0** — event streams (``events:tasks``), que consume el orquestador;
* **1** — broker de Celery, que drenan ``workers``/``workers-aux``;
* **2** — result backend de Celery.

Apuntar el arnés a la 1 no da un error: da un **rojo mentiroso**. El worker vivo
está bloqueado en ``BRPOP default`` sobre esa misma base, así que se lleva el
mensaje que el test acababa de encolar antes de que el test pueda leerlo, y la
aserción que cae es ``assert len(raw) == 1`` con cero elementos — a tres capas de
distancia del despacho, sin nada que apunte a Docker. Pasa en aislamiento (si el
stack está parado) y falla en lote: la firma exacta que se despacha como
«flaky». Sucedió: una tanda de 4 shards repartida sobre las bases 1-4 tumbó los
seis tests de despacho de ``test_agent_skills`` /
``test_agent_tool_specs_serialization`` / ``test_agent_tools_enforcement``.
Y hay una segunda cara peor que el rojo: el test hace ``DEL default`` sobre la
cola del stack vivo, o sea que puede TIRAR trabajo real encolado.

De ahí la guarda de abajo, que se niega a arrancar sobre esas tres bases. Para
paralelizar, usa las de la 5 en adelante (la 15 es el default del arnés).

Ojo también con esta vía, que es justo donde reaparece la trampa de arriba: **la
URL que exportes a mano TIENE que llevar la contraseña**.
``redis://localhost:6379/9`` sin credencial vuelve a fallar con
``AuthenticationError``, no con «acceso denegado», y otra vez dentro de una
fixture.

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
from urllib.parse import quote, urlsplit, urlunsplit

__all__ = [
    "PLATFORM_REDIS_DATABASES",
    "TEST_REDIS_URL",
    "default_redis_url",
    "redis_password",
    "redis_port",
]

#: Bases de Redis que el stack de docker-compose usa EN CALIENTE (ver el
#: docstring): 0 event streams, 1 broker de Celery, 2 result backend. El arnés no
#: puede usarlas: el worker vivo drena la cola antes de que el test la lea.
PLATFORM_REDIS_DATABASES = frozenset({0, 1, 2})


def _from_docker_env(clave: str) -> str:
    """El valor de ``clave`` en ``docker/.env``, o cadena vacía.

    Leer el ``.env`` es deliberado y acotado al arnés: es el MISMO fichero que
    lee el compose, así que el arnés y el stack no pueden divergir. Es también
    de donde sale la contraseña de Postgres en ``conftest.py``.
    """
    env = Path(__file__).resolve().parents[2] / "docker" / ".env"
    if not env.is_file():
        return ""
    prefijo = f"{clave}="
    for linea in env.read_text(encoding="utf-8").splitlines():
        if linea.startswith(prefijo):
            return linea.split("=", 1)[1].strip()
    return ""


def redis_password() -> str:
    """La contraseña de la Redis de desarrollo, si la hay.

    Se busca en dos sitios, por orden: la variable de entorno
    (``TEST_REDIS_PASSWORD``, y si no ``REDIS_PASSWORD``), y el ``docker/.env``
    que el propio compose lee. Nunca se imprime.
    """
    directa = os.environ.get("TEST_REDIS_PASSWORD") or os.environ.get("REDIS_PASSWORD")
    if directa:
        return directa
    return _from_docker_env("REDIS_PASSWORD")


def redis_port() -> int:
    """El puerto que el compose PUBLICA en el host para su Redis.

    No es siempre 6379, y darlo por hecho fue justo el fallo del 2026-08-19: en
    esta máquina el 6379 lo ocupa una Redis local de Laragon (5.0.14, **sin
    contraseña**), así que el compose lo publica en otro puerto —``REDIS_PORT``
    de ``docker/.env``, que el propio compose interpola en
    ``127.0.0.1:${REDIS_PORT:-6379}:6379``—. El arnés lee la MISMA variable para
    no poder apuntar a otro servidor que el stack.
    """
    directo = os.environ.get("TEST_REDIS_PORT") or os.environ.get("REDIS_PORT")
    bruto = directo or _from_docker_env("REDIS_PORT") or "6379"
    try:
        return int(bruto)
    except ValueError:
        return 6379


def default_redis_url() -> str:
    """`redis://[:pwd@]localhost:<REDIS_PORT>/15` — DB 15 para no pisar la dev."""
    pwd = redis_password()
    credencial = f":{quote(pwd, safe='')}@" if pwd else ""
    return f"redis://{credencial}localhost:{redis_port()}/15"


def _database_index(url: str) -> int | None:
    """El número de base de una URL de Redis, o ``None`` si no se puede leer.

    ``redis://host:6379/1`` -> 1. Una URL sin path (``redis://host:6379``)
    significa base 0, igual que en redis-py. Lo que no se sabe leer no se
    bloquea: la guarda es contra un despiste conocido, no un validador de URLs.
    """
    path = urlsplit(url).path.lstrip("/")
    if not path:
        return 0
    try:
        return int(path)
    except ValueError:
        return None


def _reject_platform_database(url: str) -> None:
    """Aborta si ``url`` apunta a una base que el stack vivo está usando.

    Se falla en la IMPORTACIÓN, con el nombre de la variable y el número de
    base, porque el modo de fallo alternativo —seguir adelante— es un rojo que
    no se parece a su causa (``assert len(raw) == 1`` con cero elementos, tres
    capas más allá) y encima borra la cola de trabajo real del stack.
    """
    indice = _database_index(url)
    if indice is None or indice not in PLATFORM_REDIS_DATABASES:
        return
    raise RuntimeError(
        f"TEST_REDIS_URL apunta a la base de Redis {indice}, que es del STACK VIVO "
        "(0 = event streams, 1 = broker de Celery, 2 = result backend). Los "
        "contenedores `orchestrator` y `workers` consumen esas bases, así que se "
        "llevarían los mensajes que encolan los tests —el rojo sale como "
        "`assert len(raw) == 1` con cero elementos— y el `DEL default` de los "
        "tests tiraría trabajo real encolado. Usa una base de la 5 en adelante "
        "(la 15 es el default del arnés)."
    )


def _prefer_ipv4_loopback(url: str) -> str:
    """`localhost` -> `127.0.0.1` en el host (y SÓLO en el host).

    En Windows el resolver devuelve `::1` antes que `127.0.0.1`, y los puertos
    que publica Docker Desktop escuchan sólo en IPv4. El intento IPv6 no falla
    rápido: tarda ~2 s en darse por rechazado, y sólo entonces se prueba la
    IPv4. O sea que **cada conexión nueva del arnés cuesta 2 s de más**, sin dar
    ningún error — se paga en horas de suite y no se ve por ningún lado.

    Donde sí se ve es en cualquier cosa con un deadline corto: `/readyz` da 2 s
    por check, así que `test_health_readiness` respondía 503 «timeout tras 2s»
    con PostgreSQL y Redis VIVOS. Ver
    `docs/03-guides/gotchas/localhost-ipv6-primero-cuesta-dos-segundos.md`.

    Se reescribe el host reconstruyendo el netloc, no con un `replace` sobre la
    URL entera: una contraseña que contuviera «localhost» se corrompería.
    """
    partes = urlsplit(url)
    if partes.hostname != "localhost":
        return url
    userinfo, arroba, hostport = partes.netloc.rpartition("@")
    netloc = f"{userinfo}{arroba}{hostport.replace('localhost', '127.0.0.1', 1)}"
    return urlunsplit(partes._replace(netloc=netloc))


def _reject_a_stranger_on_the_port(url: str) -> None:
    """Aborta si quien contesta en ese puerto NO es la Redis del compose.

    El 2026-08-19 el arnés estuvo hablando con una Redis que no era la del
    stack: el contenedor no llegó a publicar su puerto —lo tenía tomado el
    ``redis-server.exe`` 5.0.14 que trae Laragon— y en ``127.0.0.1:6379``
    contestaba ese, **sin contraseña**. Ninguna guarda lo veía:
    ``_reject_platform_database`` mira el número de base, no con quién habla.

    La señal es barata y no admite discusión: si el compose configura
    contraseña, su Redis TIENE que rechazar una conexión sin autenticar. Si
    alguien contesta ``PONG`` sin credencial, ese alguien no es la Redis del
    compose.

    Sólo se aborta en ese caso. Si la conexión se rechaza (stack parado) no se
    dice nada: el fallo posterior ya es legible, y abortar aquí cambiaría un
    mensaje claro por otro peor.
    """
    if not redis_password():
        return
    try:
        import redis as _redis
    except ImportError:  # pragma: no cover - redis es dependencia del arnés
        return

    partes = urlsplit(url)
    try:
        cliente = _redis.Redis(
            host=partes.hostname or "127.0.0.1",
            port=partes.port or redis_port(),
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        cliente.ping()
    except Exception:
        return

    raise RuntimeError(
        f"En {partes.hostname}:{partes.port} contesta una Redis que NO pide "
        "contraseña, pero el compose la configura con `--requirepass`. O sea que "
        "el arnés está hablando con OTRO servidor: lo más probable es que el "
        "contenedor no haya podido publicar su puerto porque ya lo ocupaba una "
        "Redis local (Laragon trae una en 6379). Comprueba con:\n"
        "    docker port agentic-platform-redis-1\n"
        "Si sale vacío, el contenedor no publica nada. Ajusta `REDIS_PORT` en "
        "docker/.env a un puerto libre y recrea el servicio, o para la Redis "
        "local. Correr la suite contra el servidor equivocado no da error: da "
        "resultados que no describen el sistema que crees estar midiendo."
    )


# `or` y no `os.environ.get(..., default)` a propósito: así una `TEST_REDIS_URL`
# exportada VACÍA (el caso de `TEST_REDIS_URL=` en la línea de comandos, o de un
# `.env` que la declara sin valor) cae en la resolución por defecto en vez de
# construir un cliente contra la cadena vacía.
TEST_REDIS_URL = _prefer_ipv4_loopback(os.environ.get("TEST_REDIS_URL") or default_redis_url())
_reject_platform_database(TEST_REDIS_URL)
_reject_a_stranger_on_the_port(TEST_REDIS_URL)
