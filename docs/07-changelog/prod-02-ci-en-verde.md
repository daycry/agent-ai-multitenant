---
plan_id: prod-02-ci-en-verde
title: CI resucitado y en verde — triggers, gates obligatorios y cobertura
status: pending_human_validation
implemented_at: 2026-06-11
docs_language: es
---

# Plan prod-02 — CI resucitado y en verde

> **Estado: `pending_human_validation`.** Las 12 tareas están implementadas y
> verificadas en verde **en local**; faltan los 4 tests humanos del plan y el
> merge del PR (cuyo propio CI es la «primera prueba de fuego»). Este changelog
> documenta lo que ha aterrizado en la rama `plan/prod-02-ci-en-verde`.

## Resumen

El arnés de CI estaba **funcionalmente muerto** (hallazgos `tests-1`/`tests-2`
de la auditoría 2026-06): los tres workflows disparaban sobre `main` mientras
la rama por defecto es `master`, de modo que los PRs reales no ejecutaban
**ningún** job; y cuando CI corría (`plan/**`) llevaba ~19 runs en rojo desde
2026-05-29 y se mergeaba igual, dejando el gate cross-tenant (Principio nº1)
sin ejecutarse 12 días. Este plan lo resucita y lo convierte en gate real.

## Cambios visibles

- **Triggers corregidos** (`tests-1`): los 3 workflows (`ci.yml`,
  `build-runtime-templates.yml`, `eval-on-prompt-change.yml`) disparan sobre
  `master` + `plan/**` + `workflow_dispatch`. Nuevo meta-test estático
  [`tests/docs/test_ci_workflows.py`](../../tests/docs/test_ci_workflows.py)
  que pinea las invariantes del arnés (rama por defecto, sin ramas obsoletas,
  dispatch manual, gate de cobertura, timeouts, perfil AppArmor).
- **Master en verde** (`tests-2`): corregido el `mypy strict` de
  `llm_providers/factory.py` (`_build_ollama` desempaquetaba `**kwargs:
dict[str,str]`) y las 16 violaciones de markdownlint (MD001/MD004/MD036 en
  `06.17`, `06.18`, ADR 0057, changelog 06.17). `pre-commit run --all-files`
  y `markdownlint docs/**` salen a 0.
- **Stack de integración arrancable en runners** (`tests-3`): el job
  `test-integration` carga el perfil `agentic-default` con `apparmor_parser`
  antes de `docker compose up`; overlay de fallback
  [`docker/docker-compose.ci.yml`](../../docker/docker-compose.ci.yml). El gate
  cross-tenant vuelve a ejecutarse (189 tests verdes en local).
- **Gates recableados**: `tests/security` y `tests/docs` cableados al job
  `test-unit` con exit-5-as-failure (`tests-4`); gate de cobertura con umbral
  **ratchet** (floor 19%, `--cov-fail-under`, `[tool.coverage]` en pyproject)
  (`tests-5`/`quality-6`); `agent-runtime:v1` construida en CI para que el e2e
  del pipeline de agentes deje de saltarse, con `skip→fail` en CI para que un
  gate Docker no desaparezca en silencio (`tests-6`); smoke parametrizado de la
  imagen api-server (`tests-7`, coordina con prod-01); **vitest** (82 specs) y
  **Playwright** (subconjunto mockeado, 245 casos) del admin-panel en CI
  (`frontend-7`).
- **Robustez del arnés** (`tests-8`): `timeout-minutes` en los 9 jobs;
  handoff determinista (`threading.Event`) en `test_pool_queue`; prohibición de
  pytest-xdist + dependencia de orden documentadas en el conftest de integración.
- **Gobernanza**: [ADR 0058](../05-architecture-decisions/0058-proteccion-rama-master.md)
  (`proposed`) con las 3 opciones de protección de `master`; medida puente
  «ningún merge a master con CI en rojo» activa en `conventions.md`.

## Hallazgos de hardening cerrados de paso

Cablear `tests/security` (que nunca había corrido en CI) destapó 2 gaps reales:

- `ollama-bootstrap` se había añadido (ADR 0056) **sin** el baseline de
  hardening; ahora hereda `no-new-privileges` + `apparmor=agentic-default`.

## Decisiones diferidas a otros planes

- **Postura AppArmor de cAdvisor privilegiado**: contradicción committeada
  entre `tests/unit` (no apparmor) y `tests/security` (apparmor); las 2
  aserciones quedan en `xfail` con referencia a **prod-08/prod-12** (sandbox-8).
  No se decide aquí.
- **Extracción `localhost:8001`→constante** y los ~11 specs Playwright con
  backend real: reencuadrados en **prod-09** (solo relevantes para specs no
  mockeados).
- **Optimización del job Playwright** (`next build`/`next start` en vez de
  `next dev`, ~44min→~15min): follow-up documentado.
- **Protección server-side de `master`**: espera la decisión humana del ADR 0058.

## Verificación

Cada tarea se validó en local con su test (`.venv` del repo + stack dev):
meta-tests del workflow (6/6), `pre-commit --all-files` + markdownlint 0,
cross-tenant 189, unit 1173 + cobertura 19.4%≥19, tests/security+docs 215+2xfail,
vitest 82, Playwright mockeado 245, e2e_smoke 1 (con contenedor real),
rate-limit+pool_queue 8. **Pendiente**: los 4 tests humanos del plan y el CI
del propio PR (primera prueba de fuego).
