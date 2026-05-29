# Buenas prácticas con PostgreSQL

Guía práctica de diseño y operación de PostgreSQL: esquema, índices, JSONB,
row-level security, conexiones, vacuum y migraciones reversibles. Agnóstica de
lenguaje; aplicable a cualquier servicio que use Postgres.

## Diseño de esquema

- Usa el tipo correcto: `timestamptz` (no `timestamp`) para tiempos, `numeric`
  para dinero (nunca `float`), `uuid` para identificadores opacos, `text` en
  lugar de `varchar(n)` salvo que el límite sea una regla de negocio real.
- Declara `NOT NULL` por defecto; la nulabilidad es la excepción, no la norma.
- Pon claves foráneas con `ON DELETE` explícito (`RESTRICT`, `CASCADE`,
  `SET NULL`) según la semántica deseada.
- Usa `CHECK` para invariantes simples y `UNIQUE` para unicidad de negocio.
- Normaliza primero; desnormaliza sólo con una razón de rendimiento medida.

## Claves primarias

- `uuid` (v4 o v7) para identificadores expuestos y sistemas distribuidos;
  evitas enumeración y colisiones entre tenants.
- Para tablas internas de alto volumen, `bigint` con `GENERATED ALWAYS AS
IDENTITY` es más compacto y mantiene localidad de índice.
- No reutilices claves naturales mutables como PK.

## Índices

- Indexa las columnas que aparecen en `WHERE`, `JOIN` y `ORDER BY`.
- Los **índices compuestos** importan por orden: pon primero la columna de
  igualdad más selectiva. Un índice `(tenant_id, status, created_at)` sirve
  para filtros por `tenant_id`, por `tenant_id + status`, etc.
- Índices **parciales** para subconjuntos consultados (`WHERE deleted_at IS
NULL`).
- Índices de **expresión** para búsquedas sobre funciones (`lower(email)`).
- `GIN` para JSONB y full-text; `GiST`/`ivfflat`/`hnsw` para vectores
  (pgvector).
- No sobre-indexes: cada índice ralentiza escrituras y ocupa espacio. Mide con
  `pg_stat_user_indexes` y elimina los que no se usan.
- Crea índices en producción con `CREATE INDEX CONCURRENTLY` para no bloquear.

## EXPLAIN y rendimiento

- Diagnostica con `EXPLAIN (ANALYZE, BUFFERS)`. Busca `Seq Scan` sobre tablas
  grandes, estimaciones de filas muy desviadas y nested loops costosos.
- Mantén estadísticas frescas (`ANALYZE`); aumenta `default_statistics_target`
  en columnas con distribución sesgada.
- Evita `SELECT *`; pide sólo las columnas necesarias.
- Pagina con keyset (`WHERE (created_at, id) < ($1, $2) ORDER BY ... LIMIT n`)
  en vez de `OFFSET` grande.

## JSONB

- Usa `jsonb` (no `json`) para datos semiestructurados; soporta índices y
  operadores.
- Indexa con `GIN` (`USING gin (data jsonb_path_ops)`) si filtras por
  contenido con `@>`.
- No metas en JSONB lo que es claramente relacional y se consulta/junta a
  menudo: pierdes integridad referencial y planificación.
- Consulta con `->`, `->>`, `#>>`, `@>` y `jsonb_path_query`.

## Row-Level Security (RLS) y multi-tenancy

RLS es la red de seguridad para aislamiento por tenant:

```sql
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON projects
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

- La aplicación fija el tenant por sesión:
  `SET LOCAL app.tenant_id = '...'` dentro de la transacción del request.
- `FORCE ROW LEVEL SECURITY` aplica la política también al dueño de la tabla.
- RLS es **defensa en profundidad**, no sustituye filtrar por `tenant_id` en la
  query: usa ambos.
- Ten cuidado con roles `BYPASSRLS` (migraciones, superusuario).

## Transacciones y concurrencia

- Mantén las transacciones cortas; no hagas I/O externo dentro de una.
- Conoce los niveles de aislamiento: `READ COMMITTED` (default) vs
  `REPEATABLE READ`/`SERIALIZABLE` cuando necesites consistencia fuerte.
- Para evitar lost updates usa `SELECT ... FOR UPDATE` o bloqueo optimista con
  una columna `version`.
- Ordena los locks de forma consistente para evitar deadlocks.

## Conexiones

- Postgres no escala con miles de conexiones: usa un **pooler** (PgBouncer en
  modo transaction, o el pool del driver) y dimensiónalo
  (`pool_size ≈ núcleos * 2..4`, no más).
- Cierra/devuelve conexiones siempre; fugas agotan el pool.
- En modo transaction de PgBouncer no uses sentencias preparadas con nombre ni
  estado de sesión persistente.

## VACUUM y autovacuum

- El MVCC deja tuplas muertas; `autovacuum` las recupera. **No lo desactives.**
- En tablas con mucho UPDATE/DELETE, afina por tabla
  (`autovacuum_vacuum_scale_factor`) para que pase más a menudo.
- Vigila el **bloat** y el **wraparound** de transaction id
  (`age(datfrozenxid)`); un wraparound sin atender para la escritura.
- `VACUUM ANALYZE` manual tras cargas masivas.

## Migraciones reversibles

- Cada migración debe poder revertirse (`down`/rollback real y probado).
- Cambios **online-safe**: añadir columna nullable es barato; añadir `NOT NULL`
  con default en tablas grandes puede reescribir la tabla (en versiones
  modernas el default constante es instantáneo, pero revísalo).
- Despliega cambios de esquema en pasos compatibles: primero añade, despliega
  código que tolera ambos estados, luego elimina (expand/contract).
- Crea índices con `CONCURRENTLY` fuera de transacción.
- No mezcles DDL y grandes DML en la misma migración bloqueante.

## Backups y operación

- Backups regulares (`pg_dump` lógico + base físico/WAL para PITR) y **prueba
  la restauración**, no sólo el backup.
- Monitoriza conexiones, locks, queries lentas (`pg_stat_statements`),
  replicación y espacio en disco.
