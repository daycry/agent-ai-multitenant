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

## F3 — `/tmp` del runtime (tmpfs 64m) puede quedarse corto — ✅ CERRADA 2026-07-27

**Qué:** `_build_test_kwargs` montaba `/tmp` como tmpfs de **64m** escrito a mano. `composer
install`/`npm ci` descargan+extraen en `/tmp`; para árboles de deps grandes 64m se queda corto
(composer ya avisa _"less than 100MiB of free space"_). guzzle (+8 deps) cupo, pero un stack pesado
podría fallar.

**Lo que lo delataba:** la entrega de HOME (`task_wf_20`, C-01) añadió `test_runtime_home_size`
—configurable, 512m— **en la línea de al lado** y dejó `/tmp` en el literal. Dos montajes hermanos,
uno tunable y el otro no.

**Resolución — opción (c), tamaño configurable:** `WORKERS_TEST_RUNTIME_TMP_SIZE`
(`test_runtime_tmp_size`, default **256m**). No la (a) subir el literal, porque el tamaño correcto
depende del stack; y no la (b) apuntar el tmp al worktree, porque eso **reintroduce C-01**: el
`git add -A` de `commit_task` acabaría comiteando los temporales de la toolchain.

**El invariante que se añadió con ello** (y que era el riesgo real de subirlo): las páginas de un
tmpfs cuentan contra el **cgroup de memoria** del contenedor, así que un `/tmp` cerca del `mem_limit`
cambia un ENOSPC legible por un **OOM-kill mudo** (exit 137 sin mensaje). El test cruza el tamaño
con el `mem_limit` de **todas** las plantillas del catálogo: nunca más de la mitad.

**Tests:** `tests/unit/test_test_runtime_tmp_size.py` (18, parametrizado sobre el catálogo entero).

**Fuera de alcance, anotado aquí para que no se pierda:** hay otros dos `/tmp` con su propio tamaño
—`isolation.py` (agent-runtime y review/preview, ya configurable vía `container_tmp_size`, default
64m, sin override en ningún compose) y `SandboxSpec.tmpfs_size` del marketplace, que `install.py`
nunca fija—. No se tocan aquí: son envelopes distintos con perfiles de riesgo distintos, y unificarlos
merece su propia decisión, no un arrastre.

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

### Análisis verificado 2026-07-27 — sigue abierta al 100%, y NO es un borrado trivial

Las cuatro tools son `run_pytest`, `run_lint`, `run_typecheck`, `run_build` (slugs kebab). Viven en:
`seeds/builtin_tools.py:179-247` (4 de las 17 filas del catálogo, `category='runtime'`,
`implementation_type='docker_command'`), `tool_names.py:138-142` (`RUNTIME_WIRED_TOOL_NAMES`) y
`:43-46` (`_CATALOG_TOOL_NAMES`), los defaults de 6 roles en `builtin_role_capabilities.py:70-88`
(~14 filas `agent_tools` vivas sobre los 11 agentes built-in) y `_RUN_TOOL_COMMANDS` en
`agent_tools_enforcement.py:68-73`.

**El riesgo que convierte esto de «M» a «M-alta si se hace a medias»** — y que es la razón de
anotarlo antes de tocarlo: si se quitan los nombres de `_CATALOG_TOOL_NAMES`,
`tool_is_runtime_wired` (`schemas/catalog.py:64-68`) deja de reconocerlos como builtins de
plataforma y **cae al atajo por `implementation_type`**, que devuelve `True` para `docker_command`.
Una fila `run_pytest` superviviente en una BD sin migrar vuelve a ser **asignable y anunciable** al
modelo: es la puerta trasera de B-04 reabierta, exactamente el fallo que
`is_unwired_platform_builtin` existe para tapar.

**Orden correcto, por tanto:** (1) sacarlos de `RUNTIME_WIRED_TOOL_NAMES` **manteniéndolos** en
`_CATALOG_TOOL_NAMES` (así siguen siendo nombres canónicos y la guarda del PUT los rechaza);
(2) limpiar los defaults de rol sustituyéndolos por `stack-exec`, o los agentes se quedan sin
toolchain; (3) migración reversible que haga soft-delete de las filas y borre sus grants; (4) sólo
entonces retirar del seed.

**Tests que hay que tocar** (≈12 ficheros): invertir el contrato de
`test_tool_catalog_contract.py:136-140` (hoy **exige** que el catálogo siembre `run_*`), añadirlos a
`_NOT_WIRED_TOOLS` en `test_runtime_wired_contract.py`, ampliar el conjunto conocido de
`test_catalog_executor_parity.py`, ajustar la cardinalidad de `test_seed_tools.py:53-59`, y añadir
uno nuevo de la migración (up/down) y otro de la puerta trasera de arriba.

## F6 — Causa raíz: creación de proyecto sin `slug` — ✅ CERRADA 2026-07-26

**Qué:** el proyecto demo "Api CI" se creó con `project.slug` VACÍO (anomalía); se arregló con un
fix de datos (`slug='api-ci'`). Falta encontrar y cerrar la vía de creación de proyecto que permite
slug vacío (validación en el endpoint / generación de slug).
**Por qué importa:** sin slug, `execution.py` no provisiona worktree (cae a tmpfs efímero) y
`stack_exec` no resuelve.
**Resolución (verificada 2026-07-26):** la vía está cerrada por tres sitios a la vez.
`create_project` llama SIEMPRE a `slugify(payload.name)` (`projects.py:349`), y `slugify`
**nunca devuelve vacío** — cae a `untitled` por contrato explícito (`slug.py:42`). El índice
único parcial de la migración **0114** (P1-02) es el backstop contra carreras, y en colisión
se sufija con `-{id8}`. No queda ninguna ruta que persista un slug vacío.
**Dónde:** producto (creación de proyecto). **Esfuerzo:** S. **Prioridad:** media.
