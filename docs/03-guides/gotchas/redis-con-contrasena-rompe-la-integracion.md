---
title: "Activar la contraseña de Redis pone en rojo los 249 ficheros de integración, con un error que no se parece a su causa"
area: tests, redis, despliegue
encountered: 2026-08-12
stack: pytest, redis-py, docker compose
---

## Síntoma

Tras un despliegue, cualquier test de integración que toque Redis muere **dentro
de una fixture**, no en una aserción:

```
ERROR tests/integration/test_document_upload_limits.py::test_...
E   redis.exceptions.AuthenticationError: Authentication required.
.venv\Lib\site-packages\redis\_parsers\hiredis.py:221: AuthenticationError
```

Salen `ERROR`, no `FAILED` —el test nunca llegó a ejecutarse—, y el traceback
apunta al parser de redis-py. Nada en él menciona la causa.

## Causa raíz

El compose arranca Redis con `--requirepass` desde que el plan **prod-10**
cerró «sin `environment` explícito no hay secretos default»: la variable
`REDIS_PASSWORD` pasó de `${VAR:-default}` a `${VAR:?error}` y el servicio la
exige. El overlay reconstruye las URLs de los servicios con la credencial
(`redis://:${REDIS_PASSWORD}@redis:6379/0`), así que **la aplicación funciona**.

Los tests, no. `tests/integration/conftest.py` construía su URL a mano
(`redis://localhost:6379/15`), sin credencial. Y son **249 ficheros** los que
usan Redis, así que se caen todos a la vez la primera vez que alguien corre la
suite después del despliegue.

Comprobación de un segundo:

```bash
docker exec agentic-platform-redis-1 redis-cli ping
# PONG                          -> sin contraseña
# NOAUTH Authentication required -> con contraseña
```

## Fix

El conftest saca la contraseña de dos sitios, por orden: la variable de entorno
(`TEST_REDIS_PASSWORD` o `REDIS_PASSWORD`) y el `docker/.env` que el propio
compose lee. Con eso, la invocación de siempre sigue funcionando sin exportar
nada.

**Y ojo al paralelizar.** El gotcha hermano
([integration-tests-share-one-database.md](integration-tests-share-one-database.md))
pide dar a cada proceso su `TEST_REDIS_URL` para no pisarse. Esa URL escrita a
mano **tiene que llevar la credencial**:

```bash
# MAL: falla con AuthenticationError dentro de la fixture
TEST_REDIS_URL=redis://localhost:6379/9 pytest tests/integration/test_x.py -q

# BIEN
TEST_REDIS_PASSWORD="$(grep '^REDIS_PASSWORD=' docker/.env | cut -d= -f2)" \
TEST_REDIS_URL="redis://:$TEST_REDIS_PASSWORD@localhost:6379/9" \
  pytest tests/integration/test_x.py -q -p no:randomly
```

## Lo que NO hay que hacer

**Quitar `--requirepass` para que los tests pasen.** Es el endurecimiento de
prod-10, y un Redis sin contraseña en el puerto del host es accesible desde toda
la LAN — que es literalmente el hallazgo que esa tarea cerró. El arnés se adapta
al sistema endurecido, no al contrario.

## La clase de problema, que volverá

Un endurecimiento de seguridad que el código de producción absorbe bien puede
romper el arnés de test **sin que nadie lo relacione**, porque el arnés construye
sus conexiones por su cuenta. Ya pasó dos veces en agosto de 2026:

- las contraseñas por defecto del compose desaparecieron y el stack no arrancaba
  hasta crear un `docker/.env` (mismo plan, misma semana);
- ésta;
- y una tercera, en la CI, descubierta el 2026-08-13:
  [ci-no-tiene-docker-env-y-el-compose-lo-exige.md](./ci-no-tiene-docker-env-y-el-compose-lo-exige.md).
  El runner tampoco tiene `docker/.env`, y el workflow cubría el hueco con una
  lista de credenciales escrita a mano que se quedó atrás. La CI llevaba meses
  roja en `docker compose config` sin que el rojo señalara al plan que lo causó.

Regla práctica: **al endurecer una credencial, busca quién más la construye a
mano** — `grep -rn "redis://\|postgresql://" tests/` — antes de dar el
endurecimiento por terminado.
