---
title: Cadena de suministro — qué se escanea, dónde y con qué umbral
docs_language: es
audience: security, devops, backend-dev, tech-lead
updated: 2026-07-31
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

| Superficie                               | Herramienta                          | Dónde corre                                   | Umbral                             | Excepciones         |
| ---------------------------------------- | ------------------------------------ | --------------------------------------------- | ---------------------------------- | ------------------- |
| 14 distribuciones Python (+ transitivas) | `pip-audit --strict --skip-editable` | `ci.yml` → `security-scan`                    | cualquier aviso conocido           | `.pip-audit-ignore` |
| npm `apps/admin-panel`                   | `npm audit --omit=dev`               | `ci.yml` → `security-scan`                    | `--audit-level=high`               | ninguna (§4)        |
| npm `apps/installer`                     | `npm audit --omit=dev`               | `ci.yml` → `security-scan`                    | `--audit-level=high`               | ninguna (§4)        |
| 5 imágenes de plataforma en cada push    | Trivy                                | `ci.yml` → `build-images`                     | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 14 runtime templates                     | Trivy                                | `build-runtime-templates.yml` (matriz)        | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| 5 imágenes publicables                   | Trivy                                | `release-images.yml` (**bloquea la release**) | `HIGH,CRITICAL` + `ignore-unfixed` | `.trivyignore`      |
| Drift del lockfile Python                | `uv lock --check`                    | `ci.yml` → `lint-python` (**bloqueante**)     | cualquier desincronía              | ninguna             |

**Por qué esos umbrales**: `--audit-level=high` porque sin umbral `npm audit`
falla con cualquier aviso `low` de la toolchain y el gate muere por fatiga;
`--omit=dev` porque eslint/vitest/playwright no se despliegan;
`ignore-unfixed: true` en Trivy porque las bases `-slim`/`alpine` acumulan CVEs
sin fix upstream y bloquearían PRs ajenos al problema; `--skip-editable` en
pip-audit porque las 14 distribuciones locales no existen en PyPI y con
`--strict` contarían como «no auditables» (sus dependencias **sí** se auditan:
están instaladas como distribuciones normales).

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

---

## 3. Inmutabilidad: nada mutable entra en el build

| Qué                 | Cómo se referencia                                | Cuántos (2026-07-31)                                |
| ------------------- | ------------------------------------------------- | --------------------------------------------------- |
| Bases de imagen     | `FROM python:3.12-slim@sha256:… ` (tag + digest)  | 22 `FROM` externos en 19 Dockerfiles bajo `docker/` |
| GitHub Actions      | `uses: owner/repo@<sha40> # vN`                   | 46 usos en 4 workflows                              |
| Composer en las PHP | etapa `FROM composer:2@sha256:…` + `COPY --from=` | 2 imágenes (`php-phpunit`, `php-pest`)              |

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
- `postgres:16-alpine` / `redis:7-alpine` de los servicios auxiliares del worker
  (`apps/workers/src/workers/test_runtime.py`): un digest en una constante de
  Python no lo refresca ningún ecosistema de Dependabot, así que pinearlo ahí
  chocaría con la regla dura del propio plan. Pendiente de decisión.
- La referencia de las imágenes de runtime en el catálogo
  (`agent-runtime-<slug>:v1`, tag mutable, build local por host): es el objeto
  del [ADR 0148](../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md),
  todavía `proposed`.

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

### Backlog conocido a 2026-07-31

`next` está en **14.2.35**, el último parche de la línea 14.2.x: eso cerró la
crítica `GHSA-955p-x3mx-jcvp` (divulgación no autenticada de Server Functions) y
`npm audit --audit-level=critical` sale **limpio** en las dos superficies. Pero
`--audit-level=high` sigue en rojo: quedan 2 avisos `high` cuyo rango abarca
**todo 14.x** (más un `postcss` empotrado) y el único fix disponible es
**`next` 16**, un salto de major con roturas. Eso necesita su propio plan; hasta
entonces el gate npm no puede ser obligatorio sin mentir.

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
- [ADR 0148 — distribución de las imágenes runtime](../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md) (`proposed`).
- [ADR 0012 — aislamiento por contenedor del agent-runtime](../05-architecture-decisions/0012-aislamiento-contenedores-agent-runtime.md).
- [Plan prod-11 — cadena de suministro](../roadmap/prod-11-cadena-suministro.md).
