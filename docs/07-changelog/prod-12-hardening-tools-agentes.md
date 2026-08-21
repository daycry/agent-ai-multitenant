---
plan_id: prod-12-hardening-tools-agentes
title: Hardening de tools de agentes — SSRF, egress, reaper y marketplace
completed_at: null
status: pending_human_validation
docs_language: es
---

# Plan prod-12 — Hardening de tools de agentes: SSRF, egress, reaper y marketplace

## Resumen

Diez hallazgos de la auditoría de producción de 2026-06 sobre la superficie que
los agentes tocan de verdad: las tools HTTP del sandbox, la red de los runtimes,
los contenedores que quedan colgando, la instalación del marketplace y el
antivirus de la ingesta. Las 10 tareas están `[x]`, repartidas en seis fases con
un orden que **no era decorativo**: primero la defensa, después el cableado.

Ese orden es el aprendizaje central del plan y está anotado como riesgo 1: si el
cableado de la allowlist (Fase B) hubiera llegado a `master` antes de la defensa
SSRF (Fase A), la protección accidental que existía —un deny-all involuntario—
se habría convertido en un SSRF explotable. La dependencia se declaró dura y se
respetó.

## Cambios

### Fase A — defensa SSRF en las tools HTTP del agent-runtime

- `agent_runtime/ssrf_guard.py`: resolución del destino y validación de la IP
  (rechaza loopback, link-local, metadata y rangos privados).
- **Cierre de la ventana TOCTOU**: se conecta a la IP ya validada mediante un
  transporte httpx propio, preservando `Host` y SNI para no romper TLS, y
  `follow_redirects=False` explícito — un 302 de un dominio permitido hacia un
  host interno no se sigue.
- Validación de las entradas de `projects.allowed_domains` en el api-server
  (migración `20260708_0105_project_allowed_domains.py`, que **crea** la
  columna: no existía).
- El opt-in de sandbox para rangos privados on-prem se dejó **deliberadamente
  sin implementar** y documentado en `docs/04-reference/tools.md`.

### Fase B — cableado de la allowlist

`allowed_domains` viaja en `ExecutionRequest` desde el proyecto hasta el spec del
agente, con **test-centinela** que impide emitir `allowed_domains` sin que el
guard de IP esté aplicado (el riesgo 1, convertido en gate de CI).

### Fase C — red del sandbox y runtimes no-root

- `network_policy='open'` deja de significar internet crudo: sale por el
  egress-proxy con allowlist de registries
  ([ADR 0094](../05-architecture-decisions/0094-egress-runtime-templates-registries-via-proxy-allowlist.md)),
  con auditoría del uso y texto de consentimiento.
- Los 14 templates de test-runtime hornean `/home/agent` (chown 1000) + `ENV
HOME` + `USER 1000:1000` **numérico** (uniforme debian/alpine), y el catálogo
  repunta todos los dep-caches y `cache_env` de `/root/…` a `/home/agent/…`.
  Imágenes PHP reconstruidas y verificadas (uid 1000, HOME escribible).

### Fase D — reaper y `docker_command`

- **`workers.reap_orphans`** (`maintenance/orphan_reaper.py`, cada 10 min):
  contenedores con `com.agentic-platform.managed=true` sin asociación viva +
  redes bridge de test-runtime vacías, con gracia anti-carrera de 10 min. El
  criterio de "vida" se **comparte** con el sweeper de zombis de prod-06 para
  que un solo punto decida qué es un huérfano: nunca doble kill, nunca criterios
  divergentes. `idle_sweep_pools` se conservó porque es otra cosa (el heartbeat
  de pools in-process).
- `docker_command`: se eligió la opción (b) del propio item — falla rápido con
  un error accionable ("no soportado dentro del sandbox del agente… usa
  `stack_exec`, ADR 0093") sin tocar `docker.from_env()`. El boot-test se
  actualizó: antes **solo pasaba inyectando un módulo `docker` falso**, que era
  exactamente el camino muerto del hallazgo.

### Fase E — marketplace y antivirus

- `InstallOrchestrator.analyze_for_install` cablea el **mismo** pipeline de
  análisis estático que ya usaba el update en `install_listing`: bloqueo → 422 +
  audit de aborto; artefacto ausente → skip honesto `no_artifact` (no cierra en
  falso el catálogo pre-registry, ADR 0081); informe en
  `detail.gates.static_analysis`. El TODO desapareció. El test incluye el pin
  "mismo pipeline": el MISMO analizador inyectado ve install y update.
- Antivirus **fail-closed por defecto**
  ([ADR 0105](../05-architecture-decisions/0105-antivirus-fail-closed-ingesta.md)):
  ante `AntivirusVerdict.ERROR` el documento queda en `pending_scan` (CHECK
  ampliada, migración `20260708_0106_documents_pending_scan.py`, reversible) y el
  sweep de pendientes lo re-encola solo; `fail_open` solo en dev/sandbox.
  Notificación `antivirus_unreachable` (in_app + telegram, umbral 15 min,
  re-aviso 6 h) con plantillas es/en.

### Fase F — cAdvisor y cierre documental

- La opción sin privilegios **se validó empíricamente** (una sonda
  no-privileged con cap-drop ALL sirve container_cpu/memory/network/fs) antes de
  decidir: cAdvisor queda sin `privileged` ni `/dev/kmsg` en el generador y en el
  overlay de dev, con AppArmor pineado. Los **dos xfail en cuarentena** de
  sandbox-8 se retiraron y los sets del pentest se endurecieron (0 servicios
  privilegiados). El trade-off (eventos de OOM-kill) y el override opt-in están en
  `docs/06-runbooks/monitoring-cadvisor.md`.
- `docs/04-reference/` actualizado (tools, marketplace, domain-model) + guías
  nuevas `03-guides/app-review-images.md` y
  `03-guides/validacion-humana-de-planes.md`.

## Divergencias respecto al plan

- **Ubicación de los tests del runtime**: viven en
  `docker/agent-runtimes/agent-runtime/tests/` (convención del árbol), no bajo
  `tests/`. Ahí están `test_ssrf_guard.py` (20),
  `test_http_tools_destination_validation.py` (7) y
  `test_docker_command_tool_retired.py` — este último es el que el plan citaba
  como `tests/unit/…`, y buscarlo donde el plan decía da un falso negativo.
- **`docker_command` no se retiró del catálogo** (opción (a), la recomendada):
  se degradó con error explícito. Extirpar la familia `run_*` de `builtin_tools`
  y de los equipos es cirugía de producto con estado en BD, y quedó anotada como
  follow-up.

## Lo que quedó fuera, con nombre

Anotado en `registry-egress-followups.md`: la **UI del panel para editar
`allowed_domains`** (hoy solo por API) y la **retirada total de `run_*` de los
seeds**.

## Tests

`docker/agent-runtimes/agent-runtime/tests/test_ssrf_guard.py`,
`…/test_http_tools_destination_validation.py`,
`…/test_docker_command_tool_retired.py`,
`tests/integration/test_allowed_domains_validation.py` (12),
`tests/unit/test_execution_request_allowed_domains.py` (6, con el centinela),
`tests/e2e/test_agent_http_allowlist_chain.py` (5, sin Docker: seams de
resolver/transporte sobre el código de producción),
`tests/integration/test_orphan_container_reaper.py`,
`tests/integration/test_marketplace_install_static_analysis.py` (4),
`tests/integration/test_ingestion_av_fail_closed.py`,
`tests/unit/test_compose_generator.py -k cadvisor`,
`tests/unit/test_runtime_catalog_dep_cache_paths.py`,
`tests/integration/test_test_runtime_nonroot_cache.py` (el plan lo citaba sin
carpeta; vive en integración).

## Estado de cierre

Los 4 tests humanos (`human_prod12_01..04`) son irremplazables desde aquí: piden
un DNS que resuelva a 127.0.0.1 para probar rebinding, un `kill -9` del worker a
mitad de run para ver actuar al reaper, parar el contenedor de ClamAV y esperar
la notificación, e instalar un listing community de verdad. Nada de eso se puede
acreditar con un test automático; es exactamente el tipo de prueba para la que el
gate humano existe.

## PR

- _pendiente_
