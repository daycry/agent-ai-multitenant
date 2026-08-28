---
adr_id: "0162"
title: "ADR 0162: La raíz del proyecto, y qué pasa cuando los tests no se ejecutaron"
status: proposed
date: 2026-08-28
deciders: [operador]
relates_to: [0045, 0084, 0087, 0093, 0129, 0148, 0159]
plan_referenced: prod-17-bucle-ai-reviewer
docs_language: es
---

# ADR 0162 — La raíz del proyecto, y qué pasa cuando los tests no se ejecutaron

> **Estado: `proposed`.** Este documento **no decide**: mide, acota y recomienda.
> Las dos decisiones que plantea tienen naturaleza distinta y pueden firmarse por
> separado. La primera es de fontanería y su recomendación es firme. La segunda
> toca el gobierno de calidad —qué puede darse por bueno sin haber ejecutado
> nada— y por eso se deja escrita con sus cuatro opciones y sus costes en vez de
> resolverse aquí.

## Contexto

El 2026-08-28, investigando por qué levantar un preview de aplicación seguía
siendo manual, la medición se llevó por delante la premisa de la que partía. La
premisa era que los agentes trabajan en la imagen del stack del proyecto y que
por eso un proyecto PHP podía servir su propia aplicación. Es falsa, y el detalle
está en [`2026-08-28-imagenes-de-proyecto-y-preview.md`](../roadmap/2026-08-28-imagenes-de-proyecto-y-preview.md).

Al tirar del hilo aparecieron dos defectos que no tienen que ver con el preview,
y que son bastante peores.

### El primero se ve en los datos de producción

De las 180 ejecuciones de la instalación viva, filtrando las llamadas a
`stack_exec` —la herramienta con la que el agente ejecuta el toolchain del
proyecto—:

| Llamadas a `stack_exec` | Verde | Rojo | Tasa |
| ----------------------- | ----- | ---- | ---- |
| **Sin** `cwd`           | 113   | 132  | 46 % |
| **Con** `cwd` correcto  | 10    | 3    | 77 % |

Y los motivos de fallo dominantes son todos el mismo fallo:

```text
14x  Could not open input file: spark
14x  sh: 1: vendor/bin/phpunit: not found
 7x  Composer could not find a composer.json file in /workspace
 6x  Cannot open bootstrap script
 5x  require(./app/Config/Paths.php): No such file or directory
```

El proyecto no vive en la raíz del worktree, sino en un subdirectorio, y **el
agente no sabe dónde está la raíz del proyecto: la tantea**. Cuando acierta, el
stack responde perfectamente — la misma base de datos registra `PHP 8.3.32`,
`CodeIgniter v4.7.4` y `PHPUnit 10.5.64` ejecutándose con éxito. La plantilla
`php-phpunit` hace su trabajo; lo que falla es el directorio desde el que se la
llama.

Hay un matiz que ordena toda la decisión 1 y conviene ponerlo pronto: el
subdirectorio del caso medido, `ci4build`, **no es una convención**. Lo creó el
propio agente a mitad de un plan. Cualquier diseño que exija al operador declarar
la raíz _al crear el proyecto_ falla en el caso greenfield, porque en ese momento
la raíz todavía no existe.

### El segundo no se ve en ningún dato, y ése es el problema

La cadena, verificada eslabón a eslabón:

1. `self_review` es **una sola llamada al LLM**
   ([`graph.py`](../../docker/agent-runtimes/agent-runtime/agent_runtime/graph.py)).
   No ejecuta tests: juzga el código leyéndolo.
2. El reviewer IA corre en **la misma imagen** que el implementador
   ([`execution.py`](../../apps/workers/src/workers/execution.py)), que es
   Python + git. Para un proyecto PHP, **estructuralmente no puede** ejecutar los
   tests.
3. Los tests del proyecto sólo se lanzan si los criterios de aceptación son
   diccionarios con `runtime` **y** `command`.
4. **Ningún productor de criterios ejecutables existe en el producto.** Los dos
   generadores —`pm_plan_draft` y `generate_task_acceptance_criteria`— tienen la
   regla contraria escrita a mano en el prompt
   ([`planning_llm.py`](../../apps/api-server/src/api_server/chat/planning_llm.py),
   [`criteria_llm.py`](../../apps/api-server/src/api_server/chat/criteria_llm.py)).
   Esto **no es un bug**: es una decisión de diseño escrita en tres sitios que
   nunca se emparejó con su contrapartida.
5. Si el test-runtime llega a lanzarse y revienta, la excepción **se traga**, y no
   en dos puntos sino en cinco.
6. Sin outcomes, `_format_test_report_block` devuelve cadena vacía
   ([`dispatch.py`](../../apps/orchestrator/src/orchestrator/dispatch.py))
   y el bloque `<test-report>` **desaparece** del prompt del reviewer.
7. El prompt del reviewer sólo dice qué hacer **cuando hay** informe
   ([`builtin_agents.py`](../../apps/api-server/src/api_server/seeds/builtin_agents.py)).
   Nada sobre su ausencia.
8. `_apply_review_verdict` no consulta en ningún momento si hubo tests.

**La ausencia de tests es indistinguible del diseño.** Un proyecto sin tests y un
proyecto cuyos tests reventaron producen exactamente el mismo prompt. Eso no es
un rojo: es un verde que no significa nada.

Conviene nombrar la asimetría, porque es la que hace grave al conjunto: el
[ADR 0087](0087-self-review-autoritativo-escalado-humano.md) sí cerró el agujero
de «prosa ambigua aprueba», y sí atrapa al agente que **confiesa** haber fallado.
Lo que no cubre es al agente que cree honestamente haber terminado porque nunca
llegó a ejecutar nada.

## Decisión 1 — De dónde sale la raíz del proyecto

### Lo que hay hoy

`_exec` ya acepta `cwd`
([`test_runtime.py`](../../apps/workers/src/workers/test_runtime.py)).
El problema no es que falte el parámetro: es **quién lo pasa**.

| Boca                  | Qué hace              | ¿Pasa `cwd`?                |
| --------------------- | --------------------- | --------------------------- |
| Vía del agente        | Lo que el agente pide | **Sí**                      |
| `default_pre_install` | `composer install`    | **No** — desde `/workspace` |
| Acceptance checks     | Ejecuta los tests     | **No** — desde `/workspace` |
| `compute_lock_hash`   | Caché de dependencias | **No** — y calla            |

Es decir: **de las cuatro, la única que lo recibe es la que menos decide**. La
que instala las dependencias y la que ejecuta los tests corren siempre desde la
raíz del worktree. Para un proyecto anidado, eso significa que ni instala ni
encuentra los tests, haga lo que haga el agente.

La cuarta merece una nota: `compute_lock_hash` mira sólo la raíz, no encuentra el
lockfile, devuelve `hash=None` y **desactiva la caché de dependencias sin un solo
log** ([`dep_cache.py`](../../packages/shared-test-runtimes/src/shared_test_runtimes/dep_cache.py)).
Es el mismo defecto pagando un precio distinto, y no estaba en el encargo.

### Opciones

| Opción                                          | Coste                                                          | Riesgo                                                    |
| ----------------------------------------------- | -------------------------------------------------------------- | --------------------------------------------------------- |
| **A1** — clave `repository_config.project_root` | Sin migración (JSONB libre); ~1-2 d-p                          | Medio: hay que decidir **dueño** antes de escribir código |
| **A2** — columna `projects.project_root`        | Migración Alembic + 4 esquemas + 2 SDK generados; ~3× A1       | Medio: misma cosa, más superficie                         |
| **B** — derivar de marcadores y **aplicar**     | Detector nuevo entero; no hay código reutilizable              | **Alto — desaconsejada**                                  |
| **C** — derivar para **proponer**               | A1 + tarea de worker que precarga y marca la fuente            | Bajo                                                      |
| **D** — cablear `cwd` en las cuatro bocas       | Firmas + callers + tests; independiente de dónde salga el dato | Bajo                                                      |

### Recomendación: **D + A1 + C**, en ese orden

**D primero, y sola si hace falta.** Cablear `cwd` en las cuatro bocas es
condición necesaria de todas las demás y no depende de ninguna: sin ella, el dato
puede existir y no llegar a donde decide.

**A1 sobre A2** por una razón y una concesión. La razón: `repository_config` es
JSONB sin esquema, así que no hay migración ni regeneración de SDK. La concesión,
que conviene dejar escrita: ese blob tiene ya siete claves que nadie lee
(`language`, `framework`, `license`, `orm`, `notes`, `iac`, `diagrams`), lo cual
demuestra que **no se autodocumenta**. Si con el tiempo `project_root` resulta ser
un concepto de primera clase, A2 sigue disponible y esta decisión no lo estorba.

**C, y expresamente no B.** Derivar del árbol del repositorio para **proponer** un
valor que el operador confirma. Aplicarlo sin confirmación es la opción B, y está
desaconsejada con precedente propio: `DEFAULT_RUN_RUNTIME_ID = "python-pytest"`
es exactamente un valor derivado que se aplica solo, y su síntoma —`command not
found`— acusa al repositorio del tenant en vez de a la configuración. El análisis
del 2026-08-28 lo formula como regla: **derivar para proponer, nunca para
aplicar**.

### La pregunta que hay que responder antes de escribir la primera línea

**¿De quién es el dato?** No es retórica: cambia el código.

Las claves de `_REPOSITORY_CONFIG_PLATFORM_KEYS`
([`projects.py`](../../apps/api-server/src/api_server/routers/projects.py))
se preservan cuando el payload del cliente no las trae — o sea, **el operador no
puede vaciarlas**. Si `project_root` lo escribe el detector, va a esa tupla y el
operador pierde la capacidad de borrarlo desde la UI. Si lo escribe el operador,
el detector no puede tocarlo sin pisarle.

**Recomendación:** el dato es **del operador**; el detector sólo lo propone cuando
está vacío, y la UI muestra de dónde salió. Un tercer estado —«detectado, sin
confirmar»— es la única forma de que el caso greenfield de `ci4build` no obligue a
elegir entre mentir y no saber.

## Decisión 2 — Qué hace el sistema cuando los tests no se ejecutaron

### Opciones

| Opción                                                 | Qué cambia                                                     | Coste            | Riesgo                                          |
| ------------------------------------------------------ | -------------------------------------------------------------- | ---------------- | ----------------------------------------------- |
| **A** — el planner genera criterios ejecutables        | Invertir la regla en los dos generadores + limpiador + esquema | Alto (4 frentes) | Alto: el planner sabe 1 de las 3 cosas que pide |
| **B** — decirle al reviewer «tests: NO EJECUTADOS»     | El bloque vacío pasa a bloque explícito                        | **Bajo**         | Creerse que basta                               |
| **C** — gate duro: `approve` no cierra sin tests       | `_apply_review_verdict` degrada a escalado                     | Bajo el sitio    | **Bloqueante hoy**                              |
| **D** — que el fallo del test-runtime deje de tragarse | 5 puntos de `except` pasan a outcome visible                   | **Bajo**         | Bajo: no cambia ningún veredicto                |

### Recomendación: **D, luego B. A después. C sólo cuando A exista**

**D es la única que no cambia nada y lo arregla todo un poco.** No toca un solo
veredicto ni un solo prompt: hace que un fallo de infraestructura **deje de
parecerse a un proyecto sin tests**. Hoy son cinco puntos donde una excepción se
convierte en silencio; convertirlos en un outcome visible es honestidad de señal,
no política. Debería ir primero por eso mismo: cuesta poco y **cambia lo que las
otras tres pueden medir**.

**B después, sabiendo lo que compra y lo que no.** Sustituir la cadena vacía por
un bloque que distinga los tres casos —«no había criterios ejecutables», «los
había y reventaron», «el proyecto no declara runtime»— cuesta tres puntos de
código. Pero sólo **se lo dice** al modelo: el veredicto sigue siendo suyo. Quien
firme B tiene que aceptar que es una mejora de información, no una garantía.

**A es la de fondo, y es cara y arriesgada.** El planner tendría que saber tres
cosas y sólo sabe una: el `runtime` sí es derivable de
`projects.default_runtime_template`; el `command` y la señal esperada, no. Y hay
que decirlo claro: **hoy la regla contraria está escrita a propósito en tres
sitios**. Invertirla es revisar una decisión, no corregir un descuido.

**C no se puede encender hoy, y conviene entender por qué.** Como no existe
ningún productor de criterios ejecutables, un gate que exija constancia de
ejecución **bloquearía el 100 % de las tareas**. C es la consecuencia natural de
A, no su alternativa. Encenderla antes convierte una guarda en una parada.

### Una nota sobre el vocabulario

Existe ya un tipo canónico de estado de test —`TestStatus`, con `passed`,
`failed`, `error`, `skipped` y `timeout`— pero **no está en el camino vivo** y no
contempla `not_run`. La tentación barata es ampliar ese tipo y darlo por hecho;
eso añadiría un estado que nadie produce ni consume, que es precisamente el modo
de fallo que este documento denuncia.

## Lo que este ADR NO decide, y por qué

Dos propuestas se consideraron durante la investigación y **se retiran**. Constan
aquí para que no se vuelvan a proponer sin leer esto.

**Unificar `shell_exec` y `stack_exec` en una tool con enrutado automático.**
Retirada. El predicado que la sostenía —«si el binario está en el sandbox,
ejecútalo ahí, que es barato»— es falso como sustituto de «ahí funcionará»: el
sandbox está en red `internal: true` y su allowlist de egress sólo admite hosts
LLM y de búsqueda, **ningún registry**. Un `composer install` enrutado «a lo
barato» no puede descargar nada. Hay además un precedente que zanja el asunto:
`git` **está** instalado en el sandbox y se excluyó a propósito del set base
porque exponerlo sólo hacía que el agente perdiera turnos con fallos crípticos
([`run_spec.py`](../../apps/workers/src/workers/run_spec.py)). El experimento ya
se hizo. Y el golpe final: `php -m` o `node --version` enrutados «a lo barato» los
contesta el contenedor equivocado — una **respuesta silenciosamente falsa**, peor
que un fallo.

**Que `allowed_commands` vacío signifique «lo que provea la imagen».** Retirada, y
no por discutible sino por peligrosa. La columna es `NOT NULL` con `server_default`
vacío, así que una sola migración convertiría **todos** los proyectos existentes de
«nada permitido» a «lo que traiga la imagen» — que no es un set curado sino `sh`,
`bash`, `curl`, `wget`. El ADR 0045 ya rechazó exactamente esto: cualquier default
no vacío es una decisión de seguridad implícita que el operador no tomó.

La separación `shell_exec` / `stack_exec` del
[ADR 0093](0093-ejecucion-de-stack-mediada-por-worker-stack-exec.md) **se
ratifica**: son dos entornos de ejecución con red, rootfs y ciclo de vida
distintos, y el nombre de la tool es hoy el único sitio donde esa diferencia es
visible al modelo, al humano que aprueba y al que audita.

## Premisas falsas que esto invalida

Ninguna casilla del roadmap queda sin objeto por estas dos decisiones —por eso
este ADR **no lleva `rejects:`**—, pero dos documentos vivos contienen premisas
que la medición desmiente y que hay que propagar al implementarlas:

- [`prod-17-bucle-ai-reviewer.md`](../roadmap/prod-17-bucle-ai-reviewer.md) y el
  [ADR 0084](0084-cableado-bucle-ai-reviewer.md) describen el bucle del reviewer
  suponiendo que el informe de tests existe. La rama «no existe» no está tratada
  en ninguno de los dos.
- `task_gov_09` sigue **abierta** y toca exactamente `_apply_review_verdict`, que
  es donde aterrizaría la opción C. Quien la aborde debe leer este ADR antes:
  las dos decisiones se pisan.

## Consecuencias

**Si se firma la decisión 1 tal como se recomienda**, un proyecto anidado deja de
depender de que el agente adivine, y la caché de dependencias deja de
desactivarse en silencio. No hay migración y el contrato público no cambia.

**Si se firma la decisión 2 en el orden recomendado**, D y B se pueden entregar de
inmediato y el reviewer pasa a ver la diferencia entre «no hay tests» y «los tests
no corrieron». El falso verde **no desaparece**: se hace visible. Eliminarlo exige
A, y bloquearlo exige C después de A.

**Obligación formal si se elige C.** Convertir el escalado automático en norma
toca el principio 7 del [`CLAUDE.md`](../../CLAUDE.md) —tests humanos a nivel de
plan—, y por la regla de precedencia de ese mismo documento, **un ADR que lo
contradiga está obligado a actualizarlo en el mismo commit en que pase a
`accepted`**. Las opciones A, B y D no lo tocan.

## Cómo se verificó

Los datos de ejecución salen de la base de datos de la instalación viva
(`executions.steps_log`, 180 ejecuciones, consultas de sólo lectura). Las
afirmaciones sobre código se comprobaron sobre el árbol en `master`. Las dos
propuestas retiradas se sometieron a tres revisores independientes con el encargo
explícito de tumbarlas; las tumbaron, y las razones que lo consiguieron están
recogidas arriba.
