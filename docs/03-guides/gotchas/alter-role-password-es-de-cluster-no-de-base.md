---
title: "`ALTER ROLE ... PASSWORD` no está acotado a una base: rompe el stack y ningún contenedor se pone en rojo"
area: postgres / arnés de tests
encountered: 2026-08-20
stack: PostgreSQL 16 en Docker Compose, api-server FastAPI + asyncpg, arnés e2e con backend vivo
docs_language: es
---

# El `ALTER ROLE` que se llevó por delante el stack, con los contenedores en verde

## Síntoma

La aplicación deja de poder consultar la base de datos, pero **nada parece roto**:

- `docker ps` dice `Up 31 hours (healthy)` para el api-server y para postgres.
- `GET /healthz` devuelve **200**.
- `GET /readyz` devuelve **503**.
- En los logs de postgres, cientos de líneas iguales:

  ```
  FATAL:  password authentication failed for user "app_user"
  ```

  (283 en 45 minutos, una cada 10 s: el intervalo del healthcheck.)

- `pg_stat_activity` sobre la base de la aplicación se queda **sin el rol de la
  aplicación** — sólo aparece el que abrió una conexión antes del cambio:

  ```sql
  SELECT usename, count(*) FROM pg_stat_activity WHERE datname='agentic_platform'
   GROUP BY usename;
  --  migrations_user | 1
  ```

Y si estás montando un arnés en otra base, el síntoma que ves primero es otro:
tu propio arnés falla con `InvalidPasswordError` **usando la contraseña correcta**
del `docker/.env`, porque el rol ya no la tiene.

## Causa raíz

**Los roles de PostgreSQL son objetos de CLÚSTER, no de base de datos.** No hay
`ALTER ROLE ... IN DATABASE x` para la contraseña: `ALTER ROLE app_user WITH
PASSWORD '…'` cambia la credencial **para todas las bases del servidor**, incluida
la del stack.

Es fácil de creer lo contrario porque casi todo lo demás que un arnés hace _sí_
está acotado a su base: `GRANT ... ON ALL TABLES IN SCHEMA public`, `ALTER DEFAULT
PRIVILEGES`, las extensiones, el `search_path`. En esa lista, la contraseña es la
excepción — y es la única que se lleva algo por delante.

Lo que hace el fallo difícil de leer son dos cosas más:

1. **El contenedor sigue `healthy`.** El healthcheck del api-server usa
   `/healthz`, que a propósito **no toca la base** (así distingue «el proceso
   vive» de «el proceso puede trabajar», y así un postgres caído no provoca un
   reinicio en bucle). O sea que Docker informa verde mientras la aplicación no
   puede leer una fila. El que sí lo dice es `/readyz`, y nadie lo mira si no
   sospecha.
2. **Las conexiones ya abiertas siguen funcionando.** PostgreSQL comprueba la
   contraseña al conectar, no en cada consulta, así que el pool que ya tenía
   sesiones sigue sirviendo un rato. El fallo aparece cuando el pool recicla, o
   sea **más tarde y en otro sitio** que el cambio que lo causó.

## Fix

**Un arnés no toca los roles compartidos.** Dos formas, en orden de preferencia:

1. **Reutilizar el rol con su contraseña de verdad**, leyéndola de donde ya vive
   (`docker/.env`: `APP_USER_PASSWORD`, `SERVICE_USER_PASSWORD`). Los `GRANT` sí
   son por base, así que se le puede dar acceso a la base desechable sin tocar
   nada de la del stack. Es lo que hace `scripts/dev/e2e-live-harness.ps1`.
2. **Crear roles propios** (`e2e_app_user`, …) si de verdad hace falta una
   credencial distinta, y darles privilegios sólo sobre la base desechable. Nunca
   `ALTER ROLE` sobre uno que use otro proceso.

Si ya ha pasado, restaurar es una línea — y las contraseñas buenas están en el
`.env`, que es la fuente de verdad del compose. Por stdin y no en `-c`, para que
el secreto no acabe en el historial ni en `ps`:

```bash
apw=$(sed -n 's/^APP_USER_PASSWORD=//p' docker/.env)
spw=$(sed -n 's/^SERVICE_USER_PASSWORD=//p' docker/.env)
printf "ALTER ROLE app_user WITH PASSWORD '%s';\nALTER ROLE service_user WITH PASSWORD '%s';\n" \
  "$apw" "$spw" | docker exec -i agentic-platform-postgres-1 psql -U postgres -v ON_ERROR_STOP=1
```

**No hay pérdida de datos**: es autenticación, no contenido. Lo que se pierde es
el tiempo hasta darse cuenta.

## Cómo verificar el fix

Las tres señales, y ninguna es «el contenedor está healthy»:

```bash
# 1. El rol de la aplicación vuelve a tener conexiones.
docker exec agentic-platform-postgres-1 psql -U postgres -t \
  -c "SELECT usename, count(*) FROM pg_stat_activity
       WHERE datname='agentic_platform' GROUP BY usename;"
#  app_user        | 1
#  migrations_user | 1

# 2. `readyz` —no `healthz`— vuelve a 200.
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/readyz   # 200

# 3. Los FATAL dejan de acumularse.
docker logs agentic-platform-postgres-1 --since 2m 2>&1 \
  | grep -c "password authentication failed"                                 # 0
```

## Historia

2026-08-20, montando a mano el arnés de los 12 specs Playwright que no mockean el
backend. Se ejecutó `ALTER ROLE app_user WITH LOGIN PASSWORD 'e2e_app_pw'`
creyendo que quedaba acotado a la base desechable `e2e_vivo`. El stack llevaba 31
horas arriba y siguió reportándose `healthy` durante los 45 minutos que tardó en
notarse; lo que lo destapó fue que **el guion del arnés falló con la contraseña
correcta**, porque leía el `.env` y el rol ya no la tenía.

La lección que va más allá de este comando: cuando un arnés y el stack comparten
servidor de base de datos, la pregunta que hay que hacerse de cada sentencia no es
«¿esto es reversible?» sino **«¿esto es de base o de clúster?»**. Y la señal a
vigilar no es el estado del contenedor: es `/readyz` y `pg_stat_activity`.
