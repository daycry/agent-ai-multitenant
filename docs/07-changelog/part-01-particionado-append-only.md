---
plan_id: part-01-particionado-append-only
title: Particionado nativo por mes de las cinco tablas append-only (ADR 0151)
completed_at: null
status: pending_human_validation
docs_language: es
---

# Plan part-01 — Particionado nativo por rango de las cinco tablas append-only

## Resumen

El [ADR 0151](../05-architecture-decisions/0151-retencion-de-tablas-append-only.md)
se firmó el 2026-08-01 con la **opción C para las cinco tablas**: `guardrail_events`,
`notification_logs`, `llm_usage_events`, `audit_log` y `executions` pasan a ser
tablas `PARTITION BY RANGE (created_at)` con una partición por mes natural.
**No se borra nada**: se compró la única opción que no obliga a acertar un plazo
de retención.

Las cinco conversiones están hechas, una por migración, con la suite verde entre
cada una. Falta lo que ninguna suite puede hacer: los tres tests humanos del plan
(§ abajo), y el despliegue.

## Lo que cambia en el esquema

| Migración | Tabla               | Además de la conversión                                                       |
| --------- | ------------------- | ----------------------------------------------------------------------------- |
| `0131`    | `guardrail_events`  | El patrón entero + el job `workers.ensure_partitions` con su alerta           |
| `0134`    | `notification_logs` | Retira `notification_log_reads.log_id` (FK CASCADE); conserva las 2 policies  |
| `0135`    | `llm_usage_events`  | `created_at` pasa a `NOT NULL` (era nullable) con relleno de las filas viejas |
| `0136`    | `audit_log`         | Conserva la policy sin `WITH CHECK` y la FK saliente a `users`                |
| `0137`    | `executions`        | Retira las **cuatro** FK entrantes (ADR 0154); 31 columnas, la tabla pesada   |

En las cinco, la clave primaria pasa de `id` a `(id, created_at)`: PostgreSQL
exige que la PK de una tabla particionada incluya la clave de partición.

## Las decisiones que hubo que tomar

### ADR 0154 — las FK entrantes se retiran, las cinco

Es el trabajo real del plan, y lo que hace que `executions` fuera la última. Una
FK no puede referenciar una PK compuesta sin llevar las dos columnas; las salidas
eran ensanchar cinco tablas hijas (y todo el código que escribe en ellas) o
retirar la constraint. Se retiró, **porque en las cinco había otro camino que
hacía el mismo trabajo**, y el ADR lo escribe caso a caso. La difícil,
`approval_requests.execution_id` (CASCADE + NOT NULL), se apoya en que el único
evento que borra una `execution` es el borrado de su `task`, que ya cascadea a
`approval_requests` por su propio `task_id`. Eso no se dejó escrito y ya: lo
ejecuta `test_deleting_a_task_still_removes_its_approval_requests`, que se pondrá
rojo el día que deje de ser cierto.

### Dos cosas que el plan daba por sabidas y no eran verdad

- **Las FK entrantes eran cinco, no cuatro.** `notification_log_reads.log_id`
  apunta a `notification_logs.id` con `ON DELETE CASCADE` y `NOT NULL`, y el plan
  no la había contado — además entra en la fase 2, no en la 5, así que la decisión
  de FK hubo que tomarla **antes** de lo que el plan preveía.
- **`notification_logs` y `llm_usage_events` no tienen columna `execution_id`.**
  El plan afirmaba que la tenían «sin FK a propósito». Solo `guardrail_events` la
  tiene.

### La pregunta abierta sobre facturación, respondida con una medida

El plan pedía comprobar si las agregaciones de coste sobre `llm_usage_events`
mejoran o no, _«en vez de suponer que mejora todo por defecto»_. Respuesta con
`EXPLAIN`: el filtro `created_at > now() - 24h` descarta las particiones del
**pasado** —donde crece el historial— y **no** las tres del colchón futuro, porque
una fila con fecha futura satisfaría la condición. O sea: el plan toca como mucho
4 relaciones, constante, mientras el pasado crece sin límite. La primera versión
del test afirmaba «escanea una sola partición» y era falsa; se cambió la
afirmación, no el código.

## Una guarda nueva que la ola 1 no tenía

Las migraciones `0134`-`0137` comparan **los conjuntos de columnas** de origen y
destino antes de copiar, además del recuento de filas. El recuento no ve una
columna olvidada al escribir el cuerpo de la tabla a mano: cuadra igual y la
columna se pierde entera y en silencio. Verificado rompiéndolo a propósito
—quitando `subject`/`body` de la `0134`— y viendo el `RuntimeError`.

## Operación

- **Runbook nuevo**: [`06-runbooks/particiones-append-only.md`](../06-runbooks/particiones-append-only.md)
  — cómo se ve una tabla particionada, qué hacer ante la alerta
  `PartitionCoverageMissing`, cómo crear una partición a mano **con su RLS**, qué
  se paga al convertir `executions` y qué no devuelve el `downgrade`.
- **Referencia**: [`04-reference/domain-model.md`](../04-reference/domain-model.md)
  § «Tablas particionadas por mes».
- **El job**: `workers.ensure_partitions` (beat diario 03:40 UTC, cola
  `privileged`) ya conoce las cinco tablas. Un test unitario
  (`test_every_partitioned_model_is_in_the_job_registry`) descubre en el ORM qué
  tablas están particionadas y rompe la suite si alguna no está registrada — que
  es la forma de que un olvido salga en CI y no el día 1 del mes que viene.

## Cobertura

| Fichero                                                 | Qué cubre                                                     | Tests |
| ------------------------------------------------------- | ------------------------------------------------------------- | ----: |
| `tests/integration/test_partition_guardrail_events.py`  | El patrón entero, ola 1                                       |    11 |
| `tests/integration/test_partition_notification_logs.py` | + la FK retirada, las 2 policies, la fila de plataforma       |    15 |
| `tests/integration/test_partition_llm_usage_events.py`  | + `created_at` nullable, la poda medida con `EXPLAIN`         |    14 |
| `tests/integration/test_partition_audit_log.py`         | + round-trip campo a campo (incluido el JSONB de `changes`)   |    12 |
| `tests/integration/test_partition_executions.py`        | + las 4 FK, la cascada de `task_id`, `steps_log` en la vuelta |    16 |
| `tests/unit/test_partition_planner.py`                  | El núcleo puro del job + las dos guardas estructurales        |    16 |

El contrato común (forma, PK, colchón, RLS por partición, propagación de índices,
el job) vive en `tests/integration/_partition_contract.py`: cuatro copias del
mismo fichero habrían divergido, y la cuarta vez que alguien arreglara un
descubrimiento roto lo arreglaría en uno y dejaría tres mintiendo en verde.

## Tests humanos pendientes

Los tres del plan, ninguno automatizable:

- **`human_part01_01`** — el día 1 del mes siguiente al despliegue, disparar un
  guardrail real y comprobar que aterriza en la partición del mes en curso. Exige
  que pase un mes.
- **`human_part01_02`** — parar el job un mes (o borrar a mano la partición de
  M+1) y comprobar que la alerta `PartitionCoverageMissing` llega al System Admin
  por in-app y Telegram. Una alerta que nadie ha visto llegar no está probada.
- **`human_part01_03`** — correr el `downgrade` de una de las migraciones sobre
  una copia restaurada del bundle de backup, con datos de verdad. El round-trip
  automático lo hace con datos sintéticos.
