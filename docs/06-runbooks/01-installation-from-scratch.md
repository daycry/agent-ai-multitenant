---
title: Instalación desde cero
docs_language: es
audience: operador, system admin
updated: 2026-05-31
---

# Runbook — Instalación desde cero

Instalar la plataforma agéntica en una **máquina virgen** (Docker Compose
en una sola máquina), por cualquiera de los dos caminos que entrega el
Plan 15:

- **CLI desatendido** (camino REAL) — `scripts/install.sh --config
install.yaml`, la orquestación headless desde un fichero YAML. Aprovisiona
  **de verdad** (escribe config, levanta el stack, migra, bootstrapea Vault y
  siembra el tenant + catálogo). Es el camino soportado para una instalación
  real (perfiles `minimal` / `recommended` / `gpu`) y para automatización.
- **Wizard** — UI temporal y autodestructiva, nueve pasos guiados
  (`apps/installer`). ⚠️ **HOY es una SIMULACIÓN**: conduce los nueve pasos por
  HTTP+SSE pero el `StepExecutor` del wizard **no aprovisiona nada** y las
  credenciales que muestra **no son reales**. Cablear el wizard al ejecutor real
  es un follow-up de la UI del instalador (prod-09). Úsalo para validar el flujo
  y la detección de GPU, **no** como instalación real.

> El camino REAL de instalación es el **CLI** (`scripts/install.sh`), que cablea
> los bindings reales por defecto y **aborta con error** si detecta seams de
> simulación sin `--dry-run` (no existe una instalación falsa silenciosa —
> deploy-1). El wizard HTTP queda como simulación hasta prod-09.

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). No
> Kubernetes, no multi-máquina, no HA multi-instancia.

## Cuándo

- Primera puesta en marcha del sistema en un host nuevo.
- Provisionar un entorno de evaluación/staging desde cero.

Para reinstalar sobre datos existentes (preservándolos), usa
`scripts/reinstall.sh` (NO este runbook borra nada por sí mismo); para
desinstalar, `scripts/uninstall.sh` con su doble confirmación.

## Prerequisitos

El paso 1 del wizard y el gate previo del CLI validan todo esto y abortan
**antes de aprovisionar** si algo falla
(`apps/installer/backend/src/installer_backend/prereqs.py`):

| Prerequisito        | Mínimo            | Notas                                                                                                                             |
| ------------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Docker Engine       | **24.0+**         | cap-drop / seccomp / rootfs read-only estables desde 24.x.                                                                        |
| Docker Compose      | **v2.21+**        | `up --wait` con el one-shot `migrations` (service_completed_successfully) es fiable desde 2.21. NO el `docker-compose` v1 legado. |
| RAM total           | **8 GiB** (suelo) | PostgreSQL+pgvector, Redis, MinIO, Vault, API, workers.                                                                           |
| Disco libre (datos) | **50 GiB**        | Imágenes + `pgdata` + object storage + backups.                                                                                   |
| Puertos 80/443      | **libres**        | Única superficie publicada = el proxy Caddy (ADR 0061). El prereq bloquea si están ocupados.                                      |
| GPU NVIDIA          | opcional          | Detección automática; habilita el perfil `gpu` (servicio `ollama`).                                                               |

Además, antes de empezar:

- Acceso a la máquina con permiso para `docker compose` y para escribir en
  la raíz de datos (`/data/agent-platform/` por defecto).
- El repositorio del sistema desplegado en el host.
- Al menos **un proveedor LLM** del catálogo cerrado (ADR 0021: Claude
  Agent SDK, GitHub Copilot, Azure AI Foundry vía APIM, Ollama) con sus
  credenciales a mano — el validador exige ≥ 1 proveedor habilitado.
- Un sitio **seguro** donde guardar las credenciales de un solo uso y las
  unseal keys de Vault (gestor de secretos, sobre sellado…). Ver
  [Guardar las credenciales](#guardar-las-credenciales-y-las-unseal-keys).

## Camino A — Wizard (9 pasos)

> ⚠️ **SIMULACIÓN (hoy).** El wizard recorre los nueve pasos pero **no
> aprovisiona** el stack y las credenciales del paso 9 **son falsas**. Para una
> instalación real usa el **Camino B (CLI)**. El cableado real del wizard es un
> follow-up de prod-09.

El instalador es un contenedor separado que sirve la UI temporal y se
**autodestruye** al terminar (`apps/installer`,
`docker-compose.installer.yml`). Arráncalo y abre la UI en el navegador:

```bash
docker compose -f apps/installer/docker-compose.installer.yml up -d
# Abre la URL que imprime (UI temporal del instalador).
```

Los nueve pasos (state machine en
`apps/installer/backend/src/installer_backend/wizard.py`):

1. **Bienvenida** — presentación y comprobación de que el host es elegible.
2. **Configuración básica** — dominio del sistema + entorno
   (`development` / `staging` / `production`).
3. **Recursos / GPU** — réplicas y memoria por worker; detección
   automática de GPU NVIDIA (si la hay, ofrece habilitar el perfil `gpu`).
4. **Almacenamiento** — raíz de datos (`/data/agent-platform`), bucket y
   credenciales de MinIO.
5. **Providers LLM** — habilitar y configurar los proveedores del catálogo
   cerrado (ADR 0021). Hay que habilitar al menos uno.
6. **Tenant inicial** — nombre del tenant y email del usuario
   administrador.
7. **Resumen** — preview de recursos y de lo que se va a generar;
   **último punto** que un humano confirma antes del paso irreversible.
8. **Instalación** — progreso + logs en tiempo real (SSE) mientras corre
   el pipeline de aprovisionamiento (ver
   [Qué se genera](#qué-se-genera)).
9. **Listo** — credenciales del administrador + unseal keys de Vault
   mostradas **UNA sola vez**, y autodestrucción del contenedor installer.

Tras el paso 1 (validación de prerequisitos) cada FAIL bloqueante muestra
su mensaje de remediación y no deja avanzar.

## Camino B — CLI desatendido

El CLI es el **gemelo headless** del wizard: corre el mismo pipeline desde
un `install.yaml`, sin navegador
(`apps/installer/backend/src/installer_backend/cli.py`,
`scripts/install.sh`).

```bash
./scripts/install.sh --config install.yaml
# equivale a: python -m installer_backend.cli install --config install.yaml
```

> **Real vs simulación (`--dry-run`).** Sin `--dry-run`, el CLI aprovisiona
> **de verdad**: escribe el `docker-compose.yml` + `.env` + `caddy/Caddyfile`
> bajo el `data_root`, hace `docker compose pull` / `up -d --wait`, aplica las
> migraciones (servicio one-shot `migrations`), bootstrapea Vault y siembra el
> tenant inicial. Requiere Docker + Compose v2 en el host (el `PrereqChecker`
> lo verifica, incluido que los puertos **80/443** estén libres — la única
> superficie publicada es el proxy Caddy, ADR 0061).
>
> `python -m installer_backend.cli install --config install.yaml --dry-run`
> ejecuta una **SIMULACIÓN** explícita (banner visible): NO toca el host y las
> credenciales mostradas son **FALSAS**. Úsalo solo para validar el `install.yaml`
> y el flujo, **nunca** como instalación real. Un instalador con seams de
> simulación cableados **sin** `--dry-run` **aborta** con código de salida
> `PROVISION` (4) y un mensaje inequívoco — no existe una instalación falsa
> silenciosa (deploy-1).
>
> El **wizard HTTP** (`/api/install/stream`) sigue usando el ejecutor de
> simulación; el camino REAL de instalación es este CLI (`scripts/install.sh`).
> Cablear el wizard al ejecutor real es un follow-up de la UI del instalador
> (prod-09).
>
> **Vault tras el install (alcance prod-01).** El paso de Vault solo
> **orquesta** (init → unseal → KV v2 → políticas): Vault queda inicializado
> pero **sin secretos escritos** y los servicios arrancan leyendo el `.env`
> generado (0600) como fuente de secretos. Escribir los valores en el KV y
> mintar tokens por servicio es de **prod-10** — no asumas que los secretos ya
> viven en Vault tras este install.
>
> **Si el install falla después del paso de Vault.** Vault ya quedó
> inicializado, así que **re-ejecutar `install.sh` no vuelve a revelar** las
> unseal keys / root token (no hay recuperación: se mostraron una vez). Para
> reintentar limpio: `scripts/uninstall.sh --purge-data` (borra `vault/file`) y
> reinstala, o usa `scripts/reinstall.sh --fresh`.

### Perfiles

En `scripts/install-profiles/` hay tres `install.yaml` completos de
partida (`task_15_11`). Copia el que más se acerque, sustituye los
**placeholders de secretos** (`CHANGE_ME_…`, `api_key`, tokens OAuth) por
valores propios de alta entropía y **no comitees** el fichero resultante:

| Perfil                              | Para qué                                     | Recursos          | GPU | Proveedores                              |
| ----------------------------------- | -------------------------------------------- | ----------------- | --- | ---------------------------------------- |
| `install-profiles/minimal.yaml`     | Máquina mínima viable / evaluación / pruebas | 1 worker, 2 GiB   | No  | Ollama (1, el mínimo que exige ADR 0021) |
| `install-profiles/recommended.yaml` | Producción media en máquina razonable        | 4 workers, 8 GiB  | No  | Azure AI Foundry (APIM) + Ollama         |
| `install-profiles/gpu.yaml`         | Máquina con GPU NVIDIA, inferencia local     | 6 workers, 16 GiB | Sí  | Ollama sobre la GPU                      |

El `install.yaml` mapea 1:1 a los pasos 2-6 del wizard (`system`,
`resources`, `storage`, `providers`, `tenant`, `ports`).

### Fases y códigos de salida

El CLI corre, en orden: **prereqs** (gate; FAIL aborta antes de tocar
nada) → `generate_config` → `pull_images` → `start_stack` →
`bootstrap_vault` → `seed_tenant` → **finalize** (revelado único). Cada
clase de fallo mapea a un código de salida distinto para que tu
automatización pueda ramificar según **por qué** falló:

| Código | Significado | Estado del stack                                                     |
| ------ | ----------- | -------------------------------------------------------------------- |
| `0`    | OK          | Instalación completada.                                              |
| `1`    | USAGE       | Argumentos mal / falta `--config`.                                   |
| `2`    | CONFIG      | `install.yaml` malformado o inválido — **sin** aprovisionar.         |
| `3`    | PREREQ      | Falló un prerequisito — aborta **antes** de aprovisionar.            |
| `4`    | PROVISION   | Falló un paso de aprovisionamiento — el stack puede quedar a medias. |
| `5`    | ABORTED     | El operador declinó una confirmación destructiva.                    |

## Qué se genera

El paso de aprovisionamiento (mismo en wizard y CLI) materializa en disco,
con secretos generados por **CSPRNG** (nunca los valores dev por defecto;
los generadores de Fase B son las tareas 15_07/15_08/15_09):

- **`docker-compose.yml`** — el stack según las opciones elegidas
  (`compose_generator.py`, task 15_07): servicios, perfiles (`gpu`,
  monitoring), `security_opt` con los perfiles seccomp/AppArmor de Fase C.
- **`.env`** — todas las variables que leen los servicios (DSNs derivadas,
  secretos de PostgreSQL/MinIO/JWT/SSO/notificaciones/webhooks, marcadores
  de `ENVIRONMENT`). Escrito con permisos `0600`, **nunca** comiteado ni
  logueado en claro (`config_generators.py`, task 15_08). Un `.env` de
  producción de este generador no contiene ningún marcador dev
  (`changeme` / `dev-only` / `minioadmin`) y pasa el guard de Plan 06.14.
- **`config/global.yaml`** — config no secreta (dominio, entorno,
  proveedores habilitados, dimensionado, almacenamiento, idiomas ES+EN).
- **El árbol de datos** bajo la raíz (por defecto `/data/agent-platform/`,
  `build_data_tree_plan`), con permisos POSIX por directorio
  (`0700` para lo que guarda secretos, `0750` el resto):

  ```text
  /data/agent-platform/        (0750)  raíz de datos
  ├── postgres/                (0700)  PGDATA
  ├── redis/                   (0750)  AOF + snapshots
  ├── minio/                   (0750)  object store
  ├── vault/file/              (0700)  backend de Vault (material secreto)
  ├── vault/logs/              (0700)  audit logs de Vault
  ├── clamav/                  (0750)  firmas antivirus
  ├── projects/                (0750)  bare repos por tenant/proyecto
  ├── worktrees/               (0750)  worktrees git por tarea
  ├── dep-cache/               (0750)  caché de dependencias compartida
  ├── backups/                 (0700)  bundles de backup (Plan 12)
  ├── ollama/                  (0750)  modelos locales (solo perfil GPU)
  ├── prometheus/              (0750)  TSDB (solo overlay de monitoring)
  └── grafana/                 (0750)  estado (solo overlay de monitoring)
  ```

- **Bootstrap de Vault** — `init` + `unseal` + KV v2 + políticas iniciales
  (`vault_bootstrap.py`, task 15_09; ver también `scripts/init-vault.sh`).
  Las **unseal keys** y el root token se emiten aquí y se entregan al
  revelado único.

## Guardar las credenciales y las unseal keys

> Punto crítico, irreversible. Las credenciales del administrador y las
> **unseal keys de Vault** se muestran **UNA sola vez** (paso 9 del wizard
> / fase `finalize` del CLI) y **no hay recuperación**: si las pierdes, no
> puedes desellar Vault tras un reinicio ni entrar al panel admin.

- En el **wizard**: aparecen en la pantalla «Listo». Cópialas antes de
  cerrar; el contenedor installer se autodestruye a continuación.
- En el **CLI**: se imprimen a **stdout** exactamente una vez, dentro de
  un bloque marcado. **Nunca** se escriben en un fichero de log por esta
  herramienta. Captúralas en el momento (redirige a un destino seguro, no
  a un log compartido).

Qué guardar y dónde:

- **Unseal keys de Vault** (varias) + **root token**: en un gestor de
  secretos o un sobre sellado, separadas entre custodios distintos.
  Imprescindibles para desellar Vault tras cada reinicio.
- **Usuario + contraseña del administrador** inicial: en tu gestor de
  contraseñas; cámbiala tras el primer login.

La rotación posterior de estas claves está en
[05-key-rotation.md](./05-key-rotation.md).

## Verificación post-instalación

Sigue [health-check.md](./health-check.md) y confirma:

1. **Contenedores sanos** — `docker compose ps`: todos `Up (healthy)`.
2. **Liveness de la API** — `GET /healthz` → `200` (puerto del api-server
   según `ports.api_server` del config; en dev el overlay publica `8001`).
3. **Salud agregada** — `GET /admin/system-health` (JWT de System Admin) →
   `status: ok`, ningún servicio en `down`.
4. **Vault desellado** — accesible y desellado con las unseal keys
   guardadas (ver [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md),
   sección «Desellar»).
5. **Login del administrador** — entra al panel admin con las credenciales
   del revelado único; el tenant inicial existe.
6. **Installer autodestruido** — el contenedor del instalador ya no
   existe (`docker compose -f apps/installer/docker-compose.installer.yml ps`
   no lo muestra).

Esto cubre los tests humanos `human_15_01` (instalación con UI) y
`human_15_02` (modo CLI desatendido) del Plan 15.

## Si algo falla

| Síntoma                                            | Dónde mirar                                                                                          |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Prereq bloqueante (Docker/Compose/RAM/disco)       | El mensaje de remediación del paso 1 / del gate del CLI (`prereqs.py`). Corrige y reintenta.         |
| CLI sale con código ≠ 0                            | Mapea el código (tabla anterior) a la fase: CONFIG (2) no aprovisionó; PROVISION (4) quedó a medias. |
| Un servicio en `Restarting` / no arranca           | [restart-services.md](./restart-services.md) + `docker compose logs <servicio> --tail 50`.           |
| Vault atascado en `Restarting`                     | [`gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md).   |
| Troubleshooting general post-deploy                | [02-troubleshooting.md](./02-troubleshooting.md).                                                    |
| Trampa conocida del toolchain (Docker, asyncpg, …) | [`docs/03-guides/gotchas/`](../03-guides/gotchas/) — busca aquí antes de inventar un fix.            |

Si la instalación quedó a medias (PROVISION) y quieres empezar limpio:
desinstala con `scripts/uninstall.sh` (doble confirmación; conserva los
datos salvo `--purge-data`) y vuelve a instalar.

## Enlaces

- Wizard + backend del instalador: `apps/installer/`.
- Generadores de Fase B: `apps/installer/backend/src/installer_backend/`
  (`compose_generator.py`, `config_generators.py`, `vault_bootstrap.py`,
  `cli.py`).
- Perfiles CLI: `scripts/install-profiles/` (`minimal` / `recommended` /
  `gpu`).
- Tras instalar: [health-check.md](./health-check.md),
  [backups.md](./backups.md), [04-disaster-recovery.md](./04-disaster-recovery.md),
  [05-key-rotation.md](./05-key-rotation.md).
