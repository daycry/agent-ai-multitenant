---
title: Instalación desde cero
docs_language: es
audience: operador, system admin
updated: 2026-08-28
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

> ✅ **El camino real llega al final en una máquina limpia. Medido el
> 2026-08-28**, en el run
> [`33197920542`](https://github.com/daycry/agent-ai-multitenant/actions/runs/33197920542)
> del job [Install E2E](../../.github/workflows/install-e2e.yml): 22 servicios
> `healthy`, migraciones aplicadas, Vault inicializado, tenant sembrado, sus
> credenciales reveladas, el proxy sirviendo HTTPS y el login entrando con la
> credencial revelada. `4 passed`, y no por omisión: el paso «Gate anti-falso-verde»
> (`scripts/check_e2e_install_report.py`) lee el informe JUnit y falla si alguno de
> los cuatro casos no se **ejecutó**.
>
> Merece la pena decir por qué esto no se sabía antes: el test se escribió en junio
> de 2026 y **nunca había corrido**. Estaba gateado por `E2E_INSTALL=1`, ningún
> workflow exportaba la variable y el gate cae en el _setup_ de la fixture, así que
> pytest recogía los cuatro casos, los saltaba y salía 0. Encenderlo costó 24
> ejecuciones y sacó defectos reales —el perfil AppArmor que nadie había aplicado
> jamás, los workers haciendo `chown` de los datos de los demás servicios, el
> almacén de artefactos del marketplace sin cablear, el watchdog con una sonda HTTP
> heredada sin servir HTTP—. De ahí salen los prerrequisitos de
> [Antes de instalar en un host Linux](#antes-de-instalar-en-un-host-linux-lo-que-descubrió-el-e2e).

> ⚠️ **Lo que sigue faltando: publicar las imágenes.** Ese run **construye las seis
> imágenes de plataforma dentro del propio job** y las sirve desde un registro
> **local** (`localhost:5000`). Ejercita el instalador, el compose generado y toda
> la secuencia de arranque; **no** acredita que la instalación funcione con las
> imágenes **publicadas**, porque hoy no hay ninguna en `ghcr.io/daycry`:
> `platform_images.json` declara `digests: {}`, el repositorio no tiene ni una
> etiqueta y `release-images.yml` no ha corrido nunca (medido el 2026-08-28).
>
> Traducido a los tres caminos de
> [04-reference/installation.md](../04-reference/installation.md):
>
> | Camino                        | Estado hoy                                                                                                             |
> | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
> | (1) Sin clonar el repositorio | **No existe todavía** para un usuario real: exige publicar las imágenes, la del **instalador** incluida (ADR 0161)     |
> | (2) Clon + `docker compose`   | Da **infraestructura**, no el producto: el compose canónico levanta PostgreSQL, Redis, MinIO, Vault…, no la plataforma |
> | (3) `scripts/install.sh`      | **Verificado de punta a punta**, con imágenes construidas en local (run `33197920542`)                                 |
>
> Publicar es un **acto del operador** y aquí no se promete fecha: el
> procedimiento, el ensayo obligatorio con un `rc` y el orden duro (las seis
> imágenes pineadas por digest **antes** que la del instalador) están en
> [09-release.md](./09-release.md).
>
> **Lo que ya NO es cierto** —y se deja escrito porque estuvo meses en este
> runbook—: las **rutas relativas** del compose generado ya no rompen la
> instalación. Se reparó el 2026-08-27. El compose se escribe en la **raíz de
> datos** (`/data/agent-platform` por defecto), no en el repo, así que cada
> `./algo` apuntaba a `/data/agent-platform/…`, donde no hay checkout — y **clonar
> el repositorio no lo arreglaba**. De siete familias de rutas relativas el
> instalador escribía una (`./caddy/Caddyfile`); ahora las seis restantes viajan
> dentro del propio paquete del instalador y se escriben bajo `stack/` (ver
> [Qué se genera](#qué-se-genera)). La avería está descrita en el
> [ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md).
>
> Lo que la hacía cara: Docker inventa como **directorio vacío** el lado host
> ausente de un bind, así que `./postgres/init` se creaba dentro del PGDATA,
> `initdb` lo veía no vacío y los SQL de inicialización (`pgvector`, roles) no
> corrían jamás — un Postgres `healthy` **sin `pgvector`**. Lo guardan ahora
> `tests/unit/test_generated_compose_is_installable.py` (deriva del código las
> rutas que el compose pide y las que la instalación produce, y exige que ninguna
> caiga dentro del almacén de otro servicio) y
> `tests/unit/test_installer_ships_stack_assets.py` (que lo que viaja siga siendo
> idéntico a `docker/`).

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). No
> Kubernetes, no multi-máquina, no HA multi-instancia.

> **¿Vas a producción?** Este runbook es la REFERENCIA del instalador
> (caminos, fases, códigos). El paso a paso completo de una instalación de
> producción con dominio propio está en
> [08-instalacion-produccion.md](08-instalacion-produccion.md).

## Cuándo

- Primera puesta en marcha del sistema en un host nuevo.
- Provisionar un entorno de evaluación/staging desde cero.

Para reinstalar sobre datos existentes (preservándolos), usa
`scripts/reinstall.sh --config install.yaml` (NO este runbook borra nada
por sí mismo). El `--config` es obligatorio: una reinstalación regenera
config y compose, así que necesita el mismo `install.yaml`. El flujo
completo —qué pasos ejecuta, cuáles no y por qué— está en
[03-system-upgrade.md](./03-system-upgrade.md#alternativa--reinstalación-con-preservación-de-datos).
Para desinstalar, `scripts/uninstall.sh` con su doble confirmación.

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

### Antes de instalar en un host Linux: lo que descubrió el e2e

Los tres requisitos de abajo **no estaban escritos**, y el gate del CLI no cubre
ninguno —o lo cubre a medias, que en la práctica es peor, porque devuelve verde—.
No salieron de una lista teórica: salieron de ejecutar la instalación de verdad,
las 24 ejecuciones que costó encender el e2e. El
[workflow](../../.github/workflows/install-e2e.yml) los deja resueltos en pasos
propios, con el mismo orden que aquí; en un host real los resuelve el operador.

**1. Los DOS perfiles AppArmor cargados en el kernel del host.** No es opcional
y no es «defensa en profundidad»: **todos** los servicios del compose generado
llevan `security_opt: apparmor=agentic-default`
(`compose_generator.py`, `APPARMOR_DEFAULT_PROFILE`), y un perfil que el kernel
no conoce hace que Docker **aborte el arranque de cada contenedor**. Sin cargar,
no hay instalación.

```bash
sudo apparmor_parser -r -W docker/apparmor/agentic-socket-proxy.profile
sudo apparmor_parser -r -W docker/apparmor/agentic-default.profile
sudo aa-status | grep -E 'agentic-(default|socket-proxy)'   # el kernel confirma
```

El segundo perfil es el que se olvida, y su fallo no se parece a un fallo de
AppArmor: `agentic-default` deniega el socket de Docker a todo el mundo
(Principio 2 — una fuga del socket es un `root` en el host) y el
`docker-socket-proxy` es el único servicio que existe para sostenerlo. Con el
perfil compartido puesto, HAProxy arranca y sus peticiones mueren con
`503 … SC--` porque no alcanza su propio backend, y con él se cae todo lo que
lanza runtimes (medido en el e2e run `33177824929`). La salida fácil —abrir el
socket en el perfil compartido— se lo daría también a los workers, que son
quienes ejecutan código no confiable: un servicio roto cambiado por el agujero
exacto que el Principio 2 cierra.

> **El gate del CLI no te salva de esto.** `check_apparmor` (`prereqs.py`) es
> **opcional**: mira si el LSM está disponible en el kernel, avisa (`WARN`) si no
> lo está, y **nunca** comprueba si los perfiles están cargados. Su remediación
> nombra `agent-runtime.profile`, que es el del sandbox — no el que pinan los
> servicios largos. Un `prereqs` en verde es compatible con un `up` que no
> arranca ni un contenedor. Cómo se cargan, se verifican y se retiran, en
> [apparmor-profiles.md](./apparmor-profiles.md).

**2. El dominio del `install.yaml` tiene que resolver a la máquina.** El sitio
que genera el instalador se sirve con `tls internal` (`proxy_generator.py`), así
que el certificado se elige por el **SNI**, que sale de la URL — no por la
cabecera `Host`, que Caddy sólo lee **después** del handshake. Entrar por IP con
`Host: <dominio>` no es equivalente: el handshake se corta antes.

```text
httpx.ConnectError: [SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error
```

Así falló el e2e cuando entraba por `https://127.0.0.1` con la cabecera `Host`
puesta (run `33196741325`): Caddy no tiene certificado para esa IP y aborta antes
de llegar a mirar la cabecera. En una máquina de pruebas basta el `/etc/hosts`
del propio host (`echo "127.0.0.1 agentic.example.com" | sudo tee -a /etc/hosts`);
en una instalación de verdad es DNS, y el paso a paso con dominio propio está en
[08-instalacion-produccion.md](./08-instalacion-produccion.md) y
[07-custom-domain.md](./07-custom-domain.md).

**3. Puertos 80 y 443 libres — y privilegio para bindearlos.** Lo primero sí lo
comprueba el gate; lo segundo **no**, y la distinción es deliberada:
`_ports_in_use` (`prereqs.py`) sólo cuenta `EADDRINUSE` como ocupación e
**ignora `EACCES`**, porque una sonda sin privilegio no puede distinguir «puerto
ocupado» de «no me dejan bindear puertos < 1024» y la instalación real corre
privilegiada. Consecuencia práctica: si instalas sin ese privilegio, el gate
pasa en verde y lo que falla es el arranque del proxy, más tarde y con peor
mensaje. Instala con el privilegio que el stack va a necesitar (ADR 0061: el
proxy Caddy es la **única** superficie publicada).

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
> **de verdad**: escribe el `docker-compose.yml` + `.env` + `caddy/Caddyfile` +
> el árbol `stack/` de auxiliares bajo el `data_root`, hace `docker compose pull` / `up -d --wait`, aplica las
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
> reinstala, o usa
> `scripts/reinstall.sh --config install.yaml --fresh --confirm-name agentic-platform --yes`
> (doble confirmación: borra el árbol de datos y reinstala desde cero).

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
- **`stack/`** — los auxiliares que el compose generado **monta**, escritos
  desde el propio paquete del instalador (`installer_backend.stack_assets`):
  los scripts de inicialización de PostgreSQL (`stack/postgres/init/` —
  `pgvector` y los roles `migrations`/`app`/`service_user`), la configuración
  de Vault (`stack/vault/config.hcl`), los perfiles seccomp
  (`stack/seccomp/`), los contextos de build de los dos tinyproxy
  (`stack/egress-proxy/`, `stack/registry-proxy/`) y —con el overlay de
  monitoring— la configuración de Prometheus/Alertmanager/Grafana.
  Configuración en `0644` y scripts en `0755`: los lee el proceso de **dentro**
  del contenedor, que no corre como el usuario que instaló.

  > No es un adorno. Docker materializa como **directorio vacío** el lado host
  > ausente de un bind, así que un auxiliar que faltase no daría error: dejaría
  > la base sin `pgvector` ni roles (el `initdb` se salta la inicialización si
  > el PGDATA no está vacío) o a Vault con un directorio donde espera su
  > fichero de configuración. Y los dos contextos de build llevan
  > `pull_policy: build`: sin ellos `docker compose up` aborta **al resolver el
  > proyecto**, sin arrancar un solo contenedor.

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
  ├── grafana/                 (0750)  estado (solo overlay de monitoring)
  └── stack/                           auxiliares que el compose monta
      └── monitoring/alertmanager/secrets/  (0755)  buzón del webhook de
                                     respaldo, vacío y a rellenar por el
                                     operador (solo overlay de monitoring)
  ```

  El resto de `stack/` aparece al escribir su contenido; el buzón de
  Alertmanager está aquí porque es el único bind que monta un directorio
  **vacío**: sin crearlo, lo crearía Docker como `root` y Alertmanager —que
  corre como `nobody`— no podría leer el webhook, fallando en silencio en cada
  envío del receiver de respaldo.

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
