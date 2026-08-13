---
title: Cadena de suministro — qué se escanea, dónde y con qué umbral
docs_language: es
audience: security, devops, backend-dev, tech-lead
updated: 2026-08-01
---

# Cadena de suministro

Referencia del **contrato de suministro** del repo: qué entra en el build, de
dónde, con qué garantía de que es lo que dice ser, y quién lo mira. Es la ficha
técnica; el procedimiento cuando algo sale en rojo vive en el runbook
[triage-vulnerabilidades.md](../06-runbooks/triage-vulnerabilidades.md).

Tres propiedades independientes, que se confunden con facilidad:

| Propiedad            | Pregunta que responde                             | Artefactos                                         |
| -------------------- | ------------------------------------------------- | -------------------------------------------------- |
| **Detección** (SCA)  | ¿lo que instalo tiene vulnerabilidades conocidas? | `pip-audit`, `npm audit`, Trivy                    |
| **Reproducibilidad** | ¿dos builds del mismo commit instalan lo mismo?   | `uv.lock`, `constraints.txt`, `package-lock.json`  |
| **Inmutabilidad**    | ¿puede cambiar bajo mis pies lo que descargo?     | `@sha256:` en los `FROM`, SHA de commit en `uses:` |

Escanear sin reproducibilidad da señal sobre un árbol que no es el que se
despliega. Reproducibilidad sin inmutabilidad congela las versiones declaradas
mientras la base de la imagen se mueve. Y ambas sin **vía de refresco**
([`dependabot.yml`](../../.github/dependabot.yml)) convierten cada pin en una
CVE congelada para siempre.

---

## 1. Detección: qué se escanea y con qué umbral

| Superficie                               | Herramienta                                                       | Dónde corre                                   | Umbral                             | Excepciones         |
| ---------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------- | ---------------------------------- | ------------------- |
| 14 distribuciones Python (+ transitivas) | `pip-audit --skip-editable` + `scripts/check_pip_audit_report.py` | `ci.yml` → `security-scan`                    | cualquier aviso conocido           | `.pip-audit-ignore` |
| npm `apps/admin-panel`                   | `npm audit --omit=dev`                                            | `ci.yml` → `security-scan`                    | `--audit-level=high`               | ninguna (§4)        |
| npm `apps/installer`                     | `npm audit --omit=dev`                                            | `ci.yml` → `security-scan`                    | `--audit-level=high`               | ninguna (§4)        |
| 5 imágenes de plataforma en cada push    | Trivy                                                             | `ci.yml` → `build-images`                     | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 14 runtime templates                     | Trivy                                                             | `build-runtime-templates.yml` (matriz)        | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 5 imágenes publicables                   | Trivy                                                             | `release-images.yml` (**bloquea la release**) | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| Drift del lockfile Python                | `uv lock --check`                                                 | `ci.yml` → `lint-python` (**bloqueante**)     | cualquier desincronía              | ninguna             |

**Por qué esos umbrales**: `--audit-level=high` porque sin umbral `npm audit`
falla con cualquier aviso `low` de la toolchain y el gate muere por fatiga;
`--omit=dev` porque eslint/vitest/playwright no se despliegan;
`ignore-unfixed: true` en Trivy porque las bases `-slim`/`alpine` acumulan CVEs
sin fix upstream y bloquearían PRs ajenos al problema; `--skip-editable` en
pip-audit porque las 14 distribuciones locales no existen en PyPI y no son
auditables (sus dependencias **sí** se auditan: están instaladas como
distribuciones normales).

**Por qué NO va con `--strict`** (corregido el 2026-08-13): `--strict` significa
«falla si la recolección falla en cualquier dependencia», y una dependencia
OMITIDA cuenta como fallo — es decir, volvía fatal justo lo que
`--skip-editable` acababa de omitir. El paso murió durante semanas en la primera
editable por orden alfabético («agent-runtime: distribution marked as
editable») sin auditar ni un paquete: un rojo permanente que no era una
vulnerabilidad y que tapaba las que sí había. La exigencia útil de `--strict`
—no dar por buena una auditoría incompleta— la hace ahora
[`scripts/check_pip_audit_report.py`](../../scripts/check_pip_audit_report.py)
sobre el JSON: tolera las omisiones por «editable» y falla ante cualquier otro
motivo de omisión.

**Reparto de Trivy entre los tres workflows**, para que ninguna imagen quede sin
escanear ni se escanee dos veces por inercia: los 14 templates van con su build
en la matriz; las 5 publicables en la release; y en `ci.yml` lo que no cae en
ninguno de los dos — `api-server` (la base pesada de la que heredan tres
backends vía `BASE_IMAGE`), `agent-runtime` y `browser-runtime` (los
contenedores donde corre el código NO confiable, Principio Rector 2) y las dos
del installer.

> **Modo del gate a 2026-07-31**: `security-scan` corre con
> `continue-on-error: true` — **informa, no bloquea**. Convertirlo en check
> obligatorio es `task_sca_gate_08` del plan y exige permisos de administración
> del repo (branch protection). El `uv lock --check` sí es bloqueante desde el
> primer día: vive en `lint-python` a propósito, porque es higiene de repo y no
> un hallazgo de vulnerabilidad.

---

## 2. Reproducibilidad: el árbol que se escanea es el que se despliega

- **Python**: `uv` gobierna un workspace con las 14 distribuciones. `uv.lock` es
  la resolución completa (212 paquetes); [`constraints.txt`](../../constraints.txt)
  es su exportación plana (198 pines `==`), la que consume el
  `pip install -e … -c constraints.txt` de **todos** los jobs de CI y del
  `docker/agent-runtimes/agent-runtime/Dockerfile`. Los rangos de los
  `pyproject.toml` siguen siendo la restricción de compatibilidad; el lock es la
  verdad reproducible. La decisión y su alternativa descartada están en el
  [ADR 0147](../05-architecture-decisions/0147-lockfile-python-uv-vs-pip-tools.md).
- **npm**: `package-lock.json` versionado en las dos superficies; CI usa
  `npm ci`, nunca `npm install`.
- **Regenerar** tras cambiar un rango:

  ```bash
  uv lock
  uv export --frozen --all-packages --all-extras --all-groups \
            --no-hashes --no-emit-workspace --no-annotate --no-header -o constraints.txt
  # y devolver la cabecera de comentarios a constraints.txt
  ```

  Si no se hace, `uv lock --check` pone `lint-python` en rojo. **Dependabot no
  regenera el lock**: es el fallo más frecuente de sus PRs (ver runbook §7).

> ⚠️ **El `.venv` del repo NO es lo que instala CI, y la diferencia ya esconde dos
> rojos.** El venv local se resolvió desde los rangos, antes de que existiera el
> lock; medido el 2026-08-01, **74 de ~170 paquetes divergen y 72 son el venv
> retrasado**. Entre ellos `fastapi` (0.136.1 local vs **0.141.1** en el lock),
> cuya 0.141 dejó de aplanar en `app.routes` las rutas de `include_router()`: dos
> guardas de `tests/unit/` pasan en local y **fallan con la resolución que CI
> instala**. Con CI caído nadie corría esa resolución, así que el rojo lleva
> semanas invisible. Cómo reproducir el entorno de CI en local, y el fix:
> [gotchas/venv-local-por-detras-del-lock.md](../03-guides/gotchas/venv-local-por-detras-del-lock.md).

---

## 3. Inmutabilidad: nada mutable entra en el build

| Qué                       | Cómo se referencia                                                 | Cuántos (2026-08-01)                                |
| ------------------------- | ------------------------------------------------------------------ | --------------------------------------------------- |
| Bases de imagen           | `FROM python:3.12-slim@sha256:… ` (tag + digest)                   | 22 `FROM` externos en 19 Dockerfiles bajo `docker/` |
| GitHub Actions            | `uses: owner/repo@<sha40> # vN`                                    | 46 usos en 4 workflows                              |
| Composer en las PHP       | etapa `FROM composer:2@sha256:…` + `COPY --from=`                  | 2 imágenes (`php-phpunit`, `php-pest`)              |
| Imágenes de runtime (×14) | `ghcr.io/agentic-platform/agent-runtime-<slug>:<versión>@sha256:…` | manifiesto de release (ADR 0148)                    |

El tag va **dentro** de la referencia además del digest (`python:3.12-slim@sha256:…`,
no `python@sha256:…`): sin él nadie sabe qué versión corre y Dependabot no puede
proponer la siguiente. Lo mismo con el comentario `# vN` de las actions.

**Por qué importa el digest y no el tag**: un tag es una referencia mutable.
Quien controle el repo de una action —o le roben el token— puede reapuntar `v5`
a un commit que exfiltre los secretos de CI; y `python:3.12-slim` de hoy no es
el de mañana. En las 14 imágenes de runtime esto no es teórico: son el lugar
donde el Principio Rector 2 deposita el aislamiento del código no confiable.

**Vía de refresco**: [`dependabot.yml`](../../.github/dependabot.yml), cuatro
ecosistemas (pip, npm, docker, github-actions), semanal, con `groups` por
ecosistema y `open-pull-requests-limit: 5`. Sin ella el digest-pinning sería
contraproducente — de ahí el orden duro del plan: Dependabot **antes** que
digest-pinning.

Lo que **no** está pineado por digest, a propósito o por decidir:

- Las imágenes `image:` de los `docker-compose*.yml` (Postgres, Redis, MinIO,
  Vault, ClamAV, Docling…): van por tag y dos por `latest`. Quedan fuera del
  alcance de `prod-11`, que se acota a los `FROM`.

  > **Medido el 2026-08-12, porque «fuera de alcance» sin número se lee como
  > «pocas»**: son **19 imágenes de terceros** referenciadas por `image:` bajo
  > `docker/`, y **0 de 19** llevan `@sha256:`. Las dos rodantes son
  > `fedirz/faster-whisper-server:latest-cpu` (`docker-compose.yml:314`) y el
  > default de `${IMAGE_SEARXNG:-searxng/searxng:latest}` (`:502`): en esas dos,
  > un `docker compose pull` puede cambiar la imagen que corre **sin que cambie
  > una línea del repo**, así que un fallo nuevo tras un `pull` no será
  > atribuible a ningún commit. A diferencia de las constantes Python del worker,
  > aquí **sí hay vía de refresco**: el ecosistema `docker` de Dependabot parsea
  > ficheros compose. O sea que la objeción de la regla dura no aplica y esto es
  > trabajo pendiente, no una excepción razonada. Pinearlas recrea medio stack en
  > el siguiente `up -d`, así que quiere su propia ventana.

- `postgres:16-alpine` / `redis:7-alpine` de los servicios auxiliares del worker
  (`apps/workers/src/workers/test_runtime.py`): un digest en una constante de
  Python no lo refresca ningún ecosistema de Dependabot, así que pinearlo ahí
  chocaría con la regla dura del propio plan. Pendiente de decisión.

  > **Lo que cambió el 2026-08-01 y afecta a esa decisión**: la premisa de arriba
  > —«en Python no hay vía de refresco»— dejó de ser cierta en general. El ADR
  > 0148 entregó exactamente eso para las 14 imágenes de runtime: un manifiesto
  > generado (`runtime_images.json`) que **no refresca Dependabot sino un job de
  > CI** (`refresh-digests`), que resuelve los digests contra el registry y abre
  > un PR. O sea: el vehículo de refresco no tiene por qué ser Dependabot, y la
  > opción (b) del plan («pinear en Python + revisión manual mensual») ya no es la
  > única salida del lado Python. Sigue siendo una decisión, no una
  > implementación: estas dos imágenes **no las publica este proyecto**, así que
  > reutilizar el mecanismo del 0148 significaría resolver digests de imágenes
  > ajenas en un job propio, que es un diseño distinto y hay que quererlo.

### 3.1 Las 14 imágenes de runtime (ADR 0148)

Es donde el Principio Rector 2 deposita el aislamiento del **código NO
confiable**, y hasta el 2026-08-01 era el eslabón más flojo de esta tabla: el
catálogo componía `agent-runtime-<slug>:v1` y el workflow construía con
`push: false`, así que **cada host se fabricaba su propia variante**. Dos
instalaciones del mismo commit ejecutaban cosas distintas y el sistema no podía
responder _«¿qué imagen exacta ejecutó el código de este tenant?»_. Sin esa
respuesta, un `.trivyignore` que habla de la imagen de CI y no de la del host no
es una excepción de seguridad: es una ficción.

Cómo funciona ahora:

| Pieza                                              | Qué hace                                                                                                                                       |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `build-runtime-templates.yml`                      | En `master` publica las 14 en `ghcr.io/agentic-platform/agent-runtime-<slug>:<versión>` (+ tag del commit). En PR y ramas `plan/**` no publica |
| `shared_test_runtimes/runtime_images.json`         | Manifiesto de release: `registry` + `version` + los 14 digests. **Lo escribe el pipeline**                                                     |
| `shared_test_runtimes/catalog.py`                  | Compone la referencia desde el manifiesto. Cero digests escritos a mano (lo vigila un test)                                                    |
| `workers/test_runtime.py` → `ensure_runtime_image` | Descarga por digest antes de lanzar. Si el pull falla, **aborta**                                                                              |
| `python -m shared_test_runtimes.release`           | La única mano que reescribe el manifiesto; valida cada digest antes de tocarlo                                                                 |

Las dos condiciones que el ADR pone para que esto no empeore nada, y dónde vive
cada una:

1. **Nada de digest sin vía de refresco.** El job `refresh-digests` resuelve los
   digests contra el registry cuando los 14 builds han pasado Trivy y abre un PR
   con el manifiesto. Un digest tecleado a mano no lo refrescaría nadie
   —Dependabot parsea Dockerfiles y compose, no fuentes Python— y sería la
   congelación de CVEs del riesgo 3.
2. **Fallback explícito, no silencioso.** Si el `pull` por digest falla, la tarea
   muere con un error legible. Caer a la imagen local con el mismo tag sería
   reintroducir el problema disfrazado de resiliencia, y encima en verde.

**Estado a 2026-08-01**: el mecanismo está entregado y el manifiesto **vacío** —
no hay release publicada todavía, así que el catálogo sigue componiendo el nombre
local que construye `scripts/dev/build-runtime-templates.sh` y el worker lo
ejecuta como siempre. El salto lo da el primer `master` que publique. Que el
estado esté escrito en el propio fichero, y no deducido de una constante, es
deliberado.

> **Lo que cambia el día que se publique**, y conviene saberlo antes: en cuanto
> el manifiesto traiga digests, `scripts/dev/build-runtime-templates.sh` deja de
> alimentar al worker. Sus imágenes se siguen llamando `agent-runtime-<slug>:v1`
> y el catálogo pasará a pedir `ghcr.io/agentic-platform/…@sha256:…`, que es otra
> cosa: la máquina de desarrollo empezará a **descargar** en vez de usar lo que
> construyó. Es lo que se quiere (el sandbox del desarrollador y el del cliente
> ejecutan lo mismo), pero un `docker build` local que «no se aplica» desconcierta
> si nadie lo avisó. Para volver temporalmente a lo local: `RUNTIME_IMAGE_REGISTRY`
> no sirve —el digest sigue exigiéndose—; hay que revertir el manifiesto.

**Host sin salida a internet**: `RUNTIME_IMAGE_REGISTRY` reapunta el repositorio
a un mirror conservando el digest. Procedimiento completo en
[installation.md](./installation.md#imágenes-de-runtime-y-hosts-sin-salida-a-internet).

---

## 4. Excepciones

Dos ficheros versionados en la raíz y ningún otro sitio:
[`.trivyignore`](../../.trivyignore) (imágenes) y
[`.pip-audit-ignore`](../../.pip-audit-ignore) (Python). Cada entrada exige
justificación legible **y** una línea `# review: YYYY-MM-DD`; el formato y el
calendario de revisión están en el runbook §5, y
`tests/unit/test_supply_chain_config.py` pone en rojo cualquier entrada que no
los cumpla.

**npm no admite ignore por aviso** — solo `--audit-level`. Por eso una
vulnerabilidad npm que no se puede arreglar **no se suprime**: se registra en un
plan o ADR y el gate npm no puede volverse obligatorio hasta resolverla.

### Backlog conocido (re-medido el 2026-08-10)

> **Actualización del 2026-08-10 — el backlog EMPEORÓ sin que cambiara una línea
> de código.** `next` sigue en 14.2.35 y `critical` sigue limpio, pero los avisos
> `high` han pasado de **2 a 3**, uno de ellos por el `postcss` empotrado. Y lo
> que más importa para decidir: el rango vulnerable que reporta npm es ahora
> **`next 9.3.4-canary.0 – 16.3.0-preview.10`**, así que **ni la línea 14 ni la 15
> lo cierran** y el destino de la migración es **`next@16.3.0`** (antes 16.2.12).
> La condición de salida de más abajo no cambia; el coste de no elegir, sí. Ocho
> advisories de `next` (bypass de middleware con i18n, DoS en Server Actions, dos
> SSRF, dos confusiones de caché, payload sin cota en Edge y la divulgación de
> endpoints de Server Functions) más cuatro de `postcss`.

### Backlog medido el 2026-08-01

`next` está en **14.2.35**, el último parche de la línea 14.2.x: eso cerró la
crítica `GHSA-955p-x3mx-jcvp` (divulgación no autenticada de Server Functions) y
`npm audit --audit-level=critical` sale **limpio** en las dos superficies. Pero
`--audit-level=high` sigue en rojo: quedan 2 avisos `high` cuyo rango abarca
**todo 14.x** (más un `postcss` empotrado) y el único fix disponible es
**`next` 16**, un salto de major con roturas. Eso necesita su propio plan; hasta
entonces el gate npm no puede ser obligatorio sin mentir.

Medición del 2026-08-01, **idéntica** a la del 2026-07-31 (npm propone
`next@16.2.12`, «which is a breaking change»):

| Comando                                       | admin-panel | installer  |
| --------------------------------------------- | ----------- | ---------- |
| `npm audit --omit=dev --audit-level=critical` | exit **0**  | exit **0** |
| `npm audit --omit=dev --audit-level=high`     | exit **1**  | exit **1** |

**Condición de salida, para no volver a medir esto a ciegas**: este backlog se
cierra cuando `npm audit --omit=dev --audit-level=high` salga en **exit 0** en las
dos superficies. Hoy eso solo puede venir de migrar a `next` 16 en el admin-panel
y en el frontend del installer; un parche nuevo de 14.2.x **no basta**, porque el
rango vulnerable de los dos avisos abarca toda la línea 14. Mientras eso siga así,
`task_next_update_01` y `task_sca_gate_08` de `prod-11` permanecen abiertas por
esta razón y no por falta de trabajo.

---

## Verificación

```bash
# Guardas estáticas de toda la cadena (actions, digests, job SCA, ignore-lists)
.venv/Scripts/python.exe -m pytest tests/unit/test_supply_chain_config.py -q

# Guardas de esta documentación
.venv/Scripts/python.exe -m pytest tests/docs/test_supply_chain_docs.py -q

# El lock no ha derivado
uv lock --check
```

## Relacionado

- [Runbook: triage de vulnerabilidades](../06-runbooks/triage-vulnerabilidades.md) — qué hacer con un rojo.
- [ADR 0147 — lockfile Python: uv workspace, no pip-tools](../05-architecture-decisions/0147-lockfile-python-uv-vs-pip-tools.md).
- [ADR 0148 — distribución de las imágenes runtime](../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md) (`accepted`, opción (a); implementado el 2026-08-01).
- [ADR 0012 — aislamiento por contenedor del agent-runtime](../05-architecture-decisions/0012-aislamiento-contenedores-agent-runtime.md).
- [Plan prod-11 — cadena de suministro](../roadmap/prod-11-cadena-suministro.md).
