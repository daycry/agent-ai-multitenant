---
title: "Cuatro shards de pytest y cinco agentes tumbaron Postgres a mitad de suite"
area: tests, docker, windows
encountered: 2026-08-19
stack: pytest, Docker Desktop, WSL2, PostgreSQL, Windows
---

## Síntoma

La suite de integración va en 4 shards paralelos. A las tres horas, dos shards
terminan con fallos que no se parecen a ningún defecto:

```
asyncpg.exceptions.ConnectionDoesNotExistError: connection was closed in the middle of operation
ConnectionError: unexpected connection_lost() call
```

Repartidos por ficheros que no tienen nada que ver entre sí
(`test_credential_rotation`, `test_fx_fetcher`, `test_max_review_retries_scope`),
y uno de los shards acaba con **29 errores** de golpe. Al mirar el contenedor:

```
$ docker ps --filter name=postgres --format '{{.Status}}'
Up About a minute (health: starting)

$ docker logs --tail 5 agentic-platform-postgres-1
LOG:  syncing data directory (fsync), elapsed time: 80.00 s, current path: ./base/...
FATAL: the database system is starting up
```

PostgreSQL está haciendo **recuperación tras caída**: no se paró limpiamente.

## Causa raíz

No es un defecto del código ni de los tests. Es **memoria del host**.

En esa ventana corrían a la vez:

- **4 procesos pytest** de integración, ~450 MB de working set cada uno;
- **5 subagentes** escribiendo en el árbol, cada uno con su herramienta;
- `vitest` y `tsc` del panel;
- el stack entero de Docker Compose (20 contenedores).

De 32 GB quedaban **4,5 GB libres** ya en reposo. Bajo pico, WSL2 se queda sin
sitio, Docker Desktop reinicia su VM, y los contenedores vuelven con un apagón
sucio detrás. `docker inspect` no lo delata: dice `restartCount=0` —ese contador
sólo cuenta reinicios por _restart policy_, no que la VM entera se haya
reiniciado— y `State.OOMKilled=false`, porque no fue el cgroup del contenedor
quien se quedó sin memoria, fue el anfitrión.

Lo que sí lo delata, y es lo que hay que mirar:

- `startedAt` reciente sin `restartCount`;
- `syncing data directory (fsync)` en los logs, o sea recuperación;
- errores de conexión repartidos por ficheros sin relación entre sí.

## El daño real: un veredicto que no vale

Lo caro no es tener que repetir la suite. Es que **el resultado no distingue un
defecto de una caída de infraestructura**, y que en la misma ventana los agentes
estaban editando los ficheros que la suite leía. Un test que falla porque su
fichero cambió a mitad de colección se lee exactamente igual que una regresión.

En esa pasada hubo ocho rojos. Al repetirlos en serie, **los cuatro del córtex y
el de WebSocket pasaron**: eran contaminación, no defectos. Perseguir esos cuatro
como si fueran regresiones habría costado horas.

## La segunda mitad: los puertos publicados no vuelven solos

Cuando la VM se reinicia, los contenedores arrancan pero **el proxy de puertos de
Docker Desktop puede quedarse tirando conexiones**. Y la señal que uno mira
primero dice que todo está bien:

```
$ docker exec agentic-platform-postgres-1 pg_isready -U postgres
/var/run/postgresql:5432 - accepting connections     # dentro: perfecto
$ docker port agentic-platform-postgres-1
5432/tcp -> 127.0.0.1:15432                          # el mapeo existe

$ python -c "asyncpg.connect('...@127.0.0.1:15432/postgres')"
ConnectionError: unexpected connection_lost() call   # desde el host: no
```

Con Redis pasa igual y el mensaje es aún más engañoso —`Connection closed by
server`— mientras `redis-cli ping` **dentro** del contenedor responde `PONG`.

Traducido a una pasada de tests: la primera vez salieron 125 errores de conexión
en 66 segundos; arreglado Postgres y repetida, 56 errores más, todos de Redis,
porque sólo había reiniciado uno de los dos.

**El arreglo es `docker restart <contenedor>`**, que vuelve a publicar el puerto.
No hace falta `docker compose up` y desde luego no `down -v`. Antes de reiniciar
Redis, comprueba que no te llevas trabajo por delante:

```
docker exec ...redis-1 redis-cli -a "$PWD" -n 1 llen default   # y las demás colas
docker exec ...redis-1 redis-cli -a "$PWD" config get appendonly
```

Con las colas a cero y `appendonly yes`, el reinicio no pierde nada: las claves
vuelven del AOF (comprobado: 332 / 12 / 3159 antes y después).

## Regla

1. **Un solo proceso de pytest de integración a la vez.** Lo dice ya
   [`integration-tests-share-one-database.md`](integration-tests-share-one-database.md)
   para la base de datos; esto lo extiende al anfitrión: aunque separes
   `TEST_PG_DB_NAME` y `TEST_REDIS_URL` por shard —que hay que hacerlo—, la
   memoria del host **no se puede separar**.
2. **No dejes agentes escribiendo mientras corre la suite.** O corre la suite, o
   escribe; no las dos cosas. La suite mide un árbol quieto.
3. **Antes de creerte un rojo, mira el sustrato.** `docker ps` y
   `docker logs postgres | tail`. Si hay recuperación, el veredicto entero se tira.
4. Si necesitas paralelismo, mide primero cuánta RAM libre queda en reposo y
   divide: cada shard de esta suite pide ~500 MB, y cada subagente, más.
5. **Tras una caída de la VM, reinicia los contenedores cuyos puertos uses**
   (`postgres` y `redis`) antes de volver a lanzar nada. «Sano por dentro» no
   quiere decir «alcanzable desde el host», y comprobarlo cuesta dos segundos:
   conecta desde el host por el puerto publicado, no con `docker exec`.

## Relacionado

- [`pytest-en-segundo-plano-no-avisa-de-que-docker-murio.md`](pytest-en-segundo-plano-no-avisa-de-que-docker-murio.md)
  — el hermano de esta trampa: cuando el demonio muere del todo, la suite no
  falla, se queda quemando CPU contra una base de datos que ya no existe.
- [`integration-tests-share-one-database.md`](integration-tests-share-one-database.md)
  — por qué dos pytest de integración a la vez se pisan.
- [`tests-de-integracion-en-la-redis-del-stack-vivo.md`](tests-de-integracion-en-la-redis-del-stack-vivo.md)
  — la otra mitad del aislamiento por shard.
