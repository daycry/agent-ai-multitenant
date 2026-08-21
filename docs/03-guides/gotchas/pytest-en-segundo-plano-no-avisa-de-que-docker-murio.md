---
title: "Una hora de pytest quemando CPU contra una base de datos que ya no existía"
area: tests, docker, windows
encountered: 2026-08-12
stack: pytest, Docker Desktop, Windows
---

## Síntoma

Se lanza la suite de integración en segundo plano y, una hora después:

- el fichero de salida sigue con **0 bytes**;
- `Get-Process python` dice que el proceso está vivo y lleva **942 s de CPU**;
- ninguna notificación, ningún error, ningún test marcado.

Todo parece «va lento pero avanza». Docker Desktop llevaba media hora caído.

## Causa raíz

Dos cosas que por separado son inofensivas y juntas producen una hora en blanco:

1. **`pytest -q` con stdout redirigido usa buffer de bloque.** No escribe los
   puntos de progreso según ocurren: los acumula. Con la suite entera por delante,
   el fichero de salida se queda vacío tanto tiempo que su vacío deja de
   informar. Un fichero vacío significa «aún no ha volcado» y también «no ha
   ejecutado nada», y desde fuera son indistinguibles.

2. **Las fixtures reintentan la conexión.** Al morir el demonio de Docker,
   `localhost:15432` deja de existir; asyncpg no falla instantáneamente en
   Windows, y cada fixture consume su tiempo antes de rendirse. Eso mantiene el
   proceso vivo y con CPU, que es justo la señal que uno interpreta como «está
   trabajando».

Diagnóstico en dos segundos, y ninguno de los dos es mirar el log:

```bash
docker ps                       # daemon vivo?
```

```powershell
Test-NetConnection localhost -Port 15432 -InformationLevel Quiet
```

## Fix

**Comprobar el sustrato antes de dar por buena una tanda larga**, no después. Si
`docker ps` falla o el puerto no responde, lo que corre no vale nada por mucha
CPU que gaste.

Y **partir la suite en shards paralelos**, que además de tardar cuatro veces
menos hace que el silencio sea sospechoso mucho antes. Cada shard necesita su
propia BD y su propia base de Redis (ver
[integration-tests-share-one-database.md](integration-tests-share-one-database.md)
y [redis-con-contrasena-rompe-la-integracion.md](redis-con-contrasena-rompe-la-integracion.md)):

```bash
ls tests/integration/test_*.py | sort > /tmp/allint.txt
split -n l/4 -d /tmp/allint.txt /tmp/shard_

P=$(grep '^REDIS_PASSWORD=' docker/.env | cut -d= -f2-)
TEST_REDIS_URL="redis://:${P}@localhost:6379/5" TEST_PG_DB_NAME=agentic_int_s0 \
  pytest $(cat /tmp/shard_00 | tr '\n' ' ') -q -p no:randomly --timeout=600
```

**La base de Redis, de la 5 en adelante.** Esta receta decía `/1`, y ahí está el
broker de Celery del stack levantado: el worker vivo drena la cola `default`
antes de que el test la lea, y seis tests de despacho salen rojos con
`assert len(raw) == 1` sobre cero elementos, sin nada que apunte a Docker (y de
paso el `DEL default` del test se lleva trabajo real encolado). Las bases 0, 1 y
2 son del stack; `_redis_url.py` ahora aborta si se las pides. Está contado en
[tests-de-integracion-en-la-redis-del-stack-vivo.md](tests-de-integracion-en-la-redis-del-stack-vivo.md).

`--timeout=600` es la otra mitad: con él, un test colgado contra una base muerta
muere en diez minutos en vez de mantener viva la ilusión indefinidamente.

## Lo que NO hay que hacer

**Dar por buena la tanda porque «el proceso seguía vivo».** Es la trampa entera:
el proceso vivo era el síntoma del fallo, no la prueba del progreso.

**Reinterpretar el silencio como éxito.** Cuando la suite termine de verdad
escribirá su línea de resumen. Sin esa línea no hay resultado — hay una espera.

## La clase de problema, que volverá

Es el criterio del proyecto aplicado a la propia verificación: **una medida que
miente cuesta más que ninguna medida**. Una barra de progreso vacía que uno lee
como «va lento» es peor que no tener barra, porque compra confianza que no
existe. Igual que los 9.344 pasos que marcaban `0 ms`, o el healthcheck de
tinyproxy que no podía fallar por el `|| true`.

Regla práctica: **antes de esperar a algo largo, comprueba que lo que espera
puede terminar**. Y si una espera larga no produce NINGUNA señal intermedia,
arréglala para que la produzca en vez de acostumbrarte a su silencio.
