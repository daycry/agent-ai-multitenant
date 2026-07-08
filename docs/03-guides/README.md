# Guides

Cómo trabajar con el sistema. Una página por flujo concreto.

El **modelo mental** de capacitación que estas guías presuponen (SABER/RECORDAR/SER/HACER
y la tabla de NIVELES) vive en
[`../04-reference/training-model.md`](../04-reference/training-model.md).

## Capacitar agentes, equipos y proyectos

| Guía                                                                       | Para qué                                                                      |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [como-capacitar-agentes.md](./como-capacitar-agentes.md)                   | Guía paraguas: las 4 vías (SABER/RECORDAR/SER/HACER), el Hub, niveles         |
| [01-create-first-project.md](./01-create-first-project.md)                 | Crear el primer proyecto desde la UI                                          |
| [kb-ingestion.md](./kb-ingestion.md)                                       | Subir documentos a una Knowledge Base (SABER)                                 |
| [knowledge-bases-rol-vs-stack.md](./knowledge-bases-rol-vs-stack.md)       | Cuándo asignar una KB al rol del agente vs al stack del proyecto              |
| [memoria-de-agentes.md](./memoria-de-agentes.md)                           | RECORDAR: scopes, escalera de lectura, back-fill, por qué private no memoriza |
| [persona-y-system-prompt.md](./persona-y-system-prompt.md)                 | SER: modelo del catálogo cerrado, prompt efectivo, edición es/en              |
| [skills-de-agentes.md](./skills-de-agentes.md)                             | SER: skills como fragmentos de persona asignables (prompt_fragment)           |
| [asignar-tools-a-agentes.md](./asignar-tools-a-agentes.md)                 | Asignar tools (y skills) a un agente y leer el set efectivo (HACER)           |
| [comandos-y-runtime-por-proyecto.md](./comandos-y-runtime-por-proyecto.md) | Autorizar comandos del stack (PHP/Node/.NET) + runtime por proyecto           |
| [configurar-mcp-server.md](./configurar-mcp-server.md)                     | Añadir un servidor MCP a un proyecto (catálogo o custom)                      |
| [human-agents.md](./human-agents.md)                                       | Crear, configurar, asignar y operar tareas de Human Agents                    |

## Operar la plataforma

| Guía                                                               | Para qué                                                                       |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| [roles-y-permisos.md](./roles-y-permisos.md)                       | Qué puede hacer cada rol del tenant (RBAC) y cómo se diferencian               |
| [configurar-proveedores-llm.md](./configurar-proveedores-llm.md)   | Configurar los 4 proveedores LLM del catálogo cerrado (System Admin)           |
| [publicar-en-marketplace.md](./publicar-en-marketplace.md)         | Publicar/despublicar skills, tools y MCP servers en el marketplace             |
| [plan-to-kanban-sync.md](./plan-to-kanban-sync.md)                 | Cómo se sincroniza la vista Plan con el Kanban                                 |
| [validacion-humana-de-planes.md](./validacion-humana-de-planes.md) | Probar la app del plan, aprobar/rechazar y el ciclo de correcciones (ADR 0107) |
| [app-review-images.md](./app-review-images.md)                     | Construir la imagen de app-preview, con ejemplos PHP/Node/Python/Go            |
| [api-publica-y-webhooks.md](./api-publica-y-webhooks.md)           | Usar la API v1 (token + SDKs + curl) y registrar webhooks entrantes            |

## Desarrollo, demos y diseño

| Guía                                                 | Para qué                                                                |
| ---------------------------------------------------- | ----------------------------------------------------------------------- |
| [watching-e2e-tests.md](./watching-e2e-tests.md)     | Ver los specs Playwright en directo (headed, slow-mo, UI inspector)     |
| [run-demo-human-tests.md](./run-demo-human-tests.md) | Ejecutar los scripts demo de tests humanos (Plan 02 + Plan 04.5)        |
| [design-tokens.md](./design-tokens.md)               | Tokens de diseño del frontend                                           |
| [ui-conventions.md](./ui-conventions.md)             | Design-system del admin-panel: primitivas, componentes, estados, a11y   |
| [gotchas/](./gotchas/)                               | Trampas conocidas del toolchain (Docker, asyncpg, mypy, OTEL, Windows…) |
