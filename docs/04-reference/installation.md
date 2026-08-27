---
title: Instalación, CLI, perfiles y endurecimiento de producción — Referencia
audience: operador, system admin, devops, security
phase: 15-instalador-produccion
updated: 2026-08-01
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
Python** `installer_backend` detrás de **seams** inyectables.

> ⚠️ **De los dos frontales, sólo uno aprovisiona.** El diseño de seams permitía
> que wizard y CLI corrieran la misma orquestación; **hoy no la corren**. El CLI
> cablea los bindings reales por defecto; el wizard HTTP se quedó en los seams de
> simulación (`main.py`: `FakeStepExecutor`, `StubPrereqChecker`,
> `StubInstallerLifecycle`). Esta página describía la versión que se quería, no
> la que hay; lo que sigue distingue una de otra en cada apartado.

### Wizard de 9 pasos — SIMULACIÓN (no instala)

| Paso | Qué hace                                                                                 | Módulo backend            |
| ---- | ---------------------------------------------------------------------------------------- | ------------------------- |
| 1    | Validación de prerequisitos (Docker, Compose v2, RAM, disco, GPU)                        | `prereqs.py`              |
| 2-6  | Captura de config (sistema, recursos/GPU, almacenamiento, providers LLM, tenant inicial) | `wizard.py` / `config.py` |
| 7    | Resumen + confirmación con preview de recursos                                           | `preview` (front)         |
| 8    | Instalación con progreso + logs en tiempo real                                           | `install.py`              |
| 9    | Credenciales mostradas **una vez** + autodestrucción del installer                       | `finalize.py`             |

La tabla describe la **intención** de cada paso. Sobre HTTP, hoy: los pasos 2-7
capturan config de verdad, y los pasos 1, 8 y 9 corren contra stubs. En concreto
el paso 9 ejecuta toda la ceremonia del revelado —una vez, sin recuperación,
autodestrucción incluida— sobre credenciales y unseal keys **generadas al vuelo y
tiradas** (`main.py::build_install_credentials`, que lo dice en su propio
docstring). **No abren nada.** Apuntarlas es apuntar ruido, y el peligro está en
que la ceremonia es indistinguible de la real: mismo aviso de «se muestran una
sola vez», misma urgencia.

Cablear el wizard al ejecutor real (plumbing de `compose_dir`/`cfg`/`secrets` por
request + una guarda de simulación en el revelado) es un follow-up de la UI del
instalador (prod-09). El diseño de la ceremonia —que sí es el bueno— está en
[ADR 0039](../05-architecture-decisions/0039-installer-autodestructivo-secretos-csprng-prod-guard.md);
el estado real de cada camino, en el runbook
[01-installation-from-scratch.md](../06-runbooks/01-installation-from-scratch.md).

### CLI desatendido — el camino REAL

```bash
# Copia un perfil, edítalo, y pásalo al instalador headless:
cp scripts/install-profiles/recommended.yaml install.yaml
# (edita install.yaml: dominio, providers, sizing, tenant inicial…)
./scripts/install.sh --config install.yaml
```

`install.sh` es un wrapper fino sobre `python -m installer_backend.cli install`.
`--config` es **obligatorio**: sin él sale con código 1 (`USAGE`) y no arranca
ninguna UI. Éste es el frontal que cablea los bindings reales, y el que **aborta
con código 4 (`PROVISION`)** si detecta un seam de simulación sin `--dry-run`
(`cli._assert_real_install_seams`) — no existe la instalación falsa silenciosa.
Códigos de salida estables:

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

### Dónde se escribe, y por qué eso decide qué más hay que escribir

El compose generado **no se escribe en el repo**: se escribe en la **raíz de
datos** (`cli.py` → `compose_dir = config.storage.data_root`,
`/data/agent-platform` por defecto) y todo `docker compose` se lanza con `cwd`
ahí. Por tanto cada **ruta relativa** `./algo` del compose generado resuelve
contra `/data/agent-platform/…`, donde no hay ningún checkout — **clonar el
repositorio no cambia nada**.

Por eso el instalador escribe también los auxiliares que ese compose monta, en un
subárbol único `stack/` y desde su propio paquete
(`installer_backend.stack_assets`, copia guardada byte a byte contra `docker/`):

| Ruta del compose generado  | Qué es                                               |
| -------------------------- | ---------------------------------------------------- |
| `./stack/postgres/init`    | `CREATE EXTENSION vector` + roles `migrations`/`app` |
| `./stack/vault/config.hcl` | Configuración de Vault (bind de **fichero**)         |
| `./stack/seccomp`          | Perfiles de los runtimes no confiables               |
| `./stack/egress-proxy`     | Contexto de build del proxy de salida a los LLM      |
| `./stack/registry-proxy`   | Contexto de build del proxy a registros de paquetes  |
| `./stack/monitoring/**`    | Prometheus + Alertmanager + Grafana (overlay)        |
| `./caddy/Caddyfile`        | Generado por instalación (dominio + modo TLS)        |

**Por qué esto no era opcional, y por qué `stack/`.** Hasta el 2026-08-27 esas
seis familias vivían sólo en el árbol `docker/` y no viajaban. El modo de fallo no
avisaba donde estaba la causa: Docker materializa como directorio vacío el lado
host ausente de un bind, así que `./postgres/init` se creaba **dentro** del PGDATA
—`initdb` encontraba un directorio no vacío y los SQL de inicialización no corrían
jamás, dejando un Postgres `healthy` **sin `pgvector`**— y `./vault/config.hcl`
acababa siendo un directorio donde el binario espera un fichero. El subárbol
`stack/` existe justo para que ninguna ruta del instalador pueda volver a
aterrizar dentro del almacén de datos de otro servicio.

Está medido, ruta por ruta y con file:line, en el
[ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
§«La avería que no estaba escrita», junto a la otra avería independiente del mismo
camino: el `docker compose pull` va contra un tag que no existe
([ADR 0160](../05-architecture-decisions/0160-versionado-de-la-plataforma.md)).
**La reparación está en curso**, con una guarda ejecutable que deriva del código
—no de una lista escrita a mano, que envejece en cuanto alguien añade un
montaje— tanto el conjunto de rutas que el compose pide como el que la
instalación produce. Sin fechas: el estado vivo es el del ADR 0161.

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

## Imágenes de runtime y hosts sin salida a internet

Las **14 imágenes de runtime** (`agent-runtime-<slug>`) son donde se ejecutan los
tests del código de los tenants, y desde el
[ADR 0148](../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md)
se distribuyen **publicadas y fijadas por digest** en vez de construirse en cada
host. El catálogo las referencia como
`ghcr.io/daycry/agent-runtime-<slug>:<versión>@sha256:<digest>`, y el
worker **descarga por digest o aborta la tarea**: nunca cae a una imagen local
con el mismo tag, porque eso es justo lo que hacía irrepetible el sandbox.

### Requisito de red del host

| Necesita                          | Para qué                                                   |
| --------------------------------- | ---------------------------------------------------------- |
| Alcanzar `ghcr.io`                | Descargar las 14 imágenes de runtime y las 5 de plataforma |
| `docker login ghcr.io` (opcional) | Solo si los packages de la organización no son públicos    |

La descarga ocurre **la primera vez que se usa cada runtime**, no en la
instalación: una plataforma que solo ejecute proyectos PHP nunca baja la imagen
de .NET. Una vez descargada por digest, el worker no vuelve al registry (un
digest es direccionable por contenido: lo que está en local **es** lo correcto),
así que una caída temporal de GHCR no para los runs de los runtimes ya usados.

### Host air-gapped: importación manual

El registry self-hosted como servicio del stack (opción b del ADR 0148) está
**documentado y NO construido** a propósito: no existe todavía ninguna
instalación sin salida a internet, y montar un `registry:2` para un caso
hipotético añadiría un servicio que operar, respaldar y asegurar para nadie. El
día que aparezca, el camino es éste y no hay que rediseñarlo con prisa.

**Lo que NO funciona**, y conviene saberlo antes de intentarlo: `docker save` +
`docker load` **no conserva la referencia por digest**. La imagen llega con el
mismo contenido pero sin `RepoDigests`, así que el `pull` por digest del worker
seguirá fallando — y el worker abortará, que es lo correcto. Retaguear con
`docker tag` no arregla nada: el digest sigue sin resolver.

Hay dos caminos que sí funcionan; los dos preservan el digest, que es lo único
que hace auditable qué se ejecutó.

**(a) Mirror levantado a mano (recomendado).** Un `registry:2` en la red interna,
alimentado desde una máquina con salida a internet. El digest del manifiesto
**no cambia** al copiarlo entre registries, así que las referencias del catálogo
siguen siendo válidas:

```bash
# En la máquina con salida (necesita `crane` o `skopeo`; ambos copian sin recomprimir):
for t in python-pytest node-jest node-vitest node-playwright php-phpunit php-pest \
         go-test java-maven java-gradle ruby-rspec rust-cargo dotnet-test \
         generic-shell generic-http; do
  crane copy "ghcr.io/daycry/agent-runtime-${t}:v1" \
             "registry.interna:5000/agentic-platform/agent-runtime-${t}:v1"
done
```

Y en el host, en el `.env` del stack:

```bash
RUNTIME_IMAGE_REGISTRY=registry.interna:5000/agentic-platform
```

Esa variable reapunta **solo el repositorio**, conservando versión y digest.
Reapuntar el registry no debilita la garantía: si el mirror sirve otra cosa, el
pull por digest falla y la tarea aborta.

**(b) `docker save` / `load` + push al mirror.** Si el aire está tan cortado que
ni `crane` puede cruzarlo, el tar viaja en un soporte físico, pero el último paso
tiene que ser un `push` a un registry interno para que el digest vuelva a
resolver:

```bash
# Máquina con salida — tirar POR DIGEST (no por tag) y empaquetar:
docker pull "ghcr.io/daycry/agent-runtime-python-pytest@sha256:<digest>"
docker save -o python-pytest.tar "ghcr.io/daycry/agent-runtime-python-pytest@sha256:<digest>"

# Máquina interna — cargar, etiquetar para el mirror y empujar:
docker load -i python-pytest.tar
docker tag <IMAGE_ID> registry.interna:5000/agentic-platform/agent-runtime-python-pytest:v1
docker push registry.interna:5000/agentic-platform/agent-runtime-python-pytest:v1
```

> **Comprobación obligatoria.** El `push` recalcula el manifiesto y **puede
> cambiar el digest** (si el daemon recomprime capas). Compara el digest
> resultante con el del catálogo antes de dar el mirror por bueno:
>
> ```bash
> docker buildx imagetools inspect \
>   registry.interna:5000/agentic-platform/agent-runtime-python-pytest:v1 \
>   --format '{{.Manifest.Digest}}'
> ```
>
> Si no coincide con el de
> `packages/shared-test-runtimes/src/shared_test_runtimes/runtime_images.json`,
> el camino (a) es el único válido: **no se edita el manifiesto a mano** para
> hacerlo cuadrar. Ese fichero lo escribe el pipeline de release, y un digest
> puesto a mano congela sus CVEs sin que nada lo refresque (ADR 0148,
> condición 1).

Los digests vigentes se consultan en ese mismo manifiesto:

```bash
cat packages/shared-test-runtimes/src/shared_test_runtimes/runtime_images.json
```

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
| [01-installation-from-scratch.md](../06-runbooks/01-installation-from-scratch.md) | Instalar desde cero (CLI real; el wizard simula)            |
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
