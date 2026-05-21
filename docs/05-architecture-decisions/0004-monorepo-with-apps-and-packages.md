---
adr: "0004"
title: Monorepo con `apps/` y `packages/`
status: accepted
date: 2026-05-20
deciders: System Architect
phase: 00-fundaciones
---

# ADR 0004 — Monorepo con `apps/` y `packages/`

## Contexto

El sistema se compone de **muchos servicios** que comparten:

- Modelos de dominio (Organization, User, Project, Plan, Task...).
- Cliente MCP genérico.
- Wrapper sobre LiteLLM + Claude SDK + Copilot OAuth.
- Motor de guardrails.
- Auth / JWT / RBAC / Casbin.

Y **artefactos independientes** que evolucionan a velocidades
distintas:

- 10 aplicaciones (`api-server`, `orchestrator`, `workers`,
  `memorizer`, `notification-dispatcher`, `webhook-dispatcher`,
  `personal-assistant`, `installer`, `admin-panel`, `web-app`).
- N **runtime templates** (python-pytest, node-jest, php-phpunit...)
  como imágenes Docker mantenidas.

Las opciones de organización:

- **Un repo por servicio + libs en paquetes versionados** —
  flexible pero caro: PR coordinados entre repos, releases
  separadas, CI duplicado.
- **Monorepo "plano"** sin separación clara — fácil al inicio, se
  vuelve un caos rápidamente.
- **Monorepo estructurado** con áreas explícitas para "lo que se
  ejecuta" y "lo que se reutiliza".

## Decisión

Monorepo estructurado en dos niveles:

- **`apps/<nombre>/`** — un servicio ejecutable (FastAPI app,
  worker Celery, frontend Next.js, watchdog). Cada uno con su
  `pyproject.toml` / `package.json` y su Dockerfile (futuro).
- **`packages/<nombre>/`** — librerías reutilizables (`shared-domain`,
  `shared-db`, `shared-auth`, `shared-llm`, `shared-mcp`,
  `shared-guardrails`, `shared-test-runtimes`).
- Tests centralizados en `tests/` para que un solo `pytest` los
  ejecute todos.
- `docs/` con la estructura canónica de 7 carpetas
  (`01-overview` ... `07-changelog`).
- `docker/` con `docker-compose.yml`, `docker-compose.dev.yml`,
  Dockerfiles de los runtime templates.
- `scripts/dev/` para el bootstrap del entorno de desarrollo;
  `scripts/` para los scripts de operador (`install.sh`,
  `uninstall.sh`, `backup.sh`, `init-vault.sh`).

## Alternativas descartadas

1. **Multi-repo.** Rechazado por el coste de coordinación durante
   las primeras fases (mucho refactor cross-cutting).
2. **Monorepo con Nx / Turborepo.** Rechazado para Fase 0: añade
   una herramienta de gestión adicional sin beneficio claro
   todavía. Revisable en Fase 5+ si los tiempos de CI lo justifican.

## Consecuencias

Positivas:

- Un único PR puede tocar el modelo, el endpoint y el frontend que
  lo consume.
- Refactors cross-cutting son triviales (un `grep` global).
- Tests de integración son fáciles: el suite ve el monorepo entero.
- `scripts/dev/bootstrap.{ps1,sh}` instala todos los `apps/` en
  modo editable de un golpe.

Negativas / cuidados:

- Hay que ser disciplinado para no acoplar `apps/<x>` con
  `apps/<y>` directamente: deben hablar vía `packages/` o vía la
  API.
- El hook mypy del pre-commit no ve los paquetes locales del
  monorepo
  ([gotcha](../03-guides/gotchas/mypy-local-package-imports.md)).
- Releases independientes requieren tagging cuidadoso (Fase 15+).

## Referencias

- `CLAUDE.md` — sección "Estructura del Repositorio".
- Documento maestro, sección 8.
