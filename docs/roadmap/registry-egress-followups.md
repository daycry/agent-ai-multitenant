---
title: Follow-ups — egress de runtime-templates / stack execution
status: open
created: 2026-06-30
owner: pendiente
related_adrs: ["0093", "0094", "0067"]
related_plans: ["prod-12-hardening-tools-agentes"]
docs_language: es
---

# Follow-ups — egress de runtime-templates y ejecución de stacks

Pendientes anotados al cerrar la entrega de **ADR 0094** (egress de runtime-templates a
registries vía `registry-proxy`) y **ADR 0093** (`stack_exec`). Ninguno bloquea lo ya entregado
(implementado + desplegado + verificado e2e en `plan/runs-visor-trabajo`); son trabajo posterior.
Cuando se aborden, mover el detalle al plan/fase que corresponda y cerrar la entrada aquí.

## F1 — Registries / git PRIVADOS con credenciales (ADR 0094 D4)

**Qué:** hoy la allowlist del `registry-proxy` cubre solo registries y git hosts **públicos** (sin
auth). Falta soportar registries/git privados (Packagist privado, GitLab self-host, Nexus/
Artifactory, PyPI interno, GitHub/GitLab con token). Implica tres cosas: allowlist **por proyecto**
(no global); inyección de credenciales desde **Vault** en el runtime (env/netrc/`auth.json` de
composer, etc.); y anti-SSRF de las entradas por-proyecto.
**Por qué importa:** la mayoría de empresas tiran de un registry/git privado; sin esto, un proyecto
con deps privadas no instala.
**Dónde:** solapa con la Ola **B0.2** de ADR 0067 (per-project `web_egress_allowlist`) y con el
gating de credenciales de Vault. Decisión del operador (2026-06-30) lo dejó fuera de la 1ª entrega.
**Esfuerzo:** M–L. **Prioridad:** media (alta si un tenant real necesita registry privado).

## F2 — `marketplace/sandbox.py`: misma semántica de egress proxificado — ✅ CERRADA 2026-07-08

**Qué:** `apps/api-server/src/api_server/marketplace/sandbox.py` (`build_sandbox_run_kwargs`,
`is_egress_allowed`) cargaba la MISMA semántica `network_policy='open'` = bridge no-interno con NAT
crudo que se eliminó en `test_runtime.py` (ADR 0094 D1). Era la **otra mitad** de
`task_prod12_net_01`.
**Resolución:** bridge SIEMPRE `internal=True`; 'open' = attach del `registry-proxy` +
`HTTP(S)_PROXY` (offline si el proxy no está, nunca NAT crudo); uso de 'open' registrado
(log + `SandboxResult`); copy de consentimiento actualizado. Ver el estado de
`task_prod12_net_01` en `prod-12-hardening-tools-agentes.md`.

## F3 — `/tmp` del runtime (tmpfs 64m) puede quedarse corto

**Qué:** `_build_test_kwargs` monta `/tmp` como tmpfs de **64m** (endurecimiento). `composer
install`/`npm ci` descargan+extraen en `/tmp`; para árboles de deps grandes 64m se queda corto
(composer ya avisa _"less than 100MiB of free space"_). guzzle (+8 deps) cupo, pero un stack pesado
podría fallar.
**Opciones:** (a) subir el tmpfs (p.ej. 256–512m) para los runtimes que instalan deps; (b) apuntar
el tmp de la tool a un dir bajo el worktree/dep-cache montado; (c) tamaño configurable por plantilla.
**Dónde:** endurecimiento del test-runtime (relacionado con prod-12). **Esfuerzo:** S.
**Prioridad:** media-baja (solo afecta a stacks con muchas deps en frío).

## F4 — Caché escribible para pip/gem/nuget-global (coordinación con task_prod12_img_01)

**Qué:** la alineación `cache_env` de ADR 0094 hace que composer(`vendor/`)/npm(`node_modules/`)/
go(cache) usen el bind-mount uid-1000. Pero pip/gem/nuget-global instalan en rutas **de root**
(`/root/.cache/pip`, `/usr/local/bundle`, `/root/.nuget/packages`) que el uid 1000 no puede escribir
salvo que el bind-mount las cubra. La solución completa (imágenes `USER 1000` + home escribible bajo
`/home/agent`) es **`task_prod12_img_01`**.
**Por qué importa:** sin ello, el caché de esos ecosistemas falla en silencio y se reinstala cada run.
**Dónde:** `task_prod12_img_01` (prod-12, Fase C). **Esfuerzo:** M (ya planificado). **Prioridad:**
media.

## F5 — Retirada total de `run_*` del catálogo de plataforma (ADR 0093 D3)

**Qué:** ADR 0093 retiró los `run_*` (docker_command rotos in-sandbox) del grant del equipo CI4,
pero las filas siguen en el catálogo de plataforma (`builtin_tools.py`) + `RUNTIME_WIRED_TOOL_NAMES`
por compatibilidad con proyectos no-CI4 que las tuvieran. Falta su retirada total (+ contract tests).
**Por qué importa:** son código muerto en la superficie de tools (deuda + confusión); el toolchain
va por `stack_exec`.
**Dónde:** cambio propio (afecta a no-CI4, no hacer big-bang en ADR 0093). **Esfuerzo:** M.
**Prioridad:** baja.

## F6 — Causa raíz: creación de proyecto sin `slug` (ADR 0093, hallazgo de despliegue)

**Qué:** el proyecto demo "Api CI" se creó con `project.slug` VACÍO (anomalía); se arregló con un
fix de datos (`slug='api-ci'`). Falta encontrar y cerrar la vía de creación de proyecto que permite
slug vacío (validación en el endpoint / generación de slug).
**Por qué importa:** sin slug, `execution.py` no provisiona worktree (cae a tmpfs efímero) y
`stack_exec` no resuelve.
**Dónde:** producto (creación de proyecto). **Esfuerzo:** S. **Prioridad:** media.
