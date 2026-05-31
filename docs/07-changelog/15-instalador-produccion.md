---
plan_id: 15-instalador-produccion
title: Instalador, Endurecimiento y Producción
completed_at: null
docs_language: es
---

# Plan 15 — Instalador, Endurecimiento y Producción

## Resumen

El sistema funcional ya estaba. Este plan lo hace **instalable por terceros sin
asistencia** y lo **endurece para producción real**. Añade un **instalador** —un
contenedor temporal que sirve un **wizard de 9 pasos** y se **autodestruye** tras
revelar las credenciales **una sola vez**— más un **modo CLI desatendido**
(`install.sh --config install.yaml`) con **plantillas por perfil** (minimal /
recommended / gpu), los **generadores** que materializan en disco el
`docker-compose.yml` + `.env` + `config/global.yaml` + el árbol
`/data/agent-platform/`, el **bootstrap de Vault** (init + unseal + KV v2 +
políticas), y los scripts de **uninstall** (doble confirmación) y **reinstall**
(preservación de datos opcional). Sobre eso aplica el **endurecimiento de
seguridad**: un **pentest interno** convertido en **invariantes automáticas** que
fallan en rojo ante un retroceso de hardening, **perfiles seccomp default-deny
por contenedor**, **perfiles AppArmor (MAC) por contenedor**, **rotación
automática de credenciales con Vault dynamic secrets**, y el **hardening del
panel admin** (MFA obligatorio + IP allowlist + sesiones de 15 min, solo en
prod). Cierra con la **documentación operativa**: **6 runbooks numerados**, el
**portal de desarrollador público**, y los **smoke tests post-deploy**.

El instalador es un **contenedor separado** (`apps/installer`, Next.js +
backend FastAPI mínimo) que NO forma parte del stack runtime. Toda la lógica de
provisioning real (prereqs, escritura de `.env` / compose / árbol de datos,
`docker compose up`, bootstrap de Vault, seed del tenant, self-destruct) vive en
el **backend Python unit-testeable** (`installer_backend`) detrás de **seams**
inyectables: los tests dirigen la orquestación con fakes deterministas **sin
tocar disco, Docker, ni un Vault real**. El mismo backend sirve a la UI del
wizard y al CLI desatendido, de modo que ambos caminos corren la **misma**
orquestación. Los secretos se generan con un **CSPRNG** (≥ 256 bits), son únicos
por instalación y están diseñados para **pasar el guard de secretos-dev en
producción** del Plan 06.14 (un `.env` de prod no lleva `changeme` / `dev-only` /
`minioadmin`). Las credenciales y las **unseal keys de Vault** se muestran
**exactamente una vez** y **no hay recuperación**: el operador es responsable de
guardarlas.

El endurecimiento de seguridad sigue una regla deliberada: el **enforcement real
del kernel** (seccomp / AppArmor cargados, escapes de contenedor reales, un Vault
vivo que acuña roles efímeros, Redis + MFA reales) **no corre en CI**. En su
lugar, cada control se entrega como **perfil + cableado en compose/runtime** y se
**valida estructuralmente** con suites de seguridad que fallan en rojo **solo
ante un retroceso de hardening**, nunca por un capricho del entorno. El
enforcement real queda documentado como **test humano** en
`internal-pentest-methodology.md` y cubierto por el **pentest externo** reservado
al humano (`task_15_27`).

Las **29 tareas** se planificaron en cinco fases (A — wizard del instalador; B —
generadores de config + CLI + perfiles; C — endurecimiento de seguridad; D —
runbooks + portal + smoke tests; E — cierre final). De ellas, **27 son
construibles y están completas + verdes** (`15_01`..`15_26` + esta `15_28`).
Las **2 restantes están reservadas al humano** y NO están hechas: `task_15_27`
(pentest externo profesional) y `task_15_29` (release v1.0.0). Ver
[Pendiente / reservado al humano](#pendiente--reservado-al-humano).

> **⚠ Gaps conocidos que NO cierran en este plan.** Los specs **Playwright e2e**
> del instalador y del portal de desarrollador están **escritos pero NO
> ejecutados** (el runtime node-playwright de este entorno no trae navegador).
> La **instalación / desinstalación / restore reales**, el **enforcement de
> kernel** (seccomp / AppArmor), la **rotación contra un Vault vivo** y el
> **hardening admin con Redis + MFA reales** son **tests humanos / de stack**.
> Plan 15 se construyó sobre la pila de los **Planes 07–14, que siguen en
> `pending_human_validation`** (faltan sus tests humanos + el merge del PR), bajo
> el **override humano** del gate `blocking_plan`. El **gap FX del Plan 11**
> (toggle de moneda del tenant / `exchange_rates`) sigue abierto. Ver
> [Pendiente / reservado al humano](#pendiente--reservado-al-humano).

## Cambios por tarea

### Fase A — Wizard del Instalador

- ✅ **`task_15_01`** — **Contenedor installer + UI temporal del wizard**
  (`apps/installer/`: `Dockerfile`, `docker-compose.installer.yml`, Next.js
  `app/` + `lib/`, backend FastAPI mínimo `installer_backend/main.py` +
  `wizard.py` + `seams.py`). El instalador es un **contenedor separado** que
  sirve la UI del wizard sobre loopback y **NO forma parte del stack runtime**.
  La orquestación real vive en el backend Python detrás de seams (mockeados en
  tests). Spec Playwright `installer-wizard.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_15_02`** — **Paso 1: validación de prerequisitos**
  (`installer_backend/prereqs.py` + `app/prereq-panel.tsx`). Valida Docker,
  Compose v2, RAM, disco y **detección de GPU NVIDIA** **antes** de tocar nada;
  un prerequisito faltante aborta con mensaje explícito y NO inicia provisioning.
  La detección host-touching va por seam.
- ✅ **`task_15_03`** — **Pasos 2-6: captura de config** (`app/steps/`:
  `basics-step.tsx`, `resources-step.tsx`, `storage-step.tsx`,
  `providers-step.tsx`, `tenant-step.tsx` + `lib/wizard.ts` /
  `installer_backend/config.py`). Captura sistema, recursos/GPU, almacenamiento,
  providers LLM (catálogo cerrado ADR 0021) y el tenant inicial. Spec Playwright
  `installer-steps.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_15_04`** — **Paso 7: resumen + confirmación con preview de
  recursos** (`app/steps/summary-step.tsx` + `lib/preview.ts`). Muestra un
  resumen revisable y un **preview de recursos** (sizing derivado del perfil)
  antes de confirmar. Spec Playwright `installer-summary.spec.ts` **escrito, no
  ejecutado**.
- ✅ **`task_15_05`** — **Paso 8: instalación con progreso + logs en tiempo
  real** (`installer_backend/install.py` + `app/steps/install-step.tsx` +
  `lib/use-install.ts`). La orquestación encadena
  prereqs → generar compose/`.env`/config → `docker compose up` → bootstrap de
  Vault → seed del tenant → finalize, streameando progreso + logs. Spec
  Playwright `installer-progress.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_15_06`** — **Paso 9: credenciales mostradas UNA vez +
  autodestrucción del installer** (`installer_backend/finalize.py` +
  `app/steps/done-step.tsx`). `FinalizeService` es una máquina de estados de un
  solo disparo (`not-installed → armed → revealed + self-destruct`): revela las
  credenciales + unseal keys **exactamente una vez** (un segundo intento es
  `CredentialsAlreadyRevealedError`), las mantiene **solo en memoria** con repr
  redactado, y **se autodestruye** vía el seam `InstallerLifecycle`. Una
  instalación incompleta NUNCA revela ni se autodestruye. Registrado en **ADR
  0039**.

### Fase B — Generación de Config y CLI

- ✅ **`task_15_07`** — **Generador de `docker-compose.yml`**
  (`installer_backend/compose_generator.py`). Construye el compose según las
  opciones del wizard (providers habilitados, GPU, sizing, almacenamiento) y
  **emite las referencias `security_opt` de seccomp + AppArmor** que el
  hardening de la Fase C exige (ver ADR 0040). Función pura sobre la config,
  unit-testeable sin Docker.
- ✅ **`task_15_08`** — **Generador de `.env` + `config/global.yaml` + árbol
  `/data/agent-platform/`** (`installer_backend/config_generators.py`). Genera el
  `.env` con **secretos CSPRNG de alta entropía** (≥ 256 bits, únicos por
  instalación, sin marcadores de secreto-dev → pasa el guard de prod del Plan
  06.14), el `config/global.yaml` (config no secreta) y el **plan del árbol de
  datos** (directorios + permisos POSIX). Las funciones puras
  (`generate_secrets` / `render_env_file` / `generate_global_yaml` /
  `build_data_tree_plan`) NO hacen I/O; la escritura real va por los seams
  `EnvFileWriter` / `DataTreeProvisioner`. Registrado en **ADR 0039**.
- ✅ **`task_15_09`** — **Bootstrap de Vault: init + unseal + KV v2 + políticas**
  (`installer_backend/vault_bootstrap.py`). Orquesta `vault operator init`, el
  unseal con las keys generadas (mostradas UNA vez, ADR 0039), habilita el motor
  **KV v2** y aplica las **políticas iniciales**. El cliente de Vault va por seam
  (mockeado en tests; el unseal real es un test humano). Reusa Vault de la ADR 0003.
- ✅ **`task_15_10`** — **Modo CLI desatendido** (`scripts/install.sh` +
  `installer_backend/cli.py`). `install.sh --config install.yaml` es un wrapper
  fino sobre `python -m installer_backend.cli install`; corre la **misma**
  orquestación que el wizard, headless, desde un YAML. **Códigos de salida**
  estables (`ExitCode`): 0 ok, 1 usage, 2 config, 3 prereq, 4 provision, 5
  aborted. Los secretos + unseal keys se imprimen a stdout **una vez** (sin
  recuperación; nunca a un log).
- ✅ **`task_15_11`** — **Plantillas YAML por perfil**
  (`scripts/install-profiles/`: `minimal.yaml`, `recommended.yaml`,
  `gpu.yaml`). Tres perfiles que cubren los casos típicos; un operador copia uno
  y lo edita para el CLI. Los perfiles de producción **no llevan marcadores de
  secreto-dev** (invariante del pentest, ADR 0039).
- ✅ **`task_15_12`** — **`uninstall.sh` con doble confirmación**
  (`scripts/uninstall.sh` + `installer_backend/uninstall.py`). Tear-down
  **destructivo y gateado**: exige (a) teclear el **nombre exacto** del
  deployment (`--confirm-name`) **y** (b) confirmar (`--yes`) — uno solo NO
  basta. Los datos se **preservan por defecto** (`docker compose down`, el árbol
  `/data` queda en disco); `--purge-data` los borra y necesita su **propia**
  confirmación extra. Los seams `docker compose down` + purga están mockeados en
  tests.
- ✅ **`task_15_13`** — **Reinstalación con preservación de datos opcional**
  (`scripts/reinstall.sh` + `installer_backend/reinstall.py`). **PRESERVE**
  (default): conserva volúmenes + DB + object store, regenera config/compose y
  **reusa los secretos + unseal keys existentes** (regenerarlos huérfanaría los
  datos cifrados — reuso obligatorio). **FRESH** (`--fresh`): borra el árbol y
  reinstala desde cero, gateado por la **misma doble confirmación** que el
  uninstall. Sin instalación previa: degrada a un first-install limpio.

### Fase C — Endurecimiento de Seguridad

- ✅ **`task_15_14`** — **Pentest interno → invariantes automáticas**
  (`tests/security/test_pentest_findings.py` +
  `docs/06-runbooks/internal-pentest-methodology.md`). La mitad **automática**
  del pentest: en vez de correr un atacante vivo (kernel / escapes / Vault vivo
  → tests **humanos** + auditoría externa `task_15_27`), **asercia la postura de
  hardening a nivel de fuente** de modo que una regresión —montar el socket
  Docker, quitar `cap_drop`, añadir un `privileged: true` injustificado, una
  tabla tenant sin RLS, un secreto-dev en un perfil de prod— **se pone en rojo
  antes de poder mergear**. Las invariantes reflejan los no-negociables de
  CLAUDE.md (§2 aislamiento, §1 multi-tenancy, ADR 0019 egress, secretos, auth).
- ✅ **`task_15_15`** — **Perfiles seccomp default-deny por contenedor**
  (`docker/seccomp/default.json` + `agent-runtime.json` +
  `tests/security/test_seccomp_profiles.py`). `defaultAction: SCMP_ACT_ERRNO`
  (default-deny: cualquier syscall fuera de la allowlist se rechaza). La familia
  peligrosa (`mount`, `ptrace`, `kexec_load`, `init_module`, `setns`,
  `unshare`…) no está en ninguna allowlist; el perfil del **agent-runtime** es un
  **subconjunto estricto** del default; cada servicio de prod referencia su
  perfil vía `security_opt`; el generador del instalador emite la referencia; el
  seam del worker reenvía el **contenido**. Registrado en **ADR 0040**.
- ✅ **`task_15_16`** — **Perfiles AppArmor (MAC) por contenedor**
  (`docker/apparmor/agentic-default.profile` + `agent-runtime.profile` +
  `tests/security/test_apparmor.py` + runbook `apparmor-profiles.md`). Cada
  perfil **deniega** las primitivas de escape (`mount`, `pivot_root`, `ptrace`,
  módulos, I/O raw, socket Docker, escrituras a `/proc/sys` y `/sys`) y
  **confina** las escrituras (no un `/** rw`); el agent-runtime solo escribe
  `/workspace` + `/tmp`. Cada servicio de prod referencia su perfil vía
  `security_opt: apparmor=…`; el generador lo emite; el seam reenvía el
  **nombre**. Registrado en **ADR 0040**.
- ✅ **`task_15_17`** — **Rotación automática de credenciales (Vault dynamic
  secrets)** (`workers/credential_rotation.py` +
  `tests/integration/test_credential_rotation.py`). El **database secrets
  engine** acuña roles PostgreSQL efímeros (TTL corto); un ciclo emite la nueva
  credencial, renueva + revoca el lease anterior y rota los estáticos (MinIO /
  JWT). Un **job Celery beat** la dispara leyendo su **cadencia de config** y
  honrando el **lever `cred_rotation_enabled`** en vivo. Es **fail-safe** (un
  fallo nunca tira el sistema; dispara alerta vía el notificador del Plan 10) y
  los secretos **nunca se loguean en claro**. El cliente de Vault va por seam
  (fake determinista en tests). Registrado en **ADR 0041**.
- ✅ **`task_15_18`** — **Hardening del panel admin (solo prod)**
  (`api_server/auth/admin_hardening.py` +
  `tests/security/test_admin_hardening.py`). Tres controles activos **solo en
  staging/prod**: **MFA obligatorio** (forced-enrollment gate), **IP allowlist**
  por CIDR (semántica de api-tokens, ADR 0037) y **sesiones cortas** (15 min por
  defecto). Predicados puros + la dependencia `require_hardened_system_admin`
  end-to-end con `SessionStore` en memoria + MFA mockeado. **Dev se queda
  usable** y **ningún no-admin** se ve atrapado. Registrado en **ADR 0042**.

### Fase D — Documentación y Runbooks

- ✅ **`task_15_19`** — **Runbook: instalación desde cero**
  (`docs/06-runbooks/01-installation-from-scratch.md`). Instalar en una máquina
  virgen por el wizard de 9 pasos o por el CLI desatendido.
- ✅ **`task_15_20`** — **Runbook: troubleshooting común**
  (`docs/06-runbooks/02-troubleshooting.md`). Diagnóstico y fix de los fallos
  frecuentes tras instalar o en operación.
- ✅ **`task_15_21`** — **Runbook: upgrade del sistema**
  (`docs/06-runbooks/03-system-upgrade.md`). Actualizar una instalación en marcha
  (imágenes + esquema) de forma reversible.
- ✅ **`task_15_22`** — **Runbook: DR completo + restore selectivo**
  (`docs/06-runbooks/04-disaster-recovery.md`). Punto de entrada canónico del DR,
  consolidando el backup/restore del Plan 12 (enlaza `dr-full-restore.md` /
  `dr-tenant-restore.md` / `dr-manual-backup.md`).
- ✅ **`task_15_23`** — **Runbook: rotación de unseal keys + credenciales**
  (`docs/06-runbooks/05-key-rotation.md`). `vault operator rekey` +
  rotación de credenciales estáticas/dinámicas (ADR 0041) + revocación de
  emergencia.
- ✅ **`task_15_24`** — **Runbook: gestión de capacity**
  (`docs/06-runbooks/06-capacity-management.md`). Escalar workers/colas,
  concurrencia + límites de tiempo, sizing y capacity de GPU.
- ✅ **`task_15_25`** — **Portal de desarrollador público + docs API**
  (`apps/admin-panel/app/developers/` + referencia
  [`dev-portal.md`](../04-reference/dev-portal.md)). Route group **público sin
  sesión** (fuera del segmento `/admin`) que agrega y enlaza fuentes existentes
  (el contrato OpenAPI del Plan 13, los SDKs, la doc canónica) en una landing
  navegable. Spec Playwright `dev-portal.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_15_26`** — **Smoke tests post-deploy**
  (`tests/smoke/`: `probes.py`, `test_smoke.py`, `test_probes_unit.py`,
  `conftest.py`). Sondas post-deploy de extremo a extremo con **skip-guard**: la
  lógica de cada probe es unit-testeable (`test_probes_unit.py` corre siempre);
  las sondas que necesitan un stack vivo hacen **skip-with-notice** cuando no hay
  endpoint configurado, de modo que la suite corre determinista en CI y se
  ejercita de verdad contra un deploy real.
- ✅ **`task_15_28`** — **Documentación final + ADRs + changelog** (esta entrada,
  las **ADR 0039 / 0040 / 0041 / 0042**, y la referencia
  [`installation.md`](../04-reference/installation.md)). Documenta lo
  implementado, registra las decisiones del plan no recogidas en ADRs previos y
  **flagea los gaps + lo reservado al humano**. Se ejecuta **antes** de
  `task_15_27` (pentest externo) por el **override humano**: el changelog deja
  constancia explícita de las 2 tareas reservadas.

### Fase E — Cierre Final (reservada al humano)

- ⛔ **`task_15_27`** — **Pentest externo (auditoría profesional)**. **NO hecha —
  reservada al humano.** Genera `docs/05-architecture-decisions/0099-external-pentest-results.md`
  (slot 0099 reservado por esta tarea; los ADRs del plan usan 0039–0042).
- ✅ **`task_15_28`** — **Documentación final + ADRs + changelog** (ver Fase D).
- ⛔ **`task_15_29`** — **Release v1.0.0**. **NO hecha — reservada al humano.**
  Crea el git tag `v1.0.0`.

## App nueva e infraestructura

### App `apps/installer/` (instalador, NO forma parte del stack runtime)

| Pieza                                            | Para qué                                                                         |
| ------------------------------------------------ | -------------------------------------------------------------------------------- |
| `app/` + `lib/` (Next.js)                        | UI temporal del wizard de 9 pasos (loopback)                                     |
| `backend/src/installer_backend/` (FastAPI + CLI) | Orquestación real de install/uninstall/reinstall detrás de seams                 |
| `Dockerfile` + `docker-compose.installer.yml`    | Contenedor temporal autodestructivo del instalador                               |
| `e2e/*.spec.ts` (Playwright)                     | Specs e2e del wizard — **escritos, no ejecutados** (sin navegador en el entorno) |

Módulos del backend: `prereqs.py` (paso 1), `wizard.py` + `config.py` (pasos
2-6), `compose_generator.py` (15_07), `config_generators.py` (15_08),
`vault_bootstrap.py` (15_09), `install.py` (paso 8), `finalize.py` (paso 9),
`cli.py` (CLI desatendido), `uninstall.py` (15_12), `reinstall.py` (15_13),
`seams.py` (Protocols host-touching mockeados en tests).

### Scripts CLI

| Script                            | Para qué                                                             |
| --------------------------------- | -------------------------------------------------------------------- |
| `scripts/install.sh`              | Install desatendido (`--config install.yaml`) → CLI Python           |
| `scripts/uninstall.sh`            | Tear-down con doble confirmación (datos preservados por defecto)     |
| `scripts/reinstall.sh`            | Reinstalar (PRESERVE por defecto / `--fresh` con doble confirmación) |
| `scripts/install-profiles/*.yaml` | Plantillas por perfil: `minimal` / `recommended` / `gpu`             |

### Hardening de seguridad

| Artefacto                                 | Para qué                                                          |
| ----------------------------------------- | ----------------------------------------------------------------- |
| `docker/seccomp/default.json`             | Perfil seccomp default-deny compartido (servicios de plataforma)  |
| `docker/seccomp/agent-runtime.json`       | Perfil seccomp del runtime no confiable (subconjunto estricto)    |
| `docker/apparmor/agentic-default.profile` | Perfil AppArmor (MAC) compartido                                  |
| `docker/apparmor/agent-runtime.profile`   | Perfil AppArmor del runtime no confiable (más estricto)           |
| `workers/credential_rotation.py`          | Motor de rotación con Vault dynamic secrets (job beat, fail-safe) |
| `api_server/auth/admin_hardening.py`      | MFA obligatorio + IP allowlist + sesiones de 15 min (solo prod)   |

### Runbooks (`docs/06-runbooks/`)

`01-installation-from-scratch.md`, `02-troubleshooting.md`,
`03-system-upgrade.md`, `04-disaster-recovery.md`, `05-key-rotation.md`,
`06-capacity-management.md` (más los runbooks de soporte
`internal-pentest-methodology.md` y `apparmor-profiles.md`).

### Tests nuevos

| Suite                                                               | Para qué                                                         |
| ------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `tests/security/test_pentest_findings.py`                           | Invariantes de hardening (aislamiento / RLS / egress / secretos) |
| `tests/security/test_seccomp_profiles.py`                           | Validación estructural de los perfiles seccomp                   |
| `tests/security/test_apparmor.py`                                   | Validación estructural de los perfiles AppArmor                  |
| `tests/security/test_admin_hardening.py`                            | Predicados + dependencia del hardening del panel admin           |
| `tests/integration/test_credential_rotation.py`                     | Mecánica del motor de rotación (Vault mockeado)                  |
| `tests/smoke/` (`probes.py` + tests)                                | Smoke tests post-deploy con skip-guard                           |
| `tests/integration/test_installer_*.py`                             | Prereqs / finalize del instalador (seams mockeados)              |
| `tests/unit/test_compose_generator.py`, `test_config_generators.py` | Generadores (funciones puras)                                    |

## CI nuevo

| Cambio                                              | Para qué                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/installer/backend` instalado en CI (`ci.yml`) | Sus tests viven en `tests/` (`test_installer_prereq.py` / `test_installer_finalize.py` / `test_installer_backend.py`) e importan `installer_backend` a nivel de módulo → el paquete se instala **editable** en las 3 listas de instalación Python (igual que `workers` / `notification-dispatcher`) o la colección de pytest + el hook mypy fallarían. **Aditivo** (12 líneas, no reescribe la pipeline) |

## Decisiones

- **Instalador autodestructivo + credenciales una sola vez + secretos CSPRNG que
  pasan el guard de prod.** El contenedor installer es temporal y se autodestruye
  tras revelar las credenciales + unseal keys **exactamente una vez** (sin
  recuperación); los secretos se generan con CSPRNG y no llevan marcadores de
  secreto-dev. Registrado en **ADR 0039**.
- **Seccomp default-deny + AppArmor MAC por contenedor.** Dos capas de
  confinamiento del kernel por contenedor, con el runtime no confiable como
  subconjunto estricto del perfil compartido; validación estructural en CI,
  enforcement real como test humano. Registrado en **ADR 0040**.
- **Rotación automática con Vault dynamic secrets.** Credenciales PostgreSQL
  efímeras (TTL corto) + rotación de estáticos, vía job beat con cadence en
  config y lever en vivo, fail-safe (un fallo nunca tira el sistema; alerta).
  Registrado en **ADR 0041**.
- **Hardening del panel admin solo en prod.** MFA obligatorio + IP allowlist por
  CIDR + sesiones de 15 min, activos solo en staging/prod, sin romper dev ni a
  usuarios no-admin. Registrado en **ADR 0042**.
- **El enforcement real del kernel/Vault/Redis no corre en CI; se valida la
  postura estructuralmente.** Cada control de seguridad se entrega como perfil +
  cableado y se asercia estáticamente; el enforcement vivo es test humano +
  pentest externo (`task_15_27`). Patrón heredado del aislamiento de la ADR 0012.
- **El backend del instalador es Python unit-testeable detrás de seams.** Toda
  acción host-touching (Docker, disco, Vault) es un Protocol con fake en tests;
  el mismo backend sirve al wizard y al CLI. Registrado en **ADR 0039**.

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier/markdown/yaml) ✅
  por tarea. El backend del instalador es mypy-strict-clean; los seams Protocol
  hacen los tests deterministas sin I/O.
- Suites pytest en verde por tarea (fases A–D): `tests/integration/test_installer_prereq.py`,
  `tests/integration/test_installer_finalize.py`, `tests/unit/test_compose_generator.py`,
  `tests/unit/test_config_generators.py`, `tests/integration/test_vault_bootstrap.py`,
  `tests/integration/test_cli_install.py`, `tests/integration/test_uninstall.py`,
  `tests/integration/test_reinstall.py`, `tests/security/test_pentest_findings.py`,
  `tests/security/test_seccomp_profiles.py`, `tests/security/test_apparmor.py`,
  `tests/integration/test_credential_rotation.py`, `tests/security/test_admin_hardening.py`,
  `tests/smoke/`.
- `compose` parsea (el compose generado por `task_15_07` es YAML válido y
  referencia los perfiles seccomp/AppArmor); `pre-commit --all-files` verde tras
  el cambio de CI (`task_15_01`).
- **Existencia de artefactos** (checks `generic-shell` de las tareas D): los 6
  runbooks numerados existen; el portal de desarrollador existe; esta entrada de
  changelog existe (`auto_15_28_a`:
  `test -f docs/07-changelog/15-instalador-produccion.md`).
- Tests cuyo enforcement real es humano (kernel seccomp/AppArmor, Vault vivo,
  Redis + MFA, instalación/desinstalación/restore reales, e2e Playwright): se
  entregan **escritos + validados estructuralmente** y marcados como verificación
  humana.

## Pendiente / reservado al humano

### Tareas reservadas al humano (NO hechas — fuera del alcance construible)

1. **`task_15_27` — Pentest externo (auditoría profesional). NO hecha.** Es una
   auditoría de seguridad **profesional externa** sobre un stack vivo; su
   entregable es `docs/05-architecture-decisions/0099-external-pentest-results.md`
   (el slot **0099 está reservado** a esta tarea — por eso los ADRs de este plan
   usan **0039–0042**, no 0099). La mitad **automática** del pentest ya está
   construida (`task_15_14` + `internal-pentest-methodology.md`); la externa la
   ejecuta y firma el humano.
2. **`task_15_29` — Release v1.0.0. NO hecha.** Crea el git tag `v1.0.0`. Es el
   acto de release final, **propiedad del humano**, y depende de que el pentest
   externo cierre.

> El plan queda en **`in_progress`** (NO `completed` ni
> `pending_human_validation`): **no todas las tareas están hechas** —faltan las 2
> reservadas al humano—. No se ha tocado el checkbox de `15_27` ni de `15_29`, ni
> el frontmatter del estado.

### Gate humano cross-plan (heredado del override)

3. **Plan 15 se construyó sobre una pila no mergeada, bajo override humano.** Los
   **Planes 07–14 siguen en `status: pending_human_validation`**: les faltan sus
   tests humanos (`human_07_*` … `human_14_*`) **y** el **merge del PR a `main`**.
   El gate `blocking_plan` de este plan (todos los planes anteriores `completed`)
   se **saltó por un override humano explícito**. La validación humana y el merge
   de esos planes —y de éste— son **propiedad del humano**.

### Gaps conocidos (escritos / validados estructuralmente, no ejecutados en vivo)

4. **Specs Playwright e2e escritos-no-ejecutados.** `installer-wizard.spec.ts`,
   `installer-steps.spec.ts`, `installer-summary.spec.ts`,
   `installer-progress.spec.ts`, `installer-prereqs.spec.ts`,
   `installer-finalize.spec.ts` (instalador) y `dev-portal.spec.ts` (portal)
   están **escritos pero PENDIENTES DE VERIFICACIÓN HUMANA**: el runtime
   node-playwright de este entorno no trae navegador. El backend se verificó por
   pytest.
5. **Instalación / desinstalación / reinstalación reales son tests humanos / de
   stack.** Toda acción host-touching (escribir `.env` / compose / árbol `/data`,
   `docker compose up`, bootstrap + unseal de Vault, self-destruct del
   contenedor) vive detrás de seams mockeados en CI. Su ejecución real son los
   Tests Humanos `human_15_01` (instalación virgen) / `human_15_02` (CLI
   desatendido) / `human_15_04` (reinstalación preservando datos).
6. **Enforcement de kernel seccomp + AppArmor es test humano.** CI valida los
   perfiles **estructuralmente**; el enforcement real exige un host Linux con
   seccomp/AppArmor cargado (no Docker Desktop/Windows) y un harness de escape —
   documentado en `internal-pentest-methodology.md` §5 + `apparmor-profiles.md`,
   cubierto por `human_15_03` y el pentest externo `task_15_27`.
7. **Rotación contra un Vault vivo es test humano.** CI prueba la mecánica del
   motor con un cliente fake y el lever contra el Postgres de test; la rotación
   real (database secrets engine acuñando roles efímeros + revocación de leases)
   exige un Vault vivo (parte de `human_15_03` + runbook `05-key-rotation.md`).
8. **Hardening admin con Redis + MFA reales es test humano.** CI valida los
   predicados + la dependencia con fakes; el enforcement real (MFA obligatorio,
   IP allowlist, sesiones de 15 min) exige Redis + el flujo MFA vivos.
9. **Gap FX del Plan 11 sigue abierto.** El toggle de moneda del tenant /
   conversión FX (`exchange_rates` / `display_currency`) **no tiene tarea
   numerada y NO se construyó** (gap heredado del Plan 11, también flageado por el
   Plan 14). No es alcance de este plan; los costes siguen en **USD canónico**.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano). El merge depende
del cierre de las 2 tareas reservadas (`task_15_27` pentest externo + `task_15_29`
release v1.0.0) y del gate humano cross-plan de los Planes 07–14.
