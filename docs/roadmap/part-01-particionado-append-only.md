---
plan_id: part-01-particionado-append-only
title: Particionado nativo por rango de las cinco tablas append-only (ADR 0151)
status: pending_human_validation
blocking_plan: []
started_at: 2026-08-01
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 8
estimated_cost_human_eur: 3.600 € – 4.800 €
estimated_cost_ai_eur: 25 € – 60 €
created_by: adr-0151
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan part-01 — Particionado nativo por rango de las cinco tablas append-only

## Cabecera

| Campo                              | Valor                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------------- |
| **ID del Plan**                    | `part-01-particionado-append-only`                                               |
| **Prioridad**                      | P1                                                                               |
| **Bloqueado por**                  | — (ninguno; nace de un ADR ya firmado)                                           |
| **Tiempo estimado (calendario)**   | 2-3 semanas                                                                      |
| **Tiempo estimado (persona-días)** | 8                                                                                |
| **Rama git sugerida**              | `plan/part-01-particionado-append-only`                                          |
| **Decisión que lo origina**        | [ADR 0151](../05-architecture-decisions/0151-retencion-de-tablas-append-only.md) |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`).

> **Por qué pasó de `approved` a `pending_human_validation` sin pisar
> `in_progress` (2026-08-10).** Las **diez** tareas están entregadas y verdes —las
> cinco conversiones, el job, el ADR de las FK, el runbook, la referencia y el
> changelog—, así que el estado que describe la realidad es «código hecho,
> esperando lo humano». `in_progress` no se llegó a usar porque CLAUDE.md prohíbe
> dos fases simultáneas y el hueco lo ocupaba
> [marketplace-v2-despliegue](./marketplace-v2-despliegue.md); marcarlo ahora sería
> retroceder para nada y rompería
> `tests/unit/test_roadmap_frontmatter.py::test_at_most_one_phase_in_progress`.
>
> **Lo que falta para `completed`** son los tres tests humanos del final de este
> fichero —uno de ellos exige literalmente que pase un mes— y el despliegue. Nada
> de eso lo puede firmar quien escribió el código.

---

## Resumen

El [ADR 0151](../05-architecture-decisions/0151-retencion-de-tablas-append-only.md)
se firmó el 2026-08-01 con la **opción C para las cinco tablas**: `guardrail_events`,
`notification_logs`, `llm_usage_events`, `audit_log` y `executions` pasan a
**tablas particionadas por rango mensual** sobre `created_at`. **No se borra
nada**: se compra la única opción que no obliga a acertar un plazo de retención.

La firma se apartó de la recomendación del propio ADR (un híbrido A+C por
familia) **a sabiendas del coste**, y se le ofreció explícitamente la alternativa
de particionar solo `executions` y `audit_log` —la mitad del riesgo— y eligió las
cinco. Este plan es la ejecución de esa decisión, y lo primero que hace es poner
por escrito lo que se paga.

## Lo que se paga, antes de empezar

Cuatro cosas, las cuatro nombradas en el ADR:

1. **La PK compuesta es lo caro, no el particionado.** PostgreSQL exige que la
   clave primaria de una tabla particionada **incluya la clave de partición**. En
   las cuatro primeras tablas eso es un cambio local (`id` → `(id, created_at)`,
   sin nadie que dependa de la unicidad de `id` solo). En `executions` no:
   `executions.id` es el destino de **cuatro claves foráneas**, y una FK no puede
   apuntar a una PK compuesta sin llevar las dos columnas. Ése es el trabajo real
   y por eso `executions` va la última (§ «Las cuatro FK», abajo).

2. **Convertir una tabla existente NO es un `ALTER`.** No hay
   `ALTER TABLE … PARTITION BY`. El patrón es: tabla nueva particionada → copia →
   intercambio → índices → RLS. Y el `downgrade` hace el camino inverso **de
   verdad** (tabla plana + copia + intercambio), no un `pass` con un comentario.

3. **Hace falta el job que cree la partición del mes siguiente.** Sin él, la
   primera inserción del mes que viene falla con
   `no partition of relation "…" found for row`. Es el modo de fallo que convierte
   esta decisión en un incidente, y por eso el job **entra en la primera ola**, no
   en la última: se entrega con la primera tabla convertida o no se entrega.

4. **La RLS y los índices se declaran por partición.** El modelo mental del
   operador se complica y el runbook tiene que recogerlo.

### Las dos guardas de la copia (y lo que NO es cierto sobre ellas)

La forma de perder datos aquí es que el `INSERT … SELECT` copie menos filas de las
que hay sin dar error. Hay un mecanismo capaz de producirlo: `FORCE ROW LEVEL
SECURITY` **se aplica también al dueño de la tabla**, así que un dueño sin
`app.tenant_id` fijado leería cero filas.

**Hoy ese mecanismo no dispara, y se escribe así en vez de adornarlo**:
`migrations_user` —el rol con el que corre Alembic— es `BYPASSRLS`
(`docker/postgres/init/02-roles.sh:22`), y `BYPASSRLS` gana a `FORCE`. Las guardas
se ponen igualmente, por dos razones distintas y las dos concretas: la primera
mantiene la migración correcta si el rol de migraciones deja de ser `BYPASSRLS`
—prod-14 ya movió los servicios a un rol con menos privilegios por ese criterio—;
la segunda no depende de ninguna teoría sobre la RLS y cubre igual un `INSERT`
truncado o una fila rechazada por falta de partición. Las migraciones de este plan:

- quitan el `FORCE` de la tabla origen **antes** de leerla (se va a destruir
  después, así que no se está debilitando nada duradero);
- crean la tabla destino **sin RLS**, copian, y activan RLS al final (mismo orden
  que la migración 0052 original: «RLS last so the table exists»);
- y **cuentan las filas a los dos lados y revientan si no cuadran**. Una copia
  incompleta es la única forma que tiene esta obra de perder datos, así que se
  convierte en un fallo ruidoso por construcción.

### El invariante de RLS y las tablas `relkind = 'p'`

`tests/integration/test_rls_invariant.py` descubre las tablas con `tenant_id` y
exige `ENABLE` + `FORCE` + policy. Su introspección miraba
`pg_class WHERE relkind = 'r'` (tablas normales). **Una tabla particionada es
`relkind = 'p'`**, así que el padre habría entrado por `information_schema.tables`
—donde sí aparece— y salido con `rls = False` del `pg_class`: un falso positivo que
habría puesto el invariante rojo sin que nada estuviera mal. La introspección pasa
a `relkind IN ('r', 'p')`, y las particiones (que sí son `'r'`) entran por el
camino normal — lo que obliga, correctamente, a que **cada partición lleve su
propia RLS**.

## Alcance

**Entra**: las cinco tablas del ADR, el job de particiones futuras con su alerta,
la RLS por partición, el `downgrade` probado y el runbook.

**No entra**:

- `task_audit_events`, la sexta familia append-only. El ADR nombra seis tablas en
  el contexto pero **la decisión firmada dice cinco**; particionarla también sería
  ampliar la decisión por nuestra cuenta.
- Cualquier **borrado** o compactación de `steps_log`. El ADR es explícito: «los
  plazos que este ADR proponía para la opción A quedan sin efecto: no hay plazo,
  no se borra».
- `DETACH` + movimiento de particiones antiguas a otro tablespace. Es la ganancia
  que el particionado **habilita**, no la que este plan entrega; sin un segundo
  tablespace declarado no hay dónde moverlas.

## Riesgos

| #   | Riesgo                                                                    | Mitigación                                                                                                                           |
| --- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | La copia se deja filas por el camino y nadie se entera                    | Recuento a los dos lados + `RuntimeError` si no cuadra, en cada migración de este plan (§ «Las dos guardas»)                         |
| 2   | El mes que viene falla la primera inserción                               | Job diario que crea M+1…M+3 **con 3 meses de colchón** + alerta `infra_alert` en cuanto M+1 falta tras una pasada                    |
| 3   | Una partición nace sin RLS y filtra entre tenants por acceso directo      | El job crea `ENABLE`+`FORCE`+policy en el mismo paso que la partición, y hay test que lo comprueba sobre una partición recién creada |
| 4   | El `downgrade` no se ha probado nunca y no funciona el día que hace falta | Test de ida y vuelta con datos dentro: `upgrade → downgrade → upgrade` conservando las filas                                         |
| 5   | Las cinco conversiones de golpe dejan el esquema a medias                 | Una tabla por ola, con la suite verde entre cada una (orden del ADR)                                                                 |

## Las cuatro FK que apuntan a `executions.id`

Enumeradas aquí porque son **el trabajo real** de la última ola, y descubrirlas
entonces sería descubrirlas tarde. Cada una necesita su decisión propia; ninguna
sobrevive tal cual a una PK compuesta.

| #   | Tabla / columna                           | Regla actual         | Declarada en                           | Qué hay que decidir                                                                                                                                                                       |
| --- | ----------------------------------------- | -------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `approval_requests.execution_id`          | `ON DELETE CASCADE`  | `db/domain.py:1386` · migración `0012` | Es `NOT NULL` y **cascadea**: o se le añade `execution_created_at` para una FK compuesta, o se sustituye la FK por un trigger/limpieza explícita. La cascada es la que más cuesta perder. |
| 2   | `memory_entries.source_execution_id`      | `ON DELETE SET NULL` | `db/memory.py:161` · migración `0020`  | Nullable: la opción barata es **retirar la FK** y dejar la columna como referencia suelta (que es lo que ya hacen `guardrail_events.execution_id` y compañía).                            |
| 3   | `eval_dataset_items.source_execution_id`  | `ON DELETE SET NULL` | `db/evals.py:262` · migración `0058`   | Igual que la 2.                                                                                                                                                                           |
| 4   | `eval_shadow_records.source_execution_id` | `ON DELETE SET NULL` | `db/evals.py:537` · migración `0059`   | Igual que la 2.                                                                                                                                                                           |

> **Corregido el 2026-08-10, al abrir el esquema: son CINCO, y este párrafo tenía
> dos errores.** (a) Falta en la tabla de arriba `notification_log_reads.log_id`
> → `notification_logs.id`, `ON DELETE CASCADE` y `NOT NULL`: la misma clase de
> problema que la nº 1, y entra en la **fase 2**, no en la 5 — así que la decisión
> del `task_part01_07` hubo que tomarla antes de lo previsto. (b) La frase de
> abajo era falsa para dos de las tres tablas que nombra. Las únicas tablas del
> esquema con columna `execution_id` / `source_execution_id` son
> `guardrail_events`, `approval_requests`, `memory_entries`, `eval_dataset_items`
> y `eval_shadow_records`. Verificado contra `pg_constraint` e
> `information_schema.columns` sobre un esquema en `head`, no contra los modelos.

Y lo que **no** es trabajo, para no buscarlo: la columna `execution_id` de
`guardrail_events` **no tiene FK** a propósito (está escrito en su modelo: «un
registro inmutable sobrevive a la fila que describe»). Ésa no se entera del cambio.

## Cómo se ejecuta: una tabla por ola

Orden de **riesgo creciente**, tal como lo fija el ADR. La regla entre olas es la
del ADR: **suite verde antes de empezar la siguiente**.

---

## Fase 1 — `guardrail_events`, la fácil, para probar el patrón entero

- [x] **`task_part01_01` — Convertir `guardrail_events` a tabla particionada por mes**:
      migración `0131`: tabla nueva `PARTITION BY RANGE (created_at)` con PK
      `(id, created_at)`, particiones mensuales que cubren los datos existentes más
      3 meses de colchón, copia con **recuento verificado**, intercambio, los cuatro
      índices en el padre (se propagan a las particiones) y RLS `ENABLE`+`FORCE`+policy
      **en el padre y en cada partición**. `downgrade` real (tabla plana + copia +
      intercambio). Modelo ORM con la PK compuesta y `postgresql_partition_by`.
  - Ficheros: `apps/api-server/migrations/versions/20260801_0131_partition_guardrail_events.py`,
    `apps/api-server/src/api_server/db/guardrail_event.py`
  - Tests: `tests/integration/test_partition_guardrail_events.py` (11 tests: forma
    particionada, PK compuesta, cobertura del colchón, RLS por partición, índices
    propagados, aislamiento cross-tenant **medido** leyendo la partición directamente,
    enrutado por el padre, rechazo de una fila sin partición, conversión de una tabla
    que ya tiene meses de datos, round-trip `downgrade`/`upgrade` con filas dentro, y
    las dos del job contra la base real)
  - ✅ **Cerrada (2026-08-01)**: entregada y verde. Rojo verificado rompiendo la
    migración a propósito dos veces: sin RLS por partición caen
    `test_every_partition_carries_its_own_rls` y `test_isolation_holds_for_a_real_app_user_session`
    (o sea, la partición sin policy **sí** filtra entre tenants — no era cargo cult);
    ignorando los meses de los datos viejos, la migración **revienta**, que es lo que
    habría pasado en producción.

- [x] **`task_part01_02` — El job que crea la partición del mes siguiente, con su alerta**:
      `workers.ensure_partitions` (beat diario 03:40 UTC). Núcleo puro y testeable
      (`required_partitions` / `missing_partitions` / `coverage_alert`) + cableado DDL.
      Crea M…M+3, aplica RLS a cada partición nueva, y si tras la pasada **M+1 sigue
      sin existir** publica un `infra_alert` platform-scoped (`PartitionCoverageMissing`,
      severidad `critical`). Conecta con el DSN admin (`backup_database_url`), porque
      `service_user` es **BYPASSRLS pero sin DDL** (prod-14 task_05).
  - Ficheros: `apps/workers/src/workers/maintenance/partitions.py`,
    `apps/workers/src/workers/maintenance/__init__.py`, `apps/workers/src/workers/beat_schedule.py`
  - Tests: `tests/unit/test_partition_planner.py` (16 tests del núcleo puro + la
    guarda de registro de la task), `tests/integration/test_partition_guardrail_events.py::test_ensure_partitions_*`
  - ✅ **Cerrada (2026-08-01)**: entregada y verde.

- [x] **`task_part01_03` — Que el invariante de RLS siga vigilando de verdad**:
      la introspección de `test_rls_invariant.py` pasa a `relkind IN ('r','p')` para
      que el **padre** particionado no salga como «sin RLS» por ser `'p'`, y las
      particiones entren por el camino normal sin excepciones ni allowlist.
  - Ficheros: `tests/integration/test_rls_invariant.py`,
    `docs/03-guides/gotchas/partitioned-table-introspection.md` (+ su fila en el índice)
  - Tests: los 7 del propio fichero, verdes con el esquema particionado.
  - ✅ **Cerrada (2026-08-01)**: entregada y verde. La trampa queda escrita en
    `gotchas/partitioned-table-introspection.md` porque va a repetirse en las cuatro
    olas que quedan, y su mensaje de error empuja al arreglo EQUIVOCADO (eximir de la
    RLS a la tabla que sí la tiene).

---

## Fase 2 — `notification_logs`

- [x] **`task_part01_04` — Convertir `notification_logs` a particionada por mes**:
      mismo patrón que la fase 1 (tabla nueva + copia verificada + intercambio + RLS
      por partición), `id` → `(id, created_at)`. Registrar la tabla en
      `PARTITIONED_TABLES` del job para que herede la cobertura futura y la alerta.
      Ojo: desde la migración `0113` esta tabla guarda **el contenido del mensaje**,
      así que es la que más PII por fila lleva — la RLS por partición no es opcional.
  - Ficheros: `apps/api-server/migrations/versions/20260810_0134_partition_notification_logs.py`,
    `apps/api-server/src/api_server/db/notification.py`,
    `apps/workers/src/workers/maintenance/partitions.py`
  - Tests: `tests/integration/test_partition_notification_logs.py` (15),
    `tests/integration/test_rls_invariant.py`
  - ✅ **Cerrada (2026-08-10)**: 15 tests verdes. **Apareció una FK entrante que este
    plan no había contado**: `notification_log_reads.log_id` → `notification_logs.id`,
    `ON DELETE CASCADE` y `NOT NULL`. O sea, la decisión de FK del `task_part01_07`
    hubo que tomarla aquí, en la fase 2, y no en la 5 — está en el ADR 0154. El padre
    conserva sus **dos** policies (`tenant_isolation` + `platform_read`); las
    particiones llevan solo la canónica, que es la que crea el job. Rojo verificado
    quitando `subject`/`body` del cuerpo de la migración: salta la guarda nueva de
    conjuntos de columnas con `RuntimeError: … Sobran en origen: ['body', 'subject']`.

---

## Fase 3 — `llm_usage_events`

- [x] **`task_part01_05` — Convertir `llm_usage_events` a particionada por mes**:
      mismo patrón. Comprobar antes las consultas de facturación (ADR 0116): si alguna
      agrega **sin filtrar por `created_at`**, el particionado no la mejora y hay que
      decir por qué se acepta, en vez de suponer que mejora todo por defecto.
  - Ficheros: `apps/api-server/migrations/versions/20260810_0135_partition_llm_usage_events.py`,
    `apps/api-server/src/api_server/db/llm_usage.py`,
    `apps/workers/src/workers/maintenance/partitions.py`
  - Tests: `tests/integration/test_partition_llm_usage_events.py` (14)
  - ✅ **Cerrada (2026-08-10)**: 14 tests verdes. Dos hallazgos. (a) `created_at`
    era **NULLABLE** en la BD (la 0109 la creó sin `nullable=False` aunque el ORM sí
    lo declara), y una fila sin fecha no cabe en ninguna partición: la migración la
    rellena con `COALESCE(updated_at, now())` en vez de reventar a mitad del
    despliegue, y hay test que lo prueba. (b) Las dos agregaciones de coste
    (`queue_sampler`) **sí filtran** por `created_at`, y la ganancia medida con
    `EXPLAIN` es más matizada que «poda»: descarta las particiones del PASADO y no
    las tres del colchón futuro. La primera versión del test afirmaba «escanea una
    sola partición», el `EXPLAIN` dijo cuatro, y se corrigió la afirmación — no el
    código.

---

## Fase 4 — `audit_log`

- [x] **`task_part01_06` — Convertir `audit_log` a particionada por mes**: mismo
      patrón. Es la tabla de la que el ADR dice que puede ser «la única prueba de
      quién aprobó un despliegue», así que aquí el `downgrade` probado importa más
      que en ninguna: una conversión que no sabe volver atrás sobre la tabla de
      auditoría es un riesgo de cumplimiento, no de rendimiento.
  - Ficheros: `apps/api-server/migrations/versions/20260810_0136_partition_audit_log.py`,
    `apps/api-server/src/api_server/db/models.py` (`AuditLog`),
    `apps/workers/src/workers/maintenance/partitions.py`
  - Tests: `tests/integration/test_partition_audit_log.py` (12)
  - ✅ **Cerrada (2026-08-10)**: 12 tests verdes. El round-trip de esta tabla no
    compara recuentos sino **campo a campo**, incluido el JSONB de `changes`:
    conservar el número de filas y perder el contenido del cambio no es haber
    conservado la prueba de nada. Se reproducen literales dos cosas de la migración
    0001 en vez de «mejorarlas de paso»: la policy del padre **sin `WITH CHECK`** (el
    comportamiento es idéntico, pero un esquema migrado distinto de uno nuevo es una
    diferencia que nadie sabrá explicar dentro de un año) y la FK saliente
    `user_id → users ON DELETE SET NULL`.

---

## Fase 5 — `executions`, la de las FK

- [x] **`task_part01_07` — Decidir las cuatro FK, con ADR si hace falta**: para cada
      una de las cuatro de la tabla de arriba, elegir entre (a) FK compuesta llevando
      `execution_created_at` a la tabla hija, o (b) retirar la FK y dejar la referencia
      suelta. La 1 (`approval_requests`, `NOT NULL` + `CASCADE`) no admite (b) sin
      sustituir la cascada por algo: si la decisión es retirarla, **hay que escribir
      quién borra esas filas**. Si la decisión no es unánime para las cuatro, va en ADR.
  - Ficheros: [`docs/05-architecture-decisions/0154-fk-hacia-tablas-particionadas.md`](../05-architecture-decisions/0154-fk-hacia-tablas-particionadas.md)
  - Tests: la condición de validez de la decisión SÍ tiene test —
    `test_partition_executions.py::test_deleting_a_task_still_removes_its_approval_requests`
  - ✅ **Cerrada (2026-08-10)**: **ADR 0154, `accepted`**. Se retiran las **cinco**
    (no cuatro: la de `notification_logs` apareció en la fase 2), opción (b), y el ADR
    escribe caso a caso quién borra ahora esas filas. La difícil,
    `approval_requests.execution_id`: su cascada era **redundante**, porque el único
    evento que borra una `execution` es el borrado de su `task` —`executions.task_id`
    es CASCADE— y ese mismo evento ya se lleva la `approval_request` por su propio
    `task_id`, también CASCADE. Verificado además que **nada borra `executions`
    directamente** en `apps/`. No se decidió «a ojo»: esa condición es un test con
    nombre propio, citado en el ADR, que se pondrá rojo el día que deje de valer.
    Se descartó (a) porque obliga a una columna nueva en cinco tablas hijas y a que
    todo el código que escribe en ellas aprenda el `created_at` del padre — con
    `approval_repo.py` entre medias.

- [x] **`task_part01_08` — Convertir `executions` a particionada por mes**: el mismo
      patrón, con la PK `(id, created_at)` y las cuatro FK ya decididas. Es la tabla
      grande —el 76 % de su tamaño es `steps_log` según la medida del ADR—, así que la
      copia hay que hacerla por lotes o asumir el bloqueo y decirlo en el runbook.
  - Ficheros: `apps/api-server/migrations/versions/20260810_0137_partition_executions.py`,
    `apps/api-server/src/api_server/db/domain.py` (`Execution` + `ApprovalRequest`),
    `apps/api-server/src/api_server/db/memory.py`, `apps/api-server/src/api_server/db/evals.py`,
    `apps/workers/src/workers/maintenance/partitions.py`
  - Tests: `tests/integration/test_partition_executions.py` (16) +
    `tests/integration/test_rls_invariant.py`
  - ✅ **Cerrada (2026-08-10)**: 16 tests verdes. **La copia NO se trocea, y la razón
    está escrita**: el `ALTER TABLE … RENAME` ya toma un `ACCESS EXCLUSIVE` durante
    toda la migración, así que trocear el `INSERT … SELECT` no acorta la ventana de
    bloqueo ni un segundo — solo reparte el mismo trabajo en más viajes. Lo que sí hay
    es medida previa y ventana de mantenimiento, en el runbook § «Convertir la tabla
    grande». Las 31 columnas van a mano, y por eso corre antes la guarda de conjuntos
    de columnas. Rojo verificado quitando la RLS de las particiones: caen
    `test_every_partition_carries_its_own_rls` y
    `test_isolation_holds_for_a_real_app_user_session` — o sea, la partición sin
    policy **sí** filtra entre tenants, igual que en la ola 1.

---

## Fase 6 — Cierre

- [x] **`task_part01_09` — Runbook del operador**: cómo se ve una tabla particionada
      (`\d+`), qué hacer si llega la alerta `PartitionCoverageMissing`, cómo crear una
      partición a mano, y por qué **no hay partición `DEFAULT`** (§ decisión de diseño
      en el docstring de `partitions.py`: una `DEFAULT` convierte un fallo ruidoso e
      inmediato en filas mal colocadas que después **impiden** crear la partición
      correcta).
  - Ficheros: [`docs/06-runbooks/particiones-append-only.md`](../06-runbooks/particiones-append-only.md)
    (+ su fila en `06-runbooks/README.md`)
  - Tests: `tests/docs/test_runbooks_consistency.py`, `tests/docs/test_docs_internal_links.py`
  - ✅ **Cerrada (2026-08-10)**: seis secciones, todas con el comando que hay que
    teclear. Incluye lo que el plan no pedía y hace falta igual: las tres causas del
    fallo del job por orden de frecuencia (DSN admin, `workers-backup` caído — que
    además significa **que tampoco hay alerta**, porque nadie la publica—, y un lock),
    qué se paga al convertir `executions`, y **qué NO devuelve el `downgrade`** (borra
    las `approval_requests` huérfanas y pone a NULL las tres referencias sueltas antes
    de recrear las FK; sin eso la constraint no se puede crear).

- [x] **`task_part01_10` — Referencia y changelog**: `docs/04-reference/` con las
      cinco tablas marcadas como particionadas y su clave, y
      `docs/07-changelog/part-01-particionado-append-only.md`.
  - Ficheros: `docs/04-reference/domain-model.md` (§ «Tablas particionadas por mes»),
    `docs/07-changelog/part-01-particionado-append-only.md`
  - Tests: `tests/unit/test_roadmap_frontmatter.py`, `tests/unit/test_docs_governance.py`
  - ✅ **Cerrada (2026-08-10)**: la referencia lista las cinco con su clave de
    partición, su PK y su migración, y añade las tres consecuencias que hay que
    conocer antes de tocarlas (PK compuesta, **ninguna admite ya FK entrante**, RLS por
    partición). El changelog cuenta también lo que este plan daba por sabido y no era
    cierto — la quinta FK y el `execution_id` que dos de las tablas no tienen.

---

## Tests humanos del plan

A nivel de plan, no de tarea (principio 7 de CLAUDE.md):

- **`human_part01_01`** — El día 1 del mes siguiente al despliegue, insertar un
  evento de guardrail real (disparar un guardrail desde un run) y comprobar que
  aterriza en la partición del mes en curso. Es la prueba de que el job hizo su
  trabajo; ninguna suite puede correrla, porque exige que pase un mes.
- **`human_part01_02`** — Parar el job un mes (o borrar a mano la partición de M+1)
  y comprobar que la alerta `PartitionCoverageMissing` llega al System Admin por
  in-app y Telegram. Una alerta que nadie ha visto llegar no está probada.
- **`human_part01_03`** — Correr el `downgrade` de una de las migraciones sobre una
  copia restaurada del bundle de backup, con datos de verdad, y comprobar que las
  filas siguen ahí. El test de ida y vuelta lo hace con datos sintéticos; esto lo
  hace con los reales.
