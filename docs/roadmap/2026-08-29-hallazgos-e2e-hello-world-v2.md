---
title: "Hallazgos del recorrido E2E: hello-world v2 en Mediapro"
status: informe
date: 2026-08-29
docs_language: es
---

# Hallazgos del recorrido E2E — `hello-world v2`

> **Qué es esto.** El diario de un recorrido real por la UI, creando un proyecto
> CodeIgniter 4 en el tenant Mediapro y llevándolo hasta cerrar un plan completo,
> con el stack ya desplegado con el código del ADR 0162 y los arreglos de
> edición de tareas.
>
> Se anota **según ocurre**, sin filtrar. Un hallazgo aquí no es todavía un
> defecto confirmado: algunos resultarán ser malentendidos míos y quedarán
> marcados como tales.

## Contexto de la ejecución

|                      |                                                            |
| -------------------- | ---------------------------------------------------------- |
| Tenant               | Mediapro                                                   |
| Proyecto             | `hello-world v2` — `1b43db92-62f7-4428-9982-861339e13949`  |
| Referencia (control) | `hello-world` (1 plan, 10 tareas, equipo del tenant)       |
| Repo                 | `daycry/test-hello-world.git` — greenfield, un `README.md` |
| Stack                | 4 imágenes reconstruidas, 35 contenedores, 0 enfermos      |

**Confusor declarado:** el proyecto de referencia corría con `claude-sonnet-5` y
este también, pero el código de la plataforma ha cambiado entre uno y otro. No es
un experimento controlado: es una validación de que el sistema funciona. Las
mejoras no son atribuibles a un cambio concreto.

---

## H1 — La plantilla declara un runtime y el formulario no lo recoge

**Dónde:** asistente de creación de proyecto, paso 2 («personaliza»).

**Qué pasa:** al elegir «Plantilla: App CodeIgniter 4» —que tiene
`default_runtime_template = php-phpunit`— el desplegable «Runtime por defecto»
aparece en **«— Sin runtime por defecto (defaults por-tool) —»**. La plantilla no
prerrellena el campo.

**Por qué importa, y no es cosmético:** «sin runtime» **no significa sin
runtime**. Significa `python-pytest` (`DEFAULT_RUN_RUNTIME_ID`). Un operador que
elige la plantilla de CodeIgniter y confía en ella se lleva un proyecto PHP que
ejecuta `composer` dentro de una imagen de Python, y el síntoma que recibe
—`command not found`— acusa a su repositorio en vez de a la configuración. Es el
defecto que el ADR 0162 mide, servido por el camino que más confianza inspira:
haber elegido la plantilla correcta.

**Cómo se sorteó:** poniendo `php-phpunit` a mano en el desplegable.

**Arreglo propuesto:** que el paso 2 preseleccione el `default_runtime_template`
de la plantilla elegida. Coste bajo, y cierra el agujero justo donde el usuario
cree que ya está resuelto.

---

## H2 — La plantilla trae el equipo _builtin_, no el del tenant

**Dónde:** mismo asistente; campo «Equipo» de la vista previa.

**Qué pasa:** el proyecto nace con el equipo **builtin** `CodeIgniter 4`,
mientras que el proyecto de referencia del mismo tenant usa `CodeIgniter 4
(copia)`, una copia editable propia. La casilla «Personalizar el equipo para este
proyecto» existe y está **desmarcada** por defecto.

**Por qué importa:** el equipo builtin es compartido y no editable. Un tenant que
ya tiene su copia personalizada —con sus prompts ajustados— crea proyectos que
**no la usan**, sin que nada se lo indique. No es un fallo de corrección, pero sí
una divergencia silenciosa entre lo que el tenant configuró y lo que sus
proyectos nuevos heredan.

**Estado:** pendiente de decidir si es defecto o comportamiento querido. Lo
anoto porque la elección por defecto no es evidente desde la pantalla.

---

<!-- Los hallazgos siguientes se añaden según aparecen durante el recorrido. -->

## H3 — La rama por defecto del formulario de git es `main`, y el repo usa `master`

**Dónde:** ficha de proyecto → «Repositorio Git» → campo «Rama por defecto».

**Qué pasa:** el campo viene precargado con `main`. El repositorio de referencia
—y el que se va a usar— tiene `master`.

**Por qué se anota:** no es un defecto (`main` es el default razonable hoy), pero
el proyecto de referencia del mismo tenant apunta al mismo remoto con `master`, y
nada en la pantalla avisa de la discrepancia hasta que el clone falla o crea una
rama que no existía. La sincronización sí reportó bien: «Rama por defecto local
creada desde el remoto».

**Estado:** anotado como fricción, no como fallo. Un `Sincronizar` que leyese la
rama por defecto del remoto y la propusiera ahorraría el paso.

---

## H4 — Refutado: el desplegable de modelos SÍ ofrece Sonnet 5

**Qué se creía:** que `claude_sdk` no expone `list_models()` y que, por tanto, el
selector de modelo caería a texto libre para ese proveedor.

**Qué se ve en pantalla:** al elegir «Claude SDK (claude_sdk)» en «Modelo del
proyecto», el campo de modelo **se convierte en desplegable** y ofrece la lista
completa, con `claude-sonnet-5` y `claude-opus-5` entre ellos. Además aparece un
selector de «Razonamiento» que no estaba antes.

**Por qué pasa:** `list_available_models_for_provider` usa `config.models` cuando
está sincronizado y, cuando no, **cae al catálogo de precios**. Para `claude_sdk`
no hay lista sincronizada, así que la lista sale de `model_prices` — que sí está
al día.

**Estado:** REFUTADO. El diagnóstico previo miraba una sola de las dos fuentes.
Queda en pie sólo la parte de los **defaults desactualizados** del código
(`claude-sonnet-4-5` en el cliente, `claude-sonnet-4` en ajustes de plataforma),
que únicamente afectan a quien no elige modelo.

Un matiz que sí conviene recordar, y que el propio código documenta: el catálogo
de precios «puede listar modelos que el proveedor NO sirve». El desplegable
ofrece lo que tiene precio, no lo que está disponible.

---

## H5 — El campo de app-preview perdió lo tecleado al desplazarse la página

**Dónde:** ficha de proyecto → «App-preview de validación humana».

**Qué pasa:** al hacer clic por coordenadas y teclear inmediatamente, el texto no
llegó al campo: la página se había desplazado entre una acción y otra. Con
referencia al elemento funcionó a la primera.

**Estado:** casi seguro **artefacto de la automatización**, no un defecto del
producto — un humano ve dónde está el cursor. Se anota sólo por si reaparece con
un patrón: si el formulario reposicionara contenido tras guardar otra sección,
sería una molestia real.

---

## Lo que SÍ funcionó, y conviene que conste

- La creación desde plantilla heredó `allowed_commands` (`php composer phpunit
spark`), las 8 KBs de CodeIgniter y las tools.
- Guardar el repositorio **encoló el clone y funcionó**: el bare repo
  `hello-world-v2.git` apareció en disco y la sincronización reportó «Rama por
  defecto local creada desde el remoto».
- El modelo quedó guardado exactamente igual que en el proyecto de referencia.
- La configuración final de los dos proyectos es idéntica campo a campo.

---

## H6 — BLOQUEANTE: crear desde plantilla da un proyecto que no puede planificar

**Dónde:** asistente de creación → chat del proyecto, modo planning.

**Qué pasa.** El proyecto nace con el equipo **builtin** `CodeIgniter 4`. Ese
equipo tiene sus 10 miembros, pero **sus agentes pertenecen al tenant
`Platform`** (`00000000…`), no al tenant del proyecto. Al abrir el chat, el
equipo no puede responder:

```text
⚠️ El equipo del proyecto no tiene agentes configurados, así que no puede
   responder en el chat. Asigna un equipo con agentes al proyecto.
```

Comprobado en BD, y es exactamente esto:

| Equipo                   | `is_builtin` | Tenant de sus agentes |
| ------------------------ | ------------ | --------------------- |
| `CodeIgniter 4`          | sí           | `00000000` (Platform) |
| `CodeIgniter 4 (copia)`  | no           | `019f8e26` (Mediapro) |
| `CodeIgniter 4 - tenant` | no           | `c5e446e7` (Demo)     |

El proyecto es de Mediapro y su equipo asignado es el de Platform. El aislamiento
multi-tenant —que funciona como debe— hace invisibles esos agentes.

**Por qué es grave y no una nota al pie.** Es el **camino por defecto**: elegir
una plantilla, aceptar lo que propone y pulsar «Crear proyecto». La casilla
«Personalizar el equipo para este proyecto» —que crearía la copia con agentes
propios— está **desmarcada por defecto**, y nada indica que sin marcarla el
proyecto no podrá planificar. El usuario no descubre el problema al crear: lo
descubre al escribir su primer mensaje en el chat.

Sustituye a la duda que quedaba abierta en H2: no es «pendiente de decidir si es
defecto», es un defecto.

**Arreglos posibles, por orden de menos a más invasivo:**

1. Que el asistente **avise** al elegir un equipo builtin: «este equipo es de
   plataforma; el proyecto no podrá usar sus agentes — marca “Personalizar el
   equipo” para tener una copia propia».
2. Que «Personalizar el equipo para este proyecto» venga **marcada por defecto**
   cuando la plantilla trae un equipo builtin.
3. Que la creación desde plantilla haga la copia **siempre**, y la casilla pase a
   significar lo contrario («compartir el equipo de plataforma, sólo lectura»).

---

## H7 — El mensaje se pinta dos veces, aunque sólo se envía una

**Dónde:** chat del proyecto, tras pulsar «Enviar».

**Qué pasa:** el mensaje aparece duplicado en pantalla, las dos veces con la
etiqueta `USER · PLANNING`. En base de datos hay **un solo** mensaje `user`, así
que el envío es correcto: lo que se duplica es el pintado (probablemente el eco
optimista de la UI conviviendo con el mensaje ya persistido que llega por
WebSocket).

**Por qué importa aunque no corrompa nada:** en una conversación de planificación
el usuario relee lo que pidió. Ver su petición dos veces le hace dudar de si
pulsó dos veces —y si duda, vuelve a mirar, o peor, cancela y reescribe.

**Estado:** defecto de UI confirmado (envío correcto, pintado incorrecto).

---

## H8 — Dos mensajes contradictorios a la vez

**Dónde:** mismo chat, misma pantalla.

**Qué pasa:** conviven el aviso de que el equipo **no puede responder** y el
indicador de que **está pensando** («El equipo está pensando… (esto puede
tardar)»). Uno de los dos sobra: si el sistema ya sabe que no hay agentes
utilizables, el spinner no debería arrancar.

**Por qué se anota:** es la misma familia que el resto del ADR 0162 — una señal
que dice algo distinto de lo que ocurre. Aquí el usuario espera a que termine
algo que no ha empezado.

---

## H9 — El flujo correcto es «adoptar y luego asignar», y no se descubre desde el proyecto

**Corrección del operador durante el recorrido.** Ante el bloqueo de H6 se optó
por elegir en el desplegable del proyecto la copia que ya existía —`CodeIgniter 4
(copia)`—, y eso está **mal**: esa copia es la que usa el proyecto de referencia,
así que los dos proyectos pasarían a compartir agentes, y tocar uno afectaría al
otro.

**El flujo correcto es otro**, y vive en la sección **Equipos**: cada equipo
built-in tiene un botón **«+ Adoptar»** que abre «Adoptar / Personalizar equipo»
—_«crea una copia editable… sus agentes se forkean (persona + tools + skills) y
el equipo original built-in no se toca»_— con dos destinos:

- **Catálogo del tenant**: el equipo y sus agentes viven a nivel de tenant y se
  reutilizan en cualquier proyecto.
- **Un proyecto**: el equipo y sus agentes quedan **atados a un proyecto
  concreto**.

Adoptando con destino «Un proyecto» se obtiene lo que hacía falta: 10 agentes
propios, aislados del proyecto de referencia.

**Los dos defectos que esto destapa:**

1. **El desplegable de equipos del proyecto mezcla todo sin distinguir nada.**
   Ofrece los 6 built-in de plataforma junto a las copias del tenant, sin marcar
   cuáles tienen agentes utilizables por ese tenant y cuáles no. Elegir un
   built-in ahí es elegir un equipo que no podrá responder (H6), y la pantalla no
   lo insinúa.
2. **Adoptar con destino «Un proyecto» NO asigna el equipo a ese proyecto.**
   Comprobado: tras adoptar, el equipo nuevo existe con sus 10 agentes atados al
   proyecto, pero `projects.team_id` sigue apuntando al anterior. Hay que ir al
   proyecto, abrir «Editar» y seleccionarlo a mano. Si el destino ya es «este
   proyecto concreto», dejar el proyecto apuntando a otro equipo es un paso a
   medias que sólo se detecta comprobando la base de datos.

---

## H10 — El botón «Editar» del proyecto responde de forma inconsistente

**Qué pasa:** en tres intentos distintos, el primer clic sobre «Editar» no abrió
el diálogo, y un segundo clic lo abrió y lo cerró en la misma acción. Sólo abrió
de forma fiable haciendo **un único clic por coordenadas**.

**Estado:** sospechoso pero NO confirmado como defecto del producto. Puede ser un
artefacto de la automatización (dos eventos de clic por acción, o un `ref` que se
recalcula). Se anota por si reaparece; un humano lo notaría como «a veces hay que
darle dos veces».

---

## H11 — CONFIRMADO EN VIVO: el planner no produce ni un criterio ejecutable

**El hallazgo central del ADR 0162, reproducido con un plan real.**

El equipo planificó de verdad: el PM convocó a cinco especialistas (Arquitecto,
Backend, DevOps, Frontend, QA), cada uno respondió, y el PM sintetizó un plan de
**6 tareas** con roles y dependencias sensatas:

| id  | rol               | título                                   |
| --- | ----------------- | ---------------------------------------- |
| t1  | `backend_dev`     | Instalar y scaffoldear CodeIgniter 4     |
| t2  | `devops`          | Configurar entorno (baseURL, permisos)   |
| t3  | `backend_dev`     | Crear controlador y ruta de la home      |
| t4  | `frontend_dev`    | Maquetar vista de home con estilo mínimo |
| t5  | `qa`              | Escribir test PHPUnit de la home         |
| t6  | `project_manager` | Documentar puesta en marcha en el README |

**Y los 25 criterios de aceptación de esas 6 tareas son STRINGS.** Ninguno es un
dict con `runtime` y `command`. Comprobado sobre el adjunto `planning_directive`
del mensaje de síntesis: `jsonb_typeof` del primer criterio de cada tarea es
`string`, en las seis.

Incluso el de la tarea de QA —la que existe _para_ verificar— es prosa:

> «Un test feature/HTTP realiza GET a / y verifica que la respuesta…»

**Consecuencia directa, y es el defecto entero en una frase:** cuando este plan
se ejecute, el test-runtime **no se disparará ni una vez** (exige dict con
`runtime` **y** `command`, `execution.py:992-998`). Un plan con una tarea de QA
dedicada se completará sin ejecutar un solo test.

**Por qué esto valida el trabajo hecho, y hasta dónde:** antes de los cambios de
esta semana, el bloque `<test-report>` habría **desaparecido** del prompt del
reviewer y la tarea se habría cerrado en verde sin que nada lo indicara. Ahora el
bloque llega y declara explícitamente el caso «no había criterios ejecutables».

El falso verde **no desaparece: se hace visible**. Que es exactamente lo que la
opción B del ADR promete, y exactamente por qué las opciones A y C siguen
haciendo falta.

**Este hallazgo no pide arreglo nuevo:** pide firmar la opción A del ADR 0162 (que
cada criterio declare cómo se verifica, y que lo declare quien escribe el test,
no el planner).
