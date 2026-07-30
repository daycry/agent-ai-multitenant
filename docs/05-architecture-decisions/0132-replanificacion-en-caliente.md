---
title: "ADR 0132: Replanificación en caliente de un plan en curso"
status: accepted
date: 2026-07-26
deciders: [operador]
relates_to: [0022, 0087, 0107, 0117]
---

# ADR 0132: Replanificación en caliente de un plan en curso

## Resolución (2026-07-26)

**Aceptadas A2 + (b) sin cancelación automática + C1 + traza de evento**, por la
delegación permanente del operador de implementar los ADR `proposed` eligiendo la
mejor opción («analizar los ADR proposed e implementarlos eligiendo la mejor
opción», 2026-06-17). Implementado en `task_wf_45`:

- **A2** — `sync_to_kanban` deja de ser aditivo: reconcilia por estado de la
  tarea. No empezada → se actualiza o se cancela; **en vuelo → 409 nombrándola**;
  terminal → se congela y se reporta. Las aristas que el spec ya no declara se
  borran (antes solo se añadían).
- **(b)** — nada se cancela solo. El 409 lleva las tareas para que el humano las
  pare desde su ficha, donde `TaskHumanActions` (`task_wf_40`) ya ofrece hacerlo.
- **C1** — sin re-aprobación, por coherencia con el ciclo de correcciones del
  ADR 0107.
- **(d)** — el resultado del sync reporta qué se actualizó, canceló y congeló.

**Todo o nada**: el rechazo por trabajo en vuelo ocurre ANTES de tocar nada. Un
replan a medias dejaría el tablero en un estado que no es ni el plan viejo ni el
nuevo, y nadie sabría cuál está mirando.

Consecuencia pendiente y deliberada: el gate de `plans.py` sigue admitiendo el
PUT del spec en `in_progress`, que es lo que hace posible este camino.

## Contexto

La auditoría del 2026-07-25 anotó como hallazgo A-06 que **no existe replanificación**:
cero ocurrencias de `replan*` en `apps/`. El recon posterior lo matizó, y el matiz cambia
por completo la naturaleza de esta decisión.

**La mitad aditiva de la replanificación ya funciona hoy, y no tiene puerta.** El camino
completo existe y se puede recorrer ahora mismo desde la UI:

1. `PUT /plans/{id}` acepta una `specification` nueva. Hasta `task_wf_42` la aceptaba en
   **cualquier** estado; ahora el gate es explícito y deja `in_progress` abierto a
   propósito, remitiendo aquí
   ([plans.py:812-836](apps/api-server/src/api_server/routers/plans.py#L812-L836)).
2. `POST /plans/{id}/sync-to-kanban` admite `in_progress` — no solo `approved`
   ([plans.py:1406](apps/api-server/src/api_server/routers/plans.py#L1406)).
3. La sección de sincronización sigue montada en la página del plan mientras corre.

Así que la pregunta de este ADR **no es «cómo diseñamos la replanificación desde cero»**,
sino «qué reglas gobiernan un camino que la gente ya puede recorrer». Es una diferencia
importante: hoy ese camino se recorre sin decisión tomada, y el ADR 0022 ya lo anticipó
en sus próximos pasos («extender `sync-to-kanban` para que admita re-syncs cuando el plan
se refina post-aprobación (**nuevas tareas, no cambios de id**)»,
[0022:177-179](docs/05-architecture-decisions/0022-plan-to-kanban-sync.md#L177-L179)).

### El agujero real: el Kanban diverge en silencio

`sync_to_kanban` es **estrictamente aditivo**. Una tarea del spec que ya se materializó
se salta sin mirar si cambió
([sync_to_kanban.py:180](apps/api-server/src/api_server/chat/sync_to_kanban.py#L180)):

```python
for spec_id in selected_ids:
    if spec_id in existing:
        result.skipped_task_ids[spec_id] = existing[spec_id]
        continue
```

Consecuencia, hoy, sin ningún aviso al operador:

| Cambio en el spec de un plan `in_progress`               | Qué pasa en el Kanban                           |
| -------------------------------------------------------- | ----------------------------------------------- |
| **Añadir** una tarea nueva                               | se materializa en el siguiente sync ✅          |
| **Editar** título/descripción/criterios de una existente | **nada** — el agente sigue con la versión vieja |
| **Borrar** una tarea del spec                            | **nada** — la tarea sigue viva y se ejecuta     |
| **Cambiar `depends_on`** de una existente                | las aristas **se añaden**, nunca se quitan      |

Las tres últimas filas son el hallazgo: el spec y el tablero dejan de decir lo mismo y
nadie se entera. El operador cree que ha replanificado; el equipo sigue ejecutando el
plan anterior. Un fallo silencioso es peor que un 409.

### Lo que ya está resuelto y no hay que reinventar

- **Cancelar trabajo en vuelo**: `cancel_tasks_and_executions(session, plan_id=…)` cancela
  toda tarea no terminal del plan, pide la cancelación de sus ejecuciones y devuelve las
  que hay que revocar en Celery
  ([execution_repo.py:346-360](apps/api-server/src/api_server/db/execution_repo.py#L346-L360)).
  Lo usan la cancelación de plan y el borrado de proyecto.
- **Ciclo de correcciones tras un rechazo humano**: el ADR 0107 ya define cómo se añaden
  tareas de corrección **al mismo plan**, con `origin=correction` y meta en
  `specification.corrections`. Una replanificación se le parece mucho.
- **Máquina de estados**: `_TRANSITIONS`
  ([plan_state_machine.py:44](apps/api-server/src/api_server/chat/plan_state_machine.py#L44))
  es la única vía legítima para mover un plan. Hoy `in_progress` solo va a `blocked`,
  `pending_human_validation` o `cancelled`.

## Decisión a tomar

Cuatro preguntas. Las tres primeras necesitan respuesta para poder implementar
`task_wf_45`; la cuarta puede diferirse.

### (a) ¿Qué pasa con las tareas ya materializadas que el spec cambia o borra?

**Opción A1 — Aditivo explícito (statu quo, pero honesto).**
El sync sigue sin tocar lo materializado, pero **lo dice**: la respuesta incluye
`diverged_task_ids` (editadas en el spec pero no en el tablero) y `orphan_task_ids`
(materializadas y ya no en el spec), y la UI las muestra como aviso. Cero riesgo, cero
capacidad nueva; convierte un fallo silencioso en uno visible.

**Opción A2 — Reconciliación de tres vías por estado de la tarea.**
El re-sync compara spec ↔ tablero y actúa según en qué estado esté cada tarea:

| Estado de la tarea materializada     | Editada en el spec   | Borrada del spec     |
| ------------------------------------ | -------------------- | -------------------- |
| `backlog` / `ready` (no ha empezado) | se actualiza         | se cancela           |
| `in_progress` / `in_review`          | **se rechaza** (409) | **se rechaza** (409) |
| `done` / `cancelled` (terminal)      | se ignora, se avisa  | se ignora, se avisa  |

La regla que la sostiene: **lo que no ha empezado se puede replanificar; lo que está en
vuelo hay que pararlo primero (y eso es una decisión humana, no un efecto colateral); lo
que ya se hizo es historia y no se reescribe.** El 409 lleva la lista de tareas en vuelo
para que el operador las pare desde la ficha —`TaskHumanActions` ya ofrece «Cancelar» y
«Bloquear» (`task_wf_40`)— y reintente.

**Opción A3 — Replanificar = cancelar y rehacer.**
Un `POST /plans/{id}/replan` cancela el plan entero (con su cascada) y crea uno nuevo con
el spec corregido, enlazado al anterior. Semántica limpísima e implementación casi
gratuita, pero pierde la continuidad: rama git, historia de commits, coste acumulado y
tareas ya hechas quedan en el plan viejo. Para un cambio de una tarea es
desproporcionado.

**Recomendación: A2**, con A1 como primera entrega si se quiere trocear. A2 es la única
que hace que replanificar signifique algo, y su parte peligrosa (tocar trabajo en vuelo)
la resuelve **negándose**, no adivinando.

### (b) ¿Y las tareas en vuelo?

No se cancelan solas. Con A2 el re-sync devuelve 409 nombrándolas y el humano decide. La
alternativa —cancelar automáticamente lo que esté corriendo— tira trabajo (y dinero de
tokens) por un cambio que el operador podría no haber querido aplicar a esa tarea. Si más
adelante se quiere el gesto de fuerza bruta, que sea un parámetro **explícito**
(`?cancel_in_flight=true`) reutilizando `cancel_tasks_and_executions`, nunca el
comportamiento por defecto.

### (c) ¿Una replanificación exige re-aprobación?

**Opción C1 — No.** El plan sigue `in_progress`; el cambio queda en la traza. Es lo que
pasa hoy.

**Opción C2 — Sí, siempre.** `in_progress → pending_approval`, lo que obliga a tocar
`_TRANSITIONS` y a decidir qué pasa con las tareas que siguen corriendo mientras espera
firma. Caro y molesto para corregir una errata.

**Opción C3 — Según el impacto.** Re-aprobación solo si el cambio **añade o borra**
tareas (cambia el alcance) o si el proyecto exige doble firma; una edición de
título/descripción/criterios no la exige. Requiere clasificar el diff, que es
exactamente lo que A2 ya calcula.

**Recomendación: C1 para la primera entrega, con la puerta abierta a C3.** El motivo es
de coherencia, no de comodidad: hoy el ciclo de correcciones del ADR 0107 añade tareas a
un plan aprobado **sin** re-aprobación. Exigirla aquí y no allí sería incoherente. Si se
decide C3, debe aplicarse a los dos caminos a la vez.

### (d) ¿Se versiona el spec?

Hoy no hay traza: `plans.specification` se sobreescribe y la versión anterior se pierde.
Con A2 el `diff` es reconstruible en el momento pero no queda registrado.

**Recomendación: registrar el evento, no versionar el documento.** Un apunte por
replanificación en la traza del plan (qué tareas se añadieron, se actualizaron, se
cancelaron y quién lo hizo), con la misma forma que `specification.corrections` del ADR 0107. Guardar copias completas del spec es un problema de almacenamiento y de UI de
comparación que no paga por sí solo todavía.

## Consecuencias

**Si se acepta la recomendación (A2 + C1 + traza de evento):**

- `sync_to_kanban` deja de ser aditivo y pasa a reconciliar; su contrato de retorno crece
  (`updated_task_ids`, `cancelled_task_ids`, `rejected_in_flight`). Hay que revisar los
  tests que hoy afirman idempotencia por «se salta lo existente».
- Aparece un 409 nuevo que la UI tiene que saber contar, con la lista de tareas en vuelo
  y un enlace a cada ficha.
- El gate de `plans.py:812` puede estrecharse a `draft`/`pending_approval` **una vez que
  la replanificación tenga su propio camino**: editar el spec dejaría de ser la puerta
  trasera y pasaría a ser el paso 1 de un gesto con nombre.
- La UI del editor del spec (`task_wf_42`) podría ofrecerse también en `in_progress`,
  avisando de que guardar no aplica nada hasta re-sincronizar.

**Si se rechaza y se elige A1**, la remediación se limita a hacer visible la divergencia
y `task_wf_45` se cierra en una tarde. Es un resultado aceptable: el fallo deja de ser
silencioso, que es la mitad del daño.

**Si no se decide nada**, el camino sigue abierto y sin reglas: cualquiera con
`tenant_member` puede reescribir el spec de un plan en curso y el tablero seguirá
divergiendo en silencio. Ésta es la única opción que no debería elegirse por omisión.

## Qué NO decide este ADR

- La replanificación **automática** por parte de un agente (que el PM decida replanificar
  al ver fracasar tres tareas). Aquí solo se decide el gesto humano.
- El versionado completo del spec con UI de comparación (ver (d)).
- La replanificación de un plan `blocked` o `pending_human_validation`: se tratan como
  fuera de alcance hasta que el gesto exista para `in_progress`.

## Verificación

Cuando se implemente (`task_wf_45`), estas son las afirmaciones que tienen que quedar
fijadas por un test, no por la memoria de nadie:

1. Editar el título de una tarea `backlog` y re-sincronizar → la tarea del tablero cambia.
2. Borrar del spec una tarea `ready` y re-sincronizar → la tarea queda `cancelled`.
3. Editar una tarea `in_progress` → 409 nombrándola; **nada** se modifica (ni las otras
   tareas del mismo sync: o se aplica todo o no se aplica nada).
4. Una tarea `done` que desaparece del spec → sigue `done`, y el aviso lo dice.
5. Quitar una dependencia del spec → la arista desaparece del tablero (hoy no).
6. El re-sync sigue siendo idempotente: repetirlo sin cambios no toca nada.
