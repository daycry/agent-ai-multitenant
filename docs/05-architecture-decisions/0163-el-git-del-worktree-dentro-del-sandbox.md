---
adr_id: "0163"
title: "ADR 0163: El `.git` del worktree dentro del sandbox del agente"
status: accepted
date: 2026-08-31
decided_on: 2026-08-31
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
| **Un obstáculo** para el agente   | `composer create-project` **exige** un directorio vacío y se niega a andamiar. Es el andamiador más estricto de los medidos (ver la corrección más abajo), pero basta uno                                       |

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

## Decisión

**Que el `.git` del worktree no exista dentro del sandbox mientras corre el
agente.**

Tres piezas, y las tres hacen falta:

1. **El worker retira el puntero antes de lanzar el contenedor y lo repone al
   salir** (`git_link_hidden`, un gestor de contexto para que el `finally` no se
   pueda desemparejar). El agente ve un directorio de trabajo normal — que es la
   verdad, porque git ahí dentro nunca funcionó. Se repone el contenido EXACTO
   leído antes, no uno reconstruido por convención. Sólo se aplica con el
   workspace ESCRIBIBLE: un run de review monta el worktree ajeno de sólo lectura
   (ADR 0095), así que ahí no hay nada que proteger y tocarlo arriesgaría pisar a
   un run concurrente.

2. **Se bloquea el worktree con `git worktree lock` mientras dura la ventana.**
   Esta es la pieza que hace segura toda la maniobra, y NO estaba en la primera
   versión de esta decisión: sin el puntero, git considera el worktree
   `prunable`, y el `git worktree prune` que dispara **cualquier tarea hermana
   del mismo proyecto** al provisionarse —o el reaper de las 03:30— se lleva sus
   metadatos. Reponer el puntero después no sirve de nada porque ya no hay a
   dónde apuntar: sería el incidente original **provocado por la propia cura**, y
   esta vez sin que ningún agente borrara nada.

3. **La provisión repara el enlace antes de sincronizar** (`repair_worktree_link`
   antes de `sync_to_head`). Cubre la muerte dura del worker —hard limit de
   Celery, OOM, reinicio del contenedor— que no ejecuta el `finally`. Sin esto,
   el reintento moría en `sync_to_head` y la tarea quedaba en
   `workspace_unavailable` en CADA relanzamiento. El orden importa: reparar
   después de sincronizar no serviría, porque nunca se llega.

El lock sobrevive a la muerte dura a propósito — es lo que impide que un prune se
lleve los metadatos antes de que el reintento repare— y `repair_worktree_link`
lo suelta al reparar. Un worktree que quedara bloqueado sería inmortal para el
reaper y el disco crecería sin que nadie lo notara.

### La mitad que se propuso y NO se implementa, y por qué

La primera versión de esta decisión pedía además que `commit_task` usara
`--git-dir=<bare>/worktrees/<id> --work-tree=<worktree>` «para que el worker deje
de depender de un fichero que vive en el workspace escribible del agente».

**Esa afirmación es falsa, y se comprobó leyendo el código.** `commit_task` no es
el único camino del worker que corre git DENTRO del worktree:

| Camino                          | Qué ejecuta                                         |
| ------------------------------- | --------------------------------------------------- |
| `sync_to_head` (`git_repos.py`) | `git -C <wt> fetch` / `reset --hard` / `clean -fdx` |
| `compute_task_review_diff`      | el diff de la tarea, sobre el worktree              |
| `commit_task`                   | `git add -A` y `git commit`                         |

Cambiar sólo el tercero no elimina la dependencia: la deja intacta en los otros
dos y la mueve de sitio en uno. El puntero hay que reponerlo igual, así que la
«mitad 2» habría sido una capa más sobre un fallo ya cubierto tres veces —
acumulación, no diseño.

Lo que aquella mitad perseguía —que un puntero ausente no pueda costar el trabajo
de la tarea— lo consiguen el lock y la reparación, y de forma que vale para TODOS
los caminos, no sólo para el commit.

### Qué se gana

| Situación                            | Con las guardas actuales                   | Sin `.git` en el sandbox               |
| ------------------------------------ | ------------------------------------------ | -------------------------------------- |
| El agente borra `.git`               | bloqueado por una puerta, abierto por otra | **no hay nada que borrar**             |
| `composer create-project .`          | **sigue fallando**                         | **funciona**                           |
| El agente ve un repo que no funciona | sí, y gasta turnos en ello                 | no: ve un directorio, que es la verdad |
| Superficie de fallo                  | dos guardas que mantener                   | la clase desaparece                    |

**Corrección del 2026-08-31, medida.** Una versión anterior de este ADR
afirmaba que `npm create`, `django-admin startproject`, `rails new` y
`cargo new` fallaban igual. **Es falso**, y conviene que conste porque el
argumento se apoyaba en ello. Comprobado ejecutándolos en sus propias imágenes
de runtime con un `.git` presente:

| Comando                     | Con `.git` delante                           |
| --------------------------- | -------------------------------------------- |
| `composer create-project .` | **falla** — «Project directory is not empty» |
| `npm init -y`               | funciona                                     |
| `cargo init`                | funciona                                     |
| `cargo new .`               | funciona                                     |

Es decir: `composer create-project` es MÁS ESTRICTO que la mayoría.

La corrección no debilita la decisión: la reformula, y en una forma que
generaliza mejor. **El problema no es que todos los andamiadores fallen; es que
`.git` es un cable trampa invisible.** El agente no puede ver que ese fichero
sostiene su propia entrega, tiene `rm` a mano, y basta UNA herramienta
estricta —o una limpieza de directorio que le parezca razonable— para que lo
corte. Que hoy sólo conozcamos un andamiador que lo fuerza no es una garantía:
es el único que hemos probado.

Y hay una asimetría que ninguna estadística de andamiadores cambia: el coste de
perderlo es **el trabajo entero de la tarea**, y el beneficio de tenerlo dentro
del sandbox es **cero** — ahí no sirve para nada.

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

**E. Andamiar en un directorio temporal y mover el resultado** (propuesta del
operador, 2026-08-31). El agente ejecuta `composer create-project … tmp` —donde
nunca hay `.git`— y luego mueve el contenido a la raíz. No toca el contrato del
sandbox, no cambia el montaje ni el commit, y `mv` ya está permitido.

**Es razonable y está medida.** Funciona para el caso que teníamos delante y
falla para otro que la propia pregunta anticipaba. Comprobado ejecutando cada
andamiador en su imagen de runtime y mirando qué ficheros OCULTOS deja:

| Andamiador                              | Ocultos que crea               | ¿Sobrevive «temp + move»? |
| --------------------------------------- | ------------------------------ | ------------------------- |
| `composer create-project` (CodeIgniter) | ninguno — usa `env`, no `.env` | **sí**                    |
| `npm init -y`                           | ninguno                        | sí                        |
| `go mod init`                           | ninguno                        | sí                        |
| `cargo new`                             | `.git`, `.gitignore`           | **no**                    |

El caso de Rust rompe la propuesta por dos vías, y la segunda es la mala:

- `mv tmp/* .` deja fuera los ocultos — el proyecto pierde su `.gitignore`;
- `mv tmp/. .` o `cp -a` mueven el `.git` DE CARGO encima del puntero
  del worktree. El worktree queda roto **exactamente igual que en el incidente**,
  pero sin que nadie haya borrado nada: mucho más difícil de ver.

No se descarta por mala, sino por **incompleta**: resuelve un andamiador y deja
abierta la misma clase para otro. Y en la variante en la que la ejecuta el
agente hereda además el coste de la alternativa C — turnos, en todos los
proyectos, para siempre.

## La sub-decisión que ninguna alternativa tenía escrita

Evaluar la propuesta E destapó un hueco **en esta misma decisión**, y conviene
que conste porque no estaba: si el `.git` no está durante el run y el agente
lanza `cargo new .`, el andamiador **crea el suyo**. Al reponer el puntero, el
worker se encuentra el sitio ocupado.

La respuesta razonable —y hay que tomarla a propósito, no descubrirla en
producción— es que **la plataforma retira el `.git` que traiga el andamiador**. El
versionado lo lleva el worktree, no el scaffolder; es lo mismo que hace
`--remove-vcs` de composer y lo que hace cualquier CI al empaquetar. Pero
significa que el trabajo de versionado que el andamiador creyó dejar hecho se
descarta, y eso tiene que estar escrito donde alguien lo lea.

## Consecuencias

- `workers/isolation.py` y el ciclo de vida de la ejecución tienen que retirar y
  reponer el puntero, con cuidado de reponerlo también cuando el run falla.
- Si el worker muere entre retirar y reponer, el worktree queda sin puntero: lo
  cubre la reparación que ya existe (`repair_worktree_link`), que pasa de ser el
  arreglo a ser lo que debe ser — una red.
- Algunas herramientas detectan la raíz del proyecto buscando `.git` hacia
  arriba. Dentro del sandbox no la encontrarán. Hay que comprobar si alguna del
  toolchain lo usa; en los runtimes actuales no se conoce ninguna que lo exija.
- La guía de `shell_exec` que explicaba que «git sale 128 aquí» se reescribió el
  2026-09-01 (`tool_usage_guidance.py`): ahora dice que no hay repositorio en el
  sandbox y que la plataforma versiona por el agente.

## Lo que se midió antes de aceptarlo

Los cuatro puntos que esta decisión se puso a sí misma, más el que salió de la
auditoría adversarial (cinco agentes, 2026-08-31):

1. **El andamiaje funciona.** `composer create-project codeigniter4/framework .`
   falla con el puntero delante («Project directory is not empty») y el worktree
   queda vacío sin él. Verificado en la imagen de runtime real.
2. **El commit y el push no cambian.** Ciclo completo con `commit_task` real:
   sha de 40, trailers `Plan-Id`/`Task-Id` en su sitio, la rama del plan llega al
   remoto. El índice vive en los metadatos del bare y no se toca.
3. **Un run abortado deja el worktree reponible.** El `finally` repone byte a
   byte, también al propagarse una excepción; y si el proceso muere de golpe, la
   provisión del reintento repara.
4. **Un andamiador con su propio `.git`** (`cargo new`) acaba con el puntero del
   worktree en su sitio, no con el repo que se inventó el scaffolder.
5. **El prune concurrente** —el que destapó la auditoría— ya no puede podar el
   worktree oculto:

   |          | Con `.git` oculto + `prune` de una hermana  |
   | -------- | ------------------------------------------- |
   | sin lock | metadatos **podados**, commit roto (rc=128) |
   | con lock | metadatos **intactos**, commit OK           |

21 tests lo fijan, todos verificados mutando producción: sin lock; sin soltar el
lock; sin ocultar; ocultando también en review; y sin reparar en la provisión.

## Addendum del 2026-09-01: tres huecos que la auditoría midió, y cómo se cerraron

Los tres se reprodujeron con git real antes de tocar nada.

1. **El lock no tenía dueño.** `git_link_hidden` reponía el puntero y soltaba el
   lock en su `finally` sin mirar de quién era. Con dos ejecuciones SOLAPADAS de
   la misma tarea —ya ocurren: la gotcha «deploy relaunches frozen tasks»— la
   provisión de B reparaba y desbloqueaba mientras A seguía corriendo, y al
   terminar A reponía el `.git` en mitad del run de B y le soltaba el lock. Ahora
   el motivo del lock lleva el `execution_id`; sólo el dueño repone y suelta, y
   si otra ejecución tomó el relevo se registra y no se toca nada.
2. **La reparación comprobaba existencia, no validez.** Con un `.git` que fuera un
   DIRECTORIO —el repo de `cargo new .` si el restore no pudo retirarlo, o un
   worker muerto entre esconder y reponer— `repair_worktree_link` decía «nada
   que reparar» y `commit_task` commiteaba en el repo del andamiador: devolvía un
   sha que no existía en el bare del plan. Ahora valida que el puntero apunta a
   los metadatos de este worktree en un bare del proyecto, descarta lo que no lo
   sea, y repara.
3. **La provisión toma el relevo, y el reaper suelta antes de podar.** Un worker
   muerto entre reponer el puntero y soltar el lock dejaba un lock huérfano que
   nadie iba a soltar: `repair_worktree_link` sólo desbloqueaba cuando reparaba,
   y `_remove_worktree` no desbloqueaba nunca — `remove --force` se negaba, el
   `rmtree` de respaldo borraba el disco y `prune` respetaba el lock, dejando un
   registro fantasma `locked` permanente y un `worktree add` del mismo id con
   rc=128. Ahora la reparación suelta cualquier lock de la plataforma haya
   reparado o no (quien provisiona o cierra es el dueño legítimo), y el reaper
   hace `worktree unlock` antes de `remove`.

## Lo que esta decisión NO garantiza

- **La ventana existe.** Entre retirar y reponer hay hasta dos horas en las que
  el worktree depende del lock. Si alguien añade un camino que pode SIN respetar
  el lock, vuelve el problema. El lock es un fichero `locked` en los metadatos:
  `git worktree prune` lo respeta, un `rm -rf` a mano no.
- **Un lock puesto a mano por una persona no se toca.** La plataforma sólo
  suelta los locks con su propio motivo (`agent run in progress […]`).
- **No cubre un worktree que ya estaba roto** antes de este cambio. El del
  incidente del 2026-08-31 sigue en disco sin registrar, y es irrecuperable
  porque sus metadatos se podaron antes de que existiera el lock.
