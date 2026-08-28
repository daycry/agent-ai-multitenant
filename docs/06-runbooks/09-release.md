---
title: "Publicar una release: ensayo, tag e imágenes en ghcr"
docs_language: es
audience: operador, system admin
updated: 2026-08-28
---

# Runbook — Publicar una release de la plataforma

Cómo se publica una versión de la plataforma: qué construye y empuja
`release-images.yml`, cómo se **ensaya** la tubería antes de estrenarla con el tag
que van a instalar los clientes, cómo se comprueba que las imágenes están de
verdad en el registro, y qué hacer cuando el ensayo sale mal.

Está escrito para alguien que **no ha publicado nunca** esta plataforma, porque
nadie lo ha hecho: **re-medido el 2026-08-28**, `release-images.yml` sigue con
`total_count: 0` runs y el repositorio no tiene ni una etiqueta. En
`ghcr.io/daycry` no hay hoy ninguna imagen de esta plataforma, y nada de este
runbook debe leerse como que la haya.

Eso es, además, lo único que separa a este proyecto de tener instalación
distribuible: la instalación desde cero **sí** está verificada de punta a punta en
un Linux limpio (run
[`33197920542`](https://github.com/daycry/agent-ai-multitenant/actions/runs/33197920542),
2026-08-28), pero contra imágenes **construidas en el propio job**. Publicar es lo
que falta, y publicar es este runbook. El estado de cada camino de instalación
está en [01-installation-from-scratch.md](./01-installation-from-scratch.md).

> **TL;DR.** Ensaya con `v1.0.0-rc1`, comprueba con `docker manifest inspect` y
> **deslogueado** que las seis imágenes existen, instala desde cero apuntando a
> esas imágenes de ensayo, y sólo entonces empuja el tag de verdad. Empujar el tag
> definitivo es **acto del operador**: nada en el repositorio lo crea, por decisión
> explícita del
> [ADR 0160](../05-architecture-decisions/0160-versionado-de-la-plataforma.md).
>
> **Y un orden que no se negocia**: la imagen del **instalador** —la séptima, la
> que hace posible instalar sin clonar— se publica **después** de que esas seis se
> puedan pinear por digest, nunca antes (§«La séptima imagen»). Desde que existe el
> job `installer`, ese orden ya no depende de que alguien lo recuerde: lo impone
> una cadena de `needs:`.

## Cuándo

- Publicar la **primera** versión de la plataforma (`v1.0.0`).
- Publicar cualquier versión posterior: el procedimiento es el mismo, y el ensayo
  también.
- Republicar tras un fallo a mitad de una release (ver «Si el ensayo falla»).

Para **actualizar una instalación ya en marcha** a una versión ya publicada, este
runbook no es el tuyo: es [03-system-upgrade.md](./03-system-upgrade.md).

## Por qué el ensayo no es opcional

El paso 4 del ADR 0160 lo dice en una frase: **«su primer run no debería ser el
que cuenta»**. Desarrollado, que es lo que importa aquí:

- **La tubería está pagada y nunca ha corrido.** `release-images.yml` se escribió
  en el plan prod-01 y desde entonces sólo se ha comprobado con guardas estáticas
  que leen el YAML (`tests/unit/test_release_images_workflow.py`,
  `test_compose_images_contract.py`, `test_app_images_are_built_by_ci.py`). Ninguna
  de ellas puede saber si el `docker push` funciona: eso sólo lo dice un run.
- **Su hermano murió exactamente así.** `build-runtime-templates.yml` publicaba en
  un namespace de GHCR ajeno al dueño del repositorio. En rama no publica —ése fue
  el disfraz—, así que el gate de siempre salía verde; **la primera vez que corrió
  en `master`, el 2026-08-21, murieron los catorce builds** con
  `denied: permission_denied: The requested installation does not exist`. La guarda
  que quedó de aquello es `tests/unit/test_ghcr_namespace_is_pushable.py`.
- **Estrenar la tubería con el tag de verdad es estrenar en producción.** Si
  `v1.0.0` falla a mitad, no te quedas donde estabas: te quedas con una release
  **publicada a medias** bajo exactamente la etiqueta a la que apunta el default
  del instalador (`PLATFORM_IMAGE_TAG:-v1.0.0`). Ese estado es peor que no haber
  publicado nada, porque `docker compose pull` resuelve unos servicios y falla en
  otros, y el operador que lo sufre no tiene forma de distinguirlo de una avería de
  red.
- **Y el tag de una release no se rebobina limpiamente.** Borrar `v1.0.0` y volver
  a empujarlo cambia lo que hay detrás de un número que alguien ya pudo instalar:
  dos stacks distintos con la misma versión. El `rc1` existe para que todo lo que
  se pueda romper se rompa contra una etiqueta desechable.

## Qué publica exactamente `release-images.yml`

Fuente: [`.github/workflows/release-images.yml`](../../.github/workflows/release-images.yml).

### Con qué dispara

| Disparador                           | Cómo se resuelve la etiqueta                                               |
| ------------------------------------ | -------------------------------------------------------------------------- |
| `push` de una etiqueta que case `v*` | El job `prep` toma `GITHUB_REF_NAME`: el nombre de la etiqueta empujada    |
| `workflow_dispatch`                  | Usa el input `tag`; si lo dejas **vacío**, cae también a `GITHUB_REF_NAME` |

> **La trampa del dispatch sin input.** Lanzado a mano desde `master` y sin
> rellenar `tag`, `GITHUB_REF_NAME` vale `master`: publicarías seis imágenes
> etiquetadas `:master`, verdes y perfectamente inútiles para el instalador, que
> pide `:v1.0.0`. El input no es opcional en la práctica, aunque el YAML diga
> `required: false`.

No dispara en cada push: ése es el gate de `ci.yml`. Permisos del workflow:
`contents: read` + `packages: write`, y se autentica en `ghcr.io` con el
`GITHUB_TOKEN` de Actions.

### Las seis imágenes

El registro es `ghcr.io/${{ github.repository_owner }}` — para este repositorio,
`ghcr.io/daycry`. Cada imagen se empuja con **dos** etiquetas: la de la release y
el SHA del commit.

| Job           | Imagen                    | Depende de           | Timeout | Notas                                                   |
| ------------- | ------------------------- | -------------------- | ------- | ------------------------------------------------------- |
| `api-server`  | `api-server`              | `prep`               | 60 min  | Imagen **base** pesada (`shared-*` + xmlsec nativo)     |
| `backend`     | `workers`                 | `prep`, `api-server` | 30 min  | Matriz; recibe `BASE_IMAGE=<registro>/api-server:<tag>` |
| `backend`     | `orchestrator`            | `prep`, `api-server` | 30 min  | ídem                                                    |
| `backend`     | `notification-dispatcher` | `prep`, `api-server` | 30 min  | ídem                                                    |
| `backend`     | `watchdog`                | `prep`, `api-server` | 30 min  | ídem                                                    |
| `admin-panel` | `admin-panel`             | `prep`               | 30 min  | Independiente; hornea `NEXT_PUBLIC_API_URL=/api`        |

El `needs:` no es decorativo: los cuatro backends heredan de `api-server` vía
`ARG BASE_IMAGE`, así que **tienen que construirse después**. `admin-panel` no
hereda de nadie y corre en paralelo con la base.

Hay un **séptimo** job en el mismo fichero, `installer`, que cuelga de los tres
anteriores y publica la imagen del instalador. Queda fuera de esta tabla porque lo
que gobierna su sitio no es el grafo de builds, sino el orden duro del ADR 0161:
§«[La séptima imagen](#la-séptima-imagen-el-instalador-y-el-orden-duro-que-la-precede)».

### A qué servicios alimentan esas seis imágenes

El compose que genera el instalador resuelve **diez** servicios contra esas seis
imágenes (`compose_generator.py`): `migrations` y `api-server` comparten la de
`api-server`; `workers`, `workers-privileged`, `workers-marketplace` y
`cortex-beat` comparten la de `workers`; y `orchestrator`,
`notification-dispatcher`, `watchdog` y `admin-panel` tienen la suya. Por eso
«faltan las imágenes» no es el fallo de un servicio: es un `up` que no llega a
existir.

### La séptima imagen: el instalador, y el orden duro que la precede

Desde el [ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md)
(firmado el 2026-08-27) existe un camino de instalación **sin clonar el
repositorio**, y su artefacto de entrada es un fichero que se descarga suelto:
[`docker/bootstrap/docker-compose.generate.yml`](../../docker/bootstrap/docker-compose.generate.yml).
Ese fichero levanta **una** imagen, la del instalador, y por eso publicarla es
parte de la release y no un trámite aparte.

**El orden duro: las seis primero, pineadas por digest; el instalador después.**
Sigue vigente, y hoy **es mecánico, no documental**. No es una preferencia de
secuencia: es lo único que hace que publicar el instalador signifique algo. Uno
que el operador verifica por digest y que acto seguido se descarga seis imágenes
por **tag mutable** no es una cadena más fuerte; es la misma cadena con el eslabón
débil movido un paso y un artefacto más que auditar — el ADR 0161 lo firma así de
literal: «sería mover el eslabón débil un paso y llamarlo arreglo».

Lo que ha cambiado desde que se escribió este runbook: **el workflow ya tiene el
job que lo hace cumplir**. `release-images.yml` declara un job `installer` con
`needs: [prep, api-server, backend, admin-panel]`, y dentro, en este orden:
resuelve los seis digests contra el registro (`docker buildx imagetools inspect`)
y reescribe `platform_images.json` → construye y empuja
`ghcr.io/daycry/installer` → escanea con Trivy → **sella** el artefacto de
arranque con el digest de lo que acaba de publicar → abre un PR con el manifiesto
y el artefacto, y **sólo si el tag es final** (`vX.Y.Z`), no para un `rc`. La
cadena de `needs:` es lo que convierte el orden en algo que no se puede saltar
por descuido, y la vigila `tests/unit/test_release_publishes_the_installer.py`.

Consecuencia que conviene tener delante antes de cortar: una CVE HIGH/CRITICAL en
cualquiera de las seis deja al instalador **sin publicar** y al manifiesto **sin
escribir**. Eso es correcto —el camino sin clon no debe existir sobre imágenes que
suspendieron el gate—, pero significa que un rojo de Trivy en la base no produce
«una release con un detalle pendiente»: produce una release de seis imágenes y
ningún camino nuevo.

Quedan **tres condiciones** que comprobar antes de cortar la release que publique
el instalador. Sólo la primera es de diseño; las otras dos son de higiene, y son
las que más fácil se pasan por alto:

| Condición                                                     | Cómo se comprueba                                                                                                                                                                                                                       |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Las seis imágenes de plataforma se referencian **por digest** | El manifiesto que resuelve el pipeline (`installer_backend/platform_images.json`) trae los seis digests, y el compose generado deja de componer `…:${PLATFORM_IMAGE_TAG}` a secas (paso 6 del ADR 0161). Hoy declara `digests: {}`      |
| CI da veredicto                                               | `gh run list --workflow=ci.yml --limit 1`. **Sí lo da** (medido el 2026-08-28). La avería de facturación de la cuenta que lo tuvo callado desde el 2026-07-30 está resuelta desde el 2026-08-27 — ver más abajo por qué se deja escrito |
| El repositorio deja a Actions abrir PRs                       | Settings → Actions → General → Workflow permissions → «Allow GitHub Actions to create and approve pull requests». Sin esa casilla el job `installer` hace **todo** su trabajo y muere en la última línea con `GraphQL: … not permitted` |

> **Por qué la fila de CI se deja escrita en vez de borrarla.** Este runbook decía
> hasta hoy que CI «está caído desde el 2026-07-30 por facturación», citando
> `CONTINUE_HERE.md`. Dejó de ser cierto el 2026-08-27 y la frase siguió aquí, que
> es exactamente el modo de fallo que la propia nota de `CONTINUE_HERE.md:223`
> describe de sí misma: sobrevivió a su causa y un agente la citó como hecho
> **tres veces en un ADR firmado**, donde sostenía que no se podía publicar nada
> porque no había controles. Los había. **Antes de repetir de aquí un estado
> operativo, compruébalo** — el comando de la fila es de una línea.

La higiene importa porque **Trivy corre después del push** (siguiente apartado) y
la guarda de digest vive en los tests: publicar sin controles efectivos, y hacerlo
justo con el contenedor que mintea el root token de Vault y las cinco unseal keys
en claro, es el peor sitio para estrenar esa costumbre.

**Dónde acaba el digest, y quién lo escribe.** El artefacto descargable lleva el
hueco a la vista:

```yaml
image: ghcr.io/daycry/installer:v1.0.0${INSTALLER_IMAGE_DIGEST:-}
```

Con el hueco vacío, el fichero referencia un **tag mutable**: quien lo descargue
mañana puede recibir otro contenido bajo el mismo nombre. Lo rellena el pipeline
al publicar (paso «Seal the bootstrap artifact…» del job `installer`),
sustituyendo la línea por su forma sellada (`…:v1.0.0@sha256:<64 hex>`), y
**nunca se escribe a mano**: un digest puesto por una persona no tiene vía de
refresco y congela sus CVEs para siempre — es la condición 1 del
[ADR 0148](../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md),
la misma que rige `runtime_images.json`.

Lo que este orden **no** compra, y conviene no confundirlo: el digest protege
contra deriva y contra que te sirvan otra cosa bajo el mismo tag; **no protege
contra suplantación**. Quien comprometa el `GITHUB_TOKEN` o la cuenta puede
publicar una imagen que el digest describirá fielmente. La firma (cosign) tiene
su propio ADR pendiente, y hasta que exista esto es lo que hay.

### Trivy corre DESPUÉS del push, y eso cambia qué significa un rojo

Cada job escanea con Trivy (`HIGH,CRITICAL`, `ignore-unfixed`, `.trivyignore`,
`exit-code: 1`) **la imagen ya publicada**. El trade-off está declarado en el
propio workflow. La consecuencia operativa, que es lo que hay que tener claro
antes de mirar un log en rojo:

- Un rojo de Trivy en `api-server` **no impide** que `api-server:<tag>` esté ya en
  el registro. Lo que sí impide, por el `needs:`, es que se publiquen los cuatro
  backends que heredan de ella.
- Es decir: un fallo de Trivy en la base deja la release **incompleta**, no limpia.
  Con un `rc` da igual; con el tag de verdad es el escenario que este runbook
  existe para evitar.
- **La séptima no tiene esa mitigación, y es la peor.** Para las seis, el `needs:`
  del job `installer` convierte un rojo en «no se publica el instalador». Para el
  Trivy que escanea **al propio instalador** no hay nada detrás: la imagen ya está
  en el registro, el paso de sellado no corre —así que el artefacto se queda en
  `master` con el hueco `${INSTALLER_IMAGE_DIGEST:-}` vacío— y el hueco vacío
  **no impide el `pull`**, porque el fichero nombra `installer:v1.0.0`, un tag que
  sí resuelve. Quien lo descargue porque «ya salió la versión» se lleva
  exactamente la imagen que acaba de suspender el gate.

  La acción es **manual y del mismo turno** en que se ve el rojo: retira del
  registro el tag recién publicado (`gh api -X DELETE` sobre la versión del
  paquete en GHCR, o Packages → _Manage versions_), arregla la CVE y vuelve a
  cortar. No lo dejes «hasta el siguiente intento»: mientras el tag esté, el
  artefacto descargable apunta a él. El arreglo estructural —tag de cuarentena y
  re-etiquetado tras Trivy, o `build --load` → scan → `push`— es follow-up de
  prod-11.

## Comprobación previa

1. **`master` verde.** La release construye lo que hay en el commit etiquetado; si
   `ci.yml` no da veredicto, la release tampoco lo da. **Y lo da**: la avería de
   facturación que tuvo a CI callado desde el 2026-07-30 se resolvió el 2026-08-27
   (`CONTINUE_HERE.md`), y `gh run list --workflow=ci.yml --limit 1` devuelve
   veredictos reales — comprobado el 2026-08-28. Comprueba el del commit que vas a
   etiquetar, no el último de cualquier rama.
2. **El corte del changelog está hecho** en las **dos** mitades
   ([`CHANGELOG.md`](../../CHANGELOG.md) y [`CHANGELOG.es.md`](../../CHANGELOG.es.md)):
   la sección numerada existe y `[Unreleased]` está vacía.
3. **El bump de versión está en el árbol.** Las quince distribuciones de plataforma
   llevan el número de la release (ADR 0160, decisión 1), y también los cuatro
   sitios con la versión escrita a mano en el código — el del instalador se sirve
   por HTTP en su `/healthz`, así que un `0.0.0` ahí contradice al tag delante de
   quien esté diagnosticando.
4. **`gh` autenticado** contra el repositorio, con scope de paquetes si vas a
   consultar visibilidad:

   ```bash
   gh auth status
   gh auth refresh -s read:packages   # sólo si `gh api user/packages` responde 403
   ```

5. **Docker disponible en la máquina desde la que verificas**, porque la
   verificación real es un `docker manifest inspect` contra el registro público.
6. **El repositorio deja a Actions abrir PRs** (Settings → Actions → General →
   Workflow permissions). El job `installer` termina abriendo el PR del manifiesto
   de digests y del artefacto sellado: sin esa casilla hace **todo** su trabajo
   —incluido publicar la imagen— y muere en la última línea con
   `GraphQL: GitHub Actions is not permitted to create or approve pull requests`.
   El arreglo es la casilla, no el workflow, y es el mismo ajuste que ya necesita
   `build-runtime-templates.yml`.

## Pasos

### 1. Ensayo obligatorio con `v1.0.0-rc1`

Se hace con una **etiqueta**, no con el dispatch, porque el objetivo del ensayo es
recorrer el mismo camino que el tag de verdad: mismo disparador, misma resolución
de `GITHUB_REF_NAME` en `prep`, mismo orden de jobs. Un dispatch con el input
relleno prueba casi todo eso, pero no el disparador.

```bash
git tag -a v1.0.0-rc1 -m "Ensayo de la tuberia de release"
git push origin v1.0.0-rc1
```

Sigue el run hasta el final:

```bash
gh run list --workflow=release-images.yml --limit 1
gh run watch "$(gh run list --workflow=release-images.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

> **Si no quieres dejar una etiqueta en el repositorio**, la alternativa es el
> dispatch — que además es la vía para publicar imágenes **sin firmar ni etiquetar
> nada**, algo que este repositorio puede hacer hoy mismo:
>
> ```bash
> gh workflow run release-images.yml -r master -f tag=v1.0.0-rc1
> ```
>
> Rellena `-f tag=` siempre; sin él publicarías `:master` (ver la trampa de
> arriba).

### 2. Comprueba que el ensayo publicó de verdad

Un run en verde dice que los pasos terminaron con exit 0. **No** dice que el
cliente pueda hacer `pull`. Lo segundo es justo lo que falla hoy, así que se
comprueba aparte y, sobre todo, **sin credenciales**:

```bash
docker logout ghcr.io

for img in api-server workers orchestrator notification-dispatcher watchdog admin-panel; do
  if docker manifest inspect "ghcr.io/daycry/$img:v1.0.0-rc1" > /dev/null 2>&1; then
    echo "OK     $img"
  else
    echo "FALTA  $img"
  fi
done
```

El `docker logout` es la mitad importante del paso. Los paquetes que publica el
`GITHUB_TOKEN` nacen **privados**, y una comprobación hecha con la sesión abierta
de quien los publicó pasa en verde mientras el cliente —que instala sin
credenciales— recibe `denied`. Es el mismo mensaje que da una imagen inexistente,
así que verificar logueado no distingue «publicada y privada» de «publicada y
pública»: la única comprobación que responde la pregunta del cliente es la que se
hace como el cliente.

Así se ve hoy, sin nada publicado (medido el 2026-08-27, contra el default del
propio instalador):

```console
$ docker manifest inspect ghcr.io/daycry/api-server:v1.0.0
Get "https://ghcr.io/v2/daycry/api-server/manifests/v1.0.0": denied
$ echo $?
1
```

Y la contraparte en la API de paquetes, que sí distingue «no existe» de «existe y
es privada»:

```console
$ gh api user/packages/container/api-server --jq '.visibility'
gh: Package not found. (HTTP 404)
```

Si tras un run verde el paquete existe pero `docker manifest inspect` deslogueado
sigue diciendo `denied`, es visibilidad: cámbiala a pública en los ajustes del
paquete (GitHub → Packages → el paquete → _Package settings_ → _Change
visibility_) y **repite la comprobación deslogueado**. Hay que hacerlo una vez por
paquete: las seis imágenes son seis paquetes.

### 3. Instala desde cero contra las imágenes del ensayo

Es el único paso que prueba de punta a punta lo que le importa al cliente, y se
puede hacer sin tocar el tag definitivo: en el `.env` que genera el instalador,
apunta `PLATFORM_IMAGE_TAG` al `rc`.

```bash
# En el .env generado por el instalador, junto a PLATFORM_REGISTRY=ghcr.io/daycry
PLATFORM_IMAGE_TAG=v1.0.0-rc1
```

Sigue [08-instalacion-produccion.md](./08-instalacion-produccion.md) en una máquina
limpia. Lo que se verifica aquí no es la plataforma: es que `docker compose pull`
**termina** para los diez servicios. Si termina con el `rc`, terminará con el
definitivo, porque el único cambio será el número.

**Y este paso es hoy exactamente el hueco que queda, ni más ni menos.** Desde el
2026-08-28 la instalación desde cero **sí** está verificada de punta a punta en un
Linux limpio —22 servicios `healthy`, migraciones, Vault, tenant sembrado, HTTPS y
login, run
[`33197920542`](https://github.com/daycry/agent-ai-multitenant/actions/runs/33197920542),
`4 passed`—, pero ese job **construye las seis imágenes dentro de sí mismo** y las
sirve desde un registro local. Todo lo que ejercita ya está probado; lo único que
no puede probar es que un `pull` **contra ghcr** resuelva. Ese es el trozo que
compra este paso 3, y por eso no se salta aunque el nocturno esté en verde: son
mitades distintas del mismo camino
([01-installation-from-scratch.md](./01-installation-from-scratch.md)).

> **El nocturno se pondrá rojo a propósito el día que publiques.** El job
> [Install E2E](../../.github/workflows/install-e2e.yml) abre con un paso llamado
> «El manifiesto de release sigue sin pinear» que **falla** en cuanto
> `platform_images.json` trae digests: a partir de ahí `PLATFORM_IMAGE_TAG` deja de
> formar parte de la referencia (`platform_images.py`) y el registro local del job
> ya no puede servir esas imágenes. No es una avería, es el aviso de que a ese job
> le toca cambiar de sujeto — pasar a instalar contra las imágenes publicadas, que
> es justo lo que un nocturno debería probar desde ese día. Cuéntalo en el PR de la
> release para que no lo descubra alguien a las tres de la mañana.

### 4. Empuja el tag definitivo — acto del operador

**Este paso no se automatiza y no se delega.** El ADR 0160 lo firma así de
explícito: _«nada en el repo debe crear el tag `v1.0.0` por su cuenta»_. No hay —ni
debe haber— un workflow, un script o un agente que lo empuje: la release es una
decisión, y la decisión tiene dueño.

```bash
git tag -a v1.0.0 -m "Plataforma v1.0.0"
git push origin v1.0.0
```

El push del tag dispara `release-images.yml` por sí solo. No hace falta lanzar nada
a mano después.

### 5. Verifica las seis imágenes definitivas

El mismo bucle del paso 2, con el tag de verdad y con el mismo `docker logout`
delante:

```bash
docker logout ghcr.io

for img in api-server workers orchestrator notification-dispatcher watchdog admin-panel; do
  if docker manifest inspect "ghcr.io/daycry/$img:v1.0.0" > /dev/null 2>&1; then
    echo "OK     $img"
  else
    echo "FALTA  $img"
  fi
done
```

**Las seis tienen que decir `OK`.** Cinco de seis no es una release: es el estado
roto a medias que el ensayo existía para evitar, y hay que cerrarlo antes de
anunciar nada — la tabla de «Si el ensayo falla» aplica igual aquí.

### 6. La séptima imagen: verificar lo que el pipeline acaba de publicar

**Ojo al cambio de reparto.** Este paso ya no consiste en publicar el instalador
aparte: el job `installer` de `release-images.yml` corre en la **misma** ejecución
del tag, después de las seis, y publica la imagen, reescribe el manifiesto de
digests y sella el artefacto de arranque. Lo que decide el operador no es _si_ ese
job corre —corre siempre—, sino si el tag se corta con las **tres condiciones** de
§«La séptima imagen» cumplidas. Lo que queda aquí es **verificar**, no ejecutar.

Y si alguna condición no se cumple, o si Trivy tumba cualquiera de las seis, el
`needs:` deja al instalador sin publicar y al manifiesto sin escribir: la release
termina de hecho en el paso 5 y el camino sin clon sigue sin existir. Eso es
correcto, no una release incompleta a medio arreglar.

La verificación es la de los pasos 2 y 5 aplicada a una imagen más:

```bash
docker logout ghcr.io
docker manifest inspect "ghcr.io/daycry/installer:v1.0.0" > /dev/null && echo "OK installer"

# El digest publicado, para CONTRASTARLO con el que el pipeline dejó sellado:
docker buildx imagetools inspect "ghcr.io/daycry/installer:v1.0.0" \
  --format '{{.Manifest.Digest}}'
```

Ese digest tiene que ser el que aparece en
[`docker/bootstrap/docker-compose.generate.yml`](../../docker/bootstrap/docker-compose.generate.yml)
en lugar de `${INSTALLER_IMAGE_DIGEST:-}`. Lo escribe el pipeline y llega a
`master` **por PR**, y sólo para un tag final `vX.Y.Z`: en un `rc` la imagen del
instalador pinea sus digests —coherencia de lo que se publicó ese día— pero el
árbol no se toca, o el siguiente que genere un compose desde `master` instalaría
una release candidate creyendo que instala estable. **No lo pegues a mano**:
además de congelar sus CVEs (ADR 0148), un fichero editado a mano y otro publicado
dejan de ser el mismo artefacto, y el camino sin clon se apoya en que lo sean.

Si tras un run verde el hueco sigue vacío, hay exactamente dos formas de que la
imagen exista y el sellado no: el Trivy del propio job `installer` (que corta
antes del sellado — ver el tercer punto de §«Trivy corre DESPUÉS del push») y la
casilla de «create and approve pull requests» del repositorio.

La comprobación final es la que hará un cliente, y se hace **como el cliente**:
en un directorio vacío, sin checkout y sin sesión abierta.

```bash
mkdir /tmp/audit && cd /tmp/audit
curl -fsSLO "https://raw.githubusercontent.com/daycry/agent-ai-multitenant/v1.0.0/docker/bootstrap/docker-compose.generate.yml"
less docker-compose.generate.yml      # el paso que justifica que esto sea un fichero
docker compose -f docker-compose.generate.yml config
```

El `config` tiene que resolver la imagen **con su digest** y mostrar exactamente
dos binds: la raíz de datos y `./install.yaml`. Si aparece cualquier otra cosa
—y muy en particular `/var/run/docker.sock`— no publiques: el artefacto ha dejado
de ser el que el ADR 0161 firmó, y su garantía se pierde sin que nada falle
(lo vigila `tests/docs/test_installer_bootstrap_artifact.py`).

## Verificación

Una release está publicada cuando las cinco cosas son ciertas, en este orden:

1. El run de `release-images.yml` del tag terminó **verde entero**: todos sus jobs
   —el `installer` incluido— y todos sus pasos de Trivy.
2. `docker manifest inspect` **deslogueado** resuelve las seis imágenes con el tag
   de la release.
3. Una instalación desde cero en una máquina limpia completa el `docker compose pull`
   de los diez servicios **sin editar** `PLATFORM_IMAGE_TAG`, es decir contra el
   default.
4. El `/healthz` del instalador y el `/openapi.json` de la api-server declaran el
   número de la release, no `0.0.0`.
5. El changelog tiene su sección numerada y `[Unreleased]` está vacía.

Y una **sexta, la del camino sin clon** (paso 6): el artefacto
`docker/bootstrap/docker-compose.generate.yml` referencia la imagen **con el
digest** de lo que se acaba de publicar, y descargarlo en un directorio vacío y
pasarle `docker compose config` resuelve sin tocar el repositorio. El job
`installer` la persigue por su cuenta en cada release final, pero puede quedarse
sin cumplir —Trivy sobre el propio instalador, o la casilla de PRs del
repositorio—. Sin esa sexta la release **es válida**: lo que no existe todavía es
el camino sin clon, y decirlo así es más honesto que llamarla incompleta.

## Si el ensayo falla

Un ensayo en rojo es el ensayo **haciendo su trabajo**. El coste de arreglarlo aquí
es una etiqueta desechable; el de arreglarlo después es una versión publicada a
medias.

| Síntoma en el log                                                             | Causa                                                                             | Qué hacer                                                                                                                                                                                                                         |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `denied: permission_denied: The requested installation does not exist`        | El workflow empuja a un namespace de GHCR que el `GITHUB_TOKEN` no puede escribir | El namespace tiene que derivar de `github.repository_owner`. Es el fallo que tumbó los 14 builds de runtime el 2026-08-21; la guarda es `tests/unit/test_ghcr_namespace_is_pushable.py`                                           |
| El job `api-server` cae en el paso de Trivy                                   | HIGH/CRITICAL **con fix disponible** en la imagen base ya publicada               | Triaje por [triage-vulnerabilidades.md](./triage-vulnerabilidades.md). Recuerda: la base ya está en el registro y los cuatro backends **no**                                                                                      |
| Los jobs `backend` salen como `skipped`                                       | `needs: api-server` — la base falló y no llegaron a intentarlo                    | Arregla la base primero; no tiene sentido mirar estos logs                                                                                                                                                                        |
| Run verde pero `docker manifest inspect` deslogueado dice `denied`            | El paquete de GHCR es **privado**, que es el default del `GITHUB_TOKEN`           | Cambia la visibilidad del paquete y repite la comprobación **deslogueado** (paso 2)                                                                                                                                               |
| Se publicó `:master` en vez de `:v1.0.0-rc1`                                  | `workflow_dispatch` sin `-f tag=`                                                 | Relanza con el input relleno. Las imágenes `:master` son basura inofensiva; bórralas del registro al limpiar                                                                                                                      |
| GitHub marca el job `cancelled` al llegar a 60 / 30 min                       | Se agotó el `timeout-minutes`                                                     | **Mide antes de tocar el número**: mira la duración real en el log del run. Subirlo sin esa medición es tapar el síntoma, y el primer run es el más lento porque la caché `type=gha` está fría — el segundo suele bastar          |
| El job `installer` cae en su propio paso de Trivy                             | HIGH/CRITICAL en la imagen del **instalador**, ya publicada                       | **Retira el tag del registro en el acto** y vuelve a cortar: el artefacto de arranque nombra `installer:<tag>`, que resuelve aunque el sellado no haya corrido. Es el único Trivy de este workflow sin nada detrás que lo mitigue |
| `GraphQL: GitHub Actions is not permitted to create or approve pull requests` | Falta la casilla de Workflow permissions del repositorio                          | La imagen del instalador **ya se publicó**; lo que falta es el PR del manifiesto y del artefacto sellado. Marca la casilla (comprobación previa 6) y relanza — no edites el manifiesto a mano                                     |

Después de arreglar la causa, **repite el ensayo con una etiqueta nueva**
(`v1.0.0-rc2`, `rc3`…) en vez de reutilizar la anterior: reempujar la misma
etiqueta con contenido distinto es, en pequeño, el mismo defecto que se quiere
evitar en el tag definitivo.

### Limpieza tras un ensayo correcto

Los `rc` dejan rastro: sus imágenes (con la etiqueta `rc` y con la del SHA) se
quedan en el registro, y la etiqueta se queda en el repositorio. Ninguna de las dos
cosas rompe nada, así que la limpieza es housekeeping y no un paso de la release:

```bash
git tag -d v1.0.0-rc1
git push origin :refs/tags/v1.0.0-rc1
```

Las imágenes `rc` se borran desde los ajustes del paquete en GitHub. Merece la pena
**no borrarlas hasta que el tag definitivo esté verificado**: si el definitivo
falla, el `rc` es lo único con lo que se puede seguir instalando.

## A quién avisar

- **Operador**: es quien empuja el tag definitivo. La decisión es suya y el paso 4
  no la delega.
- **System Admin / DevOps**: ejecuta el ensayo, revisa el triaje de Trivy y hace la
  instalación desde cero del paso 3.
- **Quien mantenga la documentación**: la entrada de changelog y el número de la
  release tienen que estar en el árbol **antes** del tag, no después.

## Enlaces

- La decisión de versionado: [ADR 0160](../05-architecture-decisions/0160-versionado-de-la-plataforma.md).
- Cómo se distribuye e instala la plataforma, y por qué el instalador genera en
  vez de aprovisionar: [ADR 0161](../05-architecture-decisions/0161-distribucion-e-instalacion-de-la-plataforma.md).
- Los tres caminos de instalación y qué exige cada uno:
  [04-reference/installation.md](../04-reference/installation.md).
- Qué versiona la API pública, y por qué los SDK van aparte: [ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md).
- Actualizar una instalación a una versión ya publicada: [03-system-upgrade.md](./03-system-upgrade.md).
- Instalar en producción de cero: [08-instalacion-produccion.md](./08-instalacion-produccion.md).
- Leer un fallo de Trivy y decidir actualizar vs suprimir: [triage-vulnerabilidades.md](./triage-vulnerabilidades.md).
- Dónde se escanea cada superficie: [cadena-suministro.md](../04-reference/cadena-suministro.md).
- Salud del stack tras instalar: [health-check.md](./health-check.md).
