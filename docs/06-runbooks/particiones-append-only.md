---
title: Particiones de las tablas append-only
docs_language: es
audience: operador, system admin
updated: 2026-08-10
---

# Runbook — Particiones de las tablas append-only

Cinco tablas del sistema están **particionadas por mes** sobre `created_at`
([ADR 0151](../05-architecture-decisions/0151-retencion-de-tablas-append-only.md),
plan `part-01`):

| Tabla               | Migración | Qué guarda                                |
| ------------------- | --------- | ----------------------------------------- |
| `guardrail_events`  | `0131`    | Cada disparo de guardrail                 |
| `notification_logs` | `0134`    | Cada notificación enviada + su contenido  |
| `llm_usage_events`  | `0135`    | Consumo LLM del asistente / córtex / chat |
| `audit_log`         | `0136`    | Quién hizo qué en el panel                |
| `executions`        | `0137`    | Cada run del bucle de agentes (la pesada) |

**No se borra nada.** El particionado no es una política de retención: es lo que
hace que la tabla no sea un solo montón. Los datos viejos siguen ahí, en la
partición de su mes.

## Lo único que puede convertir esto en un incidente

Una tabla `PARTITION BY RANGE` **rechaza** una fila cuya fecha no cae en ninguna
partición:

```
no partition of relation "executions" found for row
```

Es decir: si nadie crea la partición del mes que viene, **la primera escritura
del día 1 falla**, y con ella el run que la produjo. De eso se encarga el job
`workers.ensure_partitions` (beat diario, 03:40 UTC), que mantiene el mes en
curso **más tres de colchón** y avisa si no puede.

> **No hay partición `DEFAULT`, y es a propósito.** Una `DEFAULT` capturaría esas
> filas y evitaría el error… convirtiéndolo en algo peor: las filas quedan en el
> cajón de sastre y después **impiden crear la partición correcta** (PostgreSQL
> escanea la `DEFAULT` al hacer `ATTACH` y rechaza el enganche si alguna fila
> pertenecería a la nueva). Un fallo ruidoso que se arregla en un minuto es mejor
> que uno silencioso que hay que desenredar a mano. Lo que sustituye a la
> `DEFAULT` es el colchón + la alerta.

## 1. Cómo se ve una tabla particionada

```bash
docker compose exec postgres psql -U postgres -d agentic_platform
```

```
\d+ executions
```

Arriba pone `Partitioned table "public.executions"` y `Partition key: RANGE
(created_at)`; abajo, la lista de particiones con su rango. Una tabla normal
diría `Table` a secas.

Para verlo de golpe en las cinco, sin `\d+` una por una:

```sql
SELECT parent.relname AS tabla,
       count(*)       AS particiones,
       min(child.relname) AS primera,
       max(child.relname) AS ultima
  FROM pg_inherits i
  JOIN pg_class parent ON parent.oid = i.inhparent
  JOIN pg_class child  ON child.oid  = i.inhrelid
 WHERE parent.relnamespace = 'public'::regnamespace
 GROUP BY 1 ORDER BY 1;
```

Y para saber en qué partición cayó una fila concreta:

```sql
SELECT id, created_at, tableoid::regclass AS particion
  FROM executions WHERE id = '…';
```

## 2. Ha llegado la alerta `PartitionCoverageMissing`

Es un `infra_alert` platform-scoped, severidad `critical`, que llega al System
Admin por in-app y Telegram. Dice qué tabla y qué partición faltan. Significa
que **el job corrió y no consiguió crear la partición del mes que viene**.

### 2.1 Comprobar el hueco

```sql
-- ¿Qué particiones existen para el mes que viene?
SELECT child.relname
  FROM pg_inherits i
  JOIN pg_class parent ON parent.oid = i.inhparent
  JOIN pg_class child  ON child.oid  = i.inhrelid
 WHERE parent.relname = 'executions'
 ORDER BY 1;
```

### 2.2 Averiguar por qué falló el job

```bash
docker compose logs workers-backup --since 24h | grep ensure_partitions
```

Las tres causas por orden de frecuencia:

1. **El DSN admin no vale.** El job conecta con `backup_database_url`
   (`migrations_user`, dueño del esquema) porque `service_user` es BYPASSRLS
   **pero sin DDL**: no puede crear una tabla. Si la contraseña rotó y el
   `.env` no, el log lo dice como error de autenticación.
2. **El worker no corrió.** La entrada de beat va a la cola `privileged`, la
   única cuyo pool (`workers-backup`) tiene ese DSN. Si ese contenedor está
   caído, el job no se ejecuta —y entonces **tampoco hay alerta**, porque nadie
   la publica. Ver [health-check.md](./health-check.md).
3. **Un lock.** Otra migración o un `VACUUM FULL` tenía la tabla tomada. Se
   resuelve solo en la pasada siguiente; si urge, crear la partición a mano
   (§ 3).

### 2.3 Verificar que la siguiente pasada la arregla

```bash
docker compose exec workers-backup \
  celery -A workers.celery_app call workers.ensure_partitions
```

Es idempotente: si ya está todo, no crea nada y no alerta.

## 3. Crear una partición a mano

Solo si el job no puede y el mes se echa encima. **Las tres sentencias van
juntas**: una partición creada sin su RLS es una puerta lateral al aislamiento
entre tenants (al consultar directamente la partición no se aplica la policy del
padre, solo la suya).

```sql
BEGIN;

CREATE TABLE executions_2026_12
    PARTITION OF executions
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

ALTER TABLE executions_2026_12 ENABLE ROW LEVEL SECURITY;
ALTER TABLE executions_2026_12 FORCE ROW LEVEL SECURITY;
CREATE POLICY executions_2026_12_tenant_isolation ON executions_2026_12 FOR ALL
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

COMMIT;
```

Reglas al copiar esto:

- **El nombre es `<tabla>_<AAAA>_<MM>`** con el cero a la izquierda. El job busca
  por ese nombre exacto; con otro, creerá que falta y lo intentará de nuevo.
- **El rango es medio-abierto**: `FROM` el día 1 del mes, `TO` el día 1 del
  siguiente. Sin solapes ni huecos.
- **Los índices NO se repiten.** PostgreSQL crea en cada partición el equivalente
  de cada índice del padre. Declararlos aquí duplicaría trabajo y espacio.
- Para `notification_logs` basta también con la policy canónica de arriba: la
  segunda policy del padre (`notification_logs_platform_read`) es solo del padre,
  por donde van todas las lecturas de la aplicación.

Comprobación:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class WHERE relname = 'executions_2026_12';
SELECT policyname FROM pg_policies WHERE tablename = 'executions_2026_12';
```

## 4. Convertir la tabla grande (solo al aplicar la migración `0137`)

`executions` es la tabla pesada —el 76 % de su tamaño es `steps_log`, medido en
el ADR 0151— y la migración **la copia entera** dentro de una transacción.

**Mide antes de la ventana de mantenimiento:**

```sql
SELECT pg_size_pretty(pg_total_relation_size('executions')) AS total,
       (SELECT count(*) FROM executions)                    AS filas;
```

Y ten en cuenta lo que NO se puede evitar: el `ALTER TABLE … RENAME` de la
migración toma un `ACCESS EXCLUSIVE` sobre la tabla **durante toda la
migración**. Trocear la copia por lotes no acortaría la ventana, solo repartiría
el mismo trabajo en más viajes. Por eso el procedimiento es el de siempre:

1. **Backup previo** (`docs/06-runbooks/dr-manual-backup.md`).
2. **Parar el stack de aplicación** salvo `postgres` — el paso de migraciones del
   [runbook de upgrade](./03-system-upgrade.md) ya lo hace.
3. Aplicar el esquema con el servicio one-shot de migraciones.
4. Verificar con el `\d+` del § 1 antes de levantar nada.

Si la migración aborta con
`la copia … se dejó filas por el camino: N en origen, M en destino`, **no ha
dejado nada a medias**: la transacción se deshace entera. Es la guarda que impide
terminar en verde con la tabla nueva incompleta. Mira el log de PostgreSQL antes
de reintentar; el sospechoso habitual es que el rol de migraciones haya perdido
su `BYPASSRLS`.

## 5. Volver atrás

Cada una de las cinco migraciones tiene un `downgrade` **real** —tabla plana,
copia verificada, intercambio, índices, RLS— y restaura además las claves
foráneas que la conversión retiró
([ADR 0154](../05-architecture-decisions/0154-fk-hacia-tablas-particionadas.md)).
Se prueba en cada ola con datos dentro (`tests/integration/test_partition_*.py`).

```bash
# Dentro del contenedor de migraciones, con el stack de aplicación parado
alembic downgrade 0136_partition_audit_log   # deshace solo executions
```

**Lo que el downgrade NO devuelve**: las `approval_requests` que hubieran quedado
apuntando a un run inexistente se **borran** antes de recrear su clave foránea
(es `NOT NULL`, no admite otra cosa), y las tres referencias `source_execution_id`
huérfanas se ponen a `NULL`. Sin eso, la constraint no se puede crear y la vuelta
atrás quedaría a medias. En una instalación sana no hay ninguna de esas filas.

## 6. Qué NO hacer

- ❌ **Crear una partición `DEFAULT`.** Ver el aviso del principio.
- ❌ **`DROP TABLE` de una partición para «liberar espacio».** El ADR 0151 decidió
  que no se borra nada; y una partición tirada se lleva por delante datos que
  ningún backup incremental recuperará selectivamente.
- ❌ **Crear la partición sin su RLS.** Es la única forma de que este cambio
  introduzca una fuga entre tenants. Si tienes que hacerla a mano, copia el bloque
  entero del § 3.
- ❌ **Añadir una tabla al particionado sin registrarla** en `PARTITIONED_TABLES`
  (`apps/workers/src/workers/maintenance/partitions.py`). Nadie crearía su
  partición del mes siguiente. Hay un test que lo impide
  (`tests/unit/test_partition_planner.py::test_every_partitioned_model_is_in_the_job_registry`),
  pero conviene saber por qué existe.
