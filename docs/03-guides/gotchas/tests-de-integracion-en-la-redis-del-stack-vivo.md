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

```
$ redis-cli -n 1 lpush default '{"probe":"x"}'   # -> 1
$ redis-cli -n 1 llen default                    # -> 0
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
