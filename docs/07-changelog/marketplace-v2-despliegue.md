---
plan_id: marketplace-v2-despliegue
title: Marketplace v2 — despliegue en proyectos, publicación con revisión y versiones
completed_at: null
docs_language: es
---

# Plan marketplace-v2-despliegue — que instalar sea recibir

## Resumen

El marketplace llegaba al catálogo y **moría ahí**. Instalar no escribía ni una
fila `agent_tools`, no configuraba ningún servidor MCP en ningún proyecto, y la
config guiada de la tool destacada se pedía **al instalar** y se guardaba a
nivel de tenant — con lo que dos proyectos no podían apuntar a URLs distintas.
En una frase: instalar era **comprar sin recibir**.

Este plan cablea ese último tramo introduciendo el **despliegue** como entidad
de primera clase (ADR 0142) y partiendo la configuración en **tres capas**: el
listing declara qué campos existen (`config_schema`), la instalación consiente
permisos y nada más, y el despliegue captura los valores **por proyecto**. La
cadena entera queda trazable: listing → versión → instalación → despliegue →
las filas concretas que creó.

## Lo que cambia, en orden de importancia

### 1. El despliegue, con retirada exacta

`marketplace/deploy.py` + `routers/marketplace_deployments.py` (fichero propio:
`marketplace.py` ya pasaba de 1.700 líneas). Materializa según el tipo:

- `mcp_server` → entrada en `projects.mcp_servers` **más** la política rol→tool
  en `projects.mcp_tool_roles`. **Sin política paralela**: el `role_map` rellena
  la política del ADR 0128 que ya existía.
- `tool` / `skill` → filas `agent_tools` / `agent_skills` para los agentes del
  equipo cuyo rol esté en el `role_map`, reutilizando la fila `Tool`/`Skill` que
  la materialización del ADR 0100 creó al instalar.

Cada fila creada queda anotada en `deployments.created_refs`, y **retirar
deshace exactamente eso**: una tool que el operador asignó a mano al mismo
agente sobrevive a la retirada. La fila del despliegue no se borra nunca (pasa a
`retired`), y un UNIQUE parcial `(installation_id, project_id) WHERE status =
'active'` hace el re-despliegue idempotente en la BD, no solo en el código.

### 2. Tres puertas de UI, una sola entidad

Ficha de la instalación («Desplegar a…» + retirar), paso **Capacidades** del
wizard de proyecto, y sección «disponibles del tenant» en las pestañas del
proyecto. Las tres escriben la misma tabla y abren el **mismo** formulario
(`components/marketplace/deployment-config-form.tsx`), que deriva sus campos del
`config_schema` — así que un listing que declare uno tiene formulario sin
escribir una línea, y las superficies no pueden divergir en silencio.

### 3. Publicar pasa por revisión

`draft → pending_review → published | rejected`, en `marketplace/review.py`,
única puerta por la que cambia `review_status`. Cada transición exige actor y
escribe doble auditoría (la del tenant autor y la de plataforma, para que
revisar un listing GLOBAL también deje rastro). La visibilidad del catálogo
filtra por `published` **encima de la RLS**: un `pending_review` ajeno es un
404, no un 403. Un rechazo sin motivo escrito es un 422.

### 4. Versiones y actualización explícita

Cada publicación deja una fila en `marketplace_listing_versions` con el
manifest, los permisos y el `config_schema` **tal como se publicaron**; la
instalación pina la que consintió. Actualizar re-pregunta **solo el delta** de
permisos, refresca cada despliegue (campos nuevos → default, retirados → fuera)
y, si el esquema nuevo exige un campo sin default, deja ESE despliegue
`disabled` **con el motivo escrito** en vez de aplicarlo a medias. Rollback = el
mismo endpoint apuntando a una versión anterior. **Nada se actualiza solo.**

### 5. Playwright al modelo nuevo (`task_mkt2_13`)

La pantalla `/admin/marketplace/listings/[id]/playwright-config` y el botón
«Configurar» del catálogo **se retiran**: pedían al instalar unos valores —la
`base_url` del sitio bajo prueba— que son del proyecto, y al instalar los
proyectos ni existen. `PlaywrightToolConfig` **no se borra**: pasa de guardar la
config a validarla. El `config_schema()` declara `x-typed-validator: playwright`
y `validate_deployment_config` lo invoca en cada despliegue, con registro
**fail-closed** (un validador declarado y no registrado es un error, nunca un
«pues no valido»).

## Migraciones

| Rev    | Qué                                                                                                            |
| ------ | -------------------------------------------------------------------------------------------------------------- |
| `0128` | `marketplace_deployments` + `marketplace_listing_versions` (RLS FORCE) + `pinned_version_id` con backfill      |
| `0129` | `listings.review_status` / `reviewed_by` / `reviewed_at` / `rejection_reason` (lo publicado hoy → `published`) |
| `0130` | `marketplace_deployments.disabled_reason`                                                                      |

**La migración de datos de Playwright NO existe, y es correcto.**
`marketplace_installations` nunca tuvo columna de configuración — el formulario
guiado renderizaba una vista previa y no persistía nada—, así que no hay valores
previos que convertir. El caso que el plan daba por esperado (el vacío) es el
real, y lo afirma un test contra `information_schema`, no la buena fe.

## Un defecto encontrado de camino, y arreglado

**El listing destacado del marketplace no se podía instalar.** `POST
/marketplace/installations` devolvía **422** («listing manifest has no
materialisable implementation_type ('')») para Playwright: la puerta de
materialización del ADR 0100 llegó DESPUÉS de `task_09_13` y el manifest de
Playwright nunca declaró `implementation_type`. Dos piezas correctas que nadie
había ejercido juntas; lo destapó el test de despliegue de esta fase.

El arreglo es declarar `implementation_type: docker_command` en
`playwright_listing_manifest()`, que es la verdad —un navegador real dentro del
runtime `node-playwright`—. Con eso la instalación entra **diferida honesta**
(ADR 0081 Fase B/C): sin fila en el catálogo `tools`, y el despliegue lo dice en
un `warning` en lugar de fingir una tool invocable que ningún agente podría
llamar. Cuando exista el sandbox out-of-process, lo que cambia es la
materialización, no este manifest.

## Deuda conocida al cerrar (verificada, no supuesta)

- **El banner de actualización NO existe.** `task_mkt2_12` prometía «banner
  "v X.Y disponible" en ficha y catálogo con el diff de permisos en claro»; el
  backend está entero (endpoint, delta, re-consentimiento, refresco, rollback)
  pero **no hay ni una llamada a `update-check` ni a
  `installations/{id}/update` en todo `apps/admin-panel`** (grep del
  2026-08-01). Mientras tanto, `human_mkt2_02` se conduce por API y así está
  escrito en su guía.
- **Playwright no llega al agente.** Ver §5 y la nota de la referencia: su
  materialización sigue diferida por el ADR 0081.

## Fuera de alcance, a propósito

Ratings y reseñas; federación con marketplaces externos; auto-update en
cualquier variante (la decisión D7 lo rechaza expresamente); y el sandbox para
tools con código propio, que sigue gated por la infra que el ADR 0081 nombra.

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_marketplace_config_schema.py \
  tests/unit/test_marketplace_deployment_models.py \
  tests/unit/test_marketplace_review_transitions.py \
  tests/unit/test_marketplace_permission_diff.py -q

# Integración: uno a la vez, o con TEST_PG_DB_NAME + TEST_REDIS_URL propios
.venv/Scripts/python.exe -m pytest tests/integration/test_marketplace_v2_chain.py -q -p no:randomly
.venv/Scripts/python.exe -m pytest tests/integration/test_playwright_deploy_config.py -q -p no:randomly
```

`test_marketplace_v2_chain.py` es el test que da sentido al plan (publicar →
instalar → desplegar → **el proyecto/el agente LO TIENEN** → retirar → limpio) y
`test_playwright_deploy_config.py` el que demuestra que el rediseño sirve para
algo: dos proyectos con `base_url` distinta conviviendo, que es lo que el modelo
viejo no podía expresar.

## Pendiente de humano

Los tres tests humanos del plan
([guía](../03-guides/human-tests/marketplace-v2-despliegue.md)) exigen navegador
y no se han ejecutado: el viaje completo en UI, la actualización con delta de
permisos y el OAuth de un MCP desplegado.
