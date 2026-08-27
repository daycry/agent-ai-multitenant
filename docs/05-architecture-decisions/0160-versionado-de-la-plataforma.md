---
adr_id: "0160"
title: "Versionado de la plataforma: qué número llevan los componentes y qué significa un v1.0.0"
status: accepted
date: 2026-08-27
deciders: [operador]
relates_to: [0037, 0148]
plan_referenced: 15-instalador-produccion
task: [task_15_29]
docs_language: es
---

# ADR 0160 — Versionado de la plataforma

> **Estado: `accepted`.** Firmado por el operador el **2026-08-27**: gana la
> **opción A** (versión única del monorepo, SDK aparte). Este ADR no existía y el
> propio `CHANGELOG.md` lo reconocía: _«there is no ADR for that yet»_. Se
> escribió porque su ausencia no es una laguna documental — **bloquea que el
> producto se pueda instalar**. La decisión, literal, está en §«Decisión».

## El hecho que lo hace urgente

`apps/installer/backend/src/installer_backend/compose_generator.py:99-100` fija
los valores por defecto con los que el wizard genera el compose de cualquier
instalación nueva:

```python
APP_IMAGE_TAG = "${PLATFORM_IMAGE_TAG:-v1.0.0}"
APP_IMAGE_REGISTRY = "${PLATFORM_REGISTRY:-ghcr.io/daycry}"
```

**Diez** servicios del compose generado resuelven contra ese default —no nueve,
como decía la primera redacción de este ADR; recontados el 2026-08-27 sobre
`compose_generator.py`: `migrations`, `api-server`, `orchestrator`, `workers`,
`workers-privileged`, `workers-marketplace`, `cortex-beat`,
`notification-dispatcher`, `watchdog` y `admin-panel`. Diez servicios que tiran
de **seis** imágenes distintas (la de `api-server` la comparten `migrations` y
`api-server`; la de `workers`, los cuatro servicios que arrancan de ella —
`workers`, `workers-privileged`, `workers-marketplace` y `cortex-beat`), y esas
seis son exactamente las que publica `release-images.yml`. Y medido el mismo
día:

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
la mayor parte de lo que le falta es técnico y adelantable — no todo, y el
límite está escrito en §«Lo que esta firma NO desbloquea».

## Lo que hay hoy, medido

El recuento, hecho a mano sobre el árbol y no de memoria:

```console
$ git ls-files | grep -E '(^|/)(pyproject\.toml|package\.json)$' | wc -l
19
```

**Git rastrea 19 manifiestos con número de versión, pero sólo 17 son
distribuciones de la plataforma.** Los otros dos hay que excluirlos
explícitamente, y no son un detalle: son justo la trampa que la guarda del bump
tendrá que codificar.

| Excluido                                              | Versión | Por qué no cuenta                                                                    |
| ----------------------------------------------------- | ------- | ------------------------------------------------------------------------------------ |
| `docs/manuals/package.json`                           | `1.0.0` | Toolchain de construcción de los manuales (PDF). No se publica ni se despliega nada. |
| `apps/admin-panel/vendor/miniverse-core/package.json` | `0.2.5` | Dependencia **vendorizada** de terceros. Su número lo pone su autor, no nosotros.    |

Si la guarda barre el árbol sin esas dos exclusiones, la release de la
plataforma le pisa el número a un vendor —que es la forma limpia de romper una
actualización futura de ese paquete— y marca como «desincronizado» un manifiesto
que nunca estuvo sincronizado. Por eso van escritas aquí y no en la cabeza de
quien implemente el paso 1.

De las 17 que sí cuentan, **catorce dicen `0.0.0`** y tres no:

| Distribución                                    | Versión declarada |
| ----------------------------------------------- | ----------------- |
| `docker/agent-runtimes/agent-runtime/pyproject` | `1.0.0`           |
| `packages/sdk-python/pyproject`                 | `0.1.0`           |
| `packages/sdk-typescript/package.json`          | `0.1.0`           |
| las otras catorce                               | `0.0.0`           |

Los tres disidentes no discrepan por capricho, y eso importa para elegir: los
dos SDK son **paquetes públicos** con su propio ciclo (Plan 13), y el
`agent-runtime` es una **imagen**, no una librería.

Y hay una segunda población que ningún manifiesto declara: **cinco sitios con la
versión escrita a mano en el código**, medidos el 2026-08-27.

| Sitio                                                             | Valor   | Dónde se ve                                    |
| ----------------------------------------------------------------- | ------- | ---------------------------------------------- |
| `apps/api-server/src/api_server/main.py:430`                      | `0.0.0` | `version=` de la app FastAPI → `/openapi.json` |
| `apps/orchestrator/src/orchestrator/app.py:94`                    | `0.0.0` | `version=` de la app FastAPI del orquestador   |
| `apps/installer/backend/src/installer_backend/__init__.py:23`     | `0.0.0` | **expuesto en el `/healthz` del instalador**   |
| `docker/agent-runtimes/agent-runtime/agent_runtime/__init__.py:9` | `1.0.0` | `__version__` del runtime de agentes           |
| `packages/sdk-python/src/agentic_platform_sdk/__init__.py:42`     | `0.1.0` | `__version__` público del SDK                  |

Los dos últimos de esa lista no salen en ningún `pyproject` que el bump vaya a
tocar por barrido de manifiestos: un `grep` de manifiestos los deja fuera y
quedan mintiendo. El del instalador es el peor de los cinco porque **se sirve por
HTTP**: un `/healthz` que responde `"version": "0.0.0"` en una instalación
etiquetada `v1.0.0` es exactamente el tipo de dato que alguien usará para
diagnosticar y le dará la respuesta contraria.

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

Las **quince** distribuciones de plataforma —las catorce que hoy dicen `0.0.0`
más el `agent-runtime`— comparten número y se mueven juntas.
Los dos SDK conservan el suyo, porque los consume gente de fuera y su
compatibilidad la gobierna el ADR 0037 (versionado del path de la API), no el
número de la plataforma. El `agent-runtime` pasa a `0.0.0` como el resto: es
una imagen del stack, y su `1.0.0` actual no significa nada que nadie mantenga.

- **A favor**: un solo número que mirar; el tag `v1.0.0` significa exactamente
  «el stack que se publicó ese día». Es lo que el default del instalador ya
  asume, así que no hay que tocarlo.
- **En contra**: un cambio en el `watchdog` sube el número de todo. Con release
  por tag eso es barato, pero infla el changelog.
- **Coste**: 6-8 h — **19 sitios**, no 15: los quince manifiestos, más cuatro de
  los cinco hardcodeos del código (`api-server/main.py`, `orchestrator/app.py`,
  `installer_backend/__init__.py` y `agent_runtime/__init__.py`; el quinto,
  `sdk-python`, sigue su ciclo y no se toca). Más la guarda, más el corte de
  changelog. La estimación original de «15 ficheros» contaba sólo manifiestos y
  daba por hechos tres hardcodeos: son cinco, y los dos que se escaparon
  (`orchestrator` e `installer`) son precisamente los que ningún barrido de
  `pyproject.toml` encuentra.

### B. Versión por distribución (semver independiente)

Cada paquete e imagen lleva su número y su cadencia.

- **A favor**: preciso; un consumidor sabe qué cambió de verdad.
- **En contra**: exige una herramienta de release por distribución y un
  mecanismo de compatibilidad entre ellas (qué `workers` va con qué
  `api-server`). Ese mecanismo **no existe** y el compose asume hoy un tag
  común para los diez servicios. Es construir un problema nuevo para resolver
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

## Recomendación de este documento (escrita antes de la firma)

> Se conserva tal cual se escribió, para que se vea qué se le puso delante al
> operador. La ratificó: la decisión firmada está en §«Decisión».

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

## Decisión

Decidido por el **operador** el **2026-08-27**. Se elige la **opción A**, y las
cuatro piezas se escriben sin margen de interpretación:

1. **Versión única del monorepo.** Las quince distribuciones de plataforma
   comparten un solo número y se mueven juntas: un tag `vX.Y.Z` significa «este
   stack, entero, tal como se publicó ese día». No hay versión por servicio ni
   matriz de compatibilidad entre piezas, porque el despliegue es de **una
   máquina con todas las piezas a la vez** y una matriz para eso sería un
   problema fabricado.
2. **Los dos SDK conservan su propio ciclo.** `packages/sdk-python` y
   `packages/sdk-typescript` **no** entran en el bump común: los consume gente
   de fuera y su compatibilidad la gobierna el
   [ADR 0037](0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md)
   (versionado del path de la API), no el
   número del stack. Atarlos a la plataforma obligaría a publicar un SDK nuevo
   cada vez que cambie el `watchdog`, que no le dice nada a quien lo consume.
3. **El `agent-runtime` baja a la versión común.** Su `1.0.0` actual no lo
   mantiene nadie ni promete nada. Es **una imagen del stack**, no una librería
   con consumidores externos, así que se alinea con el resto y deja de sugerir
   una estabilidad que no tiene.
4. **Qué promete un `v1.0.0` (pregunta 3): el contrato de la API REST v1, y
   nada más.** No promete compatibilidad del esquema de BD —eso lo gobiernan las
   migraciones de Alembic— ni del formato del bundle de backup, que tiene su
   propio ADR. **Prometer compatibilidad de cosas que ya tienen mecanismo propio
   es prometer lo que no se puede comprobar**: nadie escribiría el test que lo
   verifica, y la promesa envejecería sin que ningún fallo avisara. Lo que sí se
   puede comprobar —y por eso es lo único que se firma— es que la API `v1`
   sigue respondiendo el contrato publicado.

**Empujar el tag sigue siendo un acto del operador.** Esta firma no lo delega ni
lo automatiza: nada en el repo debe crear el tag `v1.0.0` por su cuenta. Todo lo
demás que enumera §«Qué se hace ahora» es técnico y adelantable sin esperar a
nadie.

### Lo que esta firma NO desbloquea

Conviene dejarlo escrito para que nadie lea este `accepted` como «ya se puede
lanzar»:

- **Es necesaria pero no suficiente.** El DAG del plan 15 encadena
  `task_15_29` (release v1.0.0) → `task_15_28` (documentación final) →
  `task_15_27` (**pentest externo**). El pentest es un acto humano con un tercero
  de por medio, y sigue delante de la release por dependencia declarada. Firmar
  el versionado quita la excusa técnica, no el orden.
- **La instalación no está rota por falta de tag, sino porque nadie ha
  publicado las imágenes.** `release-images.yml` nunca ha corrido
  (`total_count: 0`), y su disparador no es sólo `push: tags: ["v*"]`: también
  tiene `workflow_dispatch` con un input `tag`. Es decir, **las seis imágenes se
  pueden publicar hoy desde `master`, a mano, sin firmar ni etiquetar nada**. Si
  alguien lee este ADR buscando por qué no se puede instalar el producto, la
  respuesta corta es esa, y el remedio corto también.

## Consecuencias de no decidir

No es neutral, y por eso se nombra: **el producto sigue sin poder instalarse
desde cero**. El default del instalador seguirá apuntando a un tag inexistente,
`task_15_29` seguirá clasificada como «acto humano» cuando la mayor parte de lo
que le falta es técnico, y el `CHANGELOG` seguirá con un `[Unreleased]` de 250
líneas que nadie puede anclar a nada.

## Qué se hace ahora

La firma ya está, así que estos cuatro pasos están desbloqueados. En este orden:

1. Bump de las **quince** distribuciones de plataforma, **más una guarda que
   derive la lista del árbol** — no una lista escrita a mano, que es el modo de
   fallo que este repo ya pagó con el `watchdog`
   (`tests/unit/test_app_images_are_built_by_ci.py`). Dos cosas que la guarda
   tiene que codificar y no puede deducir sola:
   - **las dos exclusiones** de §«Lo que hay hoy, medido»
     (`docs/manuals/package.json` y
     `apps/admin-panel/vendor/miniverse-core/package.json`), con el motivo al
     lado, porque una exclusión sin motivo escrito se borra en el siguiente
     refactor;
   - **los cinco hardcodeos del código**, de los que cuatro se mueven con la
     plataforma. Un barrido de `pyproject.toml`/`package.json` no los ve, y son
     los que acaban sirviendo un `0.0.0` por HTTP en un stack etiquetado
     `v1.0.0`.
2. Corte del changelog: `[Unreleased]` pasa a `[1.0.0]` y se abre uno vacío,
   **en las dos mitades** — `CHANGELOG.es.md` debe conservar la misma estructura
   o rompe `tests/docs/test_bilingual_docs.py`.
3. Runbook `docs/06-runbooks/09-release.md`.
4. Ensayo de `release-images.yml` con un tag de prueba (`v1.0.0-rc1`) **antes**
   del de verdad: su primer run no debería ser el que cuenta.

Empujar el tag `v1.0.0` sigue siendo del operador. Todo lo demás es técnico y
adelantable.
