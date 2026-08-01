# Marketplace v2 (despliegue) — tests humanos

Esta guía cubre los **3 tests humanos** del plan `marketplace-v2-despliegue`.
Los tres necesitan **navegador**, que es justo lo que los automáticos no
tienen: la cadena entera está pineada en integración
(`tests/integration/test_marketplace_v2_chain.py`), pero que la UI la
recorra sin callejones sin salida solo lo dice un humano usándola.

> **Qué se valida, en una frase.** Que instalar deje de ser «comprar sin
> recibir»: que lo instalado se pueda **desplegar** en proyectos concretos con
> configuración distinta en cada uno, que publicar pase por **revisión**, y que
> una versión nueva pida el **delta** de permisos y no todo otra vez.

## TL;DR

No hay `setup_demo_marketplace_v2.py` ni launcher dedicado para este plan. El
catálogo de arranque lo siembra el runner de seeds (Plan 09.1); sin él te
encuentras el marketplace vacío.

```powershell
.\scripts\dev\up.ps1                           # api-server :8001 + admin-panel :3000 + postgres + redis
.\.venv\Scripts\python.exe -m api_server.seeds # built-ins + listings oficiales (idempotente)
```

Las pantallas implicadas:

```
http://localhost:3000/admin/marketplace                          # catálogo + instaladas + compartir
http://localhost:3000/admin/marketplace/private                  # publicar un listing propio
http://localhost:3000/admin/marketplace/review                   # cola de revisión (System Admin)
http://localhost:3000/admin/marketplace/installations/{id}       # ficha: desplegado-en, desplegar, retirar
http://localhost:3000/admin/projects/new                         # wizard, paso «Capacidades»
http://localhost:3000/admin/projects/{id}/mcp-servers            # pestaña MCP: disponibles del tenant
http://localhost:3000/admin/projects/{id}/agent-tools-diagnostic # pestaña Tools: disponibles del tenant
```

## Pre-requisitos

| Requisito                                      | Por qué                                                                            |
| ---------------------------------------------- | ---------------------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                    | api-server + admin-panel + postgres + redis                                        |
| Esquema en `0130` o superior                   | Las tres migraciones del plan (`0128` despliegues, `0129` revisión, `0130` motivo) |
| Un usuario `tenant_admin`                      | Instalar, desplegar y retirar son operaciones de Tenant Admin                      |
| Un usuario `system_admin`                      | Aprobar / rechazar / promocionar en la cola de revisión                            |
| **Dos** proyectos del mismo tenant             | El caso que da sentido al plan es dos configuraciones distintas conviviendo        |
| Un equipo con un agente del rol destino        | Sin agente de ese rol no hay a quién asignar (el despliegue lo avisa, no falla)    |
| Un MCP remoto con OAuth (para `human_mkt2_03`) | El flujo «Conectar» del ADR 0127 se encadena al despliegue                         |

> **Ojo con el rol destino.** Si el despliegue devuelve un aviso del tipo
> «ningún agente del proyecto tiene los roles …», no es un fallo del test: es
> el sistema diciendo la verdad. Revisa el equipo del proyecto o el `role_map`
> antes de dar el paso por rojo.

---

## `human_mkt2_01` — El viaje completo en navegador

**Qué prueba**: la cadena entera de punta a punta y, sobre todo, **el caso que
el modelo viejo no podía expresar**: dos proyectos usando la misma capacidad
instalada una sola vez, con configuración distinta.

**Precondiciones**:

- Sesión `tenant_admin` en el tenant A y sesión `system_admin` disponible.
- Dos proyectos en el tenant A (o uno, y el segundo se crea en el paso 5).
- Un manifest de MCP de prueba con `targets: [backend_dev]` y un
  `config_schema` que declare `base_url`.

**Pasos**:

1. Como `tenant_admin`, publica el MCP de prueba en
   `/admin/marketplace/private`. La UI debe decir **«pendiente de revisión»**,
   no «publicado».
2. Comprueba desde otro usuario del mismo tenant que **no aparece** todavía en
   el catálogo (`/admin/marketplace`, pestaña Catálogo).
3. Como `system_admin`, entra en `/admin/marketplace/review`, revisa el
   manifest y **apruébalo**.
4. Vuelve como `tenant_admin` e **instálalo** desde el catálogo. Fíjate en que
   la instalación **solo pide consentimiento de permisos**: no hay formulario de
   configuración en ningún punto del install.
5. Crea un proyecto nuevo en `/admin/projects/new`; en el paso **Capacidades**
   marca la capacidad recién instalada y rellena su `base_url` con
   `https://app-a.example`. Termina de crear el proyecto.
6. Abre la pestaña **MCP** de ese proyecto: la entrada debe estar configurada
   con esa `base_url`. Abre la ficha de un agente del rol destino y comprueba
   que tiene lo que le tocaba.
7. Vuelve a `/admin/marketplace/installations/{id}` y usa **«Desplegar a…»**
   sobre un **segundo** proyecto, esta vez con `base_url`
   `https://app-b.example`.
8. **Retira** el despliegue del primer proyecto y vuelve a mirar el segundo.

**Resultado esperado**: el listing no es visible hasta que se aprueba; instalar
no pide configuración; los dos proyectos quedan con **su propia** `base_url`
(paso 6 y 7 muestran valores distintos); la ficha de la instalación dice
«desplegado en 2 proyectos»; y tras el paso 8 el primer proyecto queda limpio
mientras **el segundo sigue intacto** — misma configuración, mismas
asignaciones. Si el segundo se rompe al retirar el primero, el test es **rojo**:
es exactamente el fallo que la retirada exacta (`created_refs`) existe para
impedir.

---

## `human_mkt2_02` — Actualización con delta de permisos

**Qué prueba**: que actualizar re-pregunte **solo lo nuevo** y que los
despliegues se refresquen — o queden `disabled` con el motivo escrito, sin
aplicarse a medias.

> **Se ejecuta por API, no por UI, y no es una preferencia.** El plan preveía un
> banner «v X.Y disponible» en la ficha y en el catálogo con el diff de permisos
> en claro; **ese banner no está implementado** (verificado el 2026-08-01: no hay
> ni una llamada a `update-check` ni a `installations/{id}/update` en todo
> `apps/admin-panel`). El backend sí está entero. Hasta que exista la UI, este
> test humano se conduce con `curl`/Postman contra la API, y **el banner queda
> como deuda anotada**, no como paso «que no se encontró».

**Precondiciones**:

- El listing de `human_mkt2_01` instalado y desplegado en al menos un proyecto.
- Permiso para publicar una versión nueva del mismo listing.
- Un token de `tenant_admin` a mano (los pasos 3-6 van por API).

**Pasos**:

1. Publica una **v2** del mismo listing añadiendo **un permiso nuevo** (deja los
   anteriores como estaban) y apruébala como `system_admin`.
2. `GET /marketplace/installations/{id}/update-check`: debe reportar la
   instalación como desactualizada y proponer la v2.
3. `POST /marketplace/installations/{id}/update` **sin** consentir el permiso
   nuevo. Mira el `permission_delta` de la respuesta: debe traer el permiso
   nuevo en `added` y **solo ése** — los ya concedidos no reaparecen.
4. Repite el `POST` consintiendo el delta. Revisa los despliegues
   (`GET /marketplace/installations/{id}/deployments`): deben haber avanzado de
   `deployed_version` **conservando su `config`**.
5. Publica una **v3** que añada al `config_schema` un campo **requerido y sin
   default**, apruébala y actualiza.
6. Haz **rollback** a la v1: el mismo `POST .../update` apuntando a la versión
   anterior.

**Resultado esperado**: el paso 3 muestra un delta de **un solo** permiso; el
paso 4 deja los despliegues actualizados con su `base_url` intacta; el paso 5
deja el despliegue afectado **`disabled` con `disabled_reason` escrito** (y no
toca los demás, que sí avanzan); el paso 6 restaura configuración y pin. En
ningún momento nada se actualiza solo.

---

## `human_mkt2_03` — OAuth de un MCP desplegado

**Qué prueba**: que el flujo «Conectar» del ADR 0127 se encadena al despliegue
en vez de competir con él, y que la UI **no finge** que la conexión está viva.

**Precondiciones**:

- Un listing `mcp_server` cuyo manifest declare OAuth.
- Credenciales del proveedor OAuth para completar el flujo en navegador.

**Pasos**:

1. Instala ese listing y despliégalo en un proyecto.
2. Mira la entrada recién creada en la pestaña **MCP** del proyecto.
3. Completa el flujo **«Conectar»** desde esa pestaña.
4. Lanza una tarea con un agente del rol destino que use una tool de ese MCP.

**Resultado esperado**: tras el paso 2 la entrada aparece **pendiente de
conexión** y la UI lo dice en claro (nunca como «activa»); tras el paso 3 pasa a
conectada; en el paso 4 el agente usa la tool sin intervención adicional.

---

## Qué NO cubren estos tres

- **La tool Playwright asignada a un agente.** Hoy su listing declara
  `implementation_type: docker_command`, que la materialización del ADR 0100
  deja **diferida** hasta el sandbox out-of-process (ADR 0081 Fase B/C):
  instalarla no crea fila en el catálogo `tools` del tenant, así que desplegarla
  guarda configuración y auditoría pero **no asigna nada a ningún agente**, y el
  despliegue lo dice en un aviso. Para el viaje de un agente usando Playwright
  de verdad, mira `human_09_03` en
  [`09-marketplace.md`](./09-marketplace.md).
- **El aislamiento cross-tenant**, la **idempotencia** del re-despliegue y el
  rechazo de configuración inválida: los cubren los tests automáticos
  (`tests/integration/test_marketplace_deploy_service.py` y
  `test_playwright_deploy_config.py`), y repetirlos a mano no añade nada.
