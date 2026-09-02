---
title: "ADR 0148: Distribución de las imágenes de runtime — registry y referencia inmutable"
status: accepted
date: 2026-07-31
deciders: [operador]
relates_to: [0012, 0051, 0094, 0129, 0147]
plan_referenced: prod-11-cadena-suministro
task: [task_registry_adr_12]
docs_language: es
---

# ADR 0148: Distribución de las imágenes de runtime — registry y referencia inmutable

> **Estado: `accepted` (firmado el 2026-08-01).** **Opción (a)**: GHCR con tag
> versionado y resolución por digest. La (b) —registry self-hosted como mirror—
> queda **documentada pero NO construida** hasta que exista una instalación
> air-gapped real; ahí esta firma se aparta de la recomendación del documento, y
> el motivo está escrito. Detalle en § «Decisión del operador».

## El statu quo, en tres líneas de código

Las **14 imágenes de runtime** son el lugar donde el Principio Rector 2 deposita
el aislamiento del código NO confiable. Hoy se referencian y se producen así:

1. `packages/shared-test-runtimes/src/shared_test_runtimes/catalog.py:31` —
   `_IMAGE_TAG = "v1"`, y `:41` compone `agent-runtime-{slug}:{_IMAGE_TAG}`.
   **Sin registry y con tag mutable.**
2. `.github/workflows/build-runtime-templates.yml` — la matriz de 14 construye
   con `push: false` + `load: true` y etiqueta `agent-runtime-<slug>:v1`. CI
   demuestra que el Dockerfile compila y **tira la imagen a la basura**.
3. `apps/workers/src/workers/test_runtime.py:693` —
   `containers.run(template.docker_image, …)`: el worker ejecuta lo que haya en
   el daemon local bajo ese nombre. No hay `pull` ni verificación de procedencia.

La consecuencia medida: **cada host construye su propia variante** de las 14
imágenes. Dos instalaciones del mismo commit del producto ejecutan el código no
confiable de sus tenants en imágenes distintas, y nadie puede decir cuál. El
digest-pinning de las bases que `task_digest_pin_11` acaba de aplicar (22 `FROM`
con `@sha256:`) fija los ingredientes, no el resultado: el `apt-get`/`npm i`/
`pip install` de cada Dockerfile sigue resolviendo contra la red en el instante
del build de cada host.

Y hay una asimetría difícil de defender: **las 5 imágenes de plataforma sí se
publican**. `prod-01` entregó `.github/workflows/release-images.yml`, que empuja
`api-server`, `workers`, `orchestrator`, `notification-dispatcher` y
`admin-panel` a `ghcr.io/agentic-platform/<app>:<tag>`, y el compose que genera
el installer ya las referencia por
`APP_IMAGE_REGISTRY = "${PLATFORM_REGISTRY:-ghcr.io/agentic-platform}"`. El
registry, el login y el patrón ya existen y están en producción **para las
imágenes menos sensibles de las dos familias**.

## Lo que está en juego (y lo que no)

En juego: reproducibilidad e integridad de los sandboxes, tiempo y ancho de
banda del `install.sh`, y el requisito de red del host de un tenant.

**Fuera de este ADR**: la implementación del push y del login es alcance de
[`prod-01-despliegue-ejecutable`](../roadmap/prod-01-despliegue-ejecutable.md).
El hardening de lo que el código puede hacer una vez dentro del contenedor es de
`prod-12`. Aquí solo se decide el **esquema de distribución y referencia**.

Coste del statu quo, para calibrar: las 14 bases incluyen
`mcr.microsoft.com/dotnet/sdk:8.0`, `mcr.microsoft.com/playwright`,
`maven:3.9-eclipse-temurin-21` y `gradle:8-jdk21`. Construirlas todas en el host
del cliente es del orden de decenas de minutos y varios GB de descarga desde
registries de terceros — exactamente el egress que el
[ADR 0094](./0094-egress-runtime-templates-registries-via-proxy-allowlist.md)
tuvo que canalizar por proxy con allowlist.

## Opciones

### (a) GHCR con tag inmutable versionado + resolución por digest — **recomendada**

`build-runtime-templates.yml` gana `push: true` hacia
`ghcr.io/agentic-platform/agent-runtime-<slug>:<version>` y el catálogo pasa a
referenciar `…:<version>@sha256:<digest>`, con el digest resuelto en el momento
de la release y versionado en el repo (un campo `digest` opcional en
`RuntimeTemplate`, poblado por el pipeline). El worker hace `pull` por digest, de
modo que ejecutar la imagen equivocada deja de ser posible.

- **A favor**: reutiliza el registry, el login y el `PLATFORM_REGISTRY` que
  `prod-01` ya puso en producción; una sola build reproducible para todos los
  hosts; instalación mucho más rápida; el digest hace la procedencia verificable;
  Dependabot ya cubre las bases de esos Dockerfiles.
- **En contra**: obliga al host del tenant a alcanzar GHCR (o a un mirror), lo
  que en instalaciones air-gapped exige un paso de importación explícito;
  presupone una organización GitHub con packages y una política de retención;
  publicar 14 imágenes grandes en cada release cuesta tiempo de CI y
  almacenamiento.

### (b) Registry self-hosted dentro del stack Compose

Un servicio `registry:2` en el propio `docker-compose.yml`, alimentado por un
job de bootstrap que construye y empuja las 14 una vez por instalación.

- **A favor**: cero dependencia de un registry externo en tiempo de ejecución;
  encaja con air-gapped; el operador es dueño de todo el material.
- **En contra**: **no resuelve el problema**. Si cada instalación construye para
  su propio registry, las imágenes siguen siendo irreproducibles entre hosts:
  solo se estabiliza dentro de un host. Añade un servicio más que operar,
  respaldar y asegurar. Es complementario a (a) —como mirror/caché—, no
  alternativa.

### (c) Statu quo build-local, documentado y acotado

Se conserva el build por host y se paga la deuda con documentación: runbook de
build, `--no-cache` obligatorio en la release y registro del `docker image
inspect` de lo construido.

- **A favor**: coste cero hoy; ninguna dependencia de red nueva.
- **En contra**: la irreproducibilidad se mantiene justo donde más duele
  (sandboxes de código no confiable); el `.trivyignore` deja de ser fiable
  porque el escaneo de CI no habla de la imagen que corre en el host; y ningún
  test puede afirmar qué se está ejecutando.

## Recomendación

**Opción (a)**, con (b) disponible como mirror opcional para instalaciones sin
salida a internet. El argumento decisivo no es el rendimiento: es que hoy el
sistema no puede responder _«¿qué imagen exacta ejecutó el código de este
tenant?»_, y esa pregunta es la que hace auditable el Principio Rector 2. La
opción (a) además no inventa infraestructura — usa la que `prod-01` ya
desplegó para las 5 imágenes de plataforma.

Dos condiciones para que (a) no empeore nada:

1. **Nada de digest sin vía de refresco.** El digest del catálogo lo reescribe el
   pipeline de release, igual que Dependabot reescribe el de las bases. Un
   digest a mano en `catalog.py` sería la congelación de CVEs que el riesgo 3 de
   `prod-11` describe.
2. **Fallback explícito, no silencioso.** Si el `pull` por digest falla, el
   worker debe abortar la tarea con un error legible, nunca caer a una imagen
   local con el mismo tag: eso reintroduciría el problema disfrazado de
   resiliencia.

## Decisión del operador (2026-08-01)

**Opción (a) — GHCR con tag versionado y resolución por digest.** El ADR pasa a
`accepted`.

El argumento que decide no es el rendimiento de la instalación: es que hoy el
sistema **no puede responder «¿qué imagen exacta ejecutó el código de este
tenant?»**, y sin esa respuesta el Principio Rector 2 —aislamiento por
contenedor— es una afirmación que nadie puede auditar. Un `.trivyignore` que se
refiere a una imagen distinta de la que corre en el host no es una excepción de
seguridad: es una ficción.

**La (b) queda documentada, NO construida.** Ésta es la parte donde esta firma se
aparta de la recomendación del ADR, que proponía (b) como mirror opcional desde
el principio. El motivo: no existe todavía ninguna instalación air-gapped real.
Montar un `registry:2` en el compose para un caso hipotético sería exactamente el
patrón que esta base repite —mecanismo entregado, cero llamantes— y añadiría un
servicio que operar, respaldar y asegurar para nadie.

Lo que sí entra en el alcance es **escribir cómo se haría**: la referencia de
instalación documenta el procedimiento de importación para un host sin salida a
internet (`docker save` / `docker load` por digest, o un mirror levantado a mano),
de modo que el día que aparezca ese cliente el camino esté trazado y no haya que
rediseñarlo con prisa. Cuando exista, se construye.

## Consecuencias (firmado)

- `_IMAGE_TAG = "v1"` desaparece del catálogo, sustituido por versión + digest
  por template. **Hasta entonces no se toca**: añadir hoy un campo `digest` que
  nadie puebla sería el patrón que esta base repite —mecanismo entregado, cero
  llamantes— y prejuzgaría la decisión.
- `build-runtime-templates.yml` pasa a `push: true` con login (implementación:
  `prod-01`); su Trivy pasa a bloquear una publicación, no solo un PR.
- El `install.sh` y el installer descargan en vez de construir; el requisito de
  red del host entra en `docs/04-reference/installation.md`.
- `tests/unit/test_runtime_catalog.py` gana la afirmación de que toda entrada del
  catálogo lleva digest, y `tests/docs/test_supply_chain_docs.py` deja de exigir
  el statu quo.

## Corrección operativa (2026-08-21): el namespace era de otra organización

La decisión no cambia; el destino sí. El ADR nombraba `ghcr.io/agentic-platform`,
y los dos workflows que publican se autentican con `secrets.GITHUB_TOKEN`. Ese
token **sólo puede publicar paquetes en el namespace del dueño del repositorio**
—aquí `daycry`—, así que el destino escrito era inalcanzable por construcción.

No se vio antes porque la publicación ocurre **sólo en `master`**: en rama el
workflow construye y escanea sin empujar, que es el gate de siempre y salía
verde. La primera vez que corrió en `master`, el 2026-08-21, los catorce builds
murieron con `denied: permission_denied: The requested installation does not
exist`.

Lo caro no fue el rojo. Fue que durante esas tres semanas `runtime_images.json`
siguió con `digests: {}` —su fallback documentado— y por tanto **cada host
siguió construyendo su propia variante** de las 14 imágenes donde vive el
aislamiento del Principio Rector 2: exactamente el estado que este ADR se firmó
para terminar. El fallback estaba escrito y era honesto; lo que faltaba era que
alguien midiese que había dejado de ser transitorio.

Y el riesgo estaba anotado. `prod-01` (§Riesgos, punto 1) decía que
`ghcr.io/agentic-platform` _«presupone una organización GitHub con packages
habilitados»_, con la mitigación «decidir el registro en la primera semana».
Nunca se decidió: una premisa pendiente acabó siendo un fallo silencioso.

**Lo que queda.** Los workflows derivan el namespace de
`${{ github.repository_owner }}` en vez de fijarlo a mano, y
[`tests/unit/test_ghcr_namespace_is_pushable.py`](../../tests/unit/test_ghcr_namespace_is_pushable.py)
lo comprueba: un workflow que se autentique en GHCR con el `GITHUB_TOKEN` no
puede empujar a un namespace escrito a mano. La alternativa —conservar
`agentic-platform` con un PAT clásico de larga vida como secreto del repo— se
descarta: entrega un secreto de alcance amplio y permanente a cambio de un
nombre, que es peor cadena de suministro que el problema que resuelve.

## Addendum del 2026-09-02: lo que el pin no cubría (`task_cv_44`)

La auditoría del 2026-09-01 (B-05, B-09) midió dos huecos que esta decisión y
la de las seis apps del instalador dejaban entre medias:

1. **Las dos imágenes que el worker LANZA por tarea** —`agent-runtime:v1` y
   `browser-runtime:v1`— no se publicaban ni pineaban: los 14 templates iban
   por la matriz y las apps por el manifiesto del instalador, y estas dos sólo
   existían como build local. Desde el 2026-09-02 entran en `release-images.yml`
   (job `runtimes`, con Trivy) y en `PLATFORM_APPS` del manifiesto de digests;
   el compose generado se las pasa al worker por `WORKERS_AGENT_RUNTIME_IMAGE` y
   `WORKERS_BROWSER_RUNTIME_IMAGE`, pineadas cuando hay release. El instalador
   pinea ocho, no seis, y sigue siendo todo o nada.
2. **Las imágenes que declara un tenant** en `repository_config.runtime_services`
   aceptaban cualquier `host/repo:tag`, y `version` en un sidecar del catálogo
   recomponía `repo:tag` deshaciendo el pin por digest. Ahora una `image:` propia
   o un `runtime_image` lleva `@sha256:` o viene de un registry/prefijo de
   `WORKERS_TENANT_IMAGE_REGISTRY_ALLOWLIST` (vacía por defecto: sólo digest), y
   una `version` del catálogo resuelve contra un mapa versión→imagen pineada;
   la que no está en el mapa se rechaza nombrando las que hay.

## Referencias

- [ADR 0051 — endpoint del catálogo de runtime templates](./0051-runtime-templates-endpoint.md)
- [ADR 0012 — aislamiento por contenedor del agent-runtime](./0012-aislamiento-contenedores-agent-runtime.md)
- [ADR 0094 — egress de los runtime templates a registries vía proxy](./0094-egress-runtime-templates-registries-via-proxy-allowlist.md)
- [ADR 0129 — servicios e imagen de runtime por proyecto](./0129-servicios-e-imagen-runtime-por-proyecto.md)
- [ADR 0147 — lockfile Python: uv workspace](./0147-lockfile-python-uv-vs-pip-tools.md)
- [Referencia: cadena de suministro](../04-reference/cadena-suministro.md)
- [Plan prod-11 — cadena de suministro](../roadmap/prod-11-cadena-suministro.md) (`task_registry_adr_12`)
