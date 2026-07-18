---
title: "Recetas: MCP servers, custom tools y skills — ejemplos completos"
audience: tenant admin, project owner, operator
phase: 06.18-tools-por-agente
updated: 2026-07-18
docs_language: es
---

# Recetas: MCP servers, custom tools y skills

Esta guía es el **recetario** de las tres vías para ampliar lo que un agente
puede HACER. Cada receta es un ejemplo completo — configuración, asignación,
qué escribir en qué prompt y cómo verificar que funciona — pensado para que
cualquier persona pueda añadir cualquiera de las tres cosas sin conocer el
código.

> Conceptos: la guía paraguas es
> [como-capacitar-agentes](./como-capacitar-agentes.md) (vía **HACER**). El
> paso a paso de la UI de MCP está en
> [configurar-mcp-server](./configurar-mcp-server.md) y el de tools en
> [asignar-tools-a-agentes](./asignar-tools-a-agentes.md).

## ¿Cuál de las tres necesito?

| Quiero…                                                           | Vía             | Ejemplo       |
| ----------------------------------------------------------------- | --------------- | ------------- |
| Conectar un servicio externo que YA habla MCP (Jira, Context7…)   | **MCP server**  | Recetas A1–A4 |
| Una operación propia con lógica mía (código o API interna)        | **Custom tool** | Recetas B1–B2 |
| Cambiar el COMPORTAMIENTO del agente (hábitos, estilo, políticas) | **Skill**       | Receta C1     |

Regla mnemotécnica: **MCP conecta, la tool ejecuta, la skill instruye.**
Las dos primeras añaden _capacidades invocables_ (el modelo las ve como
funciones con schema); la tercera añade _texto de sistema_ que condiciona
cómo y cuándo usarlas.

## El principio común: nadie "inyecta" tools en el prompt a mano

En las tres vías, **la plataforma construye el prompt por ti**:

- Cada tool asignada (builtin, MCP o custom) se presenta al modelo
  automáticamente con su `description` y su `input_schema`. **No** copies
  schemas ni listas de tools en ningún prompt.
- El `prompt_fragment` de cada skill asignada viaja como bloque del system
  prompt de todos los runs del agente (ADR 0050).
- Lo único que escribes tú en lenguaje natural es **la instrucción de uso**:
  o en la **tarea** (acción puntual: "publica X con la tool Y") o en la
  **persona/skill** del agente (hábito: "siempre que cierres tarea, haz Y").

| Capa                                     | Dónde se edita                            | Cuándo usarla                                           |
| ---------------------------------------- | ----------------------------------------- | ------------------------------------------------------- |
| Descripción + criterios de la **tarea**  | El plan (Kanban / specification)          | Acciones puntuales de ESA tarea                         |
| **Persona** del agente (`system_prompt`) | Ficha del agente → Persona                | Identidad y políticas permanentes de ese agente         |
| **Skill** (`prompt_fragment`)            | Catálogo de skills + asignación al agente | Hábito reutilizable que quieres compartir entre agentes |

---

# Parte A — MCP servers

## El flujo universal (idéntico para cualquier MCP)

1. **Declarar el server en el PROYECTO** —
   `/admin/projects/{id}/mcp-servers` → "Añadir MCP server" (o
   `PUT /api/projects/{id}` con `mcp_servers`). La conexión es por-proyecto.
2. **Probar** — botón "Probar" (endpoint
   `POST /api/projects/{id}/mcp/test-connection`): hace el handshake MCP real
   y lista las tools que el server expone.
3. **Importar tools** — botón "Importar tools"
   (`POST /api/projects/{id}/mcp/servers/{name}/import-tools`): materializa
   cada tool en el catálogo del tenant como `<server>.<tool>`.
4. **Asignar a agentes** — ficha del agente → Tools
   (`PUT /api/agents/{id}/tools`). Solo los agentes con la tool asignada la
   ven en sus runs (allowlist agente ∩ modo).
5. **Prompt** — nombra la tool EXACTA (`<server>.<tool>`) en la tarea, o la
   política de uso en una skill/persona.

Verificación siempre igual: en el visor del run, el step **`mcp_wire`** dice
si el server conectó y qué tools registró; los steps `tool_call` muestran
cada invocación con sus args.

## Receta A1 — Atlassian (Confluence + Jira)

Objetivo: los agentes publican documentación en Confluence y sincronizan
issues de Jira.

**Opción recomendada para agentes: server self-hosted con API token.**
El MCP remoto oficial de Atlassian usa OAuth interactivo (un humano en un
navegador), que no encaja con runs autónomos. El server comunitario
`mcp-atlassian` (imagen Docker) habla con la API de Atlassian Cloud usando
API token y expone las tools por `streamable_http`.

1. Despliega el server como servicio adjunto al stack, conectado a la red
   de agentes (`agentic-agents`):

   ```yaml
   # docker-compose.override.yml (junto al compose del stack)
   services:
     mcp-atlassian:
       image: ghcr.io/sooperset/mcp-atlassian:latest
       environment:
         JIRA_URL: https://tuempresa.atlassian.net
         CONFLUENCE_URL: https://tuempresa.atlassian.net/wiki
         JIRA_USERNAME: bot@tuempresa.com
         JIRA_API_TOKEN: ${ATLASSIAN_API_TOKEN}
         CONFLUENCE_USERNAME: bot@tuempresa.com
         CONFLUENCE_API_TOKEN: ${ATLASSIAN_API_TOKEN}
       command: ["--transport", "streamable-http", "--port", "9000"]
       networks: [agentic-agents]
   ```

2. Declara el server en el proyecto:

   | Campo      | Valor                           |
   | ---------- | ------------------------------- |
   | Nombre     | `atlassian`                     |
   | Transporte | `streamable_http`               |
   | URL        | `http://mcp-atlassian:9000/mcp` |

3. "Probar" → debe listar tools (`confluence_create_page`,
   `jira_transition_issue`, `jira_search`, …). "Importar tools" → aparecen en
   el catálogo como `atlassian.confluence_create_page`, etc.
4. Asigna al agente las que quieras permitir (no todas: menor superficie,
   mejor foco del modelo).
5. Prompt — en la **tarea**:

   > _"Publica el resumen de cierre como página de Confluence usando la tool
   > `atlassian.confluence_create_page` con `space_key` DOCS y título
   > 'Cierre <plan>'. Después transiciona la issue PROJ-42 a Done con
   > `atlassian.jira_transition_issue`, comentando la URL de la página."_

   Y en los criterios de aceptación: _"Se invocó
   `atlassian.confluence_create_page` con space_key DOCS"_ — el reviewer
   valida contra eso.

   Si quieres que TODOS los cierres de tarea sincronicen Jira sin pedirlo
   cada vez, crea una **skill** (ver Parte C) con ese hábito y asígnala al
   equipo.

> Verificado e2e en esta plataforma (2026-07-18) con un server MCP de prueba
> equivalente: handshake + import + asignación + run real invocando
> `atlassian.confluence_create_page` y `atlassian.jira_transition_issue`.

## Receta A2 — Context7 (documentación de librerías al día)

Objetivo: los agentes consultan documentación actualizada de frameworks
(evita APIs alucinadas de versiones viejas). Context7 es un MCP remoto
público de Upstash.

1. Declara el server en el proyecto:

   | Campo      | Valor                          |
   | ---------- | ------------------------------ |
   | Nombre     | `context7`                     |
   | Transporte | `streamable_http`              |
   | URL        | `https://mcp.context7.com/mcp` |

   Funciona sin credencial (con rate limits). Con cuenta, guarda la API key
   en Vault y referénciala como credencial del servidor
   (`vault:secret/data/mcp/context7/{project_id}`, clave
   `CONTEXT7_API_KEY`) — viaja como cabecera.

   > **Egress**: los runs de agentes salen a internet por el proxy de egress
   > allowlisted (ADR 0019/0094). Añade `mcp.context7.com` a la allowlist de
   > dominios del proyecto o el transporte no podrá salir.

2. Probar → expone `resolve-library-id` y `get-library-docs`. Importar →
   `context7.resolve-library-id`, `context7.get-library-docs`. Asignar a los
   agentes dev.
3. Prompt — aquí lo natural es un **hábito** (skill o persona), no la tarea:

   > _"Antes de usar una API de un framework del que no estés seguro,
   > resuelve la librería con `context7.resolve-library-id` y consulta
   > `context7.get-library-docs` con el topic concreto. Prefiere la firma
   > devuelta por la documentación a tu memoria."_

## Receta A3 — GitHub (plantilla del catálogo)

GitHub está entre las **plantillas verificadas** del picker: "Añadir MCP
server" → plantilla **GitHub 🔒** → autorrellena transporte/comando y la ruta
de Vault esperada (`GITHUB_TOKEN`). Sigue el flujo universal desde "Probar".
El paso a paso con capturas está en
[configurar-mcp-server](./configurar-mcp-server.md) (el ejemplo 1 usa Jira,
idéntico patrón).

## Receta A4 — Un MCP propio de tu empresa

Si tienes un servicio interno (ERP, CRM, data warehouse), exponerlo como MCP
son ~30 líneas con el SDK oficial (`FastMCP`):

```python
# server.py — expón tus operaciones como tools MCP
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("erp", host="0.0.0.0", port=9000)

@mcp.tool()
def consultar_stock(sku: str) -> dict:
    """Devuelve el stock disponible de un SKU en almacén."""
    ...  # tu lógica contra el ERP

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

Despliégalo en la red `agentic-agents` (como en A1) y sigue el flujo
universal con URL `http://erp-mcp:9000/mcp`. La descripción del docstring de
cada tool es EXACTAMENTE lo que el modelo lee: escríbela pensando en él
(qué hace, cuándo usarla, qué devuelve).

---

# Parte B — Custom tools

Una custom tool es una fila del catálogo (`/admin/tools` → "Crear tool", rol
`tenant_admin`) con un `implementation_type` que decide QUIÉN la ejecuta.

## Receta B1 — `python_function` (lógica propia en el sandbox)

Objetivo: una operación determinista propia — el ejemplo: normalizar
entradas de changelog.

1. Crea la tool (`POST /api/tools` o UI):

   ```json
   {
     "name": "changelog_stamp",
     "description": "Genera una entrada de changelog normalizada a partir de version y summary. Devuelve {entry, length}.",
     "category": "custom",
     "input_schema": {
       "type": "object",
       "properties": {
         "version": { "type": "string", "description": "Version semver, p.ej. 1.2.3" },
         "summary": { "type": "string", "description": "Resumen breve del cambio" }
       },
       "required": ["version", "summary"]
     },
     "implementation_type": "python_function",
     "implementation_ref": "def run(args: dict):\n    entry = f\"## v{args['version']} - {args['summary']}\"\n    return {\"entry\": entry, \"length\": len(entry)}\n",
     "security_level": "safe",
     "timeout_seconds": 10
   }
   ```

   El contrato del código: definir un `def run(args: dict) -> Any` a nivel
   de módulo. Corre en un **subprocess aislado** dentro del sandbox del run
   (intérprete fresco, env vacío, timeout duro) — nunca `eval` en el proceso
   del agente.

2. Asígnala al agente (ficha → Tools). Nada más: el modelo la verá como
   función `changelog_stamp` con su schema, automáticamente.
3. Prompt — en la tarea pides el RESULTADO (no el mecanismo), o conviertes
   el mecanismo en hábito con una skill (Receta C1 usa exactamente esta
   tool).
4. Verificación: en el visor del run, el step `tool_call` de
   `changelog_stamp` con `args` y `result.ok=true`.

- **`description` es prompt.** Es lo único que el modelo lee para decidir
  usarla: di qué hace, cuándo usarla y qué devuelve.
- **`input_schema` es contrato.** El runtime valida los args ANTES de
  ejecutar (los inválidos ni llegan al código) — describe cada propiedad.

## Receta B2 — `http_endpoint` (API interna como tool)

Para exponer un endpoint HTTP existente sin escribir código Python: crea la
tool con `implementation_type: http_endpoint` y en `implementation_ref` la
plantilla de URL con placeholders del `input_schema`:

```
https://api.interna.empresa.com/stock/{sku}
```

Cada placeholder `{...}` se sustituye por el arg homónimo validado. La
respuesta del endpoint es el output de la tool. Recuerda: el dominio debe
estar en la **allowlist de dominios del proyecto** (el guard SSRF revalida
cada resolución) y la credencial, si la hay, en Vault — nunca en la URL.

> Existe un tercer tipo, `docker_command` (la familia `run_pytest`,
> `run_build`…): ejecuta un comando en el runtime-template del proyecto.
> Se cubre en [comandos-y-runtime-por-proyecto](./comandos-y-runtime-por-proyecto.md).

---

# Parte C — Skills

Una skill NO añade capacidades invocables: añade **instrucción de sistema**
reutilizable (`prompt_fragment`) que viaja en todos los runs del agente que
la tenga asignada (ADR 0050). Es la pieza para convertir "cómo queremos que
trabaje" en configuración versionable, en lugar de repetirlo en cada tarea.

## Receta C1 — Skill de hábito que activa una custom tool

Objetivo: que el agente use SIEMPRE `changelog_stamp` (Receta B1) al
escribir changelogs, sin que cada tarea lo pida.

1. Crea la skill (`/admin/skills` → "Crear skill" o `POST /api/skills`):

   ```json
   {
     "name": "Estilo de changelog corporativo",
     "category": "docs",
     "description": "Formato corporativo de entradas de changelog y sello de cierre.",
     "prompt_fragment": "Cuando generes o escribas entradas de changelog: usa SIEMPRE la tool changelog_stamp para producir la entrada normalizada (no la formatees a mano) y termina el resumen final de tu trabajo con la palabra exacta CHANGELOG-OK."
   }
   ```

2. Asígnala al agente (ficha → Skills, `PUT /api/agents/{id}/skills`).
   Asegúrate de que el agente también tiene la tool `changelog_stamp`
   asignada (una skill no otorga tools; `required_tools` documenta la
   dependencia y la UI la señala).
3. La tarea del plan ya NO menciona la tool:

   > _"Genera la entrada de changelog de la versión 1.2.3 con el resumen
   > 'Integración MCP validada' y guárdala en docs/CHANGELOG-PRUEBA.md."_

   El fragment hace el resto: el modelo llama `changelog_stamp` y sella su
   resumen con `CHANGELOG-OK`.

4. Verificación: el step `tool_call` de `changelog_stamp` en un run cuya
   tarea no la nombraba = el fragment llegó y actuó.

**Cuándo skill vs persona vs tarea**: la persona es la identidad de UN
agente; la skill es un hábito **compartible entre agentes** (asignas la
misma a todo el equipo); la tarea es lo puntual de un trabajo concreto. Si
te descubres copiando la misma frase en varias tareas → skill. Si es
identidad de un solo agente → persona.

---

# Matriz final: qué tocar, dónde, para cada cosa

| Paso                | MCP server                               | Custom tool                       | Skill                                 |
| ------------------- | ---------------------------------------- | --------------------------------- | ------------------------------------- |
| Crear               | Proyecto → MCP servers                   | Catálogo → Tools (`tenant_admin`) | Catálogo → Skills (`tenant_admin`)    |
| Credenciales        | Vault (`auth_ref`)                       | Vault si `http_endpoint` con auth | n/a                                   |
| Materializar        | "Importar tools" → `<server>.<tool>`     | n/a (ya es una fila del catálogo) | n/a                                   |
| Activar en agente   | Ficha agente → Tools                     | Ficha agente → Tools              | Ficha agente → Skills                 |
| Prompt              | Tarea (puntual) o skill/persona (hábito) | Tarea pide el resultado           | El fragment ES el prompt              |
| Verificar en el run | Step `mcp_wire` + steps `tool_call`      | Step `tool_call`                  | Tool usada sin que la tarea la nombre |

# Troubleshooting

| Síntoma                                       | Causa probable                                                    | Arreglo                                                                    |
| --------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------- |
| No hay step `mcp_wire` en el run              | El spec no llevó `mcp_servers` (server no declarado al despachar) | Declara el server ANTES de lanzar el plan                                  |
| `mcp_wire` ok pero el modelo no ve las tools  | Tools no asignadas al agente (allowlist las filtra)               | Ficha del agente → Tools → asignar las `<server>.<tool>`                   |
| `mcp_wire` error `TRANSPORT_ERROR` / timeout  | URL/red: el server no es alcanzable desde la red de agentes       | Servicio en `agentic-agents` (interno) o dominio en la allowlist (externo) |
| `AUTH_ERROR`                                  | Secreto ausente/incorrecto en Vault                               | Revisa la ruta exacta del `auth_ref` y sus claves                          |
| El run agota iteraciones sin llamar a la tool | Tarea no autocontenida (pide un insumo que no existe)             | Garantiza el insumo o pide crearlo primero (`write_file`)                  |
| La skill "no hace nada"                       | Skill sin asignar, o pide una tool que el agente no tiene         | Asigna skill Y tool; revisa `required_tools`                               |
