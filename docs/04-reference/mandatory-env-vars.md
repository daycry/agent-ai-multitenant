---
title: Variables de entorno OBLIGATORIAS (sin default)
docs_language: es
audience: operador, system admin
updated: 2026-07-31
---

# Variables de entorno obligatorias

Hasta prod-10 el compose canónico —el de **producción**— caía en silencio a
contraseñas escritas en este repositorio público:

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme-dev-only}
```

Es decir: un despliegue que olvidase una variable **arrancaba igualmente**, con
una credencial conocida, sin un aviso, y sin forma de notarlo hasta que alguien
entrase. Desde `task_prod10_05` / `task_prod10_06` (hallazgos secrets-6 y
secrets-7) cada credencial se declara `${VAR:?mensaje}`: si falta, el stack **no
arranca** y dice qué poner y dónde.

> **Fallar al arrancar es mejor que arrancar con una credencial vacía o
> conocida.** El fallo se ve; la credencial débil no.

## Cómo se rellenan

`docker compose` carga automáticamente el `.env` del directorio del primer
fichero `-f` — aquí, `docker/`:

```bash
cp docker/.env.example docker/.env
# y edita los valores antes de cualquier despliegue que no sea tu portátil
```

En producción los valores los inyecta el instalador desde Vault; `.env.example`
trae valores de desarrollo que son **públicos por definición** (están en un
fichero commiteado).

### Trampa de Compose que conviene conocer

`${VAR:?…}` se resuelve al **cargar cada fichero, antes del merge de overlays**.
Un `docker-compose.dev.yml` con `${VAR:-un-default}` **no rescata** al base: el
error salta igual. Comprobado con `docker compose config`. Por eso los valores de
desarrollo viven en `.env.example` y no en el overlay.

## Catálogo

| Variable                   | Fichero que la exige        | Qué protege                                                                                   |
| -------------------------- | --------------------------- | --------------------------------------------------------------------------------------------- |
| `POSTGRES_PASSWORD`        | `docker-compose.yml`        | Superusuario de PostgreSQL.                                                                   |
| `MIGRATIONS_USER_PASSWORD` | `docker-compose.yml`        | Rol dueño del esquema (DDL de Alembic).                                                       |
| `APP_USER_PASSWORD`        | `docker-compose.yml`        | Rol de aplicación (NOBYPASSRLS) con el que corren los servicios.                              |
| `REDIS_PASSWORD`           | `docker-compose.yml`        | Sesiones de servidor + broker de Celery + contadores de rate limit. Ver nota abajo.           |
| `MINIO_ROOT_PASSWORD`      | `docker-compose.yml`        | Object storage: adjuntos, documentos de KB y **bundles de backup**.                           |
| `SEARXNG_SECRET`           | `docker-compose.yml`        | Firma de formularios/cookies del buscador del córtex.                                         |
| `GRAFANA_ADMIN_PASSWORD`   | `docker-compose.monitoring` | Panel con acceso a todas las métricas del stack. Sólo si apilas el overlay de observabilidad. |

### `REDIS_PASSWORD` — los clientes también

Redis no es una caché de resultados: aloja las **sesiones** (una sesión revocable
vive ahí), el **broker de Celery** —o sea, la capacidad de encolar trabajo para
los workers— y los contadores de rate limit. Corría sin autenticación y, con el
overlay de dev, publicado en `0.0.0.0`: toda la LAN corporativa.

Al activar `requirepass`, **todas** las URLs de cliente llevan la credencial:

```
redis://:<REDIS_PASSWORD>@redis:6379/<db>        # dentro del stack
redis://:<REDIS_PASSWORD>@localhost:6379/<db>    # desde el host
```

Incluidas las de los tests de integración (`TEST_REDIS_URL`) y cualquier
`redis-cli` a mano (`redis-cli -a "$REDIS_PASSWORD" ping`).

### Puertos: sólo loopback en desarrollo

`docker-compose.dev.yml` y `docker-compose.monitoring.dev.yml` publican **todos**
sus puertos en `127.0.0.1:` (prod-10 `task_prod10_06`). El default de Docker es
`0.0.0.0`, que en un portátil corporativo significa la oficina entera. Todo lo
local sigue funcionando igual; si de verdad necesitas acceso desde otra máquina,
añade tu propio overlay en vez de ensanchar éste.

## Tokens de Vault por servicio

No son variables del compose sino del `.env`, y las genera un script — nunca se
escriben a mano ni se comparte el root token entre servicios:

```bash
VAULT_TOKEN=<root, una sola vez>  ./scripts/vault-mint-service-tokens.sh >> docker/.env
```

Produce cuatro tokens **periódicos y huérfanos**, uno por política de
`installer_backend.vault_bootstrap`:

| Variable                   | Política de Vault         |
| -------------------------- | ------------------------- |
| `API_SERVER_VAULT_TOKEN`   | `api-server`              |
| `WORKERS_VAULT_TOKEN`      | `workers`                 |
| `ORCHESTRATOR_VAULT_TOKEN` | `orchestrator`            |
| `NOTIFY_VAULT_TOKEN`       | `notification-dispatcher` |

**Periódicos**: no caducan mientras se renueven dentro de su periodo, y el
api-server los renueva solo en segundo plano
(`api_server.vault_client.VaultTokenManager`, métrica
`agentic_vault_token_ttl_seconds`). **Huérfanos**: revocar el root token no se
los lleva por delante — condición necesaria para poder revocar el root token
expuesto sin tumbar la plataforma.

## Cuando el arranque falla

| Síntoma                                                     | Causa                                                   | Arreglo                                                                     |
| ----------------------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------- |
| `required variable POSTGRES_PASSWORD is missing a value`    | No hay `docker/.env`.                                   | `cp docker/.env.example docker/.env`                                        |
| `NOAUTH Authentication required` desde un servicio          | Una URL de Redis sin credencial.                        | `redis://:<REDIS_PASSWORD>@…`                                               |
| El api-server aborta con `environment='dev' … dev defaults` | Falta `API_SERVER_ENVIRONMENT` y la BD no es localhost. | Declara `API_SERVER_ENVIRONMENT=dev\|staging\|prod`, o pon secretos reales. |
| `/admin/system-health` en `degraded` con `Vault is SEALED`  | Vault arrancó sellado tras un reinicio del host.        | [restart-services.md](../06-runbooks/restart-services.md), paso 0.          |

## Relacionado

- [`docs/06-runbooks/restart-services.md`](../06-runbooks/restart-services.md) — desellado post-reinicio.
- [`docs/06-runbooks/02-troubleshooting.md`](../06-runbooks/02-troubleshooting.md) — errores de arranque.
- `docker/.env.example` — la plantilla, con comentarios por variable.
- ADR 0145 — token periódico vs AppRole y estrategia de unseal.
