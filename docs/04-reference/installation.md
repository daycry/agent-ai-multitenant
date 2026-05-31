---
title: Instalación, CLI, perfiles y endurecimiento de producción — Referencia
audience: operador, system admin, devops, security
phase: 15-instalador-produccion
updated: 2026-05-31
---

# Instalación, CLI, perfiles y endurecimiento de producción — Referencia

Esta página documenta lo que entrega el **Plan 15**: el **instalador** (wizard de
9 pasos + CLI desatendido), las **plantillas por perfil**, los **artefactos que
genera** en disco, los scripts de **uninstall** / **reinstall**, y el
**endurecimiento de seguridad** de producción (seccomp + AppArmor + rotación de
credenciales + hardening del panel admin). Para los procedimientos paso a paso
ver los **runbooks** enlazados; para el fondo de cada decisión ver los **ADRs**
enlazados.

> **Alcance.** Docker Compose en una sola máquina (no Kubernetes, no
> multi-máquina). El instalador NO forma parte del stack runtime: es un
> contenedor temporal que se autodestruye tras instalar.

## El instalador

El instalador vive en `apps/installer/` y se ejecuta como un **contenedor
separado** (`docker-compose.installer.yml`) que sirve la UI del wizard sobre
loopback. Toda la orquestación real (prereqs, generación de config, `docker
compose up`, bootstrap de Vault, seed del tenant, finalize) vive en el **backend
Python** `installer_backend` detrás de **seams** inyectables, de modo que el
**wizard** y el **CLI desatendido** corren la **misma** orquestación.

### Wizard de 9 pasos

| Paso | Qué hace                                                                                 | Módulo backend            |
| ---- | ---------------------------------------------------------------------------------------- | ------------------------- |
| 1    | Validación de prerequisitos (Docker, Compose v2, RAM, disco, GPU)                        | `prereqs.py`              |
| 2-6  | Captura de config (sistema, recursos/GPU, almacenamiento, providers LLM, tenant inicial) | `wizard.py` / `config.py` |
| 7    | Resumen + confirmación con preview de recursos                                           | `preview` (front)         |
| 8    | Instalación con progreso + logs en tiempo real                                           | `install.py`              |
| 9    | Credenciales mostradas **una vez** + autodestrucción del installer                       | `finalize.py`             |

El paso 9 revela las credenciales del admin inicial y las **unseal keys de
Vault exactamente una vez** y **sin recuperación**: el operador es responsable de
guardarlas. Acto seguido el contenedor installer **se autodestruye**. Ver
[ADR 0039](../05-architecture-decisions/0039-installer-autodestructivo-secretos-csprng-prod-guard.md)
y el runbook
[01-installation-from-scratch.md](../06-runbooks/01-installation-from-scratch.md).

### CLI desatendido

```bash
# Copia un perfil, edítalo, y pásalo al instalador headless:
cp scripts/install-profiles/recommended.yaml install.yaml
# (edita install.yaml: dominio, providers, sizing, tenant inicial…)
./scripts/install.sh --config install.yaml
```

`install.sh` es un wrapper fino sobre `python -m installer_backend.cli install`.
Corre la **misma** orquestación que el wizard, headless. Códigos de salida
estables:

| Código | Significado                                                          |
| ------ | -------------------------------------------------------------------- |
| 0      | Instalación completada                                               |
| 1      | Error de uso (args mal / falta `--config`)                           |
| 2      | Error de config (`install.yaml` inválido; NO se provisiona nada)     |
| 3      | Error de prereq (un prerequisito falló; aborta ANTES de provisionar) |
| 4      | Error de provisión (un paso falló; el stack puede quedar a medias)   |
| 5      | Abortado (el operador declinó una confirmación destructiva)          |

Los secretos + unseal keys se imprimen a stdout **una vez** (sin recuperación;
nunca a un fichero de log).

### Plantillas por perfil

Bajo `scripts/install-profiles/`:

| Perfil             | Para qué                                               |
| ------------------ | ------------------------------------------------------ |
| `minimal.yaml`     | Instalación mínima (recursos ajustados, sin GPU)       |
| `recommended.yaml` | Instalación recomendada para la mayoría de despliegues |
| `gpu.yaml`         | Instalación con GPU NVIDIA habilitada                  |

Los perfiles de producción **no llevan marcadores de secreto-dev** (invariante
del pentest interno).

## Artefactos generados

El instalador materializa en disco (a través de seams; nunca commiteados):

| Artefacto                                | Generador                            | Contenido                                                                                                                       |
| ---------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `docker-compose.yml`                     | `compose_generator.py`               | Stack según las opciones del wizard + referencias `security_opt` (seccomp/AppArmor)                                             |
| `.env`                                   | `config_generators.py`               | Variables de entorno con **secretos CSPRNG** (≥ 256 bits, sin marcadores de secreto-dev → pasa el guard de prod del Plan 06.14) |
| `config/global.yaml`                     | `config_generators.py`               | Config no secreta (dominio, environment, providers, sizing, almacenamiento, idiomas)                                            |
| `/data/agent-platform/`                  | `config_generators.py` (plan) + seam | Árbol de directorios + permisos POSIX (repos, worktrees, dep-cache, object-store, Vault, monitoring)                            |
| Vault: init + unseal + KV v2 + políticas | `vault_bootstrap.py`                 | Bootstrap de Vault (unseal keys mostradas una vez)                                                                              |

Los secretos generados son **únicos por instalación** y de alta entropía. Ver
[ADR 0039](../05-architecture-decisions/0039-installer-autodestructivo-secretos-csprng-prod-guard.md).

## Uninstall y reinstall

### `uninstall.sh` — tear-down con doble confirmación

```bash
# Headless (datos preservados por defecto):
./scripts/uninstall.sh --confirm-name <deployment> --yes
# Wipe del árbol de datos (necesita su propia confirmación extra):
./scripts/uninstall.sh --confirm-name <deployment> --yes --purge-data
```

Exige teclear el **nombre exacto** del deployment (`--confirm-name`) **y**
confirmar (`--yes`): uno solo no basta. Los datos se **preservan por defecto**;
`--purge-data` los borra y necesita una confirmación extra.

### `reinstall.sh` — reinstalación con preservación opcional

```bash
# PRESERVE (default): conserva datos + reusa secretos/unseal keys existentes
./scripts/reinstall.sh --config install.yaml
# FRESH: borra el árbol y reinstala desde cero (misma doble confirmación)
./scripts/reinstall.sh --config install.yaml --fresh --confirm-name <deployment> --yes
```

En modo **PRESERVE** el reuso de los secretos + unseal keys existentes es
**obligatorio**: regenerarlos huérfanaría los datos cifrados (Postgres/MinIO +
el árbol cifrado por Vault están ligados a ellos).

## Endurecimiento de producción

> **Regla.** El **enforcement real del kernel / Vault / Redis NO corre en CI**.
> Cada control se entrega como **perfil + cableado en compose/runtime** y se
> **valida estructuralmente** (suites de seguridad que fallan en rojo solo ante
> un retroceso de hardening). El enforcement real es **test humano** +
> **pentest externo** (`task_15_27`).

### Aislamiento de contenedores (seccomp + AppArmor)

| Capa     | Servicios confiables (plataforma)                     | Runtime no confiable (agent/test)                          | ADR                                                                                       |
| -------- | ----------------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| seccomp  | seccomp **por defecto de Docker** (no se sobrescribe) | `docker/seccomp/agent-runtime.json` (subconjunto estricto) | [0040](../05-architecture-decisions/0040-seccomp-apparmor-default-deny-por-contenedor.md) |
| AppArmor | `docker/apparmor/agentic-default.profile`             | `docker/apparmor/agent-runtime.profile` (más estricto)     | [0040](../05-architecture-decisions/0040-seccomp-apparmor-default-deny-por-contenedor.md) |

> **Revisado (ADR 0040, 2026-05-31).** Los servicios confiables usan el
> **seccomp por defecto de Docker** + `no-new-privileges` + `cap_drop` +
> AppArmor — **no** un perfil hand-rolled (aplicarlo rompía postgres/vault/minio).
> `docker/seccomp/default.json` se conserva como perfil **opt-in** de
> endurecimiento extra, no cableado por defecto. La allowlist estricta
> (`agent-runtime.json`) es para el runtime no confiable que pina el worker.

Cada servicio confiable pina `no-new-privileges` + `apparmor=agentic-default`
vía `security_opt`; el generador de compose del instalador emite la misma
postura. Cargar y verificar los perfiles AppArmor en el host: runbook
[apparmor-profiles.md](../06-runbooks/apparmor-profiles.md). Metodología del
pentest interno:
[internal-pentest-methodology.md](../06-runbooks/internal-pentest-methodology.md).

### Rotación automática de credenciales

`workers/credential_rotation.py` rota con el **database secrets engine de Vault**
(credenciales PostgreSQL efímeras con TTL corto) + los estáticos (MinIO / JWT),
vía un **job Celery beat** con cadence en config y un **lever `cred_rotation_enabled`**
en vivo. Es **fail-safe** (un fallo nunca tira el sistema; dispara alerta). Ver
[ADR 0041](../05-architecture-decisions/0041-rotacion-credenciales-vault-dynamic-secrets.md)
y el runbook
[05-key-rotation.md](../06-runbooks/05-key-rotation.md).

### Hardening del panel admin (solo prod)

`api_server/auth/admin_hardening.py` aplica tres controles **solo en
staging/prod** (dev queda usable, ningún no-admin se ve afectado):

- **MFA obligatorio** (forced-enrollment gate).
- **IP allowlist** por CIDR (semántica de api-tokens).
- **Sesiones cortas** (15 min por defecto).

Ver [ADR 0042](../05-architecture-decisions/0042-hardening-panel-admin-mfa-ip-allowlist-sesiones-cortas.md).

## Runbooks operativos

| Runbook                                                                           | Cuándo                                                      |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| [01-installation-from-scratch.md](../06-runbooks/01-installation-from-scratch.md) | Instalar desde cero (wizard o CLI)                          |
| [02-troubleshooting.md](../06-runbooks/02-troubleshooting.md)                     | Diagnóstico de fallos tras instalar o en operación          |
| [03-system-upgrade.md](../06-runbooks/03-system-upgrade.md)                       | Actualizar imágenes + esquema de forma reversible           |
| [04-disaster-recovery.md](../06-runbooks/04-disaster-recovery.md)                 | DR completo o restore selectivo por tenant                  |
| [05-key-rotation.md](../06-runbooks/05-key-rotation.md)                           | Rotar unseal keys + credenciales y revocación de emergencia |
| [06-capacity-management.md](../06-runbooks/06-capacity-management.md)             | Escalar workers/colas, sizing y capacity de GPU             |

## Verificación y pendientes

- Backend del instalador mypy-strict-clean; orquestación detrás de seams →
  tests deterministas en CI **sin** Docker / disco / Vault reales.
- Suites verdes: `tests/integration/test_installer_*.py`,
  `tests/unit/test_compose_generator.py`, `tests/unit/test_config_generators.py`,
  `tests/integration/test_vault_bootstrap.py`, `tests/integration/test_cli_install.py`,
  `tests/integration/test_uninstall.py`, `tests/integration/test_reinstall.py`,
  `tests/security/*`, `tests/integration/test_credential_rotation.py`,
  `tests/smoke/`.
- **Pendiente / reservado al humano.** La instalación / desinstalación / restore
  reales, el enforcement de kernel (seccomp/AppArmor), la rotación contra un Vault
  vivo, el hardening admin con Redis + MFA reales y los specs Playwright
  (instalador + portal) son **tests humanos / de stack**. El **pentest externo**
  (`task_15_27`, genera el ADR 0099) y el **release v1.0.0** (`task_15_29`) están
  **reservados al humano**. Detalle en el
  [changelog del Plan 15](../07-changelog/15-instalador-produccion.md).
