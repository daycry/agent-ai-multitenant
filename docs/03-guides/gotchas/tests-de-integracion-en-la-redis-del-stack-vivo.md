---
title: "El arnés en la base de Redis del stack vivo: el worker se come el mensaje y el rojo sale tres capas más allá"
area: tests, docker, workflows
encountered: 2026-08-18
stack: pytest, Redis, Celery, docker-compose
---

## Síntoma

Tests de despacho que **pasan solos y fallan en lote**, con una aserción que no
menciona ni Redis ni Docker:

```
>       assert len(raw) == 1
E       AssertionError: assert 0 == 1
```

Le tocó a los seis tests de despacho de `test_agent_skills.py`,
`test_agent_tool_specs_serialization.py` y `test_agent_tools_enforcement.py` en
una tanda de la suite de integración repartida en 4 shards. Cada uno hace lo
mismo: encola con el `TaskDispatcher`, y lee la cola `default` de Redis para
comprobar qué se enhebró en el payload del run.

## Causa raíz

La tanda se paralelizó dando a cada shard **su** base de Redis: 1, 2, 3 y 4. La
1 no es una base libre: es el **broker de Celery del stack de docker-compose**,
que estaba levantado.

```
$ docker inspect agentic-platform-workers-1 --format '{{json .Args}}'
["celery","-A","workers.celery_app","worker","--queues=default,ingestion,test,review",...]
$ docker inspect agentic-platform-workers-1 | grep BROKER
WORKERS_BROKER_URL=redis://:***@redis:6379/1
```

O sea que había un worker **vivo** bloqueado en `BRPOP default` sobre la misma
base a la que apuntaba el arnés. Se comprueba en dos líneas:

```console
$ redis-cli -n 1 lpush default '{"probe":"x"}'
1
$ redis-cli -n 1 llen default
0
```

El mensaje que el test acababa de encolar se lo llevaba el worker antes de que
el test pudiera leerlo. El reparto real del compose:

| base | quién la usa en caliente                                    |
| ---- | ----------------------------------------------------------- |
| 0    | event streams (`events:tasks`) — los consume el orquestador |
| 1    | broker de Celery — lo drenan `workers` y `workers-aux`      |
| 2    | result backend de Celery                                    |

Lo que hace cara esta trampa es que **el rojo no se parece a su causa**: no hay
error de conexión, ni de autenticación, ni nada que apunte a Docker. Sale una
aserción de negocio a tres capas del despacho, reproducible al 100 % en lote y
verde en aislamiento — si para el stack, verde también en lote. Es la firma
exacta de lo que se despacha como «flaky», y el siguiente paso natural es borrar
la guarda en vez del defecto.

Y hay una segunda cara, peor que el rojo: esos tests hacen `DEL default` para
limpiar. Sobre la base 1, eso **tira trabajo real encolado del stack**.

## Fix

`tests/integration/_redis_url.py` se niega a arrancar sobre las bases del stack:

```python
PLATFORM_REDIS_DATABASES = frozenset({0, 1, 2})
...
TEST_REDIS_URL = _prefer_ipv4_loopback(os.environ.get("TEST_REDIS_URL") or default_redis_url())
_reject_platform_database(TEST_REDIS_URL)
```

Falla en la importación, diciendo qué base se pidió y por qué no vale. Al
paralelizar, **de la 5 en adelante** (la 15 es el default del arnés y la que usa
la CI). Lo cubren `test_las_bases_del_stack_vivo_estan_prohibidas` y
`test_las_bases_del_arnes_siguen_permitidas` en
`tests/unit/test_redis_url_hygiene.py`.

## Regla que queda

Al paralelizar la suite hay que aislar **dos** cosas, no una:
`TEST_PG_DB_NAME` (ver `integration-tests-share-one-database.md`) y la base de
Redis — y ésta, además, no compite sólo con las otras tandas de pytest: compite
con el stack que tienes levantado en la misma máquina.

## Peor que la base equivocada: el SERVIDOR equivocado (2026-08-19)

Todo lo de arriba da por hecho que en el puerto del host contesta la Redis del
compose. En esta máquina no era así, y no había ninguna guarda que lo viese.

`agentic-platform-redis-1` **no publicaba puerto ninguno**:

```console
$ docker port agentic-platform-redis-1
$ netstat -ano | findstr :6379
  TCP    127.0.0.1:6379    LISTENING    16336
$ (Get-Process -Id 16336).Path
C:\laragon\bin\redis\redis-x64-5.0.14.1\redis-server.exe
```

El compose declara `127.0.0.1:${REDIS_PORT:-6379}:6379`, pero el puerto lo tenía
tomado el `redis-server.exe` **5.0.14 que trae Laragon**, sin contraseña. Así que
en `127.0.0.1:6379` contestaba ése, y el arnés —que construía la URL con la
contraseña de `docker/.env`— o fallaba con `AuthenticationError`, o, cuando el
contenedor sí llegaba a publicar en algún momento, alternaba entre dos servidores
distintos sin que nada lo dijera.

Lo detectó un subagente al que el arreglo «oficial» no le funcionaba, y tuvo que
sobrescribir la URL a mano para poder correr sus tests. Es la peor forma de
detectarlo: cada uno se hace su parche local y el arnés compartido sigue mintiendo.

**Arreglo, en tres piezas:**

1. `REDIS_PORT` de `docker/.env` pasa a un puerto libre (aquí 6380) y el servicio
   se recrea. El compose ya tenía la variable; lo que faltaba era usarla.
2. `tests/integration/_redis_url.py` **lee ese `REDIS_PORT`** en vez de dar 6379
   por hecho, del mismo `.env` del que ya leía la contraseña. Arnés y stack no
   pueden divergir porque leen el mismo fichero.
3. Guarda nueva `_reject_a_stranger_on_the_port`: si el compose configura
   contraseña, su Redis **tiene** que rechazar una conexión sin autenticar. Si
   alguien contesta `PONG` sin credencial, ése no es el Redis del compose, y se
   aborta en la importación con el `docker port` que hay que ejecutar.

La guarda vieja (`_reject_platform_database`) mira el número de base; ésta mira
**con quién se está hablando**. Son dos preguntas distintas y hacían falta las dos.

Comprobado en las tres direcciones: apuntando al 6379 de Laragon aborta;
apuntando al 6380 del compose no dice nada; y con el puerto muerto tampoco —si el
stack está parado, el fallo posterior ya es legible y abortar aquí sólo cambiaría
un mensaje claro por otro peor.
