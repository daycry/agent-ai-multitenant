---
title: "La CI lleva meses roja en `docker compose config` y en local da exit 0: el runner no tiene `docker/.env` y el compose lo exige"
area: ci, docker, despliegue
encountered: 2026-08-13
stack: docker compose v2.x, GitHub Actions (ubuntu-latest)
---

## Síntoma

El job «Integration tests» muere en el primer paso que toca compose, con un
error que señala a un servicio que ni se iba a levantar:

```
error while interpolating services.postgres.environment.SERVICE_USER_PASSWORD:
required variable SERVICE_USER_PASSWORD is missing a value:
set SERVICE_USER_PASSWORD in docker/.env (cp docker/.env.example docker/.env)
##[error]Process completed with exit code 1
```

Tres detalles que despistan:

1. **En local, el mismo comando da exit 0.** Reproducir «el fallo de la CI» en la
   máquina de desarrollo no lleva a nada.
2. **Fallan también los pasos de diagnóstico.** `Stack logs on failure` y
   `Tear down` (`docker compose … logs` / `down -v`) abortan con el MISMO error,
   así que el job termina con tres `##[error]` seguidos y ni un log del stack.
3. **El servicio culpable puede estar bajo un perfil que nadie levanta.** El
   segundo aborto lo firma `services.watchdog`, que vive en `profiles:
[watchdog]` y no forma parte de ningún `up` de la CI.

## Causa raíz

Dos hechos que por separado son correctos y juntos rompen la CI.

**Uno.** El plan **prod-10** («sin `environment` explícito no hay secretos
default») convirtió cada credencial del compose canónico de `${VAR:-changeme}` a
`${VAR:?mensaje}`. Es la decisión correcta: un despliegue al que le falte una
credencial **no arranca**, en vez de arrancar con la contraseña publicada en este
repositorio. Y la interpolación de Compose ocurre **al cargar cada fichero**,
antes de filtrar por perfiles y antes de mergear los `-f`, así que una sola
variable ausente no se lleva su servicio: se lleva el proyecto entero, incluidos
`config`, `ps`, `logs` y `down`.

**Dos.** `docker/.env` está en `.gitignore` a propósito, y el runner arranca de
un `checkout` limpio. El job cubría el hueco enumerando las credenciales en un
bloque `env:` escrito a mano:

```yaml
env:
  POSTGRES_PASSWORD: changeme-dev-only
  # … cinco más
```

Esa lista es una **segunda fuente de verdad**, y se quedó atrás en cuanto
prod-14 añadió `SERVICE_USER_PASSWORD` y prod-08
`API_SERVER_ALERTS_INGEST_TOKEN`. Desde ese día la CI de integración estaba roja
en el primer paso — y nadie lo relacionaba con esos planes, porque el rojo
aparecía en un job que no habían tocado y no se reproducía en local.

## Fix

Materializar el fichero que el compose espera, **copiándolo** en vez de
reescribir su contenido en el workflow, justo antes del primer `docker compose`:

```yaml
- name: Materialise docker/.env (ephemeral CI credentials)
  if: steps.detect.outputs.has_compose == 'true'
  run: |
    cp docker/.env.example docker/.env
    redis_password="$(sed -n 's/^REDIS_PASSWORD=//p' docker/.env)"
    echo "TEST_REDIS_URL=redis://:${redis_password}@localhost:6379/15" >> "$GITHUB_ENV"
```

Y borrar el bloque `env:` del job. Lo importante no es el `cp`, es **quién
mantiene la lista**: `docker/.env.example` la mantiene una guarda
(`tests/unit/test_compose_required_env_is_documented.py` cruza cada `${VAR:?…}`
de todos los compose contra las asignaciones del ejemplo), mientras que un
bloque `env:` sólo lo mantiene la memoria de quien añade la siguiente
credencial.

Al quitar el bloque hay que barrer lo que colgaba de él. Aquí eran tres pasos con
`TEST_REDIS_URL: redis://:${{ env.REDIS_PASSWORD }}@localhost:6379/15`: sin el
`env:` del job eso no da error, **interpola a una credencial vacía** y produce el
`NOAUTH` dentro de una fixture de
[redis-con-contrasena-rompe-la-integracion.md](./redis-con-contrasena-rompe-la-integracion.md).
De ahí que la URL se exporte una sola vez a `$GITHUB_ENV`.

## Sobre los secretos del `.env.example` en la CI

Son aceptables ahí y sólo ahí, y conviene que el workflow lo diga por escrito:
son **públicos** (están en el repositorio) y **efímeros** (este stack lo levanta
y lo destruye el propio job, no se publica fuera del runner y muere en el
`down -v`). En producción los inyecta el instalador desde Vault, y el `${VAR:?…}`
es precisamente lo que impide que un despliegue real arranque con estas cadenas.
Lo que **no** vale es la lectura perezosa de esto: «en CI da igual, pongo
cualquier cosa» convierte el workflow en el sitio donde las credenciales se
inventan una a una, que es de dónde venía el problema.

## Cómo verificar el fix

El fallo se reproduce en local escondiendo el `.env` un momento. **Ese fichero no
está en git**: cópialo fuera antes de tocarlo y restáuralo al terminar.

```bash
cp docker/.env /tmp/env.backup            # red de seguridad
mv docker/.env docker/.env.bak
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
               -f docker/docker-compose.ci.yml config > /dev/null; echo "exit=$?"
# exit=1  ← el fallo de la CI

cp docker/.env.example docker/.env        # el arreglo, a mano
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
               -f docker/docker-compose.ci.yml config > /dev/null; echo "exit=$?"
# exit=0

rm docker/.env && mv docker/.env.bak docker/.env && ls -la docker/.env
```

Para reproducir el fallo **exacto** de la CI (no el genérico), exporta primero
las seis variables que el job declaraba a mano: el primer aborto pasa a ser
`SERVICE_USER_PASSWORD`, que es lo que se ve en el log.

Y la guarda que impide la reincidencia:

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_compose_required_env_is_documented.py -q
```

Comprueba las dos mitades del contrato: que toda `${VAR:?…}` viaje en
`.env.example`, y que la CI haga el `cp` **antes** del primer `docker compose`.

## La clase de problema, que volverá

Un endurecimiento correcto del despliegue puede romper un consumidor que
construye su configuración por su cuenta —el arnés de tests, la CI, un script de
demo— sin que el rojo señale al plan que lo causó. Ya van tres veces con el mismo
`prod-10`: el stack de dev, la suite de integración (el gotcha hermano) y ésta.

Regla práctica, la misma que cierra el gotcha de Redis y ahora con un matiz:
**al endurecer una credencial, busca quién más la construye a mano** —
`grep -rn 'VAR' .github/ scripts/ tests/` — y si lo que encuentras es una LISTA
de credenciales, no la actualices: haz que lea la lista que ya está guardada.
