---
title: Instalación, CLI, perfiles y endurecimiento de producción — Referencia
audience: operador, system admin, devops, security
phase: 15-instalador-produccion
updated: 2026-08-28
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

## Los tres caminos de instalación

Hay tres formas de poner esto en una máquina y **no son intercambiables**: cada
una exige cosas distintas del host y hoy están en estados distintos. La tercera
columna está **medida** sobre el árbol en el
[ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
(firmado el 2026-08-27), no estimada — que es la diferencia entre elegir camino y
reservar una máquina para descubrirlo.

| Camino                                                                                                  | Qué exige del host                                                                                                                  | Estado hoy                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(1) Sin clonar** — descargar el compose de arranque, leerlo, ejecutarlo, y después `up` + `bootstrap` | Docker + Compose v2, salida a `ghcr.io`, un `install.yaml` y la raíz de datos creada. **Ni git, ni Python, ni el repositorio**      | **No disponible todavía.** No hay ninguna imagen del instalador publicada, así que el `run` termina en `denied`. El artefacto ya existe y es auditable; falta publicar                                         |
| **(2) Con clon + `docker compose`**                                                                     | git, el repositorio, `docker/.env` (nueve variables sin default abortan sin él)                                                     | **Levanta infraestructura, no la plataforma.** El compose canónico no declara los servicios de aplicación: da Postgres, Redis, MinIO, Vault, ClamAV, docling, proxies, searxng, Ollama                         |
| **(3) Con los scripts** — `./scripts/install.sh --config install.yaml`                                  | git, el repositorio, **Python 3.12 y el paquete `installer_backend` importable** (`scripts/dev/bootstrap.sh`), y root sobre `/data` | **Es el camino real, y hoy no termina en una máquina limpia**: el paso `PULL_IMAGES` va contra un tag que nunca se ha publicado ([ADR 0160](../05-architecture-decisions/0160-versionado-de-la-plataforma.md)) |

Tres cosas que no se deducen de la tabla y deciden la elección:

- **(1) y (3) ejecutan el MISMO código**, el CLI `installer_backend`. Lo que
  cambia es quién pone el intérprete —la imagen publicada o tu host— y **hasta
  dónde llega**: en (1) el contenedor **genera** el árbol de arranque y sale, y
  el `docker compose up` lo ejecutas tú; en (3) el propio CLI lanza también el
  `up`. No es un detalle de comodidad: es la decisión firmada del ADR 0161, y el
  motivo está abajo.
- **(2) no es un camino de producción**, aunque lo parezca. Es el stack de
  desarrollo: sirve para tener la infraestructura delante, no para instalar el
  producto.
- **El wizard HTTP no es un cuarto camino.** Es una **simulación** (`FakeStepExecutor`)
  que recorre los nueve pasos sin aprovisionar nada; véase el apartado siguiente.

### (1) Sin clonar: descargar, leer, ejecutar

El artefacto de entrada es un fichero, no un `curl … | bash`, y esa diferencia es
la decisión: se descarga, **se lee**, y sólo entonces se ejecuta. Vive en
[`docker/bootstrap/docker-compose.generate.yml`](../../docker/bootstrap/docker-compose.generate.yml)
y cabe en una pantalla a propósito.

```bash
sudo mkdir -p /data/agent-platform
curl -fsSLO https://raw.githubusercontent.com/daycry/agent-ai-multitenant/v1.0.0/docker/bootstrap/docker-compose.generate.yml
less docker-compose.generate.yml     # este paso NO es decorativo: es su función

# tu install.yaml, al lado del compose (sale de un perfil, ver más abajo)
docker compose -f docker-compose.generate.yml run --rm generate

cd /data/agent-platform && docker compose up -d --wait
docker compose run --rm bootstrap    # <-- la finalización: ver la nota de abajo
```

> **El tercer comando es la finalización, y sólo se ejecuta una vez.** El
> one-shot `bootstrap` inicializa Vault (init + desellado + KV v2 + las cuatro
> políticas por servicio), siembra el tenant inicial con su primer System Owner
> y el catálogo built-in, y **revela las credenciales UNA sola vez** por stdout:
> las cinco unseal keys, el root token y la contraseña del administrador.
> Cópialas antes de cerrar la terminal — **no tienen recuperación**: un Vault ya
> inicializado no se re-inicializa, porque hacerlo sería destructivo.
>
> Corre dentro de la red del stack, y ése es todo el motivo por el que existe
> como servicio del compose en vez de como un paso del instalador: el servicio
> `vault` **no publica ningún puerto** —el único que publica es Caddy,
> [ADR 0061](../05-architecture-decisions/0061-reverse-proxy-tls.md)—, así
> que desde el host no es alcanzable. Es idempotente: si Vault ya estaba
> inicializado no lo re-inicializa (y no inventa claves nuevas en el revelado), y
> si el usuario admin ya existía **no** revela una contraseña, porque `init_tenant`
> no cambia la de un usuario que ya está. Ver §«`generate` — escribir el árbol y
> salir».

**Qué se está leyendo cuando se lee ese fichero**: que baja **una** imagen, que le
monta **sólo** la raíz de datos y el `install.yaml` en solo lectura, y que **no
monta `/var/run/docker.sock`**. Lo último es lo que separa este diseño del que se
descartó: montar el socket es acceso root efectivo al host, la alternativa que el
[ADR 0060](../05-architecture-decisions/0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md)
rechazó por escrito. Por eso el contenedor **genera y no aprovisiona**, y por eso
los comandos son tres y no uno.

> **Hoy estos comandos no funcionan, y decirlo es parte de documentarlos.** No hay
> imagen del instalador publicada: el `run` sale con `denied`. Lo que decide que
> este camino exista es la publicación, y ésa tiene un **orden duro** —las seis
> imágenes de plataforma pineadas por digest primero— escrito en el runbook
> [09-release.md](../06-runbooks/09-release.md) §«La séptima imagen». Mientras
> tanto, el camino soportado es el (3).

### (2) Con clon: la infraestructura, no el producto

```bash
git clone https://github.com/daycry/agent-ai-multitenant.git && cd agent-ai-multitenant
cp docker/.env.example docker/.env      # ANTES del `up`: hay variables sin default
docker compose -f docker/docker-compose.yml up -d
```

Levanta la capa de infraestructura y nada más. Los servicios de aplicación
(`api-server`, `workers`, `orchestrator`, `admin-panel`…) los declara el compose
que **genera el instalador**, no éste. El paso a paso de desarrollo está en
[02-getting-started/01-installation.md](../02-getting-started/01-installation.md).

### (3) Con los scripts: el camino soportado hoy

```bash
cp scripts/install-profiles/recommended.yaml install.yaml
# edita install.yaml: dominio, providers LLM, sizing, tenant inicial…
./scripts/install.sh --config install.yaml
```

`--config` es obligatorio. Los prerrequisitos **no documentados en la checklist
del runbook de producción** son los que hacen fallar esto en una máquina limpia:
`install.sh` es un wrapper de `python -m installer_backend.cli install`, así que
necesita Python 3.12 y el paquete importable; en Debian/Ubuntu limpio no existe
siquiera un binario llamado `python`.

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

| Código | Significado                                                                               | Qué hacer al recogerlo                                                        |
| ------ | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 0      | Instalación completada                                                                    | —                                                                             |
| 1      | Error de uso (args mal / falta `--config`)                                                | Corregir la invocación                                                        |
| 2      | Error de config (`install.yaml` inválido; NO se provisiona nada)                          | Corregir el YAML                                                              |
| 3      | Error de prereq (un prerequisito falló; aborta ANTES de provisionar)                      | Arreglar el host y reintentar                                                 |
| 4      | Error de provisión (un paso falló; el stack puede quedar a medias)                        | Revisar el paso que falló; reintentar es seguro                               |
| 5      | Abortado (el operador declinó una confirmación destructiva)                               | Nada: no se tocó nada                                                         |
| 6      | `generate` no pudo escribir el árbol de arranque                                          | No se levantó nada; puede haber ficheros a medias bajo la raíz de datos       |
| 7      | Purga INCOMPLETA: `uninstall --purge-data` no pudo borrarlo todo                          | **NO** dar la máquina por limpia: puede seguir ahí el `.env` con los secretos |
| 8      | UNSAFE: la raíz de datos tiene una instalación previa ilegible; **no se ha escrito nada** | Recuperar el `.env` (o su `.env.bak.*`) o asumir la pérdida (ver abajo)       |
| 9      | Excepción imprevista, ya traducida a un mensaje en stderr                                 | Leer el mensaje; la raíz de datos puede tener escrituras parciales            |

**Las cuatro últimas filas faltaban aquí hasta el 2026-08-28**, y el 6 llevaba
meses existiendo en el código sin estar documentado — la tabla iba del 0 al 5, así
que la automatización del operador trataba como «desconocido» justo el código que
iba a recibir. Cada una separa una decisión distinta de quien recoge el error: el
6 dice «no se levantó nada» frente a «el stack puede estar a medias»; el 7, «no
des la máquina por limpia»; el 8, «me niego, arréglalo tú» frente a «reintenta»;
y el 9 existe para que ningún fallo imprevisto vuelva a salir como traza de
Python con un exit 1, que en esta misma tabla significa «argumentos mal».

Los secretos + unseal keys se imprimen a stdout **una vez** (sin recuperación;
nunca a un fichero de log). Con una excepción acotada y deliberada: ver
§«Si la instalación se interrumpe después de inicializar Vault».

### Reejecutar el instalador: qué pasa con los secretos

La primera instalación falla tarde con frecuencia —Caddy no arranca porque otro
servicio tiene el 443— pero para entonces PostgreSQL **ya hizo su `initdb`** con
la contraseña del primer `.env`. Lo que hace el instalador al relanzarlo:

| Estado de `{data_root}`                          | Qué hace                                                                                               |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| Vacía                                            | Acuña todos los secretos con CSPRNG                                                                    |
| Con `.env` legible                               | **Reutiliza** sus secretos; copia el anterior a `.env.bak.<timestamp>` (0600) antes de sobrescribir    |
| Con `.env` legible al que le falta un data-bound | **Aborta con 8**, sin escribir nada                                                                    |
| Con `.env` ilegible (permisos, corrupción)       | **Aborta con 8**, sin escribir nada                                                                    |
| Con `postgres/PG_VERSION` y **sin** `.env`       | **Aborta con 8**: los secretos de esos datos se han perdido y ninguno de los que se generen los abrirá |

Los secretos que se reutilizan sí o sí (perderlos no «rota» nada: deja los datos
huérfanos) son las tres contraseñas de rol de PostgreSQL, el usuario y la clave
raíz de MinIO, y las tres claves Fernet de las columnas cifradas. Los que firman
o autentican (JWT, token interno, token de alertas, firma de URLs de review,
contraseña de Redis) se reutilizan si están y se acuñan si faltan, **diciéndolo**
por su nombre de variable — rotar el secreto JWT cierra todas las sesiones
abiertas.

`--force-new-secrets` (en `install` y en `generate`) es la puerta de emergencia:
acuña todo de nuevo asumiendo que los datos que haya en disco quedan
inaccesibles. **Sigue haciendo la copia del `.env` anterior**: que el operador
asuma la pérdida no es motivo para quitarle la última copia de los secretos
viejos.

### Si la instalación se interrumpe después de inicializar Vault

Entre `vault operator init` y el revelado final hay minutos —la siembra del
catálogo built-in— y en ese tramo las cinco unseal keys existen **sólo en la
memoria del proceso**. Si el proceso muere ahí (una sesión SSH que se cae, un
paso que falla), un reintento no las recupera: `bootstrap_vault` detecta que
Vault ya está inicializado y se niega, correctamente, a re-inicializar.

Por eso el instalador escribe `{data_root}/UNSEAL-KEYS-BORRAME.txt` a 0600 **en
cuanto** Vault se inicializa, y lo **borra en cuanto** las claves salen por
pantalla. Si ese fichero está en disco, es que algo se interrumpió:

1. copia de ahí las cinco claves y el root token (con desellado manual, [ADR
   0145](../05-architecture-decisions/0145-vault-operable-tokens-y-unseal.md), los
   cinco shares se reparten entre cinco custodias distintas — ese fichero **no**
   es una de ellas);
2. reanuda con `--vault-unseal-keys-from {data_root}/UNSEAL-KEYS-BORRAME.txt`;
3. **borra el fichero.**

El flag pide un fichero y no la clave en la línea de comandos a propósito: un
share en `argv` queda a la vista de cualquier usuario del host en `ps` y en el
historial del shell.

### `generate` — escribir el árbol y salir (camino sin clon)

Es el subcomando del [ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
opción D, el que corre dentro de la imagen del instalador: **no habla con
Docker** (montar el socket del daemon es acceso root al host, lo que rechazó el
[ADR 0060](../05-architecture-decisions/0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md)),
se le monta sólo la raíz de datos, ejecuta únicamente el paso `generate_config` y
sale con **6** si no pudo escribir.

```bash
docker run --rm -v /data/agent-platform:/data/agent-platform \
  -v ./install.yaml:/install.yaml:ro \
  ghcr.io/daycry/installer:v1.0.0 generate --config /install.yaml
```

No tiene `--dry-run`, y es intencionado: el entregable de este subcomando **es**
el árbol de ficheros, así que simularlo sólo produce un log en verde sobre una
raíz de datos vacía.

**Dos cosas que este camino NO hace, y que el banner final dice por escrito:**

- **No corre la puerta de prerequisitos.** No puede: desde dentro del contenedor
  no se ven el daemon Docker, la versión de Compose ni los puertos del host. Lo
  único que sí mide —y mide— es el disco libre de la raíz de datos, que está
  montada. El resto (80/443 libres, Docker ≥ 24.0, Compose ≥ 2.21, 8 GiB de RAM)
  el banner los **lista** con los mismos umbrales que `prereqs.py`, para que el
  operador los compruebe antes del `up`.
- **No finaliza la instalación**, y no puede: la finalización habla con Vault, y
  Vault sólo es alcanzable desde dentro de la red del stack. El paso
  `docker compose run --rm bootstrap` —init de Vault, siembra del tenant,
  revelado de credenciales— lo ejecuta el operador después del `up`, y el banner
  lo imprime como el segundo de los dos comandos que quedan. Es la segunda mitad
  del paso 8 del ADR 0161, y **aterrizó el 2026-08-28**
  (`apps/api-server/src/api_server/bootstrap/`); desde entonces los dos caminos
  de instalación acaban ejecutando la misma implementación.

  La regla del banner no se retiró con la deuda que la motivó: **sólo manda
  ejecutar lo que existe**. Hay una guarda ejecutable que cruza esa declaración
  con el árbol del repositorio, así que si el módulo desapareciera el banner
  volvería a marcar el paso como `NO DISPONIBLE` en vez de ordenar un comando que
  falla.

  Este párrafo decía hasta el 2026-08-28 que el banner «remite a terminar desde
  el host con `python -m installer_backend.cli install --config install.yaml`».
  Era falso, y de la peor manera: ese camino **tampoco terminaba** —moría en el
  mismo paso de Vault, porque el servicio no publica puerto y el cliente hablaba
  contra `127.0.0.1:8200`—, así que ofrecía como remedio la avería que se quería
  remediar. Un test lo daba por bueno, con lo que la suite en verde certificaba
  una salida de emergencia rota: el mismo modo de fallo que ese test nació para
  cazar, una vuelta más arriba. Esa guarda disparó el 2026-08-28, que es para lo
  que estaba: el módulo aterrizó, la suite se puso roja, y el banner, la bandera
  y este documento se actualizaron a la vez.

`tls_mode: provided` funciona en los dos caminos, pero de forma distinta: desde
el host, el instalador **copia** `tls_cert_path`/`tls_key_path` a
`{data_root}/caddy/tls/` (`server.crt` 0644, `server.key` 0600); desde el
contenedor esas rutas del host no son alcanzables, así que el par tiene que estar
**ya** en `{data_root}/caddy/tls/`. Si no se cumple ninguna de las dos, el paso 1
falla con un mensaje que nombra las rutas — antes se aceptaba en silencio y Caddy
arrancaba sin certificado, tumbando el `up --wait` entero.

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
