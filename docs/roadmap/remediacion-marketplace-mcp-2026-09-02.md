---
plan_id: remediacion-marketplace-mcp-2026-09-02
title: Remediación del marketplace y de los MCP por proyecto — de la instalación al run, con anclas Jira/Confluence
status: pending_approval
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 3 semanas
estimated_effort_person_days: 14
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

UI (`apps/admin-panel`):

- **UI-01 · ALTO** · `app/admin/marketplace/page.tsx:282-333`: la tarjeta del catálogo no tiene
  botón **Instalar**; `e2e/marketplace-admin.spec.ts:179` lo fija en negativo. Sólo se instala
  por API.
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

## Ola 0 — La cadena abre de punta a punta (P0 · ~4,5 d)

Tres huecos que hoy hacen que «instalar un MCP desde el panel y que el agente lo use» sea
imposible sin tocar la API a mano y el filtro del proxy a mano.

### `task_mk_00` — Instalar desde el catálogo (UI-01)

- [ ] **Título**: la tarjeta del catálogo (`app/admin/marketplace/page.tsx:282-333`) gana un
      botón **Instalar** que llama al POST de instalación ya existente
      (`routers/marketplace/installations.py:212`), muestra el `202 + Location` del análisis
      asíncrono (`installations.py:177-202`) y salta a la pantalla de consentimiento
      (`installations/[id]/permissions/page.tsx`). El e2e que fija la ausencia
      (`e2e/marketplace-admin.spec.ts:179`) se invierte.
      **Test**: vitest de la tarjeta (botón, estado «analizando», error 4xx visible); e2e
      `marketplace-admin` con el flujo instalar → consentir → aparece en «Instaladas».
      **Coste**: 1 d.

### `task_mk_01` — Las tools de un MCP se importan solas (MK-02, UI-03)

- [ ] **Título**: al guardar un servidor MCP (`routers/mcp.py` POST/PUT) y al desplegarlo desde
      el marketplace (`marketplace/deploy.py:430-440`, donde hoy sólo avisa «vuelve a
      desplegar») se encadena `discover_tools` + upsert idempotente de las filas `Tool`
      `mcp_tool` (la misma lógica que `routers/mcp.py:308`, fail-closed y con el fallo en el
      preámbulo como ya hace `test_mcp_failure_in_preamble.py`). En la UI, la tarjeta del
      servidor (`mcp-server-card.tsx`) muestra el recuento de tools importadas o el badge
      «tools sin importar», con «Probar conexión» e «Importar» como acciones directas fuera
      del diálogo de edición; el estado vacío de `mcp-tool-roles-section.tsx:197` enlaza a la
      causa.
      **Test**: integración `test_mcp_tool_import_and_threading.py` extendida (guardar → filas
      sin pulsar nada; segundo guardar no duplica; discovery caído → sin filas y aviso);
      vitest de la tarjeta con los tres estados.
      **Coste**: 1,5 d.

### `task_mk_02` — Un MCP remoto sale por el egress (MK-03, UI-03)

- [ ] **Título**: ADR corto primero (toca la postura de seguridad de los ADR 0060 y 0129):
      allowlist de hosts MCP remotos como **platform setting** gestionado por System Admin
      (`platform_settings`, UI en el área de sistema), que el instalador vuelca al filtro del
      egress-proxy (`apps/installer/.../stack_assets/egress-proxy/filter.txt` +
      `docker/egress-proxy/filter.txt`) y que el api-server comprueba al guardar y al probar
      la conexión de un servidor con `url` externa: si el host no está, error accionable
      («añade `mcp.atlassian.com` a la allowlist de MCP remotos») en vez del `403 Filtered`
      crudo de `mcp-connection-test-section.tsx:135-141`. `project.allowed_domains` sigue
      gobernando sólo `http_request`. Opción descartada en el ADR: derivar el filtro de
      `projects.mcp_servers` en caliente (el proxy es estático y cualquier proyecto podría
      abrir egress).
      **Test**: unit del validador de host contra la allowlist; integración del POST con host
      no permitido (422 con el mensaje); unit del generador del instalador (filter.txt contiene
      los hosts del setting); vitest del mapeo del error.
      **Coste**: 2 d (0,5 ADR + 1,5 implementación).

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
