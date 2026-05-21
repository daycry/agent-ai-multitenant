# Introducción

**agentic-platform** es una plataforma multi-tenant que permite
construir, configurar y orquestar equipos de **agentes de IA
autónomos** especializados —Project Manager, Arquitecto, Backend
Dev, Frontend Dev, QA, DevOps, Reviewer, Technical Writer...— que
trabajan de forma cooperativa sobre proyectos de software.

## ¿Para quién es?

Empresas y equipos internos —no SaaS comercial masivo— que quieren
desplegar IA agéntica en **una sola máquina** (Docker Compose, no
Kubernetes) bajo su propio control:

- Departamento de desarrollo que quiere acelerar la implementación
  con agentes que asumen tareas de un Plan dirigido por humanos.
- Operador de IT que necesita una plataforma de IA con multi-tenancy
  estricto, auditoría completa y validación humana configurable por
  proyecto.

## Qué hace

- Define equipos (Project Manager + 2 Backend + 1 QA, por ejemplo) y
  les asigna proyectos.
- Cada proyecto contiene un **Plan** con tareas en un DAG; los
  agentes ejecutan tareas en paralelo respetando dependencias.
- Las tareas que tocan código corren en contenedores
  **runtime templates** aislados (red restringida, sin Docker
  socket, capacidades capadas). Stack del usuario (python-pytest,
  node-jest, php-phpunit...) intercambiable.
- Doble Kanban: vista de Planes (gerencial) + vista de Tareas por
  Plan (operativa).
- LLM agnóstico: **LiteLLM** como gateway, soporte adicional para
  Claude Agent SDK y GitHub Copilot OAuth Device Flow.
- Guardrails declarativos por capas (plataforma → tenant → proyecto)
  en cuatro puntos del ciclo: pre/post_llm y pre/post_tool.
- Validación humana configurable por proyecto con 13 categorías de
  acciones sensibles y 4 plantillas (Sandbox, Desarrollo, Producción,
  Cliente Externo).

## Principios rectores

Los detalles viven en [CLAUDE.md](../../CLAUDE.md). Los esenciales:

1. **Multi-tenancy desde el día uno.** Cada tabla con `tenant_id`,
   PostgreSQL RLS activado.
2. **Aislamiento por contenedor.** Los workers nunca ejecutan código
   de usuario directamente.
3. **Plan = unidad de cambio.** Rama git por plan, commits con
   trailers `Plan-Id` / `Task-Id`, PR automático al cerrar.
4. **Idiomas:** español + inglés.
5. **Stack:** Python 3.12 con FastAPI, PostgreSQL 16+pgvector,
   Redis 7, LangGraph y Celery; frontend Next.js 14.

## ¿Qué no es?

- No es SaaS multi-cliente masivo: el alcance es Docker Compose en
  una máquina, multi-tenant a nivel **equipos / departamentos**.
- No es Kubernetes (vendrá si llega la demanda; lo cierra Fase 15).
- No es un editor de prompts: el sistema **ejecuta** workflows
  agénticos, no los diseña a mano cada vez.

## Próximos pasos

- [Arquitectura](./02-architecture.md) — visión técnica.
- [Instalación](../02-getting-started/01-installation.md) —
  cómo levantarlo en local.
- [Primer arranque](../02-getting-started/03-first-run.md) —
  cómo registrarte y crear tu primer tenant.
