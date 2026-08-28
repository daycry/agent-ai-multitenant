# Instalación

Guía para levantar el stack **en local, para desarrollar**. No es una
instalación de producción y no pretende serlo: aquí se clona el repo, se corre
`api-server` y `admin-panel` desde el código y Vault va en modo dev.

> **¿Buscas producción?** Salta a la sección **«Instalación de producción: los
> tres caminos»**, al final de esta página. Va aparte porque el camino de
> producción **no es este** —esta página es el camino (2) de esa tabla— y porque
> hay que saber en qué estado está cada uno antes de reservar una máquina.

## Prerequisitos

| Componente     | Versión mínima | Cómo verificar           |
| -------------- | -------------- | ------------------------ |
| Docker Engine  | 24+            | `docker --version`       |
| Docker Compose | v2+            | `docker compose version` |
| Python         | 3.12+          | `python --version`       |
| Node.js        | 20+            | `node --version`         |
| Git            | 2.40+          | `git --version`          |

En Windows funciona con **Docker Desktop**. El host por defecto del
Postgres es 15432 (no 5432) para no chocar con un postgres local
(Laragon, etc.).

## 1. Clonar el repositorio

```bash
git clone https://github.com/daycry/agent-ai-multitenant.git
cd agent-ai-multitenant
```

## 2. Bootstrap del entorno de desarrollo Python

Crea el `.venv/`, instala las dependencias de los paquetes Python
locales (`apps/api-server`, `apps/watchdog`) en modo editable y
registra el git hook de pre-commit:

```bash
# Windows
.\scripts\dev\bootstrap.ps1

# Linux / macOS
./scripts/dev/bootstrap.sh
```

Idempotente: si ya tienes `.venv/`, lo reutiliza.

## 3. Levantar el stack Docker

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.dev.yml \
  up -d
```

Esto arranca la infraestructura: **PostgreSQL** (+pgvector), **Redis**,
**MinIO**, **Vault** (modo dev), **ClamAV**, **docling-serve** (parseo de
documentos), **egress-proxy** y **Ollama** (embeddings; un init
`ollama-bootstrap` descarga el modelo y termina — `Exited (0)` es normal).
Espera 30-60 segundos a que estén healthy (`docker compose ps`).

Para añadir la **observabilidad** (Prometheus + Alertmanager + Grafana +
cAdvisor) suma el overlay de monitoring; en **Windows** añade además el override
de Windows (necesario para `node-exporter`):

```bash
# Linux/macOS
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.monitoring.yml -f docker/docker-compose.monitoring.dev.yml up -d

# Windows (Docker Desktop / WSL2)
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml \
  -f docker/docker-compose.monitoring.yml -f docker/docker-compose.monitoring.dev.yml \
  -f docker/docker-compose.windows.yml up -d
```

**Qué hace cada contenedor, en qué puerto y cómo acceder (URLs + credenciales
dev)** está en la referencia:
[`docs/04-reference/stack-services.md`](../04-reference/stack-services.md).
GPU (CUDA) opcional: [runbook Ollama](../06-runbooks/ollama-gpu-setup.md).

> **Atajo:** `scripts/dev/up.ps1` (Windows) / `up.sh` (Linux/macOS) levanta
> docker + api-server + admin-panel de una vez y **al terminar imprime las URLs
> y credenciales de acceso**. Añade `-Monitoring` / `--monitoring` para incluir
> Prometheus/Alertmanager/Grafana. Parar: `down.ps1 -Docker` / `down.sh --docker`.

Si Vault se queda en `Restarting`, mira
[`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md).

## 4. Frontend (admin-panel)

```bash
cd apps/admin-panel
npm install
npm run dev
```

→ http://localhost:3000/

## 5. Variables de entorno

Las dev-only están en `docker/.env.example`. Cópialo a `docker/.env`
y ajusta si necesitas puertos diferentes:

```bash
cp docker/.env.example docker/.env
```

En producción, los secretos vienen de **Vault** —no de `.env`— y los genera el
instalador (§siguiente). La excepción escrita a esa regla —los secretos que un
tenant configura para un tercero, en columna cifrada con Fernet— está en
[ADR 0146](../05-architecture-decisions/0146-fernet-en-db-vs-vault.md) y en
`CLAUDE.md` §«Dónde vive un secreto».

## 6. Tests

Suite completa (unit + integración):

```bash
.venv/Scripts/python -m pytest tests/ -v        # Windows
.venv/bin/python -m pytest tests/ -v            # Linux / macOS
```

## Instalación de producción: los tres caminos

Nada de lo de arriba instala la plataforma en una máquina de producción. Lo de
arriba **es** el camino (2) de esta tabla: levanta la infraestructura y se queda
ahí. Los tres caminos existen, exigen cosas distintas y hoy están en estados
distintos. El estado no está estimado: la anatomía de cada camino está medida en
el
[ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md),
y lo que el camino (3) hace de verdad en una máquina limpia está medido
**ejecutándolo** — el nocturno
[Install E2E](../../.github/workflows/install-e2e.yml), ejecución
[33197920542](https://github.com/daycry/agent-ai-multitenant/actions/runs/33197920542)
del 2026-08-28.

| Camino                                                                                                  | Qué exige del host                                                                                                                                      | Estado hoy                                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(1) Sin clonar** — descargar el compose de arranque, leerlo, ejecutarlo, y después `up` + `bootstrap` | Docker + Compose v2 y salida a `ghcr.io`. **Ni git, ni Python, ni el repositorio**                                                                      | **No disponible todavía**, y el e2e no lo cambia: no hay imagen del instalador publicada y el `run` sale con `denied`. Falta **publicar las imágenes**, la del propio instalador incluida                                                                                                          |
| **(2) Con clon + `docker compose`** — lo de esta página                                                 | git + el repositorio + `docker/.env`                                                                                                                    | **Infraestructura sí, plataforma no.** Sin cambios: el compose canónico no declara los servicios de aplicación                                                                                                                                                                                     |
| **(3) Con los scripts** — `./scripts/install.sh --config install.yaml`                                  | git + el repositorio + **Python 3.12 con `installer_backend` importable** (`scripts/dev/bootstrap.sh`) + AppArmor cargado + el dominio resolviendo aquí | **Verificado de punta a punta el 2026-08-28** en un Linux limpio: 22 servicios healthy, migraciones aplicadas, Vault inicializado, tenant sembrado, HTTPS y login con la credencial revelada. **Con las seis imágenes construidas en el propio job y servidas desde un registro local**, ver abajo |

El camino (1) es un **fichero que se descarga y se lee antes de ejecutarlo**
—[`docker/bootstrap/docker-compose.generate.yml`](../../docker/bootstrap/docker-compose.generate.yml)—,
no un `curl … | bash`, y lo que arranca **genera** el árbol de instalación en la
raíz de datos y sale: el `docker compose up` lo ejecuta el operador. Los tres
comandos, con lo que hay que mirar al leer el fichero, están en la referencia
[04-reference/installation.md](../04-reference/installation.md) §«Los tres
caminos de instalación».

Los caminos (1) y (3) ejecutan **el mismo CLI**: lo que cambia es quién pone el
intérprete —la imagen publicada o tu host— y hasta dónde llega (en (1) el
contenedor genera y sale; en (3) el CLI lanza también el `up`). Y hay un frontal
más que **no** es un cuarto camino porque no instala nada — ésta es la diferencia
entre los dos que viven en el repositorio:

- **CLI desatendido** (`scripts/install.sh --config install.yaml`) — el camino
  **REAL**. Cablea los bindings reales por defecto y **aborta con código 4
  (`PROVISION`)** si detecta un seam de simulación sin `--dry-run`: no existe la
  instalación falsa silenciosa. `--config` es obligatorio; sin él sale con
  código 1 y no arranca nada.
- **Wizard HTTP** (`apps/installer`) — una **SIMULACIÓN**. Recorre los nueve
  pasos, pero su `StepExecutor` por defecto es `FakeStepExecutor`: no aprovisiona
  nada y **las credenciales y las unseal keys de Vault que revela al final no
  sirven para nada**. Sirve para revisar el flujo y la detección de GPU;
  cablearlo al ejecutor real es un follow-up (prod-09).

```bash
cp scripts/install-profiles/recommended.yaml install.yaml
# edita install.yaml: dominio, providers LLM, sizing, tenant inicial…
./scripts/install.sh --config install.yaml
```

El procedimiento completo, con prerequisitos, fases y códigos de salida, está en
el runbook
[`06-runbooks/01-installation-from-scratch.md`](../06-runbooks/01-installation-from-scratch.md);
el paso a paso de producción con dominio propio, en
[`06-runbooks/08-instalacion-produccion.md`](../06-runbooks/08-instalacion-produccion.md).

### Lo que hoy no termina es la PUBLICACIÓN, no la instalación

Hasta hoy este apartado decía que el camino real no llegaba al final en una
máquina limpia, y era verdad. **Ya no.** El 2026-08-28 el camino (3) instaló
entero en un Linux limpio: 22 servicios healthy, migraciones aplicadas, Vault
inicializado, tenant sembrado con sus credenciales reveladas, el proxy sirviendo
HTTPS y el login funcionando con esa credencial. Es el nocturno
[Install E2E](../../.github/workflows/install-e2e.yml), ejecución
[33197920542](https://github.com/daycry/agent-ai-multitenant/actions/runs/33197920542).

**Lo que no termina, y hay que decirlo con el mismo cuidado, es la publicación.**
Ese e2e **construye las seis imágenes de plataforma dentro del propio job** y las
sirve desde un registro local, porque en `ghcr.io/daycry` no hay ninguna. De ahí
salen las dos consecuencias que quedan vivas:

1. **El camino (1) sigue sin existir para un usuario real.** Sin imagen del
   instalador publicada, su `docker compose run` termina en `denied` — y con ella
   faltan las seis de plataforma que ese camino descargaría después
   ([ADR 0160](../05-architecture-decisions/0160-versionado-de-la-plataforma.md)).
2. **En tu máquina, el camino (3) todavía necesita esas imágenes.** El paso
   `PULL_IMAGES` hace `docker compose pull` contra un tag que nunca se ha
   publicado y aborta. El e2e lo rodea construyéndolas y reapuntando
   `PLATFORM_REGISTRY` / `PLATFORM_IMAGE_TAG` a su registro local; publicar es lo
   que quita ese rodeo. Es un acto del operador y aquí no se promete fecha.

Lo que **sí** se reparó, y conviene que conste porque su modo de fallo no avisaba
dónde estaba la causa: ~~las rutas relativas del compose generado no resolvían~~
**(reparado el 2026-08-27)**. El instalador escribe el `docker-compose.yml` en la
**raíz de datos** (`/data/agent-platform` por defecto), no en el repo, y lanza
`docker compose` desde ahí: cada `./algo` resolvía contra `/data/agent-platform/…`,
donde no hay ningún checkout — **clonar el repositorio no lo arreglaba**, que era
la mitad contraintuitiva. Ante el lado host ausente de un bind, Docker lo
materializa como directorio vacío: `./postgres/init` acababa **dentro** del
PGDATA, `initdb` encontraba un directorio no vacío y los SQL reales (`pgvector`,
roles de servicio) no corrían nunca. Lo que se veía era un Postgres `healthy` sin
`pgvector`, con el error saliendo a la primera consulta que la necesita. De siete
familias de rutas el instalador escribía una; ahora escribe las siete, las seis
nuevas bajo `stack/` y desde su propio paquete. Lo sostienen dos guardas que
derivan del código —no de una lista a mano— las rutas que el compose pide y las
que la instalación produce (`tests/unit/test_generated_compose_is_installable.py`)
y la integridad de lo que el instalador lleva dentro
(`tests/unit/test_installer_ships_stack_assets.py`).

### Lo que hace falta en tu máquina para instalar como lo hace el e2e

El instalador no prepara el host: eso lo hace quien instala. Estas tres cosas las
pone el job antes de llamar a `install.sh`, y sin ellas la instalación no llega al
final.

| Hace falta                                            | Si falta                                                                                                                                                          | Cómo                                                                                                                                                          |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Que el **dominio del perfil resuelva a esta máquina** | El proxy corta el handshake TLS (`TLSV1_ALERT_INTERNAL_ERROR`) y no sirve nada                                                                                    | `echo "127.0.0.1 tu.dominio" \| sudo tee -a /etc/hosts`, o un DNS que apunte al host                                                                          |
| Los **perfiles AppArmor** cargados en el kernel       | Todos los servicios del compose generado pinan `apparmor=agentic-default` (y el `docker-socket-proxy`, el suyo): Docker aborta el arranque de **cada** contenedor | `sudo apparmor_parser -r -W docker/apparmor/agentic-default.profile` (y el del socket-proxy); ver [apparmor-profiles.md](../06-runbooks/apparmor-profiles.md) |
| Las **seis imágenes de plataforma** alcanzables       | `PULL_IMAGES` aborta con rc=1 contra un tag que no existe                                                                                                         | Hoy: construirlas y servirlas desde un registro propio, con `PLATFORM_REGISTRY`. Mañana: publicadas en `ghcr`                                                 |

**Por qué el dominio y no la IP** — el detalle que costó una ejecución entera y
que hasta hoy no estaba escrito en ninguna guía. El sitio se sirve con `tls internal`, y en
TLS el nombre viaja en el **SNI**, que sale de **la URL**. La cabecera `Host` no
sirve para esto: el proxy sólo la lee **después** del handshake, y para entonces
ya ha tenido que elegir certificado. Entrar por `https://127.0.0.1` con
`Host: tu.dominio` no es una variante equivalente de entrar por el nombre; es un
handshake sin certificado que ofrecer. El e2e lo hacía así y murió con
`TLSV1_ALERT_INTERNAL_ERROR` antes de mirar una sola respuesta (run 33196741325).

## Próximos pasos

- [Primer arranque](./03-first-run.md) — registrar un user, promover
  a System Admin, crear un tenant.

## Si algo falla

Antes de inventar nada, **busca primero** en
[`docs/03-guides/gotchas/`](../03-guides/gotchas/) — un catálogo de trampas
ya documentadas (puertos, RLS, asyncpg, mypy, OTEL, Docker, Windows...).
