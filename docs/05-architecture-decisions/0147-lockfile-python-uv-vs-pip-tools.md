---
title: "ADR 0147: Lockfile Python del monorepo — uv workspace, no pip-tools"
status: accepted
date: 2026-07-31
deciders: [tech-lead]
relates_to: [0021]
plan_referenced: prod-11-cadena-suministro
task: [task_uv_lock_09, task_ci_lock_10]
docs_language: es
---

# ADR 0147: Lockfile Python del monorepo — uv workspace, no pip-tools

> **Nace `accepted`, no `proposed`.** El plan prod-11 lo clasifica
> explícitamente como **decisión técnica de toolchain, no de producto** («puede
> aprobarla el tech lead»). No cambia lo que el sistema hace ni cómo se opera:
> cambia con qué versiones exactas se construye. Dejarlo `proposed` sería
> escribir un ADR pendiente sobre algo que ya está implementado y verificado —
> el pecado documental que esta casa ya cometió otras veces.

## Contexto: no existía lockfile Python en todo el monorepo

La auditoría de producción (2026-06-10, hallazgos `gap5-4` y `quality-5`)
constató que las **13 distribuciones Python** del repo declaran únicamente
**rangos** (`fastapi>=0.110,<1`, `structlog>=24.1,<25`, …) y que no hay ningún
artefacto que fije la resolución. Las consecuencias, medidas y no inferidas:

1. **Builds no reproducibles.** Cada `pip install -e` de CI y cada
   `docker build` del `agent-runtime` resuelven las transitivas contra PyPI en
   ese instante. Dos ejecuciones del **mismo commit** pueden instalar árboles
   distintos, así que un verde de CI no dice nada del árbol de mañana ni del
   que corre en producción.
2. **El SCA queda sin sujeto.** `pip-audit` y Dependabot solo pueden dar señal
   precisa sobre un conjunto de versiones concreto. Sin lock, «esta rama está
   limpia» significa «lo estaba en el momento de resolver», que no es una
   afirmación auditable. Por eso este ADR es **prerrequisito** de las fases B y
   D de prod-11 y no un adorno.
3. **La deriva es silenciosa.** Una transitiva que rompe entra sin que ningún
   fichero del repo cambie: no hay diff que revisar ni PR que rechazar.

El problema tiene una dificultad propia de este repo: no es un proyecto, son
**13 distribuciones** con dependencias cruzadas por nombre
(`agent-runtime` → `shared-llm`, `shared-mcp`, `shared-domain`,
`shared-guardrails`; `shared-guardrails[content-safety]` → `shared-llm`) que
**no existen en PyPI**, más un `requirements-dev.txt` con el toolchain.

## Opciones consideradas

### Opción A — `uv` con workspace (elegida)

Un `[tool.uv.workspace]` en el `pyproject.toml` raíz enumera las 13
distribuciones; `uv lock` produce **un** `uv.lock` con una resolución
consistente para todas a la vez, y `uv export` lo proyecta a un
`constraints.txt` plano que `pip` entiende.

- **A favor**: entiende el monorepo de forma nativa; resuelve las dependencias
  entre miembros vía `[tool.uv.sources] … { workspace = true }` declaradas UNA
  vez en la raíz (sin tocar los 13 `pyproject.toml`); `uv lock --check` detecta
  drift entre los rangos y el lock; `uv export --no-emit-workspace` produce
  exactamente lo que el `pip install -e … -c constraints.txt` de CI necesita, sin
  reescribir la forma de instalar; resolución en ~20 s.
- **En contra**: añade una herramienta más al toolchain (aunque solo para
  generar/validar el lock: **CI y las imágenes siguen instalando con `pip`**);
  `uv.lock` es un formato propio de uv.

### Opción B — `pip-tools` (`pip-compile`)

- **A favor**: veterano, salida en formato `requirements.txt` estándar.
- **En contra**: **no entiende el monorepo**. Exigiría un `requirements.in` por
  distribución (13 ficheros) y 13 `pip-compile` independientes, cuyas
  resoluciones pueden **discrepar entre sí** — y CI instala las 13 en el MISMO
  entorno, así que la última en instalarse ganaría y el conjunto real no sería
  ninguno de los 13 locks. Tampoco resuelve por sí solo las dependencias por
  nombre entre paquetes locales, ni ofrece un equivalente a `uv lock --check`.

### Opción C — Poetry / PDM

- **A favor**: workspaces (Poetry con plugin, PDM nativo) y lock propio.
- **En contra**: obligan a migrar los 13 `pyproject.toml` a su formato de
  dependencias y a cambiar cómo instalan CI y los Dockerfiles. Coste
  desproporcionado para el problema, y con riesgo sobre los `pip install -e`
  que hoy funcionan.

### Opción D — No hacer nada (statu quo)

Rechazada: es la causa raíz de `gap5-4`/`quality-5` y deja sin sujeto preciso a
todo el trabajo de SCA de prod-11.

## Decisión

Se adopta **la opción A**: workspace `uv` en el `pyproject.toml` raíz, `uv.lock`
versionado y `constraints.txt` exportado y versionado.

Reglas que acompañan la decisión:

1. **Los rangos de cada `pyproject.toml` no se tocan.** Siguen expresando la
   restricción de COMPATIBILIDAD («con qué versiones sé funcionar»);
   `constraints.txt` expresa la resolución REPRODUCIBLE («con cuáles se validó
   este commit»). Son cosas distintas y las dos hacen falta.
2. **`pip` sigue siendo el instalador** en CI y en las imágenes
   (`pip install -e <dist> -c constraints.txt`). uv genera y valida el lock; no
   se convierte en dependencia de ejecución del build.
3. **`uv lock --check` corre en `lint-python`**, un job bloqueante — no en
   `security-scan`, que arranca en modo informe. Un lock desincronizado es
   higiene de repo, no un hallazgo de vulnerabilidad.
4. **`constraints.txt` se exporta sin hashes.** Un fichero de constraints con
   hashes obliga a que TODA la instalación vaya con hash, y los `pip install -e`
   (editables) del monorepo no pueden tenerlo. Se gana reproducibilidad de
   versión, no verificación de integridad del artefacto descargado; esa capa,
   si se quiere, es un follow-up (índice interno / `--require-hashes` en un
   flujo sin editables).
5. **El grupo `dev` del `pyproject.toml` raíz y `requirements-dev.txt` deben
   coincidir.** Existen los dos porque el segundo es lo que instalan los
   scripts de bootstrap y el primero es lo que entra en la resolución del lock.
   La guarda `test_root_dev_group_mirrors_requirements_dev` pone en rojo la
   desincronía.

## Consecuencias

### Positivas

- `uv.lock` + `constraints.txt` versionados: dos instalaciones limpias del mismo
  commit resuelven idéntico (test humano `human_prod11_03` de prod-11).
- `pip-audit` y Dependabot pasan a hablar de un conjunto concreto y auditable.
- Subir una dependencia deja **diff revisable** en `uv.lock`/`constraints.txt`,
  con lo que una transitiva nueva entra por PR y no por sorpresa.
- El `agent-runtime` —la imagen donde corre el bucle del agente— se construye
  con versiones fijas.

### Negativas / coste asumido

- Hay que regenerar el lock al tocar cualquier `pyproject.toml`
  (`uv lock` + reexportar). `uv lock --check` avisa, pero es un paso más.
- Congelar la resolución puede destapar de golpe algo que los rangos abiertos
  ocultaban (riesgo 4 de prod-11). Mitigación: la suite completa debe quedar
  verde instalada desde el lock antes de dar por cerrada `task_ci_lock_10`.
- `uv` pasa a ser un requisito del entorno de desarrollo para tocar
  dependencias (no para ejecutar ni para construir).

### Neutras

- El formato `uv.lock` es propio de uv, pero `constraints.txt` —el fichero que
  consumen CI y las imágenes— es un `requirements.txt` estándar. Si algún día
  se abandona uv, lo que hay que sustituir es el generador, no los consumidores.

## Enlaces

- Plan [`prod-11-cadena-suministro`](../roadmap/prod-11-cadena-suministro.md),
  tareas `task_uv_lock_09` y `task_ci_lock_10`.
- [ADR 0021](0021-shared-llm-layer-catalogo-cerrado.md) — `shared-llm` como
  dependencia de ruta del `agent-runtime`, el caso que obliga a las
  `[tool.uv.sources]` de workspace.
- [Runbook de triage de vulnerabilidades](../06-runbooks/triage-vulnerabilidades.md).
