---
plan_id: remediacion-marketplace-mcp-2026-09-02
title: Remediación del marketplace y de los MCP por proyecto — de la instalación al run, con anclas Jira/Confluence
status: in_progress
blocking_plan: []
started_at: 2026-09-03
completed_at: null
estimated_duration_calendar: 3 semanas
estimated_effort_person_days: 16
created_by: claude-fable-5-1-audit-2026-09-02
docs_language: es
priority: P1
source_audit: auditoria-marketplace-mcp-2026-09-02 (dos pasadas de un agente de solo lectura, backend y UI; hallazgos resumidos en §Hallazgos)
---

# Plan de remediación — Marketplace, skills, tools y MCP (2026-09-02)

## Cabecera

| Campo             | Valor                                                                                                          |
| ----------------- | -------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**   | `remediacion-marketplace-mcp-2026-09-02`                                                                       |
| **Prioridad**     | P0 (ola 0) · P1 (olas 1-2) · P2 (ola 3, condicionada a ADR)                                                    |
| **Bloqueado por** | Ninguno. Asume mergeado el PR #182 (plan `remediacion-ciclo-vida-proyecto-2026-09-01`)                         |
| **Rama sugerida** | `plan/remediacion-marketplace-mcp-2026-09`                                                                     |
| **Método**        | TDD estricto: test en rojo reproduciendo el hallazgo, arreglo, verde. Un commit por tarea. UI con vitest + e2e |
| **Origen**        | Auditoría del 2026-09-02 (backend + UI), §Hallazgos                                                            |

## Resumen

El marketplace y la integración MCP están **bien construidos hasta la fila en base de datos** y
se rompen en los tres puntos donde la fila tiene que convertirse en una capacidad que el modelo
usa: no hay forma de instalar desde el panel, las tools de un servidor MCP sólo existen para el
agente si alguien pulsa «Importar tools», y un MCP remoto (Atlassian, GitHub) no pasa el
egress-proxy. A eso se suma que dos tipos de manifiesto se instalan `enabled` sin materializar
nada y que las «anclas» de un proyecto (epic padre en Jira, página raíz en Confluence) no tienen
dónde vivir: las skills `atlassian-*` las mendigan en la descripción de cada plan.

Las olas van por **daño ÷ coste**. La ola 0 abre la cadena de punta a punta (instalar, importar
solo, salir al remoto). La ola 1 hace honesto el contrato (diferidos, avisos que hoy se
descartan, procedencia, un test que llame la tool de verdad). La ola 2 convierte «Jira padre +
Confluence raíz» en un ajuste de proyecto que el run recibe como preámbulo. La ola 3 es lo que
exige diseño: que la plataforma, no el modelo, lea los hijos del epic y cree bajo el padre.

## Hallazgos (resumen de la auditoría del 2026-09-02)

Backend (`apps/api-server/src/api_server`, `apps/workers/src/workers`, runtime):

- **MK-01 · ALTO** · `marketplace/materialize.py:194-204`: `python_function` y `docker_command`
  se instalan `enabled` **sin fila** (ADR 0081 B/C diferido); el runtime sí los ejecutaría
  (`tool_wiring.py:132,179`). Se vende una capacidad que no llega.
- **MK-02 · ALTO** · `agent_tools_enforcement.py:250-290` + `routers/mcp.py:308`: un MCP
  declarado sin «Importar tools» conecta y registra sus tools, pero el modelo no las ve
  (`agent_tool_schemas.py:388-398`). Dead end silencioso.
- **MK-03 · ALTO** · `docker/egress-proxy/filter.txt`: nueve hosts, ninguno Atlassian; un MCP
  remoto (`https://mcp.atlassian.com/v1/mcp`, plantilla `atlassian-remote` de
  `shared_mcp/catalog.py:670`) devuelve `403 Filtered`. `project.allowed_domains` no aplica al
  transporte MCP (`http_endpoint_tool.py:140`).
- **MK-04 · MEDIO** · `marketplace/seed.py:24-36`: el catálogo oficial sólo siembra skills y
  Playwright; el kind `mcp_server` no distribuye nada.
- **MK-05 · MEDIO** · `Project` (`db/domain/projects.py:89-247`) no tiene anclas Jira/Confluence;
  las skills builtin `atlassian-*` (`seeds/builtin_skills.py:563-640`) piden los ids en la
  descripción del plan o en un comentario. Sin ingesta de Confluence a la KB.
- **MK-06 · BAJO** · `routers/agents/common.py:46-91`: fork/adopción clona tools/skills/KB; el
  contexto MCP es del proyecto y un fork que cambia de proyecto lo pierde sin aviso.
- **MK-07 · BAJO** · `docs/03-guides/configurar-mcp-server.md:263` sigue pidiendo asignar la
  tool MCP al agente, cosa que el ADR 0128 fase 3 ya no exige; el paso de egress no está
  documentado.
- **MK-08 · MEDIO** · No hay test end-to-end marketplace → run: `test_marketplace_v2_chain.py`
  para en «el agente tiene la fila».

Lo que el mapeo del 2026-09-03 anadió (y que cambia el orden de la ola 0):

- **MK-09 · BLOQUEANTE** · El [ADR 0052](../05-architecture-decisions/0052-import-mcp-tools-catalogo.md)
  está `accepted` y **rechazó expresamente** el auto-import (su opción P-B, «por meter ruido/superficie
  no querida»). Por la cadena de precedencia de `CLAUDE.md`, `task_mk_01` no puede implementarse sin un
  ADR que enmiende esa alternativa rechazada y deje el 0052 sin mentir.
- **MK-10 · BLOQUEANTE** · `mcp/config.py::_to_runtime_config` (`routers/mcp.py:463`) **no propaga
  `oauth_ref`** y `MCPClient.connect` tampoco lo consume: el OAuth lo resuelve el RUNTIME vía
  `internal_agent`. Un MCP con OAuth —Atlassian, el caso que motiva el plan— **no se puede descubrir
  desde el api-server**.
- **MK-11 · ALTO** · `get_tenant_session` mantiene abierta la transacción del request durante todo el
  handler. Descubrir dentro de `PUT /projects/{id}` mete una llamada de red de hasta 300 s en esa
  transacción: es el antipatrón que cerró prod-13 (`tests/integration/test_assistant_no_tx_during_llm.py`).
- **MK-12 · MEDIO** · `marketplace/materialize.py:229-250` ya crea una fila `Tool` `mcp_tool` con nombre
  **sin namespacear**, invisible al runtime (`agent_tools_enforcement.py:282-284` exige el prefijo). Tras
  el auto-import convivirían duplicados y `dematerialize_installation` sólo borra la suya.
- **MK-13 · ALTO** · `docker/egress-proxy/Dockerfile` hornea `filter.txt` en la imagen y **ningún compose
  lo monta como bind**: cambiar la allowlist exige `build` + `up -d --force-recreate`. Un ajuste que
  valide sin re-renderizar el fichero convierte un fallo honesto de red en un «guardado en verde» que
  miente.
- **MK-14 · ALTO** · El instalador **no puede leer la BD**: `installer_backend` no importa `api_server` y
  en `GENERATE_CONFIG` todavía no hay base de datos. «El instalador vuelca el platform setting al
  filtro», tal como lo escribía `task_mk_02`, no es implementable sin una segunda fuente en
  `InstallerConfig` o un paso de aplicación posterior.
- **MK-15 · MEDIO** · `POST /projects/{id}/mcp/test-connection` (`routers/mcp.py:246`) descubre **desde el
  api-server**, que vive en `agentic-net` y no tiene `HTTP_PROXY`: «Probar conexión» puede salir verde
  mientras el run muere con `403 Filtered`. Sin cerrar esto, el mensaje accionable de egress no se llega
  a disparar nunca.
- **MK-16 · MEDIO** · Instalar **no es** desplegar: `POST /marketplace/installations` sólo crea la fila; el
  servidor llega a `projects.mcp_servers` en el despliegue (`marketplace/deploy.py:397-460`). Y navegar
  siempre a consentimiento es incorrecto en dos de los tres caminos: un listing `verified` nace `ENABLED`
  sin permisos que otorgar (`marketplace/install.py:369-374`) y con `async_gates` la fila nace
  `analyzing` sin nada materializado. `async_gates` además es **opt-in** (`schemas/marketplace.py:143`,
  default `False`) y, sin worker en esa cola, `analyzing` es permanente.

UI (`apps/admin-panel`):

- **UI-01 · ALTO** · `app/admin/marketplace/page.tsx:282-333`: la tarjeta del catálogo no tiene
  botón **Instalar** — ni el panel entero emite un solo `POST /marketplace/installations`. Sólo se
  instala por API. _Corregido el 2026-09-03_: el plan decía que `e2e/marketplace-admin.spec.ts:179`
  fija la ausencia en negativo, y es falso; esa línea es el NOMBRE del test y el único negativo
  (`:196`) es sobre el enlace de configuración de Playwright (ADR 0142), que NO se toca.
- **UI-02 · ALTO** · `components/marketplace/available-capabilities-section.tsx:114-131`: el
  despliegue descarta `warnings` y `oauth_pending`; `created_refs` (`deployment-types.ts:87`) no
  se pinta en ningún sitio.
- **UI-03 · ALTO** · Pestaña MCP (`mcp-server-card.tsx:51-109`, `mcp-server-dialog.tsx:326`):
  «Probar conexión» e «Importar tools» viven dentro del diálogo de edición; tras guardar nada
  dice que falta importar; el error del proxy se pinta crudo
  (`mcp-connection-test-section.tsx:135-141`).
- **UI-04 · MEDIO** · `app/admin/marketplace/review/page.tsx` es huérfana: ni el sidebar
  (`components/layout/admin-shell.tsx:190`) ni ninguna página la enlazan.
- **UI-05 · MEDIO** · `agent-tools-section.tsx` / `agent-skills-section.tsx`: sin procedencia
  (listing, versión); `agent-fork-dialog.tsx` no avisa de que las tools MCP son del proyecto.
- **UI-06 · BAJO** · `agent-skills-section.tsx:218-301` con strings en español hardcodeadas;
  shares e instaladas muestran UUID en vez de nombres (`page.tsx:470,633-698`).
- **UI-07 · BAJO** · Sin e2e de importar tools, de la cola de revisión ni de asignar skills.

## Criterios de cierre del plan

1. Todos los checkboxes marcados `[x]` con su test automático en verde.
2. Suites `unit` + `integration` + runtime + vitest + Playwright (subset mockeado) sin
   regresiones respecto al baseline.
3. Tests humanos `human_mk_01..03` (§Tests humanos) validados por el operador.
4. Entrada en `docs/07-changelog/remediacion-marketplace-mcp-2026-09-02.md`.
5. Los ADR 0081, 0128, 0129 y 0142 actualizados en el mismo commit que los cambios que los
   contradicen; el nuevo ADR de la allowlist de MCP remotos (`task_mk_02`) `accepted` antes de
   implementar esa tarea (regla de precedencia de `CLAUDE.md`).

---

## Ola 0 — La cadena abre de punta a punta (P0 · ~7 d)

> **Reordenada el 2026-09-03** tras el mapeo con anclas verificadas. El orden original
> (`mk_00 → mk_01 → mk_02`) era incorrecto por dos razones medidas: (1) mientras el egress bloquee,
> el auto-import **falla para todo MCP remoto**, así que `mk_01` entregado antes sólo funcionaría con
> transporte `stdio` y sus tests pasarían por mocks; (2) `mk_01` contradice un ADR aceptado (MK-09) y
> `mk_02` toca la postura de seguridad del egress, así que **los dos ADR van primero** — es el criterio
> de cierre nº 5 del plan y la cadena de precedencia de `CLAUDE.md`. `mk_00` puede ir en paralelo, pero
> su e2e no puede afirmar «el agente la usa» hasta que `mk_01` esté.

### `task_mk_0a` — ADR de la allowlist de MCP remotos en el egress (MK-03, MK-13, MK-14, MK-15)

- [ ] **Título**: escribir el ADR 0165 y llevarlo a `accepted` por el operador. Decide QUIÉN manda sobre
      la allowlist (platform setting como fuente de verdad con paso de aplicación, o el `filter.txt` del
      repo con el api-server sólo validando), y cierra explícitamente: escapado ERE + anclado `^…$` y
      validador de FQDN; prohibición de hosts internos, IPs literales y `169.254.169.254`; si la
      allowlist es de host o de `host:puerto` (hoy `ConnectPort 443 8443`); el `Allow` por IP cliente de
      tinyproxy si el api-server pasa a probar por el proxy; quién aprueba la apertura de un host de un
      tenant y dónde queda su auditoría; que esto **no** es control de exfiltración; cómo se evita la
      deriva ajuste↔fichero (MK-13); qué fuente usa el instalador (MK-14); y si «Probar conexión» debe
      salir por el proxy (MK-15).
      **Test**: `tests/docs` y `tests/unit/test_docs_governance.py` en verde con el ADR nuevo.
      **Coste**: 0,5 d.

### `task_mk_0b` — ADR que enmienda el 0052 sobre el auto-import (MK-09, MK-10, MK-11, MK-12)

- [ ] **Título**: escribir el ADR 0166 y llevarlo a `accepted`. Enmienda la alternativa P-B rechazada por
      el [ADR 0052](../05-architecture-decisions/0052-import-mcp-tools-catalogo.md) —que queda actualizado
      en el mismo commit— y decide cómo dejan de existir servidores MCP cuyas tools el modelo nunca ve:
      síncrono al guardar (descartado a priori por MK-11), asíncrono por cola, semiautomático de un clic,
      o descubrimiento desde el worker. Cierra: qué pasa con los MCP OAuth que hoy no se pueden descubrir
      desde el api-server (MK-10); qué sustituye al control de supply chain de P-A; qué se hace con la
      fila `Tool` sin namespacear de `materialize.py` (MK-12); idempotencia y borrado al retirar un
      servidor; fail-open o fail-closed al guardar; y los límites (`Tool.name` es `String(120)` con
      único por tenant).
      **Test**: `tests/docs`, `test_docs_governance.py` y, si usa `rejects:`,
      `tests/docs/test_adr_precedence.py`.
      **Coste**: 0,5 d.

### `task_mk_02` — Un MCP remoto sale por el egress (MK-03, MK-13, MK-14, MK-15, UI-03)

- [ ] **Título**: implementar lo que decida el ADR 0165. En cualquiera de sus variantes: `mcp.atlassian.com`
      (y los hosts que el ADR fije) pasan el proxy; el api-server valida el host al guardar un servidor
      con `url` externa y al probar la conexión, con un error accionable que diga **cómo se aplica** el
      cambio, en vez del `403 Filtered` crudo de `mcp-connection-test-section.tsx:135-141`; y las dos
      copias del filtro (`docker/egress-proxy/filter.txt` y la de `installer_backend.stack_assets`) se
      mueven en el mismo commit (`tests/unit/test_installer_ships_stack_assets.py`).
      `project.allowed_domains` sigue gobernando sólo `http_request`.
      **Test**: unit del validador (escapado, anclado, hosts prohibidos); integración del guardado y del
      test de conexión con host no permitido; unit del render del filtro y de la guarda de deriva entre
      las dos copias; vitest del mapeo del error en el panel.
      **Coste**: 2 d.

### `task_mk_01` — Las tools de un MCP llegan al catálogo sin un paso manual (MK-02, MK-09…MK-12, UI-03)

- [ ] **Título**: implementar lo que decida el ADR 0166, extrayendo primero la lógica de
      `import_mcp_tools` (`routers/mcp.py:308-457`) a una función reutilizable (hoy vive en línea) para no
      duplicarla entre el guardado, el despliegue del marketplace (`deploy.py:418-424`, donde hoy sólo
      avisa «vuelve a desplegar») y el endpoint manual. En la UI, la tarjeta del servidor
      (`mcp-server-card.tsx`) muestra el recuento de tools importadas o «tools sin importar», con probar e
      importar como acciones directas fuera del diálogo de edición, y el estado vacío de
      `mcp-tool-roles-section.tsx:197` enlaza a la causa.
      **Test**: integración (guardar/desplegar → filas `Tool` namespaceadas sin pulsar nada, o el
      contrato que fije el ADR; segundo intento no duplica; discovery caído → sin filas y con aviso; un
      servidor retirado deja de anunciar sus tools); el rojo legítimo hoy es el sembrado manual de
      `tests/integration/test_marketplace_v2_chain.py:241-253`; vitest de la tarjeta con sus tres estados.
      **Coste**: 2 d.

### `task_mk_00` — Instalar desde el catálogo (UI-01, MK-16)

- [ ] **Título**: la tarjeta del catálogo (`app/admin/marketplace/page.tsx:282-333`) gana un botón
      **Instalar** que llama al `POST /marketplace/installations` ya existente
      (`routers/marketplace/installations.py:85`). Tres cosas que el mapeo obliga a decidir y dejar
      escritas en el commit: (1) el botón y su mutación viven en un módulo aparte —`page.tsx` está en 728
      líneas de un techo de 800 que vigila `scripts/check-component-size.test.ts` sobre el árbol real—;
      (2) todo el texto sale del diccionario, porque `check-i18n.test.ts` corre sobre el árbol real y
      `marketplace` no está en ninguna allowlist; (3) a dónde se navega tras instalar, sabiendo que un
      listing `verified` nace `ENABLED` sin permisos que consentir y que `async_gates` es opt-in y sin
      worker deja `analyzing` para siempre (MK-16). Se añaden además los estados `analyzing` y `blocked`
      a `STATUS_BADGE` (`page.tsx:145`), que hoy se pintarían crudos.
      **Test**: vitest de la tarjeta (ofrece instalar y manda el `listing_id`; un 409 de ya-instalado se
      lee con `errorText` y no se pinta el cuerpo crudo; lo ya instalado no se ofrece dos veces; a un
      miembro no admin no se le ofrece); e2e nuevo que captura el `postDataJSON` y comprueba el destino
      de la navegación. El e2e existente (`marketplace-admin.spec.ts:179`) cambia de nombre, y su
      negativo de la línea 196 —el enlace de configuración de Playwright, ADR 0142— **no se toca**.
      **Coste**: 1 d.

## Ola 1 — El contrato es honesto (P1 · ~4,5 d)

### `task_mk_10` — Los tipos diferidos dejan de venderse (MK-01)

- [ ] **Título**: reabrir el ADR 0081 B/C con dos opciones y decidir: (a) materializar
      `python_function` y `docker_command` con el gate de revisión ya existente
      (`workers/marketplace_gates.py`, `routers/marketplace/admin.py`), puesto que el runtime ya
      los ejecuta; (b) rechazar esos manifiestos en la publicación (`private.py:92`) hasta que
      exista (a). Mientras no se decida, aplicar (b) y que la pestaña «Instaladas» no muestre
      `enabled` a un item sin fila (`page.tsx:149`).
      **Test**: integración `test_marketplace_materialization.py` (publicar `python_function`
      → 422 con motivo, o fila si se elige (a)); vitest del badge.
      **Coste**: 1,5 d.

### `task_mk_11` — La puerta de despliegue enseña lo que pasó (UI-02)

- [ ] **Título**: `available-capabilities-section.tsx:114-131` pinta `warnings` y
      `oauth_pending` con el mismo bloque que ya usa `deployments-section.tsx:340-379`; la fila
      del despliegue muestra `created_refs` (qué agentes recibieron qué tool/skill, qué proyecto
      recibió el MCP); el aviso de tipo diferido va traducido y enlaza a `task_mk_10`.
      **Test**: vitest de la sección con respuesta que trae `warnings` y con `created_refs`;
      e2e `marketplace-deploy` comprobando el resumen.
      **Coste**: 1 d.

### `task_mk_12` — Un test que llama la tool de verdad (MK-08, UI-07)

- [ ] **Título**: `tests/integration/test_marketplace_v2_chain.py` gana un tramo que despacha
      la tarea con `ScriptedModelClient` y comprueba que la tool desplegada **se invoca** en el
      run (spec con `tool_specs` + paso `act` con esa tool); Playwright gana `mcp-import-tools`
      y `agent-skills-assign` (subset mockeado).
      **Test**: los propios.
      **Coste**: 1 d.

### `task_mk_13` — Procedencia, cola de revisión y fork (UI-04, UI-05, MK-06)

- [ ] **Título**: el sidebar (`components/layout/admin-shell.tsx:190`, `system_admin`) y la
      cabecera del marketplace enlazan `/admin/marketplace/review`; `agent-tools-section.tsx` y
      `agent-skills-section.tsx` muestran listing y versión cuando la fila viene del
      marketplace (`source_installation_id`); `agent-fork-dialog.tsx` avisa de que las tools
      MCP son del proyecto y no viajan con el agente (y `routers/agents/common.py:46-91` lo
      registra en la respuesta del fork).
      **Test**: vitest de las tres pantallas; e2e de la cola de revisión.
      **Coste**: 1 d.

## Ola 2 — Anclas de integración por proyecto (P1 · ~3,5 d)

Lo que convierte «Jira padre + Confluence raíz» en un ajuste que el run recibe, sin depender de
que alguien lo escriba en cada plan.

### `task_mk_20` — `project.integrations` (MK-05)

- [ ] **Título**: nueva clave JSONB validada `integrations` en `Project`
      (`db/domain/projects.py`, migración reversible, RLS heredado de la tabla) con un esquema
      por proveedor (`schemas/projects.py`): `jira: {project_key, parent_issue_key}`,
      `confluence: {space_key, root_page_id}`, extensible; endpoint `PATCH /projects/{id}`
      existente; formulario «Integraciones» en los ajustes del proyecto del panel con i18n
      ES+EN. Sin secretos aquí: las credenciales siguen en el servidor MCP (Vault/OAuth).
      **Test**: unit del esquema (claves desconocidas → 422; formato de `parent_issue_key`
      `ABC-123`); integración del PATCH; vitest del formulario.
      **Coste**: 1,5 d.

### `task_mk_21` — El run recibe las anclas y las skills las usan (MK-05)

- [ ] **Título**: `dispatch._build_common_request` añade `integrations` al request y el runtime
      lo pliega como bloque de preámbulo junto a la persona del agente
      (`assemble_system_preamble`); las skills builtin `atlassian-*`
      (`seeds/builtin_skills.py:563-640`) se reescriben para leer el epic padre y la página
      raíz del preámbulo y sólo caer a la descripción del plan si faltan; guía
      `docs/03-guides/configurar-mcp-server.md` con el ejemplo Atlassian completo.
      **Test**: unit del dispatch (el request lleva `integrations`); runtime (preámbulo con
      las anclas); docs guard del seed de skills.
      **Coste**: 1 d.

### `task_mk_22` — El catálogo distribuye MCP y la doc dice la verdad (MK-04, MK-07)

- [ ] **Título**: `marketplace/seed.py` siembra listings `mcp_server` para `atlassian-remote` y
      `github-remote` (las plantillas `stdio` quedan fuera por transporte, como en
      `routers/mcp_catalog.py:87-108`); `docs/03-guides/configurar-mcp-server.md:263-270` deja
      de pedir la asignación por agente (ADR 0128 fase 3) y documenta la allowlist de
      `task_mk_02`.
      **Test**: `test_marketplace_seed.py` (dos listings nuevos, idempotentes); docs guard.
      **Coste**: 0,5 d.

### `task_mk_23` — i18n y nombres (UI-06)

- [ ] **Título**: `agent-skills-section.tsx:218-301` pasa por el diccionario (ES+EN); shares e
      «Instaladas» muestran el nombre del listing y del tenant en vez del UUID
      (`page.tsx:470,633-698`), con buscador de tenant en el diálogo de compartir.
      **Test**: vitest i18n del panel (guard existente `i18n.test.tsx`); vitest de la lista.
      **Coste**: 0,5 d.

## Ola 3 — La plataforma trabaja el árbol, no el modelo (P2 · condicionada a ADR · ~2 d)

### `task_mk_30` — Hijos del epic y creación bajo el padre como tools de plataforma

- [ ] **Título**: hoy leer los hijos del epic y crear sub-issues o páginas bajo el padre depende
      al 100 % del LLM llamando tools MCP genéricas. Proponer en un ADR dos tools de plataforma
      (`jira_children_of_parent`, `confluence_page_under_root`) que envuelvan las del MCP con
      las anclas de `project.integrations` ya puestas, más una ingesta opcional del subárbol de
      Confluence a la KB del proyecto. Sólo se implementa si el ADR se acepta.
      **Test**: los del ADR.
      **Coste**: 2 d (si se acepta).

---

## Tests humanos

| ID            | Qué valida                                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------------------------- |
| `human_mk_01` | Desde el panel: instalar una tool del catálogo, consentir, desplegar por rol y ver al agente invocarla en un run    |
| `human_mk_02` | Declarar el MCP remoto de Atlassian, conectar por OAuth, ver las tools importadas sin pulsar nada y un run que crea |
|               | una sub-issue bajo el epic del proyecto                                                                             |
| `human_mk_03` | Un proyecto con anclas Jira/Confluence: el run muestra el preámbulo con ellas y la skill `atlassian-*` las usa      |

## Riesgos del plan

- **`task_mk_02` abre egress**: por eso va por platform setting y ADR, no por proyecto. Un
  host mal escrito en la allowlist deja el egress como estaba (default-deny); uno de más abre
  un destino a todos los sandboxes: el ADR tiene que exigir dominio exacto, no comodín.
- **`task_mk_10` (a)** reabre la superficie de ejecución de código arbitrario del marketplace:
  el gate de revisión existe, pero hay que medir que se aplica antes de materializar.
- **Migración de `integrations`**: JSONB nullable, reversible; ninguna lectura falla si está
  vacío. El preámbulo se omite si no hay anclas.
- **Dependencia de #182**: `spec_approval_category` y el gate fail-closed de tools MCP sin
  categoría ya están en `master`; este plan los asume.
