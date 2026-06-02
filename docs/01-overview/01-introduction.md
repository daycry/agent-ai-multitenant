---
title: Introducción
docs_language: es
audience: todos
updated: 2026-06-02
---

# Introducción

**agentic-platform** es una plataforma multi-tenant que permite
construir, configurar y orquestar equipos de **agentes
autónomos** —de IA **y humanos**— especializados (Project Manager,
Arquitecto, Backend Dev, Frontend Dev, QA, DevOps, Reviewer, Technical
Writer, Security Engineer, Personal Assistant...) que trabajan de forma
cooperativa sobre proyectos de software. La unidad operativa es el
**Plan**: un DAG de tareas con dependencias que los agentes ejecutan en
paralelo, materializado como una rama git `plan/{id}-{slug}` y cerrado
con un PR automático.

Para la visión técnica end-to-end (topología de contenedores, flujo de
un plan, multi-tenancy) ve a
[`docs/context/architecture-overview.md`](../context/architecture-overview.md).

## ¿Para quién es?

Empresas y equipos internos —no SaaS comercial masivo— que quieren
desplegar IA agéntica en **una sola máquina** (Docker Compose, no
Kubernetes) bajo su propio control:

- Departamento de desarrollo que quiere acelerar la implementación
  con agentes que asumen tareas de un Plan dirigido por humanos.
- Operador de IT que necesita una plataforma de IA con multi-tenancy
  estricto, auditoría completa y validación humana configurable por
  proyecto.

## Qué hace (capacidades del sistema final)

El sistema cubre el ciclo completo, desde definir un equipo hasta
entregar un PR, con observabilidad, control de coste y seguridad
empresarial. Las capacidades, por bloques:

### Agentes, equipos y proyectos

- **Agentes de IA y agentes humanos.** Un agente lleva `agent_type` =
  `ai` o `human`. Los **Human Agents** representan a una persona real:
  reciben tareas en su bandeja, las aceptan/rechazan, registran sesiones
  de trabajo (`HumanWorkSession`) e imputan **coste humano** (`rate ×
horas`). El modo de revisión del entregable es por proyecto
  (`auto_approve` o `peer_human_reviewer`). Ver
  [guía de Human Agents](../03-guides/human-agents.md) y
  [ADR 0046](../05-architecture-decisions/0046-human-agents-agent-type-y-workflows-mixtos.md).
- **Catálogo linked vs forked.** Agentes, skills y tools built-in
  visibles cross-tenant; un tenant los referencia (linked) o los forkea
  para editarlos (forked) — [ADR 0006](../05-architecture-decisions/0006-linked-vs-forked-agents.md).
- **Equipos** con roles, líder y prioridad de asignación; **proyectos**
  con team principal, MCP servers, KBs RAG, política de aprobación,
  presupuesto y —nuevo— **`allowed_commands`** (allowlist deny-by-default
  de binarios para `shell_exec`) y **`default_runtime_template`** (el
  stack de tests por defecto).

### Orquestación y ejecución aislada

- **Plan = DAG.** El orquestador asigna tareas listas (sin dependencias
  pendientes) a workers Celery, que **nunca** ejecutan código de usuario:
  lanzan contenedores efímeros **runtime templates** (agent-runtime,
  test-runtime, review-runtime) con red restringida, sin socket Docker,
  capacidades capadas y perfiles seccomp/AppArmor (confiables vs no
  confiables — [ADR 0040](../05-architecture-decisions/0040-seccomp-apparmor-default-deny-por-contenedor.md)).
- **Runtime templates políglotas:** `python-pytest`, `node-jest`,
  `node-vitest`, `php-phpunit`, `dotnet-test`, etc. Los workers solo
  orquestan; los runtimes ejecutan.
- **Doble Kanban:** vista de Planes (gerencial) + vista de Tareas por
  Plan (operativa).

### Tools, conocimiento y memoria

- **Tools** built-in + custom + **MCP** + `shell_exec` / `run_command`
  con allowlist por proyecto y **asignación por agente**
  (`/agents/{id}/tools`,
  [ADR 0044](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md));
  los comandos shell por proyecto + runtime por stack en
  [ADR 0045](../05-architecture-decisions/0045-comandos-shell-por-proyecto-y-runtime-por-stack.md).
- **Knowledge Bases (RAG)** con pgvector + ingestión **Docling**;
  categorías de KB; citaciones.
- **Memoria** en cuatro scopes (privada / team_shared / project_shared /
  global) indexada por el **memorizer**.
- **Chat / planning sub-graph:** una conversación con la IA produce un
  Plan en borrador que el humano aprueba (firma simple o doble según
  umbral de coste).

### Proveedores LLM, coste y guardrails

- **LLM agnóstico, catálogo cerrado** de cuatro proveedores (ADR 0021):
  Claude Agent SDK (Pro/Max), GitHub Copilot (OAuth Device Flow + JWT
  minted), Azure AI Foundry vía APIM y Ollama (local + cloud). Son
  **platform-global**, gestionados por el System Admin desde
  `/admin/llm-providers`, con credenciales **solo en Vault** (ADR 0028).
- **Catálogo de precios** alimentado por LiteLLM (limitado a las familias
  de proveedores activos), **presupuestos** por tenant/proyecto con
  conversión **FX**, alertas de umbral, auto-pausa y **snapshot de coste
  por llamada**.
- **Guardrails declarativos por capas** (plataforma → tenant → proyecto)
  en cuatro puntos del ciclo: pre/post_llm y pre/post_tool, con eventos y
  reglas de alerta.

### Plataforma empresarial

- **Marketplace** de listings (skill / tool / mcp_server) con niveles de
  confianza (verified / community / experimental), catálogo oficial y
  publicación privada por tenant.
- **SSO empresarial** (OIDC / SAML) + **MFA** (TOTP + WebAuthn) junto al
  login con contraseña; **SCIM** para aprovisionamiento.
- **API pública v1** con tokens por scope + **webhooks** entrantes y
  salientes.
- **Evals de calidad** + **dashboard de estadísticas** por tenant
  (consumo, runs, export) y cross-tenant para el System Admin.
- **Notificaciones multicanal** + **asistente personal**.
- **Visor de documentación** integrado (`/admin/docs` y
  `/projects/{id}/docs`).
- **Backup / restore** y, en Fase 15, el **instalador** (wizard).
- **Validación humana** configurable por proyecto con 13 categorías de
  acciones sensibles y 4 plantillas (Sandbox, Desarrollo, Producción,
  Cliente Externo).

## Principios rectores

Los detalles viven en [CLAUDE.md](../../CLAUDE.md). Los esenciales:

1. **Multi-tenancy desde el día uno.** Cada tabla con `tenant_id`,
   PostgreSQL RLS activado; `app_user` (runtime) es NOBYPASSRLS,
   migraciones/admin usan BYPASSRLS.
2. **Aislamiento por contenedor.** Los workers nunca ejecutan código
   de usuario directamente.
3. **Plan = unidad de cambio.** Rama git por plan, commits con
   trailers `Plan-Id` / `Task-Id` / `Execution-Id`, PR automático al
   cerrar.
4. **LLM desacoplado, catálogo cerrado** (ADR 0021): un quinto proveedor
   exige un ADR. LiteLLM ya no se usa como capa de inferencia.
5. **Idiomas:** español + inglés.
6. **Stack:** Python 3.12 con FastAPI, PostgreSQL 16+pgvector,
   Redis 7, LangGraph y Celery; frontend Next.js 14.

## ¿Qué no es?

- No es SaaS multi-cliente masivo: el alcance es Docker Compose en
  una máquina, multi-tenant a nivel **equipos / departamentos**.
- No es Kubernetes (vendrá si llega la demanda; lo cierra Fase 15).
- No es un editor de prompts: el sistema **ejecuta** workflows
  agénticos, no los diseña a mano cada vez.

## Próximos pasos

- [Arquitectura (resumen)](./02-architecture.md) — visión técnica.
- [Arquitectura end-to-end](../context/architecture-overview.md) — el
  sistema final completo con diagramas Mermaid.
- [Instalación](../02-getting-started/01-installation.md) —
  cómo levantarlo en local.
- [Primer arranque](../02-getting-started/03-first-run.md) —
  cómo registrarte y crear tu primer tenant.
