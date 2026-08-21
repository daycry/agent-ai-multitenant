---
title: "ADR 0154: Qué se hace con las claves foráneas que apuntan a una tabla particionada"
status: accepted
date: 2026-08-10
deciders: [operador, arquitectura]
relates_to: [0151]
plan_referenced: part-01-particionado-append-only
task: [task_part01_07]
docs_language: es
---

# ADR 0154: Las FK que apuntan a una tabla particionada

> **Estado: `accepted`.** Se **retiran las cinco** claves foráneas entrantes y la
> referencia queda como columna suelta, igual que ya hacían `guardrail_events`,
> `notification_logs` y `llm_usage_events` con su `execution_id`. La integridad
> que se pierde en cada caso está sustituida por un camino de borrado que YA
> existe y que se nombra abajo tabla por tabla; donde no lo hubiera, la decisión
> habría sido la contraria.

## Contexto

El [ADR 0151](0151-retencion-de-tablas-append-only.md) convierte cinco tablas
append-only en tablas particionadas por rango mensual sobre `created_at`.
PostgreSQL exige que **la clave primaria de una tabla particionada incluya la
clave de partición**, así que `id` pasa a `(id, created_at)` en las cinco.

Eso rompe toda FK entrante: una clave foránea tiene que referenciar una clave
única _completa_, y `(id)` deja de serlo. No hay forma de conservar la FK tal
cual; solo hay dos salidas:

- **(a) FK compuesta** — llevar `<padre>_created_at` a la tabla hija, rellenarlo
  para las filas existentes, hacerlo `NOT NULL` si el original lo era, y declarar
  `FOREIGN KEY (padre_id, padre_created_at) REFERENCES padre (id, created_at)`.
  Conserva la semántica exacta, incluido el `ON DELETE`.
- **(b) Retirar la FK** — dejar la columna como referencia suelta. La base ya no
  garantiza que apunte a algo, ni propaga borrados.

## El inventario real: son CINCO, no cuatro

El plan `part-01` enumeró cuatro FK, todas hacia `executions.id`. Al abrir el
esquema apareció una quinta, hacia `notification_logs.id`, que nadie había
contado y que además entra **antes** (fase 2 del plan, no fase 5):

| #   | Hija · columna                            | Regla actual         | ¿NOT NULL? | Hacia               |
| --- | ----------------------------------------- | -------------------- | ---------- | ------------------- |
| 1   | `notification_log_reads.log_id`           | `ON DELETE CASCADE`  | sí         | `notification_logs` |
| 2   | `approval_requests.execution_id`          | `ON DELETE CASCADE`  | sí         | `executions`        |
| 3   | `memory_entries.source_execution_id`      | `ON DELETE SET NULL` | no         | `executions`        |
| 4   | `eval_dataset_items.source_execution_id`  | `ON DELETE SET NULL` | no         | `executions`        |
| 5   | `eval_shadow_records.source_execution_id` | `ON DELETE SET NULL` | no         | `executions`        |

Verificado contra el catálogo (`pg_constraint` sobre un esquema en `head`), no
contra los modelos: es la única lectura que no se deja fuera una FK creada por
una migración y no reflejada en el ORM.

## Decisión

**Se retiran las cinco (opción b), y la columna queda como referencia suelta.**

El criterio no es «es más barato» —que lo es—, sino que en las cinco existe otro
camino que hace el trabajo que hacía la FK. La pregunta que el plan `part-01`
exigía responder es literalmente _«hay que escribir quién borra esas filas»_.
Aquí está escrito:

### 1. `notification_log_reads.log_id` (CASCADE, NOT NULL)

Un _receipt_ de lectura del inbox in-app. La cascada existía «por si algún día se
purgan los logs» —así lo dice el docstring de la migración 0048: _«FK to
`notification_logs` (CASCADE) so a (hypothetical) log purge takes its receipts
with it»_—, y el ADR 0151 acaba de decidir que **ese día no llega**: no se borra
nada. Una cascada cuyo disparador se ha decidido que no ocurrirá no protege nada.

Qué se pierde de verdad: que la base impida crear un receipt hacia un `log_id`
inexistente. El efecto de un receipt huérfano es que un `LEFT JOIN` del inbox no
casa y la fila no aparece — no hay corrupción visible ni fuga entre tenants (el
receipt lleva su propio `tenant_id` con su RLS).

### 2. `approval_requests.execution_id` (CASCADE, NOT NULL) — la difícil

Es la que el plan marcaba como «la que más cuesta perder», y es la que más
atención merece porque es `NOT NULL` y cascadea. La razón por la que aun así se
retira es concreta y comprobable:

`approval_requests` tiene **tres** FK con `ON DELETE CASCADE` — hacia
`executions`, hacia `tasks` y hacia `projects`— y `executions.task_id` es a su vez
`ON DELETE CASCADE` hacia `tasks`. O sea: **el único evento que borra una fila de
`executions` es el borrado de su `task`**, y ese mismo evento ya borra la
`approval_request` por su propio `task_id`. La cascada por `execution_id` es
redundante hoy: no hay ningún camino en el que dispare y la de `task_id` no.

Comprobado además que **nada borra `executions` directamente**: no hay `DELETE`
sobre esa tabla en el código (`delete(Execution)` / `DELETE FROM executions` no
aparecen en `apps/`), lo cual es coherente con su propio docstring —_«Executions
are NOT soft-deleted — they are an immutable audit record»_— y con el ADR 0151.

**La condición de validez de esta decisión, escrita para que se pueda auditar:**
si algún día se añade un borrado de `executions` que NO venga de borrar su
`task`, esta decisión deja de ser correcta y hay que volver a la opción (a) o
borrar explícitamente las `approval_requests` en ese camino. Queda como test:
`tests/integration/test_partition_executions.py::test_deleting_a_task_still_removes_its_approval_requests`.

### 3-5. Las tres `source_execution_id` (SET NULL, nullable)

Son exactamente el caso que el plan llamaba «la opción barata»: nullable, sin
semántica que dependa de la FK. `memory_entries.source_execution_id` documenta
«de qué run salió esta memoria»; los dos de evals, «de qué run se promovió este
ítem». Perder el `SET NULL` significa que la columna puede quedar apuntando a un
run borrado; leerla ya era opcional (era nullable) y ningún consumidor asume que
el run exista.

Y son idénticas a lo que la plataforma **ya** hace a propósito en
`guardrail_events.execution_id`, cuya razón está escrita en el modelo: _«UUIDs
kept as plain columns (no FK) so an event survives the referenced row being
deleted — an immutable audit record outlives the work it describes»_. Retirar
estas tres no inventa un patrón: extiende el que ya se eligió.

> **Una corrección al plan, de paso.** `part-01` afirma que «las columnas
> `execution_id` de `guardrail_events`, `notification_logs` y `llm_usage_events`
> no tienen FK a propósito». Es cierto en `guardrail_events` y **falso en las
> otras dos: no tienen esa columna**. Las únicas tablas con `execution_id` /
> `source_execution_id` en el esquema son `guardrail_events`, `approval_requests`,
> `memory_entries`, `eval_dataset_items` y `eval_shadow_records`.

## Alternativa descartada, y por qué

**(a) FK compuesta** conserva la semántica exacta y sería la respuesta correcta si
alguno de los cinco casos tuviera un borrado real que propagar. Se descarta por
tres costes concretos, no por pereza:

1. **Una columna nueva en cinco tablas hijas**, con backfill y `NOT NULL` en dos
   de ellas. En `approval_requests` y `notification_log_reads` el backfill es un
   `UPDATE` correlacionado sobre la tabla particionada entera.
2. **Todo el código que inserta en esas hijas tiene que aprender el nuevo
   valor.** `approval_repo.py`, el marcador de leído del inbox, el promotor de
   dataset y el memorizer pasan a necesitar el `created_at` del padre, un dato que
   hoy no manejan. Es la clase de cambio que se cuela: un `INSERT` olvidado
   revienta en producción y no en la suite.
3. **Se paga en cada escritura para siempre** por una integridad cuyo evento
   disparador se ha decidido que no ocurre.

La asimetría decide: si la decisión (b) resulta equivocada, el síntoma es una
fila huérfana en una tabla auxiliar y la vuelta atrás es una migración; si (a)
resulta equivocada, el síntoma es una columna redundante en cinco tablas y código
de escritura acoplado a la clave de partición del padre.

## Consecuencias

- Las cinco FK desaparecen del esquema y de los modelos ORM. La columna se queda.
- El `downgrade` de cada migración de `part-01` **restaura su FK**: volver a la
  tabla plana devuelve la integridad referencial tal como estaba. Un `downgrade`
  que dejara la FK retirada no sería una vuelta atrás.
- Los índices de esas columnas (`ix_approval_requests_execution_id`,
  `ix_notification_log_reads_user_log`, …) **no se tocan**: los usan las consultas,
  no la FK.
- Este ADR **caduca** si aparece un borrado de `executions` o de
  `notification_logs` que no venga de borrar su padre. El test citado en el punto
  2 es el que lo detecta.
