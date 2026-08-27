---
adr_id: "0160"
title: "Versionado de la plataforma: qué número llevan los componentes y qué significa un v1.0.0"
status: proposed
date: 2026-08-27
deciders: [operador]
relates_to: [0037, 0148]
plan_referenced: 15-instalador-produccion
task: [task_15_29]
docs_language: es
---

# ADR 0160 — Versionado de la plataforma

> **Estado: `proposed`.** Este ADR no existía y el propio `CHANGELOG.md` lo
> reconocía: _«there is no ADR for that yet»_. Se escribe porque su ausencia no
> es una laguna documental — **bloquea que el producto se pueda instalar**.

## El hecho que lo hace urgente

`apps/installer/backend/src/installer_backend/compose_generator.py:99-100` fija
los valores por defecto con los que el wizard genera el compose de cualquier
instalación nueva:

```python
APP_IMAGE_TAG = "${PLATFORM_IMAGE_TAG:-v1.0.0}"
APP_IMAGE_REGISTRY = "${PLATFORM_REGISTRY:-ghcr.io/daycry}"
```

Nueve servicios resuelven contra ese default. Y medido el 2026-08-27:

```console
$ git tag                                          # vacío
$ gh api repos/daycry/agent-ai-multitenant/tags     # []
$ gh api repos/daycry/agent-ai-multitenant/releases # []
$ gh api .../actions/workflows/release-images.yml/runs
total_count: 0
```

**Una instalación desde cero hoy hace `pull` de `ghcr.io/daycry/api-server:v1.0.0`
contra un registry donde ese tag nunca se publicó.** No falla en un caso raro:
falla siempre, en el camino principal.

Por eso `task_15_29` («Release v1.0.0») no es la ceremonia que cierra el plan
después del pentest. Es la mitad que le falta al test humano de instalación, y
es adelantable ya.

## Lo que hay hoy, medido

Diecisiete distribuciones declaran versión. **Catorce dicen `0.0.0`** y tres no:

| Distribución                                    | Versión declarada |
| ----------------------------------------------- | ----------------- |
| `docker/agent-runtimes/agent-runtime/pyproject` | `1.0.0`           |
| `packages/sdk-python/pyproject`                 | `0.1.0`           |
| `packages/sdk-typescript/package.json`          | `0.1.0`           |
| las otras catorce                               | `0.0.0`           |

Los tres disidentes no discrepan por capricho, y eso importa para elegir: los
dos SDK son **paquetes públicos** con su propio ciclo (Plan 13), y el
`agent-runtime` es una **imagen**, no una librería.

La maquinaria de publicación sí está construida y nunca ha corrido:
`release-images.yml` dispara con `on: push: tags: ["v*"]` y publica **seis**
imágenes — `api-server`, `workers`, `orchestrator`, `notification-dispatcher`,
`watchdog` y `admin-panel`.

## Qué hay que decidir

Tres preguntas, y sólo la primera es de fondo:

1. **¿Una versión para todo el monorepo, o una por distribución?**
2. **¿Los SDK públicos siguen el número de la plataforma o el suyo?**
3. **¿Qué promete un `v1.0.0`** — ¿compatibilidad de la API REST, del esquema de
   BD, del formato del bundle de backup, o sólo «esto es lo que se publicó»?

## Opciones

### A. Versión única del monorepo, SDK aparte

Las catorce distribuciones de plataforma comparten número y se mueven juntas.
Los dos SDK conservan el suyo, porque los consume gente de fuera y su
compatibilidad la gobierna el ADR 0037 (versionado del path de la API), no el
número de la plataforma. El `agent-runtime` pasa a `0.0.0` como el resto: es
una imagen del stack, y su `1.0.0` actual no significa nada que nadie mantenga.

- **A favor**: un solo número que mirar; el tag `v1.0.0` significa exactamente
  «el stack que se publicó ese día». Es lo que el default del instalador ya
  asume, así que no hay que tocarlo.
- **En contra**: un cambio en el `watchdog` sube el número de todo. Con release
  por tag eso es barato, pero infla el changelog.
- **Coste**: 4-6 h — bump de 15 ficheros, guarda, corte de changelog.

### B. Versión por distribución (semver independiente)

Cada paquete e imagen lleva su número y su cadencia.

- **A favor**: preciso; un consumidor sabe qué cambió de verdad.
- **En contra**: exige una herramienta de release por distribución y un
  mecanismo de compatibilidad entre ellas (qué `workers` va con qué
  `api-server`). Ese mecanismo **no existe** y el compose asume hoy un tag
  común para los nueve servicios. Es construir un problema nuevo para resolver
  uno que no tenemos: un despliegue en UNA máquina, con todas las piezas
  desplegadas a la vez.
- **Coste**: 3-5 días, más el mantenimiento continuo.

### C. No versionar y publicar por digest

Retirar el tag del default del instalador y resolver las seis imágenes por
digest, como ya hace el catálogo de runtimes del ADR 0148.

- **A favor**: coherente con el 0148, y elimina la clase de fallo — un digest
  no puede apuntar a algo que no existe.
- **En contra**: un operador no puede decir en qué versión está, ni el runbook
  de upgrade puede hablar de «subir de la X a la Y». Y el changelog se queda
  sin ancla: hoy `CHANGELOG.md` sólo tiene `[Unreleased]` precisamente porque
  no hay nada a lo que anclarlo.
- **Coste**: 1-2 días.

## Recomendación de este documento (no es la decisión)

**A**, y por el motivo menos glamuroso: es la única que **no exige construir
nada nuevo**. El default del instalador ya asume un tag común, el workflow de
release ya dispara con `v*`, y el `CHANGELOG` ya está escrito para tener
secciones por versión. A convierte cuatro ficheros de configuración en
verdaderos; B y C piden herramienta o runbook nuevos.

La pregunta 3 se responde con lo mínimo defendible: **`v1.0.0` promete el
contrato de la API REST v1 y nada más**. El esquema de BD lo gobiernan las
migraciones de Alembic y el formato del bundle su propio ADR; prometer
compatibilidad de cosas que ya tienen su mecanismo es prometer lo que no se
puede comprobar.

## Consecuencias de no decidir

No es neutral, y por eso se nombra: **el producto sigue sin poder instalarse
desde cero**. El default del instalador seguirá apuntando a un tag inexistente,
`task_15_29` seguirá clasificada como «acto humano» cuando la mayor parte de lo
que le falta es técnico, y el `CHANGELOG` seguirá con un `[Unreleased]` de 250
líneas que nadie puede anclar a nada.

## Qué se hace cuando esto se acepte

En este orden, y ninguno antes de la firma:

1. Bump coherente de las distribuciones que la opción elegida agrupe, **más una
   guarda que derive la lista del árbol** — no una lista escrita a mano, que es
   el modo de fallo que este repo ya pagó con el `watchdog`
   (`tests/unit/test_app_images_are_built_by_ci.py`).
2. Corte del changelog: `[Unreleased]` pasa a `[1.0.0]` y se abre uno vacío,
   **en las dos mitades** — `CHANGELOG.es.md` debe conservar la misma estructura
   o rompe `tests/docs/test_bilingual_docs.py`.
3. Runbook `docs/06-runbooks/09-release.md`.
4. Ensayo de `release-images.yml` con un tag de prueba (`v1.0.0-rc1`) **antes**
   del de verdad: su primer run no debería ser el que cuenta.

Empujar el tag `v1.0.0` sigue siendo del operador. Todo lo demás es técnico y
adelantable.
