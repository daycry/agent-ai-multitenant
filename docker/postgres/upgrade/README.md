# docker/postgres/upgrade

Scripts que aplican a una base de datos **que ya existe** cambios que los
ficheros de `../init/` solo harían en un contenedor nuevo.

## Por qué existe esta carpeta

`docker-entrypoint-initdb.d` (nuestro `../init/`) se ejecuta **una única vez**:
cuando el volumen de datos de PostgreSQL está vacío. Cualquier despliegue en
marcha —es decir, todos los que ya están instalados— nunca verá un fichero nuevo
que se añada a `init/`. Sin un script de upgrade, un cambio de roles o de
privilegios «funciona en local» (donde se recrea el volumen) y no llega a
producción.

Regla: **todo cambio en `init/` que no sea la creación inicial necesita su
script aquí**, y los dos deben ejecutar el MISMO SQL, no dos copias que se
desincronizan.

## Scripts

| Script                     | Qué aplica                           | Cuándo                                              |
| -------------------------- | ------------------------------------ | --------------------------------------------------- |
| `20260730-service-user.sh` | El rol `service_user` (plan prod-14) | Antes de cambiar el `database_url` de los servicios |

## Cómo se ejecutan

Todos son idempotentes: se pueden lanzar en cada despliegue.

```bash
# Desde el host, contra el contenedor del stack:
docker compose -f docker/docker-compose.yml exec -T postgres \
  env SERVICE_USER_PASSWORD="$SERVICE_USER_PASSWORD" \
  bash /docker-entrypoint-initdb.d/../upgrade/20260730-service-user.sh
```

Si la carpeta `upgrade/` no está montada en el contenedor, la vía corta es pasar
el script por stdin:

```bash
docker compose exec -T postgres bash -s < docker/postgres/upgrade/20260730-service-user.sh
```

…pero entonces el script no encuentra `../init/04-service-role.sql` relativo a sí
mismo: en ese caso copia primero el SQL al contenedor, o ejecútalo directamente:

```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d agentic_platform \
  < docker/postgres/init/04-service-role.sql
```

## Orden de despliegue de `service_user`

1. Aplicar `20260730-service-user.sh` (crea el rol y sus GRANT).
2. Comprobar la salida del propio script: `create_must_be_false` debe ser `f`.
3. **Después** cambiar el `database_url` de workers / orchestrator /
   notification-dispatcher y el `admin_database_url` de la api-server.

Al revés, los cuatro servicios arrancarían a la vez contra un rol inexistente
(riesgo nº 6 del plan prod-14). El rollback es devolver la variable de entorno a
`migrations_user`.
