---
adr_id: "0167"
title: "Hijos del epic y creación bajo el padre como tools de plataforma (propuesta)"
status: proposed
date: 2026-09-09
authors: [claude-fable-5-1, operador]
plan_referenced: remediacion-marketplace-mcp-2026-09-02
related: ["0052", "0127", "0128", "0142", "0165", "0166"]
docs_language: es
---

# ADR 0167 — Hijos del epic y creación bajo el padre como tools de plataforma

> **Estado: `proposed`.** Lo escribe `task_mk_30` del plan
> `remediacion-marketplace-mcp-2026-09-02` (ola 3) para que el operador decida.
> **No se implementa nada hasta que pase a `accepted`**; si se rechaza, la ola 3
> queda cerrada en negativo y las anclas de `project.integrations` siguen
> haciendo su trabajo por la vía del preámbulo (`task_mk_21`).

## Contexto

Con la ola 2 del plan, un proyecto tiene sus **anclas de integración**
(`project.integrations`: proyecto y epic padre de Jira, espacio y página raíz
de Confluence) y el run las recibe como bloque del preámbulo. Las skills
`atlassian-*` las leen antes que la descripción del plan. Eso resuelve el
«dónde»: el agente sabe bajo qué epic y bajo qué página trabajar.

Lo que **no** resuelve es el «cómo». Hoy, leer los hijos del epic, crear una
sub-issue bajo él o una página hija bajo la raíz depende al 100 % de que el LLM
encadene bien las tools MCP **genéricas** de Atlassian (`jira_search` con un
JQL correcto, `jira_create_issue` con el campo `parent` bien puesto,
`confluence_create_page` con `parent_id`). En la prueba e2e de Atlassian del
2026-07-18 (guía `configurar-mcp-server.md`, §Trampas) y en los runs del plan
`prod-16` se vieron los tres modos de fallo típicos:

1. **JQL inventado**: el modelo compone `parent = PLAT-120` cuando el sitio del
   cliente usa `"Epic Link"` (o al revés), y la búsqueda devuelve vacío; el agente
   concluye «no hay hijos» y duplica trabajo.
2. **Padre omitido**: crea la issue en el proyecto pero sin `parent`, o la página
   en el espacio pero en la raíz; el árbol se ensucia y hay que recolocar a mano.
3. **Iteraciones quemadas**: cada intento consume una llamada de modelo y un
   round-trip MCP; con presupuestos por run ajustados, el agente se queda sin
   iteraciones antes de llegar al trabajo real.

Los tres son **determinables**: dadas las anclas, hay una forma correcta de
preguntar y de crear, y no depende de la creatividad del modelo.

## Decisión propuesta

Añadir **dos tools de plataforma** (`builtin`, cableadas en el runtime como las
demás familias, ADR 0049) que envuelvan a las del MCP de Atlassian del proyecto
**con las anclas ya puestas**, más una ingesta opcional:

| Tool                          | Qué hace                                                                                                                                                                                                                                           | Con qué anclas                       |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `jira_children_of_parent`     | Lista las issues hijas del epic/issue padre del proyecto (clave, tipo, estado, título, asignado). Prueba en orden las dos formas de parentesco que Jira Cloud expone (`parent = KEY` y `"Epic Link" = KEY`) y devuelve la que responde.             | `jira.project_key`, `parent_issue_key` |
| `confluence_page_under_root`  | Crea o actualiza (por título) una página **hija** de la raíz del proyecto; si la raíz no existe o no es visible, falla con un error tipado en vez de crearla suelta.                                                                                | `confluence.space_key`, `root_page_id` |
| *(opcional)* ingesta del subárbol | Job que trae el subárbol de Confluence bajo la raíz a la KB del proyecto (Docling), para que `rag_search` responda con la documentación viva del proyecto sin que el agente pasee por Confluence en cada run.                                     | `confluence.space_key`, `root_page_id` |

Reglas de la propuesta:

- **Envuelven, no sustituyen.** Por dentro llaman a las tools `<server>.<tool>`
  del MCP de Atlassian que el proyecto ya declara (ADR 0127/0128): mismas
  credenciales (OAuth del proyecto), mismo egress (ADR 0165), mismo gate de
  aprobación (`spec_approval_category` de la tool MCP subyacente). No hay una
  segunda vía a Atlassian.
- **Sin anclas, no existen.** Si `project.integrations` no trae el ancla que
  necesitan, la tool no se registra en el run (como una familia sin config), y
  el preámbulo no la anuncia. Un proyecto sin Jira no ve `jira_children_of_parent`.
- **Sin MCP, tampoco.** Si el servidor de Atlassian del proyecto no está
  disponible en el run (`mcp_failures`), la tool devuelve el mismo error tipado
  que la MCP subyacente: no se inventa nada.
- **El nombre del servidor no se cablea.** Se resuelve por `auth_kind="oauth"` +
  URL de la plantilla `atlassian-remote` (o por el bloque `mcp_server` del
  listing del marketplace), igual que hoy hace `_uses_oauth`.
- **Categoría de aprobación**: la de la tool MCP que envuelven (`external_http_post`
  para crear; lectura para listar).

## Alternativas consideradas

1. **Dejarlo en el prompt** (estado actual tras `task_mk_21`): las anclas van en
   el preámbulo y las skills explican el procedimiento. Barato y sin superficie
   nueva; pero el «cómo» sigue dependiendo del modelo, y los tres modos de
   fallo de arriba no desaparecen — se mitigan.
2. **Tools de plataforma** (esta propuesta): determinismo donde es determinable;
   coste ~2 d; añade dos builtins que hay que mantener cuando Atlassian cambie su
   API (hoy el MCP oficial ya la abstrae, así que el riesgo es moderado).
3. **Skills «recetadas» con ejemplos literales de JQL/payload**: mejora el
   prompt sin código nuevo, pero un ejemplo literal envejece con cada sitio
   (campos personalizados) y vuelve a ser el modelo quien lo adapta.
4. **Un MCP propio de la plataforma delante del de Atlassian**: máximo control,
   pero es infra nueva (proceso, egress, auth) para dos operaciones.

## Consecuencias

**Si se acepta (a):**

- `docker/agent-runtimes/agent-runtime/agent_runtime/atlassian_tools.py` con las
  dos tools; `tool_wiring.py` las registra sólo si `spec["integrations"]` trae
  las anclas y el MCP de Atlassian está cableado.
- Fila `Tool` builtin para cada una (seed `builtin_tools`), `security_level`
  `sandboxed`, categoría `integrations`; aparecen en la pestaña Tools del agente
  como cualquier builtin (la política de roles de la MCP subyacente sigue
  mandando sobre lo que pueden hacer por dentro).
- Las skills `atlassian-*` ganan una frase: «si tienes `jira_children_of_parent`,
  úsala antes que componer JQL a mano».
- Ingesta del subárbol: tarea Celery en la lane `default`, disparada desde la
  ficha del proyecto (botón «Traer documentación de Confluence a la KB»), con
  la misma deduplicación por `source_uri` que la ingesta de Docling.
- Tests: unit de las dos tools con un MCP falso (parentesco por `parent` y por
  `Epic Link`; padre no visible → error tipado; sin anclas → no registrada);
  runtime boot (`tool_specs` + anclas → cableada); e2e humano `human_mk_02`
  ampliado con «la sub-issue aparece bajo el epic».

**Si se rechaza (b):**

- Se cierra `task_mk_30` en negativo con este ADR como referencia; el plan
  queda completo con la ola 2 y las anclas en el preámbulo.
- Se anota en la guía `configurar-mcp-server.md` §Trampas que el parentesco
  Jira depende del modelo y cómo revisarlo.

## Decisión del operador

_Pendiente._ Marcar aquí **(a) aceptar** o **(b) rechazar**, con fecha, y cambiar
`status` a `accepted` o `rejected`. Si (a), la implementación abre sus casillas
bajo `task_mk_30` en el plan (coste estimado 2 d) o en un plan siguiente.
