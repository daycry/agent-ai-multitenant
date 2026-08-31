---
adr_id: "0163"
title: "ADR 0163: El `.git` del worktree dentro del sandbox del agente"
status: proposed
date: 2026-08-31
deciders: [operador]
relates_to: [0012, 0019, 0021, 0040, 0072, 0089, 0093, 0095, 0162]
plan_referenced: 2026-08-29-hallazgos-e2e-hello-world-v2
docs_language: es
---

# ADR 0163 — El `.git` del worktree dentro del sandbox del agente

## Contexto

El 2026-08-31, en la primera ejecución real del proyecto `Hello World CI4 v3`
del tenant Mediapro, un agente hizo esto:

```text
stack_exec  composer create-project codeigniter4/framework .   -> falla
delete_file .git                                               -> deleted: true
stack_exec  composer create-project codeigniter4/framework .   -> falla
delete_file README.md                                          -> deleted: true
stack_exec  composer create-project codeigniter4/framework .   -> OK
```

Instaló CodeIgniter 4.7.4 correctamente y `php spark routes` respondió. Al
cerrar la tarea:

```text
git add -A failed (rc=128): fatal: not a git repository
```

El deliverable quedó **hecho, en disco y fuera de toda rama**. La tarea acabó
`blocked`.

### Por qué esto no es un agente portándose mal

El worker monta el worktree de la tarea en `/workspace`
(`workers/isolation.py`), y **sólo eso**: el bare repo no se monta. Dentro va
`.git`, que en un worktree de git no es un directorio sino un **fichero** de una
línea con un puntero `gitdir:` a `<bare>/worktrees/<task_id>` — una ruta que en
el contenedor **no existe**.

Ese fichero es, simultáneamente:

|                                   |                                                                                                                                                                                                                 |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Inútil** para el agente         | Todo `git` sale 128. Lo documenta el propio código: «git is BROKEN here anyway: the worktree's `.git` points to the bare repo's worktree metadata, which is NOT mounted in the sandbox» (`workers/run_spec.py`) |
| **Imprescindible** para el worker | `commit_task` hace `git add -A` con `cwd=worktree` y descubre el repo a través de ese puntero                                                                                                                   |
| **Un obstáculo** para el agente   | `composer create-project` **exige** un directorio vacío. También `npm create`, `django-admin startproject`, `rails new`, `cargo new`…                                                                           |

Con las tres cosas a la vez, y teniendo `rm` en la allowlist base del SDK, que el
agente lo borre **no es una aberración: es el resultado esperado**. Le pedimos
instalar un framework cuyo instalador canónico exige directorio vacío, le dimos
la herramienta para vaciarlo, y pusimos ahí un fichero que desde su punto de
vista no hace nada.

### Lo que ya se hizo, y por qué no basta

Dos guardas, las dos útiles y las dos insuficientes:

1. **`file_tools` rechaza `.git`** (`_mutable_path`). Cierra `delete_file` y
   `write_file`. **Esquivable**: `shell_exec("rm .git")` hace lo mismo, y `rm`
   está en `_SDK_BASE_SHELL_COMMANDS`.
2. **`commit_task` repara el enlace** con `git worktree repair` antes de tocar
   git. Cubre el resultado, sea cual sea el mecanismo — pero **sólo mientras
   sobrevivan los metadatos del bare**: en cuanto un git ve el puntero roto
   dispara `worktree prune` y ya no hay nada que reparar. Medido en los dos
   casos.

Y sobre todo: **ninguna de las dos le devuelve al agente la capacidad de
instalar CodeIgniter**. Con la guarda puesta, `composer create-project .` sigue
fallando porque el directorio sigue sin estar vacío. Hemos convertido «destruye
el repositorio en silencio» en «falla más alto», que es mejor, pero la tarea
sigue sin poder hacerse por el camino natural.

## Decisión que se propone

**Que el `.git` del worktree no exista dentro del sandbox mientras corre el
agente, y que el worker deje de depender de él para commitear.**

Dos cambios que van juntos:

1. **El worker retira el puntero antes de lanzar el contenedor y lo repone
   después.** El agente ve un directorio de trabajo normal.
2. **`commit_task` usa rutas explícitas**:
   `git --git-dir=<bare>/worktrees/<task_id> --work-tree=<worktree> add -A`.
   Las dos rutas las conoce el worker (las construyó al provisionar). Verificado
   en laboratorio: con `.git` borrado, esta forma commitea sin problema mientras
   los metadatos existan — y con el paso (1) ya nadie los rompe.

### Qué se gana

| Situación                                              | Con las guardas actuales                   | Sin `.git` en el sandbox               |
| ------------------------------------------------------ | ------------------------------------------ | -------------------------------------- |
| El agente borra `.git`                                 | bloqueado por una puerta, abierto por otra | **no hay nada que borrar**             |
| `composer create-project .`                            | **sigue fallando**                         | **funciona**                           |
| `npm create`, `django-admin startproject`, `rails new` | igual de rotos                             | funcionan                              |
| El agente ve un repo git que no funciona               | sí, y gasta turnos en ello                 | no: ve un directorio, que es la verdad |
| Superficie de fallo                                    | dos guardas que mantener                   | la clase desaparece                    |

El punto que más pesa es el segundo: **hoy la plataforma no puede andamiar un
proyecto nuevo por el camino canónico de su propio stack**, y eso afecta a todos
los runtimes, no sólo a PHP.

## Alternativas consideradas

**A. Dejarlo como está, con las dos guardas.** Es lo que hay ahora. Descartada
como solución: no arregla el andamiaje, y mantiene dos guardas cuyo único
trabajo es tapar una decisión de diseño. La primera vez que alguien añada una
tercera puerta de escritura habrá que acordarse de las dos.

**B. Quitar `rm` de la allowlist base.** Cierra la puerta que queda abierta hoy,
pero no las que vengan (un script, un `Makefile`, el propio `composer` con otro
subcomando), y le quita al agente una herramienta legítima. Trata el síntoma.

**C. Enseñar al agente el rodeo** (instalar en subdirectorio y mover). Funciona
—`mv` está permitido— y es lo que dice hoy el mensaje de rechazo de la guarda.
Pero convierte en trabajo del modelo algo que es una decisión de infraestructura,
y se paga en turnos cada vez, en todos los proyectos, para siempre.

**D. Montar un subdirectorio del worktree como `/workspace`.** Deja el `.git`
fuera del alcance sin tocar el commit, pero cambia la disposición que ven los
agentes y rompe la equivalencia entre «la raíz del workspace» y «la raíz del
repo» — justo lo que el ADR 0162 acaba de fijar como fuente de confusión.

## Consecuencias

- `workers/isolation.py` y el ciclo de vida de la ejecución tienen que retirar y
  reponer el puntero, con cuidado de reponerlo también cuando el run falla.
- Si el worker muere entre retirar y reponer, el worktree queda sin puntero: lo
  cubre la reparación que ya existe (`repair_worktree_link`), que pasa de ser el
  arreglo a ser lo que debe ser — una red.
- Algunas herramientas detectan la raíz del proyecto buscando `.git` hacia
  arriba. Dentro del sandbox no la encontrarán. Hay que comprobar si alguna del
  toolchain lo usa; en los runtimes actuales no se conoce ninguna que lo exija.
- La guía de `shell_exec` que hoy explica que «git sale 128 aquí» deja de tener
  sentido y hay que reescribirla: ya no habrá repo que confunda.

## Lo que hay que medir antes de aceptarlo

1. Que `composer create-project codeigniter4/framework .` termina en verde en un
   worktree sin `.git`. Es el caso que motivó el ADR.
2. Que el commit con `--git-dir`/`--work-tree` produce el mismo sha y los mismos
   trailers que el camino actual.
3. Que un run abortado a mitad deja el worktree reponible.
