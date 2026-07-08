---
plan_id: prod-12-hardening-tools-agentes
title: Hardening de tools de agentes — SSRF, egress, reaper y marketplace
status: in_progress
blocking_plan: null
started_at: 2026-06-30
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 17
estimated_cost_human_eur: 7.700 € – 10.200 €
estimated_cost_ai_eur: 50 € – 100 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-12 — Hardening de tools de agentes: SSRF, egress, reaper y marketplace

## Cabecera

| Campo                              | Valor                                  |
| ---------------------------------- | -------------------------------------- |
| **ID del Plan**                    | `prod-12-hardening-tools-agentes`      |
| **Estado**                         | `in_progress`                          |
| **Prioridad**                      | P1                                     |
| **Bloqueado por**                  | — (coordinar con prod-01 y prod-06)    |
| **Tiempo estimado (calendario)**   | 3-4 semanas                            |
| **Tiempo estimado (persona-días)** | 17 (suma de tareas: 17,0)              |
| **Rama git sugerida**              | `plan/prod-12-hardening-tools-agentes` |

---

> **Estado (2026-07-06, auditoría de roadmap)**: frontmatter corregido de `pending_approval`/
> `started_at: null` a `in_progress`/`started_at: 2026-06-30` para que coincida con la nota que el
> propio cuerpo del documento ya llevaba fechada en `task_prod12_net_01` (ADR 0094 aprobado, mitad
> de la tarea implementada y desplegada — ver esa sección para el detalle). El resto del plan
> (marketplace, reaper, resto de SSRF/egress) sigue sin empezar.
>
> **Estado (2026-07-08, tanda autónoma)**: **Fases A y B COMPLETAS** (`9cd2eb5` — ssrf*01/02/03 +
> allow_01/02, ver nota en la Fase A) y **task_prod12_docker_01 HECHA** (`4d53f92`, opción b).
> **task_prod12_mkt_01 investigada y BLOQUEADA**: `InstallOrchestrator.install()` (gates
> completos) existe, pero corre sobre el `source_dir` de un artefacto FETCHEADO — la primera
> instalación no tiene registry de artefactos ni materialización del contenido del listing
> (el mismo gap H4/M1 de la auditoría de marketplace 2026-06-24); cablear solo el analizador
> exige antes materializar el manifest a disco. Pendientes: net_01 (mitad marketplace),
> img_01, reaper_01, mkt_01 (tras materialización), av_01 (+ADR corto), cadv_01, docs_01
> (añadir además: UI del panel para `allowed_domains` y el follow-up de retirar run*\* de
> seeds — docker_01 opción a).

## Resumen

La auditoría de producción (2026-06-10) encontró que la superficie de tools de los
agentes tiene una deuda de diseño SSRF seria pero hoy **latente**: las tools HTTP
(`http_get`/`http_post` y los `http_endpoint`) validan el destino comparando solo el
hostname textual contra la allowlist, sin resolver a IP, sin rechazar rangos privados/
loopback/link-local ni el endpoint de metadata 169.254.169.254, y httpx re-resuelve el
DNS después de validar (TOCTOU/DNS-rebinding) — gap4-1, **disputado** en verificación
porque hoy no es alcanzable: el worker nunca propaga `allowed_domains` al spec del
agente, así que la allowlist llega vacía y las tools de red están en deny-all de facto
(gap4-2), que además las deja inoperativas para el operador. Este plan trata gap4-1
como deuda de diseño a cerrar **ANTES** de cablear la allowlist: primero la defensa de
IP + anclaje de DNS + `follow_redirects=False` explícito (gap4-3), después el cableado.

Completan el cuadro: `network_policy='open'` crea un bridge NO interno con internet
crudo para código de test/marketplace no confiable (sandbox-3); no existe reaper de
contenedores huérfanos pese a etiquetarlos para ello (sandbox-5); las imágenes de
test-runtime corren como root y su dep-cache bajo `/root` no es escribible por el uid
1000 que fuerza el worker (sandbox-6); la tool `docker_command` es inservible dentro
del sandbox pero se cablea como vía de los `run_*` (sandbox-7); cAdvisor corre
privileged con montajes de host (sandbox-8); el endpoint de instalación del marketplace
omite el análisis estático ya implementado — TODO conocido (quality-4); y el antivirus
de ingestión es fail-open si ClamAV está caído (api-1).

## Alcance

**Entra**:

- Defensa SSRF completa en las tools HTTP del agent-runtime: resolución + validación de
  IP (denylist de rangos internos/metadata), anclaje de DNS, `follow_redirects=False`
  explícito y validación de las entradas de allowlist en el api-server.
- Cableado de `allowed_domains` proyecto → `ExecutionRequest` → `_agent_spec` → runtime,
  **gateado** a que la defensa anterior esté mergeada y en verde.
- `network_policy='open'` sin internet crudo: egress por el egress-proxy + auditoría de uso.
- Reaper beat de contenedores (label `com.agentic-platform.managed=true`) y redes huérfanas.
- Imágenes de test-runtime no-root (`USER 1000:1000`) + dep-cache en ruta escribible.
- Retirar/arreglar la tool `docker_command` y aclarar su relación con los `run_*`.
- Análisis estático (`StaticAnalyzer`) también en la PRIMERA instalación del marketplace.
- Antivirus fail-closed configurable (default producción) con estado `pending_scan` y alerta.
- cAdvisor: minimizar privilegios o documentar el trade-off y hacer el overlay opt-in explícito.

**Queda fuera**:

- El cableado del egress-proxy en el worker (`WORKERS_EGRESS_PROXY_URL`) y del compose
  generado — el hallazgo gap4-4 fue REFUTADO como hallazgo propio y su cierre vive en
  **prod-01-despliegue-ejecutable**, junto con sandbox-1 (socket Docker/red del servicio
  workers), sandbox-2 (pinning de seccomp/AppArmor) y sandbox-4 (API interna inalcanzable).
  Este plan implementa la defensa a nivel de aplicación, que debe sostenerse SOLA aunque
  el proxy no esté; el proxy es defensa en profundidad, no sustituto.
- El sweeper de ejecuciones zombi dirigido por la tabla `executions` — es
  task_prod06_zombi_01 de **prod-06-ciclo-vida-ejecucion**. El reaper de este plan
  (sandbox-5) es genérico por label/edad e incluye redes; ambos deben compartir helper
  (ver coordinación en task_prod12_reaper_01) para no matarse contenedores mutuamente.
- El gate de SCA/auditoría de dependencias en CI — **prod-11-cadena-suministro**.
- Las alertas de plataforma (la regla "AV caído > N min" se emite aquí; la cadena de
  notificación/alerting se endurece en **prod-08-observabilidad-alertas**).

## Decisiones clave

1. **Estrategia anti-DNS-rebinding (gap4-1)** — Opciones: (a) anclaje de DNS a nivel de
   aplicación: resolver una sola vez, validar TODAS las IPs y conectar a la IP fijada
   (transporte httpx custom con resolución cacheada, preservando cabecera Host y SNI);
   (b) delegar el anti-rebinding al egress-proxy obligatorio y dejar en la app solo la
   validación de IP en el momento del check. **Recomendación: (a)** — el proxy no está
   cableado por defecto (prod-01) y la defensa de la app debe sostenerse sola; el proxy
   queda como segunda capa cuando llegue.
2. **Semántica de `network_policy='open'` (sandbox-3)** — Decisión de producto: (a)
   eliminar el internet crudo: 'open' enruta SIEMPRE por el egress-proxy con allowlist
   por proyecto; (b) mantener 'open' crudo pero restringido a runtimes confiables, con
   registro de auditoría y aviso reforzado en la consola de consentimiento. **No se
   decide aquí: se redacta ADR con ambas opciones** (task_prod12_net_01) porque cambia
   el contrato visible del consentimiento. La estimación asume la opción (a), la más cara.
3. **Destino de `docker_command` (sandbox-7)** — Opciones: (a) retirar la tool del
   catálogo del agent-runtime y documentar que los `run_*` van por `TestRuntimeRunner`
   del worker; (b) mantenerla devolviendo un error claro "no soportado en sandbox" en
   boot. **Recomendación: (a)** — hoy falla en la primera llamada con un error confuso
   (la imagen no instala el paquete `docker` ni recibe socket); mantener código muerto
   en la superficie de tools es deuda y confusión para el operador.
4. **Modo de fallo del antivirus (api-1)** — Decisión de producto: ante
   `AntivirusVerdict.ERROR`, (a) fail-closed: el documento queda `pending_scan`, no se
   indexa y un sweep lo reintenta; (b) fail-open actual con warning. **Recomendación:
   (a) como default con setting de plataforma** (`av_failure_mode`, fail-open solo
   permitido en dev/sandbox); se documenta como ADR corto propuesto para aprobación humana.
5. **cAdvisor privileged (sandbox-8)** — Opciones: (a) ejecutarlo sin `privileged` con
   los montajes mínimos + cap-drop, validando que las métricas necesarias sobreviven;
   (b) mantener el patrón estándar privileged pero documentar el trade-off en el runbook
   y hacer el overlay de monitoring opt-in con aviso explícito. **Recomendación: intentar
   (a) y caer a (b) documentado si las métricas se degradan** — presupuestado para ambas.

## Tareas

### Fase A — Defensa SSRF en las tools HTTP del agent-runtime (gap4-1, gap4-3)

> **HECHAS (2026-07-08, `9cd2eb5`) — Fases A y B completas.** Variantes de ubicación respecto al
> plan: los tests del runtime viven en `docker/agent-runtimes/agent-runtime/tests/` (convención
> del árbol) — `test_ssrf_guard.py` (20) y `test_http_tools_destination_validation.py` (7); la
> validación del api-server en `tests/integration/test_allowed_domains_validation.py` (12, con
> la migración 0105 que CREA la columna `projects.allowed_domains` — no existía); el cableado en
> `tests/unit/test_execution_request_allowed_domains.py` (6, incluye el test-centinela del
> riesgo 1) y el e2e de la cadena en `tests/e2e/test_agent_http_allowlist_chain.py` (5, sin
> Docker: seams de resolver/transporte sobre el código de producción). El opt-in sandbox para
> rangos privados on-prem (decisión de task_prod12_ssrf_03) queda deliberadamente SIN
> implementar — documentado en `docs/04-reference/tools.md`. Falta la UI del panel para editar
> `allowed_domains` (hoy vía API), anotado en task_prod12_docs_01.

#### `task_prod12_ssrf_01` — Guard de destino con resolución y validación de IP

- [x] **Título**: Crear `agent_runtime/ssrf_guard.py` (módulo compartido) que: resuelva
      el hostname UNA vez (`getaddrinfo`, A y AAAA), valide TODAS las IPs resueltas con
      `ipaddress` (`is_private`, `is_loopback`, `is_link_local`, `is_reserved`,
      `is_multicast`, más 169.254.169.254 y fd00::/8/::1 explícitos) y rechace si
      CUALQUIERA cae en rango interno. Integrarlo en `HttpRequestTool._validate`
      (docker/agent-runtimes/agent-runtime/agent_runtime/http_tool.py:42-48) y en
      `HttpEndpointTool._validate_url` (http_endpoint_tool.py:123-136), que hoy solo
      hacen `host not in self.allowed_domains`. Rechazar también IP literales en la URL.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_ssrf_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_ssrf_guard.py -v"
  - id: auto_prod12_ssrf_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_http_tools_destination_validation.py -v"
  ```

#### `task_prod12_ssrf_02` — Anclaje de DNS y `follow_redirects=False` explícito

- [x] **Título**: Cerrar la ventana TOCTOU: conectar a la IP validada (transporte httpx
      con resolución fijada/cacheada preservando Host y SNI, decisión 1) en vez de dejar
      que `client.stream()` re-resuelva (http_tool.py:54-56, http_endpoint_tool.py:149-156).
      Pasar `follow_redirects=False` de forma EXPLÍCITA al construir `httpx.Client()` en
      ambas tools (hoy depende del default implícito del pin `httpx>=0.27,<1.0`,
      pyproject.toml:26); si en el futuro se necesitan redirects, re-validar cada salto.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: task_prod12_ssrf_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_ssrf_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_http_tools_dns_pinning_redirects.py -v"
  ```

#### `task_prod12_ssrf_03` — Validar las entradas de allowlist en el api-server

- [x] **Título**: En el endpoint del api-server que persiste `allowed_domains` del
      proyecto, validar cada entrada: rechazar IP literales, `localhost`, nombres
      no-FQDN y los hostnames internos del compose (vault, api-server, redis, postgres,
      minio…), salvo opt-in explícito de proyecto sandbox (plantilla de validación
      Sandbox). Normalizar (lowercase, sin esquema/puerto) antes de guardar. Mensaje de
      error claro para el operador.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_ssrf_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_allowed_domains_validation.py -v"
  ```

### Fase B — Cableado de la allowlist (gap4-2) — GATEADA por la Fase A

#### `task_prod12_allow_01` — Propagar `allowed_domains` del proyecto al spec del agente

- [x] **Título**: Añadir el campo `allowed_domains` a `ExecutionRequest`
      (apps/workers/src/workers/execution.py:91-146, incluyendo `as_dict`/`from_dict`),
      poblarlo en el dispatcher/orchestrator desde el campo del proyecto, y emitirlo en
      `_agent_spec` (execution.py:226) para que `__main__.py:120` del runtime deje de
      recibir siempre `frozenset()` (deny-all). **Regla dura (orden del auditor jefe):
      esta tarea NO se mergea hasta que task_prod12_ssrf_01/02/03 estén en verde** — el
      deny-all accidental es hoy la única protección frente al SSRF de gap4-1.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: task_prod12_ssrf_01, task_prod12_ssrf_02, task_prod12_ssrf_03
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_allow_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_request_allowed_domains.py -v"
  ```

#### `task_prod12_allow_02` — Test e2e de la cadena de allowlist + documentación

- [x] **Título**: Test e2e: proyecto con `allowed_domains=["example.com"]` → el agente
      alcanza example.com con `http_get`, recibe rechazo claro para cualquier otro host,
      para IP literales y para hosts que resuelven a rango privado. Documentar en
      `docs/04-reference/` la semántica (lista vacía = deny-all, defensa de IP siempre
      activa, el egress-proxy de prod-01 como segunda capa) y el mensaje de diagnóstico
      que ve el operador cuando un dominio no está permitido.
- **Tiempo**: 1 día · **Complejidad**: s
- **Depende de**: task_prod12_allow_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_allow_02_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_agent_http_allowlist_chain.py -v"
  ```

### Fase C — Red del sandbox y runtimes no-root (sandbox-3, sandbox-6)

#### `task_prod12_net_01` — `network_policy='open'` sin internet crudo

- [ ] **Título**: Redactar ADR (decisión 2) y, tras aprobación, implementar: en
      `TestRuntimeRunner._create_bridge` (apps/workers/src/workers/test_runtime.py:581-588,
      hoy `internal = policy != "open"`) y en el sandbox del marketplace
      (apps/api-server/src/api_server/marketplace/sandbox.py:211), la política 'open'
      deja de crear un bridge no-interno con egress libre: enruta por el egress-proxy
      con allowlist por proyecto (opción a) o queda restringida a runtimes confiables
      con auditoría (opción b). En ambos casos: registrar cada uso de 'open' en el audit
      log y reflejar el riesgo en el texto de la consola de consentimiento.
- **Estado (2026-06-30)**: ADR redactado y aprobado → **ADR 0094** (opción a). La mitad de
  **`TestRuntimeRunner`** está IMPLEMENTADA + DESPLEGADA + verificada e2e: `_create_bridge` es
  siempre `internal=True` (fin del NAT crudo de 'open'); el egress va por el nuevo `registry-proxy`
  (allowlist de registries públicos) que el worker conecta al bridge per-task; audit-log
  `stack_exec_egress`. **Pendiente de esta tarea**: la mitad de
  `marketplace/sandbox.py` (mismo helper de attach, sin NAT crudo) + la allowlist por-proyecto +
  el texto de consentimiento → ver `docs/roadmap/registry-egress-followups.md` (F1, F2). NO marcar
  `[x]` hasta cerrar también el marketplace.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_net_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_network_policy_open_egress.py -v"
  ```

#### `task_prod12_img_01` — Imágenes de test-runtime no-root y dep-cache escribible

- [ ] **Título**: Hornear `USER 1000:1000` y un home escribible (`/home/agent`) en los
      Dockerfiles de los templates de test-runtime (p.ej.
      docker/agent-runtimes/python-pytest/Dockerfile:32, hoy sin `USER`, y el resto del
      catálogo), igual que ya hace el agent-runtime como defensa en profundidad. Alinear
      `dep_cache_mount` en packages/shared-test-runtimes/src/shared_test_runtimes/catalog.py:159
      (`/root/.cache/pip`, `/root/.nuget/packages`…) con rutas bajo `/home/agent`
      escribibles por el uid 1000 que el worker fuerza (test_runtime.py:756) — hoy el
      cacheo de dependencias falla en silencio y reinstala en cada run.
- **Nota (2026-06-30, ADR 0094)**: la alineación `cache_env` por plantilla ya se añadió (apunta cada
  tool a su `dep_cache_mount` montado). composer/npm/go cachean por el bind-mount uid-1000;
  pip/gem/nuget-global que escriben en rutas root SIGUEN necesitando esta tarea (imágenes
  `USER 1000` + home escribible). Ver `docs/roadmap/registry-egress-followups.md` (F4).
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_img_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_runtime_catalog_dep_cache_paths.py -v"
  - id: auto_prod12_img_01_b
    runtime: python-pytest
    command: "pytest tests/integration/test_test_runtime_nonroot_cache.py -v"
  ```

### Fase D — Reaper de huérfanos y tool `docker_command` (sandbox-5, sandbox-7)

> **HECHA (2026-07-08) — task_prod12_docker_01, opción (b) del propio item**: el executor
> `DockerCommandTool` falla rápido con error accionable («not supported inside the agent
> sandbox… use stack*exec, ADR 0093») sin tocar `docker.from_env()`; el seam de tests conserva
> el camino real. Test `tests/test_docker_command_tool_retired.py` + el boot-test actualizado
> (antes solo pasaba inyectando un módulo `docker` fake — el camino muerto exacto del hallazgo).
> Se eligió (b) sobre la (a) recomendada deliberadamente: con `stack_exec` como vía real ya
> desplegada, extirpar la familia del catálogo/seeds (run*\* en builtin_tools + equipos) es
> cirugía de producto con estado en BD — queda anotada como follow-up en task_prod12_docs_01.

#### `task_prod12_reaper_01` — Reaper beat de contenedores y redes huérfanos

- [x] **Título**: Nueva tarea beat en apps/workers/src/workers/maintenance.py (registrada
      en beat_schedule.py) que liste `containers.list(filters={'label':
'com.agentic-platform.managed=true'})` (el label ya se estampa en container.py:31
      "para encontrar y reapear huérfanos"), identifique los que no tienen ejecución viva
      asociada (label execution-id + margen de edad) y los elimine; análogamente las
      redes bridge de test-runtime huérfanas. Sustituir el no-op `idle_sweep_pools`
      (hoy `return {"swept": 0}`). **Coordinación con prod-06**: task_prod06_zombi_01
      implementa un sweeper dirigido por `executions` stale; si aterriza primero,
      extender su helper en lugar de duplicar — un solo punto decide qué contenedor es
      huérfano, para evitar doble kill o criterios divergentes.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_reaper_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_orphan_container_reaper.py -v"
  ```

> **HECHA (2026-07-08) — task_prod12_reaper_01**: `workers.reap_orphans` (cada 10 min,
> `maintenance/orphan_reaper.py`) — contenedores `managed=true` sin asociación VIVA (execution
> `running` / review `running|suspended`; criterio de vida COMPARTIDO con el sweeper de zombis
> de prod-06, nunca doble-kill) + redes bridge de test-runtime vacías; gracia anti-carrera 10
> min; sin label de asociación solo cae a hard-limit+25 %. `idle_sweep_pools` se conserva (es el
> heartbeat de pools in-process, otra cosa). Test `tests/integration/test_orphan_container_reaper.py`.

#### `task_prod12_docker_01` — Retirar (o degradar con error claro) la tool `docker_command`

- [x] **Título**: Aplicar la decisión 3: la tool `docker_command`
      (docker/agent-runtimes/agent-runtime/agent*runtime/docker_command_tool.py:139)
      hace `docker.from_env()` dentro del sandbox, pero la imagen no instala el paquete
      `docker` (pyproject.toml:6) ni recibe socket por diseño (Dockerfile:8 "carries NO
      Docker client") — hoy los `run*\*`(run_pytest/run_lint/…) inyectados por esta vía
fallan en la primera llamada. Opción (a): retirarla del catálogo y del wiring,
documentando que la ejecución real de tests va por`TestRuntimeRunner` del worker.
      Opción (b): error explícito "no soportado en sandbox" en boot. Actualizar la
      asignación de tools en seeds/catálogo para que el operador no pueda asignar una
      tool muerta.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_docker_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_docker_command_tool_retired.py -v"
  ```

### Fase E — Marketplace y antivirus (quality-4, api-1)

#### `task_prod12_mkt_01` — Análisis estático en la PRIMERA instalación del marketplace

- [ ] **Título**: Cablear `InstallOrchestrator._run_static_analysis`
      (apps/api-server/src/api_server/marketplace/install.py:618, bandit/semgrep ya
      implementados y funcionando) también en `install_listing`
      (routers/marketplace.py:902, donde vive el `TODO(Plan 09 Fase B/C)`), igual que ya
      hace `perform_installation_update` (routers/marketplace.py:1158). Retirar el TODO.
      Persistir los hallazgos del análisis y respetar el gate de consentimiento existente
      (community/experimental instalan DISABLED hasta consentir). Test que verifica que
      install y update pasan por el MISMO pipeline de análisis.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_mkt_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_install_static_analysis.py -v"
  ```

#### `task_prod12_av_01` — Antivirus fail-closed configurable con `pending_scan`

- [ ] **Título**: Aplicar la decisión 4 (tras ADR corto aprobado): en el pipeline de
      ingestión (apps/api-server/src/api_server/ingestion/pipeline.py:101-107), ante
      `AntivirusVerdict.ERROR` (clamd inalcanzable/timeout, antivirus.py:101) NO indexar:
      dejar el documento en estado `pending_scan` y reintentar vía el sweep de Celery
      beat que ya re-encola pendientes. Setting de plataforma `av_failure_mode` con
      default fail-closed en producción (fail-open solo dev/sandbox, registrado en
      platform_settings). Emitir notificación (notification-dispatcher) cuando el
      backend AV lleve N minutos inalcanzable — la regla de alerta final vive en prod-08.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_av_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ingestion_av_fail_closed.py -v"
  ```

### Fase F — cAdvisor y cierre documental (sandbox-8)

#### `task_prod12_cadv_01` — Minimizar privilegios de cAdvisor o documentar el trade-off

- [ ] **Título**: Aplicar la decisión 5 sobre el servicio cAdvisor del overlay de
      monitoring (apps/installer/backend/src/installer_backend/compose_generator.py:636,
      hoy `privileged: True` + `/dev/kmsg` + montajes `/:/rootfs:ro`,
      `/var/run:/var/run:ro` — que expone el socket Docker en lectura — y
      `/var/lib/docker:ro`, sin cap-drop ni AppArmor): probar la variante sin
      `privileged` con montajes mínimos y cap-drop; si las métricas se degradan,
      mantener el patrón estándar pero documentar el riesgo en el runbook de monitoring
      y condicionar el overlay a un opt-in explícito del operador con el trade-off visible.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod12_cadv_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_compose_generator.py -k cadvisor -v"
  ```

#### `task_prod12_docs_01` — Documentación de referencia y runbook

- [ ] **Título**: Actualizar `docs/04-reference/` con la superficie endurecida: semántica
      de `allowed_domains` y defensas SSRF de las tools HTTP, semántica final de
      `network_policy` (según ADR de la decisión 2), catálogo de tools sin
      `docker_command` (o con su error documentado), modo de fallo del antivirus y
      estados de documento (`pending_scan`). Añadir al runbook de monitoring el apartado
      de cAdvisor y, si aparecieron trampas de toolchain durante el plan, registrarlas
      en `docs/03-guides/gotchas/`.
- **Tiempo**: 1 día · **Complejidad**: s
- **Depende de**: task_prod12_allow_02, task_prod12_net_01, task_prod12_docker_01, task_prod12_av_01, task_prod12_cadv_01
- **Tests automáticos**: no aplica (documentación); el gate es la revisión humana del PR.

## Hallazgos de auditoría cubiertos

| fid       | Severidad        | Tarea(s) que lo cierran                                       |
| --------- | ---------------- | ------------------------------------------------------------- |
| gap4-1    | high (disputado) | task_prod12_ssrf_01, task_prod12_ssrf_02, task_prod12_ssrf_03 |
| gap4-2    | medium           | task_prod12_allow_01, task_prod12_allow_02                    |
| gap4-3    | low              | task_prod12_ssrf_02                                           |
| sandbox-3 | medium           | task_prod12_net_01                                            |
| sandbox-5 | medium           | task_prod12_reaper_01                                         |
| sandbox-6 | low              | task_prod12_img_01                                            |
| sandbox-7 | low              | task_prod12_docker_01                                         |
| sandbox-8 | low              | task_prod12_cadv_01                                           |
| quality-4 | medium           | task_prod12_mkt_01                                            |
| api-1     | medium           | task_prod12_av_01                                             |

Nota sobre gap4-1: quedó **disputado** en la verificación adversarial — el agujero de
diseño es real pero hoy inalcanzable por el deny-all accidental de gap4-2. Este plan lo
trata como deuda de diseño con orden estricto: defensa primero (Fase A), cableado
después (Fase B). El hallazgo gap4-4 (egress-proxy no cableado) fue REFUTADO como
hallazgo independiente; su cableado pertenece a prod-01.

## Riesgos

1. **Orden de merge invertido**: si task_prod12_allow_01 llega a master antes que la
   Fase A, la protección accidental (deny-all) se convierte en un SSRF explotable.
   Mitigación: dependencia dura declarada en el plan, revisión del PR por fases y un
   test en CI que falla si `_agent_spec` emite `allowed_domains` sin que exista
   `ssrf_guard` aplicado en ambas tools.
2. **El anclaje de DNS rompe TLS/SNI**: conectar a la IP fijada exige preservar la
   cabecera Host y el SNI del certificado; una implementación ingenua rompe todas las
   peticiones HTTPS legítimas. Mitigación: transporte httpx custom con tests contra un
   servidor TLS real en integración, no solo mocks.
3. **La denylist de IP bloquea casos legítimos on-prem**: APIs internas del operador en
   rangos privados (p.ej. un GitLab en 10.x) dejarían de ser alcanzables. Mitigación:
   opt-in explícito por proyecto tipo Sandbox (decisión documentada en
   task_prod12_ssrf_03) con auditoría, nunca default.
4. **Enrutar 'open' por el egress-proxy puede romper la instalación de dependencias** de
   los test-runtimes (npm/pip/nuget contra registries variados). Mitigación: allowlist
   de registries comunes en el filtro del proxy + ADR de la decisión 2 aprobado antes de
   implementar; coordinación con prod-01, que es quien cablea el proxy.
5. **Reaper agresivo mata contenedores vivos**: un criterio de edad mal calibrado o el
   solape con el sweeper de prod-06 puede matar runs legítimos o el mismo contenedor dos
   veces. Mitigación: helper único compartido, doble verificación contra la fila de
   execution antes del `rm -f` y margen = hard limit + 25 %.
6. **Fail-closed del antivirus paraliza la ingestión** si ClamAV queda caído mucho tiempo
   o mal dimensionado: la cola `pending_scan` crece sin límite visible. Mitigación:
   notificación temprana (N minutos), métrica de profundidad de `pending_scan` para
   prod-08 y runbook de recuperación.

## Tests humanos del Plan

```yaml
- id: human_prod12_01
  description: "La defensa SSRF se sostiene end-to-end con la allowlist cableada"
  hint: "Proyecto con allowed_domains=['example.com'] y un agente con http_get asignado"
  checklist:
    - "El agente descarga con éxito una URL de example.com"
    - "Una URL de un dominio no listado recibe rechazo claro (no timeout silencioso)"
    - "http://169.254.169.254/latest/meta-data y http://localhost:8000 rechazados aunque se intenten añadir a la allowlist (el api-server rechaza la entrada al guardar)"
    - "Un dominio de prueba que resuelve a 127.0.0.1 (tipo rebinding) es rechazado por el guard de IP"
    - "Una respuesta 302 de un dominio permitido hacia un host interno NO se sigue"

- id: human_prod12_02
  description: "Egress de test-runtimes y reaper de huérfanos"
  hint: "Usar un proyecto sandbox con un runtime python-pytest y docker ps en el host"
  checklist:
    - "Con network_policy='open' (según ADR aprobado): el tráfico sale por el egress-proxy o queda restringido — nunca internet crudo sin registro"
    - "Cada uso de 'open' aparece en el audit log y el texto de consentimiento avisa del riesgo"
    - "Matar el proceso worker (kill -9) durante un run: tras el siguiente ciclo del reaper, docker ps no muestra contenedores com.agentic-platform.managed huérfanos ni redes test-runtime colgadas"
    - "Un run de pytest con cache de dependencias reutiliza el cache (el segundo run instala notablemente más rápido) y el contenedor corre como uid 1000"

- id: human_prod12_03
  description: "Marketplace y antivirus endurecidos"
  hint: "Instalar un listing community nuevo y parar el contenedor de ClamAV"
  checklist:
    - "Instalar por primera vez un listing community: el análisis estático se ejecuta y sus hallazgos quedan visibles (mismo comportamiento que en update)"
    - "El TODO de routers/marketplace.py:902 ya no existe en el código"
    - "Con ClamAV parado, subir un documento a una KB: queda en pending_scan, NO se indexa ni aparece en RAG"
    - "Al levantar ClamAV de nuevo, el sweep reescanea y el documento se indexa solo"
    - "La notificación de 'antivirus inalcanzable' llega tras N minutos de caída"

- id: human_prod12_04
  description: "Superficie de tools y monitoring coherentes"
  hint: "Revisar el catálogo de tools del admin-panel y el compose generado"
  checklist:
    - "docker_command ya no es asignable a agentes (o muestra el error documentado), según la decisión aprobada"
    - "Los run_* de tests siguen funcionando por la vía del TestRuntimeRunner del worker"
    - "El compose generado refleja la decisión de cAdvisor (sin privileged, o con el opt-in y el runbook que documenta el trade-off)"
    - "docs/04-reference/ documenta allowed_domains, network_policy y av_failure_mode tal y como se comportan de verdad"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. La cadena SSRF probada en CI: el e2e `test_agent_http_allowlist_chain.py` pasa y existe
   el test-centinela que impide emitir `allowed_domains` sin guard de IP activo.
3. ADRs de las decisiones 2 (network_policy 'open') y 4 (av_failure_mode) redactados en
   `docs/05-architecture-decisions/` y aprobados por un humano; implementada la opción elegida.
4. Los 4 tests humanos del plan validados por un humano.
5. `docs/04-reference/` y runbook de monitoring actualizados (task_prod12_docs_01).
6. Entrada de changelog en `docs/07-changelog/prod-12-hardening-tools-agentes.md`.
7. PR del plan mergeado a `master`.

## Próximo Plan

**prod-13-rendimiento-y-datos** [P1] — Rendimiento y gestión de datos: event loop, pool,
retención e índices. Coordinaciones pendientes desde este plan: la métrica de profundidad
de `pending_scan` (task_prod12_av_01) y el audit log de usos de `network_policy='open'`
(task_prod12_net_01) generan datos cuya retención/purga define prod-13; y las reglas de
alerta (AV caído, reaper activo) se cablean en prod-08-observabilidad-alertas.
