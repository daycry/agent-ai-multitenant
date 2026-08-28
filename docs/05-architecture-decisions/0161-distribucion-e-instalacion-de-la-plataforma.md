---
title: "ADR 0161: Cómo se distribuye e instala la plataforma — los tres caminos, y qué le falta a cada uno"
status: accepted
date: 2026-08-27
deciders: [operador]
relates_to: [0060, 0061, 0094, 0129, 0145, 0146, 0148, 0160]
plan_referenced: 15-instalador-produccion
task: [task_15_01, task_15_07, task_15_10]
docs_language: es
---

# ADR 0161 — Distribución e instalación de la plataforma

> **Estado: `accepted`.** Firmado por el operador el **2026-08-27**: gana la
> **opción D con el envoltorio de la B** —el instalador **genera** el árbol de
> arranque y **no provisiona** el host, y se entra por un fichero compose
> descargable y auditable— y la **opción A queda descartada** por exigir el
> socket de Docker. La decisión literal, con sus tres razones y las respuestas a
> las preguntas que este documento planteaba, está en §«Decisión».
>
> El resto se conserva **tal como se le puso delante al operador**,
> §«Recomendación de este documento» incluida, para que se vea sobre qué medición
> se decidió. Dos avisos de lectura: §«Lo que hay hoy, medido» describe el árbol
> **antes** de la reparación del suelo —lo corrige el recuadro de actualización
> de esa misma sección— y §«Qué se hace cuando esto se acepte» lleva ya marcado,
> paso a paso, qué está hecho y qué queda.

## El hecho que lo motiva

El wizard de instalación —lo único de este repo empaquetado como contenedor de
bootstrap— **se construye desde el árbol de fuentes**. Sus dos servicios se
declaran con `build:`, no con `image:`:

```yaml
# apps/installer/docker-compose.installer.yml:21-24
installer-backend:
  build:
    context: ./backend
    dockerfile: Dockerfile
# apps/installer/docker-compose.installer.yml:43-46
installer-ui:
  build:
    context: .
    dockerfile: Dockerfile
```

Y la forma de arrancarlo que documenta el propio fichero
(`apps/installer/docker-compose.installer.yml:8`) lleva el `--build` delante:

```bash
docker compose -f apps/installer/docker-compose.installer.yml up -d --build
```

**Consecuencia directa: instalar exige clonar el repositorio, aunque la
plataforma venga de un registry.** Las seis imágenes de aplicación sí están
parametrizadas para bajarse —`compose_generator.py:99-100` compone
`${PLATFORM_REGISTRY:-ghcr.io/daycry}/<app>:${PLATFORM_IMAGE_TAG:-v1.0.0}`— pero
el contenedor que las orquesta no se puede traer: **no existe imagen publicada
del instalador**. `.github/workflows/release-images.yml` publica seis imágenes
(`api-server` en `:46`, la matriz `[workers, orchestrator,
notification-dispatcher, watchdog]` en `:105` y `admin-panel` en `:139`) y
ninguna es la del instalador. Lo único que se construye de él es local y
efímero, para pasarle Trivy: `.github/workflows/ci.yml:1054`
(`image-ref: agentic-platform/installer:ci`) y `:1066`
(`agentic-platform/backend:ci`).

Ése es el hecho medido. Lo que sigue existe porque, al ir a presupuestar el
camino sin clon, apareció algo que cambia el orden de las prioridades.

## Lo que hay hoy, medido

El operador pide tres caminos. Éste es el estado real de los tres, medido sobre
el árbol el 2026-08-27:

| Camino                                 | Estado hoy                                                                                                                             |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| **(1) Sin clonar**                     | **No existe, ni degradado.** No hay imagen del instalador publicada, no hay script descargable, y el wizard containerizado es un stub. |
| **(2) Clonando + `docker compose up`** | **Levanta infraestructura, no la plataforma.** El compose canónico no declara los servicios de aplicación.                             |
| **(3) Scripts desatendidos**           | **No puede terminar hoy en una máquina limpia**, y por dos averías independientes, no una.                                             |

Tres precisiones, porque cada una cambia una decisión distinta:

**(1) Lo único empaquetado como imagen es precisamente lo que finge.** El wizard
HTTP cablea seams falsos por defecto —`main.py:244-247`:
`StubPrereqChecker()`, `StubInstallerLifecycle()`, `FakeStepExecutor()`— y su
propio docstring lo dice con todas las letras (`main.py:262-269`): «it is a
**SIMULATION**, it does NOT provision a real stack, and the credentials it
reveals are NOT real. The REAL install path is the CLI». El runbook de
producción ya lo recoge (`docs/06-runbooks/08-instalacion-produccion.md:25-27`),
pero el README del propio instalador afirma lo contrario:
`apps/installer/README.md:51` — «The installer actually provisions a real stack
(Docker, `pg_*`, Vault)». Quien lea ese README diseñará el camino sin clon sobre
una premisa falsa.

**(2) El compose canónico no tiene capa de aplicación, y lo dice él mismo.**
`docker/docker-compose.yml:55-58`: «The app services (api-server, workers,
orchestrator) are not yet declared in this compose file (dev runs them locally;
the production declarations land with the Plan 15 installer)». Clonar y levantar
da Postgres, Redis, MinIO, Vault, ClamAV, docling, los dos proxies, searxng y
Ollama. Producto, ninguno.

**(3) Las dos averías del camino de los scripts.** La primera está firmada: el
[ADR 0160](0160-versionado-de-la-plataforma.md) mide que `git tag` está vacío y
que `release-images.yml` nunca ha corrido, así que el `docker compose pull` del
paso `PULL_IMAGES` (`real_step_executor.py:112`) va contra un tag que no existe.
**La segunda no la ha escrito nadie hasta ahora, y es la que motiva la mitad de
este documento**: el compose que el instalador genera pide ficheros que el
instalador no escribe.

### La avería que no estaba escrita

El compose generado **no se escribe en el repo**: se escribe en la raíz de
datos. `cli.py:498` — `compose_dir = config.storage.data_root`, que por defecto
es `/data/agent-platform` (`config.py:279`); y todo `docker compose` se lanza
con `cwd=compose_dir` (`real_step_executor.py:102`) sobre
`{compose_dir}/docker-compose.yml` (`real_step_executor.py:89-90`).

Ahí está el error de lectura que hace esto contraintuitivo: **clonar el repo NO
arregla el problema.** Un `./egress-proxy` de ese fichero no resuelve a
`<repo>/docker/egress-proxy`, sino a `/data/agent-platform/egress-proxy`, donde
no hay nada ni con clon ni sin él. El camino (3) falla por esto igual que
fallaría el camino (1).

Lo que el ejecutor escribe de verdad está enumerado y es corto
(`real_step_executor.py:131-160`): `docker-compose.yml`, `.env`,
`config/global.yaml`, `caddy/Caddyfile` y un árbol de 17 directorios
(`config_generators.py:503-521`). Lo que el compose generado pide es esto:

| Ruta relativa del compose generado                                  | Qué es                                        | ¿La escribe el instalador? | Dónde vive de verdad     |
| ------------------------------------------------------------------- | --------------------------------------------- | -------------------------- | ------------------------ |
| `"build": "./egress-proxy"` (`compose_generator.py:585`)            | Contexto de build, servicio **CORE** (`:119`) | **No**                     | `docker/egress-proxy/`   |
| `"build": "./registry-proxy"` (`compose_generator.py:620`)          | Contexto de build, servicio **CORE** (`:120`) | **No**                     | `docker/registry-proxy/` |
| `./postgres/init:…` (`compose_generator.py:433`)                    | SQL de `CREATE EXTENSION vector` y roles      | **No**                     | `docker/postgres/init/`  |
| `./vault/config.hcl:…` (`compose_generator.py:521`)                 | Configuración de Vault (bind de **fichero**)  | **No**                     | `docker/vault/`          |
| `./docker/seccomp:…` (`compose_generator.py:945`)                   | Perfiles seccomp de los runtimes              | **No**                     | `docker/seccomp/`        |
| `./monitoring/**` (`compose_generator.py:1436-1437,1535,1614-1615`) | Prometheus, Alertmanager, Grafana             | **No**                     | `docker/monitoring/`     |
| `./caddy/Caddyfile:…` (`compose_generator.py:1264`)                 | Configuración del proxy                       | **Sí** (`:153-155`)        | generado                 |

**Siete familias de rutas, una escrita.** Y dos de las seis que faltan no sólo
faltan: **colisionan con binds de datos que el propio instalador crea**.

> **Actualización del 2026-08-27 — esta medición ya no describe el árbol.** Las
> seis familias que faltaban se repararon el mismo día en que se escribió este
> ADR: viajan dentro del paquete del instalador
> (`installer_backend.stack_assets`, copia guardada byte a byte contra `docker/`)
> y el paso `GENERATE_CONFIG` las escribe bajo un único subárbol
> `{compose_dir}/stack/`. Con eso desaparecen también las dos colisiones —
> `stack/postgres/init` ya no cae dentro del PGDATA y `stack/vault/config.hcl`
> se escribe como fichero— y la duda abierta del recuadro de abajo deja de
> bloquear: la cadena ya no puede empezar, porque no hay bind ausente.
> Lo fijan `tests/unit/test_generated_compose_is_installable.py` y
> `tests/unit/test_installer_ships_stack_assets.py`. **El resto del documento
> sigue vigente**: los tinyproxy siguen construyéndose en destino (pregunta 6 sin
> firmar) y los bloqueantes 3 a 9 siguen abiertos.

- `{data_root}/postgres` es el PGDATA (`compose_generator.py:432`, creado a
  `0o700` por `config_generators.py:504`), y `./postgres/init` resuelve
  **dentro** de él.
- `{data_root}/vault` contiene `file/` y `logs/` (`compose_generator.py:519-520`,
  creados en `config_generators.py:507-508`), y `./vault/config.hcl` resuelve al
  lado — pero como **fichero**, y nadie lo escribe.

Docker materializa como **directorio vacío** el destino ausente de un bind. Con
eso, el modo de fallo no es un error en la línea que lo causa: es un `initdb` que
encuentra un PGDATA no vacío y unos SQL de inicialización que **no corren
nunca** —o sea, un Postgres que arranca `healthy` sin `pgvector` y sin los roles
de servicio— y un Vault que encuentra un directorio donde su binario espera un
fichero de configuración. Es el fallo caro: el que no avisa donde está la causa.

> **Duda abierta, y hay que medirla antes de presupuestar.** La cadena «bind
> ausente → Docker crea un directorio vacío → el init de Postgres no corre /
> Vault no lee su config» está **deducida** del comportamiento documentado de
> Docker (`create_host_path`), no medida en este stack. Lo que sí está anclado es
> la geometría de rutas (`cli.py:498` + `compose_generator.py:432-433`). Tampoco
> se ha comprobado si un contexto de build inexistente aborta el proyecto
> **entero** o sólo esos dos servicios — cambia si el operador ve el problema o
> se le queda un stack a medias.

## Qué hay que decidir

1. **¿Existe el camino (1) —instalar sin clonar— como producto soportado?** Hoy
   no existe y ninguna promesa escrita del repo lo sostiene salvo por omisión.
2. **Si existe, ¿cuál es su artefacto?** ¿Una imagen que se arranca con
   `docker run`, un fichero compose descargable, o las dos cosas?
3. **¿El instalador en contenedor PROVISIONA el host, o sólo GENERA lo que el
   operador ejecuta?** Es la pregunta que decide si hay que montar el socket de
   Docker, y con ella la excepción al
   [ADR 0060](0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md).
4. **¿Los ficheros auxiliares viajan con el instalador, o el compose generado
   deja de pedirlos?** Hay que elegir una de las dos: hoy no se hace ninguna, y
   eso rompe el camino (3) con clon incluido.
5. **¿Qué listón de integridad se le exige a una imagen del instalador?** ¿Tag,
   digest, firma? Es el eslabón que separa «traer una imagen» de «confiar en una
   imagen», y el repo no tiene firma de ningún tipo.
6. **¿Qué se hace con los dos tinyproxy?** Se publican como imágenes, o viajan
   como contexto de build dentro del artefacto del instalador.

## Los bloqueantes reales

No hay atajo: **el instalador lee cosas del árbol que hoy no viajan en ninguna
imagen**. Ésta es la lista completa que salió de la investigación, con lo que
cuesta cada una.

1. ~~**Los seis ficheros/directorios auxiliares** de la tabla de arriba.~~
   **CERRADO el 2026-08-27** (ver la actualización de la sección anterior). Era el
   bloqueante mayor y —esto es lo importante— **se pagaba igual con clon que sin
   él**, porque el compose vive en `data_root`: no era el precio del camino (1),
   era una deuda del camino (3) que el camino (1) heredaba. Por eso se hizo
   primero, que es el orden que recomienda este mismo documento.
2. **Los dos tinyproxy no se publican en ningún registry** y llevan
   `pull_policy: build` puesto a propósito. _(Al 2026-08-27 ya no bloquea el
   camino (3): sus contextos de build viajan con el instalador y se escriben en
   `stack/egress-proxy/` y `stack/registry-proxy/`. Sigue abierta la pregunta 6 —
   si se publican como imagen—, y el argumento de por qué NO se publicaron por
   iniciativa propia está escrito en el propio generador.)_
   sin él, `docker compose pull` intenta bajarlos de Docker Hub y sale con rc=1,
   abortando el paso `PULL_IMAGES` del wizard. Está medido en el comentario del
   propio generador (`:592-598`) y guardado por
   `tests/unit/test_infra_images_are_scanned.py:264-282`
   (`test_a_locally_built_service_is_never_pulled`). **Publicarlos no es un
   cambio de una línea**: hay que mover también esa guarda y la de `:232-242`,
   que hoy exige que el generador los emita con `build:`.
3. **Los tres perfiles YAML**
   (`scripts/install-profiles/{minimal,recommended,gpu}.yaml`) sólo viven en el
   árbol: el Dockerfile del instalador copia `pyproject.toml` y `src/` y nada más
   (`apps/installer/backend/Dockerfile:27-28`). Es el bloqueante **más barato**
   de la lista —son ficheros de texto con placeholders `CHANGE_ME_*`— pero hoy
   bloquea.
4. **El perfil AppArmor `agentic-default`** que el compose generado pide en cada
   `security_opt` hay que cargarlo en el host desde
   `docker/apparmor/agentic-default.profile` (documentado en
   `compose_generator.py:294-299`). En Linux, sin cargarlo, Docker aborta el
   arranque del contenedor.
5. **La imagen del instalador no lleva el cliente `docker`.**
   `apps/installer/backend/Dockerfile:5` parte de `python:3.12-slim` y `:27-29`
   sólo hace `pip install .`; `real_step_executor.py:92` invoca siempre
   `["docker", "compose", …]`, que `command_runner.py` lanza con `Popen`. Falla
   con rc=127. Y hay intención escrita de lo contrario: `prereqs.py:395-396` dice
   que la sonda se mantiene fina «so the installer image needs nothing beyond the
   stdlib + the Docker CLI» — el Dockerfile nunca cumplió esa mitad del trato.
6. **`hvac` no está declarado como dependencia.**
   `apps/installer/backend/pyproject.toml:12-16` lista `fastapi`,
   `uvicorn[standard]`, `pydantic` y `structlog`; `real_bindings.py:48`
   importa `hvac` en caliente. El paso `BOOTSTRAP_VAULT` termina en
   `ImportError`, y además **sin capturar**: `real_step_executor.py:164-167` sólo
   atrapa `VaultBootstrapError`. Un `pip install .` de esa imagen jamás traerá
   `hvac`.
7. **`yaml` funciona por accidente.** `compose_generator.py:56` lo importa a
   nivel de módulo y no está declarado: llega de rebote por el extra `[standard]`
   de `uvicorn` (`pyproject.toml:13`). No bloquea hoy; el día que ese extra
   cambie, el instalador deja de importar y el error saldrá al arrancar el
   contenedor, lejos de la causa.
8. **`scripts/install.sh` no es autosuficiente.** Es un wrapper de tres líneas
   útiles (`:35` `PYTHON_BIN="${PYTHON_BIN:-python}"`, `:39`
   `exec … -m installer_backend.cli install "$@"`) que necesita el paquete
   `installer_backend` importable — o sea, alguien que haya corrido
   `scripts/dev/bootstrap.sh:51-55`. El runbook de producción no lo pide en su
   checklist (`docs/06-runbooks/08-instalacion-produccion.md:36-51`: Docker,
   Compose, RAM, disco, dominio, puertos, credenciales LLM; **ni Python ni
   `pip`**) y el validador de prerequisitos tampoco lo comprueba. En
   Debian/Ubuntu limpio no existe siquiera un binario llamado `python`.
9. **El propio compose del instalador exige el repo** (`build:` en `:21-24` y
   `:43-46`) — el hecho con el que abre este ADR.

Y dos cabos sueltos menores que conviene arreglar de paso, porque son del mismo
contrato roto:

- **`config/global.yaml` se escribe y no lo monta nadie.** Lo escribe
  `real_step_executor.py:147-151`; no aparece en ningún `volumes:` del generador.
- **El generador no emite `searxng`**, que el compose canónico sí declara
  (`docker/docker-compose.yml:506-513`, ADR 0067, con su propio bind de
  `docker/searxng/settings.yml`). No se ha podido determinar si es omisión
  deliberada o desfase: queda como duda abierta.

**Lo que ninguno de estos bloqueantes es: sistémico.** La parte difícil —que el
conocimiento de «cómo es el stack» viaje fuera del repo— **ya está resuelta**. El
generador de configuración es puro Python sin una sola plantilla en disco: el
compose se construye como un `dict` tipado y se serializa con `yaml.dump`
(`compose_generator.py:10-20`, «this module is pure, no I/O»). Una imagen
publicada del instalador sabe generar el compose entero sin ver el repositorio.
Lo que falta son **seis ficheros y dos contextos de build**, no una
reimplementación.

## La superficie de seguridad del camino sin clon

Cinco cosas ya están decididas en este repo y no hay que reinventarlas. La sexta
es la que no tiene precedente, y es la que de verdad decide.

**1. El socket de Docker: el principio no es «los workers no lo montan».** El
[ADR 0060](0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md) fija que
**un único contenedor** lo monta —el `docker-socket-proxy`, en solo-lectura— y
que la API pasa por una ACL por endpoint:
`CONTAINERS`/`IMAGES`/`NETWORKS`/`POST` permitidos, `EXEC`/`VOLUMES`/`SWARM`
**denegados** (`ADR 0060:43-57`). La alternativa «(a) socket directo en el
worker» está **rechazada por escrito**, y el motivo es literal: _escape a root_.

Esto importa porque el camino sin clon empuja justo hacia ahí:
`apps/installer/docker-compose.installer.yml` **no monta nada** —no tiene clave
`volumes:` en ninguno de sus dos servicios— y el propio fichero declara una
«Fase B» pendiente en la que «the backend mounts the host docker socket / data
dir to actually provision the stack» (`:14-16`), promesa que repite
`apps/installer/backend/Dockerfile:33-35`. Es decir: **el diseño pendiente del
instalador containerizado es exactamente la alternativa que el ADR 0060
rechazó.** Y el socket-proxy no sirve de salida: su ACL deniega `VOLUMES`, y el
instalador necesita bind-mounts del host para escribir `/data/agent-platform`.
O se amplía esa ACL —debilitando el proxy **para todos**— o el instalador
necesita otra vía. Eso es decisión de ADR, no de implementación.

**2. Superficie publicada: sólo Caddy.** El
[ADR 0061](0061-reverse-proxy-tls.md) fija 80/443 como única superficie y por eso
se retiraron los `ports` de api-server y admin-panel. El instalador publica hoy
**8080 y 3100 en todas las interfaces**
(`apps/installer/docker-compose.installer.yml:28-29` y `:51-52`, sin bind a
`127.0.0.1`), **sin autenticación** (`main.py:366-570`: los endpoints sólo llevan `Depends`
de servicios, ningún gate de identidad) y con **CORS `allow_origins=["*"]`**
(`main.py:356-364`), justificado por un comentario que afirma que el instalador
es «reachable only on the install host's loopback/LAN» — restricción que el
compose no impone. Distribuir eso como imagen convierte un descuido de bootstrap
local en un endpoint sin auth que mintea el **root token de Vault y las cinco
unseal keys** (`main.py:314-316`).

**3. Digest sobre tag mutable: ya hay patrón, y es bueno.** El
[ADR 0148](0148-distribucion-imagenes-runtime-por-digest.md) lo decidió para las
14 imágenes de runtime, y la implementación está en
`packages/shared-test-runtimes/src/shared_test_runtimes/images.py`: `_DIGEST_RE`
valida con regex estricta (`:66-69`), `RuntimeImageManifestError` prefiere fallar
al arrancar antes que componer una referencia dudosa (`:72-77`),
`split_reference` esquiva el bug clásico del `:` del puerto (`:85-98`),
`pinned_pull_reference` distingue «no declara procedencia» de «procedencia
inválida» (`:101-112`), y `RUNTIME_IMAGE_REGISTRY` permite reapuntar a un mirror
sin debilitar nada, porque el digest sigue mandando. **Las seis imágenes de
plataforma que el instalador descarga NO usan nada de esto**: van por tag
mutable (`compose_generator.py:99-100`) y se bajan con un `docker compose pull`
liso. Es la misma objeción con la que el 0148 condenó el statu quo de las 14, sin
resolver en las 6.

**4. Trivy corre DESPUÉS del push, y está declarado.** En
`.github/workflows/release-images.yml:71-78` el comentario lo admite: «el
escaneo va DESPUÉS del push, no antes… se prefirió el cambio de menor riesgo».
Un Trivy rojo no significa «no se publicó»: significa «se publicó y además está
roto». Hay **una** salvedad que sí es gate efectivo, y sólo existe en el workflow
de los runtimes: `refresh-digests` corre sólo si los catorce builds acabaron en
verde (`.github/workflows/build-runtime-templates.yml:224-227`), así que una
imagen con HIGH/CRITICAL nunca llega al catálogo aunque su blob esté publicado.
**`release-images.yml` no tiene ningún job equivalente**: si el instalador se
publica desde ahí, hereda el flanco sin la mitigación.

**5. La guarda de digest en los `FROM` no mira `apps/`.**
`tests/unit/test_supply_chain_config.py:897-913` itera sobre los Dockerfiles bajo
`docker/`, así que `apps/installer/backend/Dockerfile:5` —`FROM
python:3.12-slim`, **sin digest**— pasa en verde por no estar mirado. Y el
contraste es el hallazgo, no el hueco suelto: la **UI** del instalador sí está
pineada por digest (`apps/installer/Dockerfile:23`) con un comentario de
diecisiete líneas que argumenta que «temporal no significa inofensivo — este
contenedor es el que recoge las credenciales de la instalación». El **backend**,
que es quien de verdad mintea el root token y las unseal keys, es el que se quedó
fuera. La decisión está tomada y escrita; no se aplicó donde importaba.

**6. No hay firma de imágenes. De ningún tipo.** Cero `cosign`, cero `sigstore`,
cero SLSA, cero attestations sobre contenedores en `docs/`, `.github/`, `apps/`,
`scripts/`, `packages/` y `tests/` (los únicos aciertos del grep son conceptos de
dominio ajenos: `provenance` es una columna del marketplace, `attestation` es
WebAuthn). **Esto es lo que separa «traer una imagen» de «confiar en una
imagen»**: hoy un `docker run ghcr.io/daycry/installer:v1.0.0` no tendría ni
digest ni firma, y no habría forma criptográfica de distinguir la imagen que
publicó el pipeline de la que publicó quien comprometa el `GITHUB_TOKEN` o la
cuenta. La cadena existente (digest-pinning + Dependabot + Trivy) protege contra
CVEs y contra deriva, **no contra suplantación**. Y a diferencia de todo lo
anterior, aquí no hay decisión escrita que consultar: es el único hueco sin
precedente.

**Por qué el listón del instalador debe ser MÁS alto que el de las seis imágenes
de plataforma, y no igual.** El instalador es el punto donde se siembra todo el
material de arranque **en claro**, antes de que Vault exista, y se muestra una
sola vez sin recuperación posible (`scripts/install.sh:29-31`: «printed to stdout
EXACTLY ONCE… there is no recovery»; `vault_bootstrap.py` captura las unseal keys
de Shamir). Una imagen de instalador comprometida no roba «unas credenciales»:
roba la raíz de confianza completa del stack, en el único instante en que está en
claro. Ese material es además el que sostiene la excepción Fernet del
[ADR 0146](0146-fernet-en-db-vs-vault.md), que existe porque el desellado de
Vault es manual ([ADR 0145](0145-vault-operable-tokens-y-unseal.md)).

**Una advertencia de calendario, y su corrección (2026-08-27).** Este párrafo
afirmaba que ninguno de estos gates corría porque «CI lleva caído desde el
2026-07-30 por facturación», citando `CONTINUE_HERE.md:223`. **Era falso**: CI
corre y pasa — run `33083267973` sobre `1aec3ebc`, doce jobs en verde.

Se deja escrito porque el error importa más que el dato. La afirmación se tomó
de un documento rancio sin comprobarla contra la realidad, y acabó **tres veces
dentro de un ADR firmado**: exactamente el modo de fallo que esta plataforma
persigue, una medida que miente costando más que no tener medida.

Lo que sí se sostiene es la regla que la sustituye, y que no caduca: **no se
publica sin que los gates corran de verdad**, comprobándolo en el momento y no
en un documento que alguien escribió hace un mes.

## Opciones

Las cuatro se presupuestan **por encima de un trabajo previo que ninguna evita**:
los seis auxiliares (bloqueante 1), los dos tinyproxy (2), los perfiles (3) y el
AppArmor (4). Ese suelo es **3-5 días** y se debe **aunque se elija C**, porque
sin él el camino (3) tampoco termina.

### A. Séptima imagen del instalador, arrancada con `docker run`

Se añade `installer` (y `installer-backend`) a `release-images.yml`, se le mete
el CLI de Docker a la imagen, y el operador ejecuta una línea:

```bash
docker run --rm -it \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /data/agent-platform:/data/agent-platform \
  ghcr.io/daycry/installer:v1.0.0
```

- **A favor**: es el «una línea» literal que pide el camino (1). El operador no
  ve Python, ni `bootstrap.sh`, ni perfiles: el contenedor lo trae todo.
- **En contra**: monta el socket, que es **acceso root efectivo al host** y la
  alternativa (a) que el
  [ADR 0060](0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md) rechazó por
  escrito. Exige un ADR propio de excepción («contenedor efímero de bootstrap, no
  servicio del stack») y no puede pasar por el socket-proxy, cuya ACL deniega
  `VOLUMES`. Añade el CLI de Docker a la imagen (superficie + CVEs) y arrastra el
  wizard sin auth con 8080/3100 en `0.0.0.0` si se distribuye tal cual.
- **Coste**: **5-8 días** sobre el suelo, más un ADR de excepción al 0060, más
  cerrar la Fase B (que hoy es una promesa escrita en dos sitios y cero código).

### B. Bootstrap por fichero compose descargable

El artefacto no es una imagen suelta sino **un fichero** que se descarga y se
levanta; sus dos servicios pasan de `build:` a `image:` apuntando a las imágenes
publicadas:

```bash
curl -fsSLO https://<host>/docker-compose.installer.yml
docker compose -f docker-compose.installer.yml up -d
```

- **A favor**: el cambio más pequeño que convierte el hecho que abre este ADR en
  falso — son cuatro líneas de un fichero de 56 (`:21-24` y `:43-46`). El fichero
  es auditable **antes** de ejecutarlo, a diferencia de un `curl | bash`; el
  operador ve qué imágenes se bajan y con qué digest. Y sigue publicando las dos
  imágenes, así que no compite con A: es su envoltorio.
- **En contra**: **no resuelve nada por sí solo**. El wizard que arranca sigue
  siendo el `FakeStepExecutor` (`main.py:244-247`), así que entrega un camino sin
  clon hacia una simulación. Hace falta decidir antes la pregunta 3 (provisiona o
  genera), o el resultado es un instalador descargable que no instala.
- **Coste**: **1-2 días** sobre el suelo y sobre la publicación de las dos
  imágenes (que comparte con A y con D).

### C. No hacer nada: el clon sigue siendo requisito

Se retira la ambigüedad en vez del clon. El camino (1) se declara fuera de
alcance, y se arregla lo que hoy promete de más.

- **A favor**: es lo único que se puede hacer **hoy** y honestamente. Y hay tres
  contradicciones doc↔código que arreglar de todos modos:
  `apps/installer/README.md:51` dice que el instalador aprovisiona de verdad
  cuando `main.py:262-269` dice que no; `README.md:166` y `README.es.md:168`
  nombran `ghcr.io/agentic-platform/*` cuando el workflow publica en
  `ghcr.io/${{ github.repository_owner }}` = `ghcr.io/daycry`
  (`release-images.yml:29`), que es lo que el compose generado pide; y
  `docs/02-getting-started/01-installation.md` pone el `docker compose up` en el
  paso 3 (`:43-50`) y el `cp docker/.env.example docker/.env` en el paso 5
  (`:96-103`), cuando nueve variables del compose usan `${VAR:?…}` sin default y
  abortan sin ese fichero.
- **En contra**: **no es neutral y no es gratis**, y ésa es la trampa. C **no
  significa «todo sigue funcionando»**: el camino (3) tampoco termina hoy, así que
  el suelo de 3-5 días se paga igual. Lo único que C ahorra es la publicación de
  las imágenes del instalador. Y deja la promesa implícita del README
  —`./scripts/install.sh --config install.yaml` como forma de empezar
  (`README.md:113-118`)— apuntando a un comando que exige un `.venv` que ningún
  documento pide.
- **Coste**: **0,5-1 día** de correcciones documentales, **más el suelo de 3-5
  días que no evita.**

### D. El instalador GENERA, no provisiona (y por eso no toca el socket)

El contenedor no habla con Docker. Se le monta **sólo** la raíz de datos, escribe
el árbol de arranque completo —compose, `.env`, `config/global.yaml`, el
`Caddyfile` y **los seis auxiliares que hoy faltan**— y sale. El `up` lo ejecuta
el operador:

```bash
docker run --rm -it -v /data/agent-platform:/data/agent-platform \
  ghcr.io/daycry/installer:v1.0.0 generate --config install.yaml
cd /data/agent-platform && docker compose up -d --wait
docker compose run --rm bootstrap   # Vault init + unseal + siembra + credenciales
```

- **A favor**: **la pregunta del socket desaparece**, y con ella el choque con el
  ADR 0060 y la necesidad de un ADR de excepción. La imagen no necesita el CLI de
  Docker (bloqueante 5 evaporado). El bootstrap de Vault y la siembra corren
  **dentro de la red del stack ya levantado**, que es donde tienen que correr, y
  eso permite publicar el paso como servicio del compose generado en vez de como
  una capacidad del contenedor de bootstrap. Reutiliza el CLI, que es el único
  camino que hoy funciona de verdad, en vez del wizard, que finge.
- **En contra**: **el «una línea» se convierte en tres.** Exige diseñar dónde
  vive el paso de finalización (servicio one-shot bajo `profiles:` del compose
  generado, o un segundo `docker run --network`), y ese diseño **no está medido**:
  es la propuesta de este documento, no un hallazgo. Y hay que decidir qué pasa
  con el wizard HTTP — que en este esquema o se cablea al ejecutor real, o se
  retira.
- **Coste**: **4-6 días** sobre el suelo, incluida la publicación de las imágenes
  del instalador que comparte con A y B.

## Recomendación de este documento

> Es una **recomendación**, no una decisión. Se escribió antes de la firma y se
> conserva para que se vea qué se le puso delante al operador. **El operador la
> aceptó el 2026-08-27**; lo que manda es §«Decisión», más abajo.

**D, entregada con el envoltorio de B, y A descartada.** Es decir: se publican
las imágenes del instalador (que A, B y D necesitan igual), el artefacto de
entrada es un fichero compose descargable y auditable antes de ejecutarlo (B), y
lo que ese fichero arranca es un instalador que **genera y no provisiona** (D).

Tres razones, por orden de peso:

1. **Es la única que no pide una excepción al ADR 0060.** A obliga a montar el
   socket, que es literalmente la alternativa que ese ADR rechazó por «escape a
   root», y no puede pasar por el socket-proxy porque su ACL deniega `VOLUMES`.
   Cambiar esa ACL debilitaría el proxy para todos. Pagar una excepción de
   seguridad estructural para ahorrarle dos comandos al operador es mal negocio,
   y peor en el contenedor que mintea el root token de Vault.
2. **Reutiliza el camino que funciona.** El instalador real es el CLI
   (`main.py:262-269` lo dice), y el wizard es un stub. D empaqueta el CLI; A y B
   empaquetan la fachada del wizard y luego hay que cablearla.
3. **El orden importa más que la opción.** Se recomienda **hacer el suelo primero
   y publicar después**: los seis auxiliares, los dos tinyproxy y los perfiles son
   el grueso del trabajo, se deben con clon o sin él, y arreglan **hoy** el camino
   (3), que es el único soportado.

Sobre la pregunta 5, la respuesta mínima defendible: **digest sí, firma todavía
no.** El digest se extiende con el módulo que ya existe
(`shared_test_runtimes/images.py`) y no cuesta inventar nada. La firma no tiene
precedente en el repo y merece **su propio ADR**, no un párrafo dentro de éste:
adoptar cosign implica decidir quién custodia la clave (o si se usa la identidad
OIDC del workflow), quién verifica y qué pasa cuando la verificación falla en un
host sin salida a internet. Lo que sí no debe pasar es publicar el instalador
**antes** de pinear por digest las seis imágenes de plataforma: sería mover el
eslabón débil un paso, con un instalador verificado que descarga seis imágenes
sin verificar.

Sobre la pregunta 1, la recomendación es **sí, el camino (1) debe existir** —pero
como _dos comandos auditables_, no como una línea mágica. El único precedente de
`curl | bash` del repo es el flojo
(`docs/06-runbooks/08-instalacion-produccion.md:75`,
`curl -fsSL https://get.docker.com | sh`, sin verificación alguna), y si se toma
como autorización tácita el camino sin clon nacerá con el estándar más bajo de la
casa.

## Decisión

Decidido por el **operador** el **2026-08-27**. Gana la **opción D entregada con
el envoltorio de la B**; la **opción A queda descartada**. Las tres piezas, sin
margen de interpretación:

1. **El instalador GENERA, no provisiona** (pregunta 3). El contenedor **no habla
   con el daemon de Docker**: se le monta **sólo** la raíz de datos, escribe el
   árbol de arranque completo —compose, `.env`, `config/global.yaml`, el
   `Caddyfile` y los auxiliares del stack— y sale. El `docker compose up` lo
   ejecuta el operador, en su host y con sus permisos.
2. **El artefacto de entrada es un fichero compose descargable y auditable**
   (pregunta 2), que es lo que aporta la opción B como envoltorio: los dos
   servicios del instalador pasan de `build:` a `image:` y sus imágenes se
   publican —cosa que A, B y D necesitaban por igual—. **No** es un `curl | bash`:
   el fichero se lee **antes** de ejecutarlo.
3. **A queda descartada, y el motivo es el socket de Docker.** Montar
   `/var/run/docker.sock` es acceso root efectivo al host, que es literalmente la
   alternativa (a) que el
   [ADR 0060](0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md) rechazó por
   escrito por «escape a root». Y no hay salida por el `docker-socket-proxy`: su
   ACL deniega `VOLUMES`, y el instalador necesita bind-mounts del host, así que
   la única forma de pasar por ahí sería relajar esa ACL **para todos los
   contenedores que la usan**.

La forma de uso que queda firmada son **tres comandos**:

```bash
docker run --rm -v /data/agent-platform:/data/agent-platform \
  ghcr.io/daycry/installer:v1.0.0 generate --config install.yaml
cd /data/agent-platform && docker compose up -d --wait
docker compose run --rm bootstrap   # Vault init + unseal + siembra + credenciales
```

### Las tres razones, por orden de peso

1. **Es la única que no pide una excepción al ADR 0060.** Pagar una excepción de
   seguridad **estructural** —una que debilita el modelo para todo el stack, no
   sólo para el instalador— a cambio de ahorrarle dos comandos al operador es mal
   negocio, y peor en el contenedor que mintea el root token de Vault y las cinco
   unseal keys en claro. Con D la pregunta del socket no se responde mejor: **se
   evapora**.
2. **Reutiliza el camino que funciona.** El instalador real es el CLI —lo dice el
   propio código, `main.py:262-269`— y el wizard HTTP es un stub cableado a
   `FakeStepExecutor`. D empaqueta el CLI; A y B empaquetaban la fachada del
   wizard y dejaban el cableado para después, que es apostar el camino nuevo a un
   trabajo que aún no está hecho.
3. **El orden importa más que la opción: el suelo primero, publicar después.** Los
   auxiliares, los dos tinyproxy y los perfiles son el grueso del trabajo, se
   deben **con clon o sin él**, y arreglan **hoy** el camino (3), que es el único
   soportado. Publicar antes habría sido distribuir un instalador que no instala,
   y con un artefacto ya publicado encima sale más caro descubrirlo.

### Las preguntas que este documento planteaba, respondidas

| #   | Pregunta                                               | Respuesta firmada                                                                                          |
| --- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| 1   | ¿Existe el camino sin clonar?                          | **Sí** — pero como **dos comandos auditables**, no como una línea mágica.                                  |
| 2   | ¿Cuál es su artefacto?                                 | Un **fichero compose descargable** (envoltorio B) que arranca **imágenes publicadas** del instalador.      |
| 3   | ¿Provisiona el host o sólo genera?                     | **Genera.** Ni socket de Docker, ni CLI de Docker dentro de la imagen (bloqueante 5 evaporado).            |
| 4   | ¿Los auxiliares viajan, o el compose deja de pedirlos? | **Viajan con el instalador.** Ya hecho: `installer_backend.stack_assets` → `{data_root}/stack/` (PR #124). |
| 5   | ¿Qué listón de integridad?                             | **Digest sí, firma todavía no.**                                                                           |
| 6   | ¿Qué se hace con los dos tinyproxy?                    | **Sigue abierta.** Sus contextos ya viajan; si además se publican como imagen, esta firma no lo decide.    |

#### Pregunta 1: sí al camino sin clon, pero en dos comandos auditables

Que exista no autoriza la forma. La línea mágica —`curl … | bash`— **se descarta
expresamente**: el único precedente de `curl | bash` del repo es el flojo
(`docs/06-runbooks/08-instalacion-produccion.md:75`,
`curl -fsSL https://get.docker.com | sh`, sin verificación de ningún tipo), y
tomarlo como autorización tácita haría nacer el camino sin clon con **el estándar
más bajo de la casa**. Lo que se firma es lo contrario: primero se descarga algo
que se puede leer, y luego se ejecuta. El precio está aceptado, y conviene decirlo
en voz alta en vez de descubrirlo en el runbook: el «una línea» se convierte en
tres.

#### Pregunta 5: digest sí, firma no — y por qué la firma no cabe en este ADR

El **digest** se adopta, y no cuesta inventar nada: el módulo ya existe
(`packages/shared-test-runtimes/src/shared_test_runtimes/images.py`,
[ADR 0148](0148-distribucion-imagenes-runtime-por-digest.md)) y se **extiende**,
en vez de escribir un parser nuevo que repita el bug del `:` del puerto.

La **firma** (cosign / sigstore) **no se firma aquí**, y no por desinterés: no
tiene un solo precedente en el repo y merece **su propio ADR**, porque adoptarla
es decidir tres cosas que este documento no ha medido — **quién custodia la
clave** (o si se usa la identidad OIDC del workflow), **quién verifica**, y **qué
pasa cuando la verificación falla en un host sin salida a internet**, que en la
máquina donde se instala esto no es un escenario exótico. Meter eso en un párrafo
sería decidir por omisión justo la parte cara. Queda como paso 10 de la lista de
trabajo.

Y con ello se nombra lo que esta firma **no** cubre: la cadena que hay hoy
—digest-pinning + Dependabot + Trivy— protege contra CVEs y contra deriva, **no
contra suplantación**. Quien comprometa el `GITHUB_TOKEN` o la cuenta puede
publicar una imagen que el digest describirá fielmente.

#### El orden duro que acompaña a la firma

**No se publica la imagen del instalador antes de que las seis imágenes de
plataforma se puedan pinear por digest.** Publicar antes sería mover el eslabón
débil un paso y llamarlo arreglo: un instalador verificado que se descarga seis
imágenes sin verificar. Y una segunda condición, de higiene y no de diseño: **se
comprueba que los gates corren de verdad en el momento de publicar** — Trivy, la
guarda de digest y los tests—, no que constaba en algún sitio que corrían.

(Aquí se afirmaba que CI llevaba caído desde el 2026-07-30 por facturación. Era
una cita a un documento rancio y se corrigió el 2026-08-27: CI pasa, run
`33083267973`. Ver la advertencia de calendario en §«La superficie de seguridad».)

## Consecuencias de no decidir

No es neutral, y hay que nombrarlo:

- **El producto sigue sin poder instalarse**, y no sólo sin clon: el camino (3)
  falla por las rutas relativas aunque se corte el tag `v1.0.0` del
  [ADR 0160](0160-versionado-de-la-plataforma.md). Son dos averías independientes
  en el mismo paso, y arreglar sólo una deja el mismo síntoma.
- **La avería de las rutas relativas sigue sin dueño.** Ningún ADR ni plan la
  menciona; el `tests/e2e/test_install_from_scratch.py` que la descubriría se
  auto-salta sin `E2E_INSTALL=1` y nunca ha podido correr en verde porque las
  imágenes no existen. Si nadie la escribe, se descubrirá en la primera
  instalación real — y el modo de fallo no señala la causa: un Postgres `healthy`
  sin `pgvector`.
- **La Fase B sigue siendo una promesa escrita en dos sitios**
  (`apps/installer/docker-compose.installer.yml:14-16` y
  `apps/installer/backend/Dockerfile:33-35`) que apunta a un patrón rechazado.
  Cuanto más tiempo esté escrita sin decisión, más probable es que alguien la
  implemente tal cual la lee.
- **La documentación seguirá diciendo tres cosas incompatibles** sobre si el
  wizard instala (`apps/installer/README.md:51` vs `main.py:262-269`), sobre dónde
  se publican las imágenes (`README.md:166` vs `release-images.yml:29`) y sobre en
  qué orden se instala (`docs/02-getting-started/01-installation.md`, pasos 3 y 5).

## Qué se hace cuando esto se acepte

**Aceptado el 2026-08-27**, así que la lista deja de ser condicional. Se conserva
en su orden y con su redacción original, y cada paso lleva delante su estado
**medido sobre el árbol ese mismo día**. El estado no es adorno: buena parte de
este ADR existe porque una avería sobrevivió meses sin dueño, y una lista de
trabajo sin estado repite ese modo de fallo un nivel más arriba.

1. **HECHO** — PR **#124**, commit `1129f987`, mergeando. Cerrar el contrato
   compose↔ficheros generados. Resultaron ser **once** rutas huérfanas con el
   overlay de monitorización (cinco con el perfil básico), no seis: los auxiliares
   viajan ahora dentro del paquete (`installer_backend.stack_assets`, copia byte a
   byte de `docker/` con guarda de deriva) y el paso `GENERATE_CONFIG` los escribe
   bajo `{data_root}/stack/`. Con eso caen también **las dos colisiones**:
   `stack/postgres/init` ya no resuelve dentro del PGDATA y `stack/vault/config.hcl`
   se escribe como fichero. La guarda que faltaba existe y **no lleva lista escrita
   a mano**: deriva las rutas del propio compose generado y las cruza con lo que la
   instalación produce de verdad
   (`tests/unit/test_generated_compose_is_installable.py`,
   `tests/unit/test_installer_ships_stack_assets.py`), así que un montaje nuevo
   entra solo.

   **Lo que ese PR no cubrió, y sigue siendo suelo**: los tres perfiles YAML
   (`scripts/install-profiles/*.yaml`, bloqueante 3) y el perfil AppArmor
   (bloqueante 4) siguen sin viajar en la imagen.

2. **PENDIENTE, y ya no bloquea.** Medir en un host limpio lo que este documento
   deduce. El paso 1 le quitó el filo: sin bind ausente, la cadena «bind ausente →
   Docker crea un directorio vacío → el init de Postgres no corre / Vault no lee su
   config» **no puede empezar**, y el contexto de build tampoco falta ya. Lo que
   queda es conocimiento de regresión —qué se rompería si alguien deshace el paso
   1— y se sigue debiendo, con su trampa conocida: sólo corre en Linux con
   `E2E_INSTALL=1` y **salta en verde por diseño** sin esa variable.
3. **PARCIAL.** De las contradicciones doc↔código de la opción C:
   - **Corregida**: `apps/installer/README.md` ya no afirma que el instalador
     aprovisiona de verdad — ahora dice que el wizard es una simulación y que el
     camino real es el CLI.
   - **Abierta**: `README.md:176` y `README.es.md:178` siguen nombrando
     `ghcr.io/agentic-platform/*`, cuando el workflow publica en
     `ghcr.io/${{ github.repository_owner }}` = `ghcr.io/daycry`
     (`release-images.yml:29`), que es lo que pide el compose generado.
   - **Abierta**: `docs/02-getting-started/01-installation.md` sigue con el
     `docker compose up` en el paso 3 (`:49`) y el
     `cp docker/.env.example docker/.env` en el paso 5 (`:104-108`). El compose
     canónico exige **ocho** variables `${VAR:?}` sin default —contadas hoy; el
     cuerpo de §«Opciones» dijo nueve— y el override de desarrollo no aporta
     ninguna: seguir el orden escrito aborta.
   - **Abierta** también la mitad documental del bloqueante 8: la checklist de
     `docs/06-runbooks/08-instalacion-produccion.md:53-74` sigue sin pedir Python
     ni `pip`, y `scripts/install.sh` sigue sin ser autosuficiente.
4. **PENDIENTE.** `hvac` y `pyyaml` siguen sin declarar en
   `apps/installer/backend/pyproject.toml:6-16` —`yaml` sigue llegando de rebote
   por el extra `[standard]` de `uvicorn`— y `real_step_executor.py:164-167` sigue
   capturando sólo `VaultBootstrapError`, así que el `ImportError` saldría crudo.
5. **PARCIAL.** Los contextos de los dos tinyproxy **ya viajan** con el instalador
   (`stack_assets/egress-proxy/`, `stack_assets/registry-proxy/`), que es lo que
   desbloqueaba el camino (3). Publicarlos además como imagen es la **pregunta 6,
   que esta firma no responde**; el día que se responda que sí, hay que mover a la
   vez las dos guardas de `tests/unit/test_infra_images_are_scanned.py`
   (`:232-242` y `:264-282`) **entendiendo el historial del `pull_policy: build`**,
   o se reintroduce el rc=1 que ya se pagó una vez.
6. **PENDIENTE — y es el paso que bloquea al 7.** El compose generado sigue
   componiendo por tag mutable (`compose_generator.py:99-100`), y
   `apps/installer/backend/Dockerfile:5` sigue siendo `FROM python:3.12-slim`
   **sin digest**, con la guarda de `tests/unit/test_supply_chain_config.py`
   mirando todavía sólo `docker/`.
7. **PENDIENTE**, bloqueado por el paso 6 —orden duro de §«Decisión»—. (Aquí
   constaba un segundo bloqueo «por CI caído desde el 2026-07-30»; era falso y
   se retiró el 2026-08-27.)
   Sigue abierto si `release-images.yml` necesita un gate posterior al push
   equivalente al `refresh-digests` de los runtimes
   (`.github/workflows/build-runtime-templates.yml:224-227`).
8. **PENDIENTE, pero ya con forma.** Gana D, así que la Fase B es el subcomando
   `generate` —que hoy no existe: `cli.py:722-808` sólo declara `install`,
   `uninstall` y `reinstall`— más el paso de finalización dentro de la red del
   stack ya levantado. **El ADR de excepción al 0060 deja de hacer falta**, que es
   la mitad del ahorro de esta firma. Sigue abierto qué pasa con el wizard HTTP: o
   se cablea al ejecutor real con guarda anti-simulación, o se retira.
9. **PENDIENTE.** El instalador sigue publicando 8080 y 3100 en `0.0.0.0`, sin
   autenticación y con CORS `*`, delante del endpoint que revela el root token de
   Vault. Con D la superficie se encoge sola **si** el wizard se retira en el paso
   8; mientras exista y se distribuya, esto va **antes** de la distribución, no
   después.
10. **PENDIENTE.** Abrir el ADR de firma de imágenes (cosign / identidad OIDC del
    workflow). Es lo único de la lista sin precedente en el repo, y esta firma lo
    deja explícitamente fuera: ver §«Decisión», pregunta 5.

## Dudas abiertas

Se dejan escritas porque presupuestar sobre ellas sería inventar. **Las dos
primeras siguen sin medir, pero ya no son alcanzables** desde que el paso 1 de la
lista de arriba hizo que ningún bind quede ausente: se conservan como
conocimiento de regresión, no como incógnitas del presupuesto.

1. **El modo de fallo exacto de las dos colisiones** (`./postgres/init` dentro del
   PGDATA, `./vault/config.hcl` como directorio) está **deducido** del
   comportamiento documentado de Docker, no medido en este stack.
2. **Si un contexto de build inexistente aborta el proyecto entero** o sólo esos
   dos servicios. Cambia si el operador ve el problema o se queda con un stack a
   medias.
3. **Si los paquetes `ghcr.io/daycry/*` son públicos o privados.** No se puede
   verificar desde el repo, y cambia por completo el modelo de amenaza del camino
   sin clon: si son privados, `docker run` exige login y el problema es otro.
4. **Si `docker/build-push-action@v7` emite attestations de provenance SLSA por
   defecto** al empujar al registry. No verificado (haría falta
   `docker buildx imagetools inspect` sobre el manifiesto publicado). Si las
   emitiera, habría algo de procedencia verificable que ni el repo ni la
   documentación mencionan — pero no sería firma con clave propia y nadie la
   verifica del lado del instalador. **No se cuenta como garantía.**
5. **Si el generador debería emitir `searxng`.** El compose canónico lo declara
   (`docker/docker-compose.yml:506-513`); el generado no. No se ha determinado si
   es omisión deliberada o desfase.
6. **Si el CLI necesita correr como root.** El `.env` y el compose se escriben con
   `0600`/`0640` bajo un `data_root` que el manual crea con `sudo mkdir`
   (`docs/06-runbooks/08-instalacion-produccion.md:83-86`). No se ha comprobado
   qué pasa si el CLI no corre como root; sería otro prerrequisito no documentado.
7. **Si `docker compose pull` sobre un `image:` con tag ya descargado revalida
   contra el registry** o usa la copia local. Afecta a cuánto de real es el riesgo
   del tag mutable en una reinstalación, no a su existencia.
8. **No se ha leído el `.docx` maestro**, que encabeza la cadena de precedencia de
   `CLAUDE.md`. Si dijera algo sobre distribución del instalador, gana a todo lo
   anterior.
