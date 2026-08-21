---
title: "Una tabla particionada es `relkind = 'p'`: la introspección que filtra por `'r'` la declara desprotegida"
area: db, tests, security
encountered: 2026-08-01
stack: PostgreSQL 16, asyncpg, particionado por rango (ADR 0151)
---

## Síntoma

Justo después de convertir una tabla a particionada (part-01 / ADR 0151), el
invariante de cobertura RLS se pone rojo acusando a esa tabla de no tener RLS…
que sí tiene:

```
AssertionError: tablas con tenant_id y RLS incompleta.
  Ofensores: ['guardrail_events: sin ENABLE ROW LEVEL SECURITY']
```

Y `psql` dice lo contrario:

```
\d+ guardrail_events
  Partitioned table "public.guardrail_events"
  ...
  Policies (forced row security enabled):
      POLICY "guardrail_events_tenant_isolation" ...
```

Variante del mismo día, en un test escrito a mano:

```
AssertionError: guardrail_events no es una tabla particionada (relkind=b'p')
assert b'p' == 'p'
```

## Causa raíz

Son dos trampas encadenadas, y las dos viven en la introspección del catálogo:

1. **`pg_class.relkind` de una tabla particionada es `'p'`, no `'r'`.** Una
   consulta que filtra `WHERE relkind = 'r'` (el filtro natural para «tablas de
   verdad», y el que tenía `tests/integration/test_rls_invariant.py`) **no
   devuelve fila** para el padre. Pero el padre **sí** aparece en
   `information_schema.tables` como `BASE TABLE`. Resultado: el descubrimiento la
   ve, la búsqueda de flags no, y el `dict.get(tabla, (False, False))` la reporta
   como si tuviera la RLS apagada. Es un **falso positivo**, y de los caros: lo
   que pide a gritos el mensaje de error es añadir la tabla a una allowlist de
   exenciones, o sea eximir de la RLS justo a la tabla que sí la tiene.

   Las **particiones** sí son `'r'`, así que entran por el camino normal — y eso
   es lo correcto, porque cada partición necesita su propia policy (una consulta
   directa contra `guardrail_events_2026_09` no pasa por la policy del padre).

2. **asyncpg devuelve el tipo `"char"` de PostgreSQL como `bytes`.** `relkind`
   es de ese tipo, así que `await conn.fetchval("SELECT relkind …")` da `b'p'` y
   `b'p' == 'p'` es `False`. Un test que compara en crudo falla diciendo
   exactamente lo contrario de lo que pasa.

## Fix

Para la introspección de flags, admitir los dos `relkind`:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class
 WHERE relnamespace = 'public'::regnamespace
   AND relkind IN ('r', 'p')   -- 'p' = particionada; sin esto el PADRE sale sin RLS
```

Para comparar `relkind` desde asyncpg, normalizar antes:

```python
value = await conn.fetchval("SELECT relkind FROM pg_class WHERE relname = $1", table)
relkind = value.decode() if isinstance(value, bytes) else str(value)
```

Y dos comprobaciones que conviene arrastrar a cada conversión de las que quedan
(`notification_logs`, `llm_usage_events`, `audit_log`, `executions`):

- listar las particiones con `pg_inherits`, no adivinándolas por nombre:

  ```sql
  SELECT child.relname FROM pg_inherits
    JOIN pg_class child  ON child.oid  = pg_inherits.inhrelid
    JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
   WHERE parent.relname = $1 AND parent.relnamespace = 'public'::regnamespace
  ```

- y comprobar el aislamiento **leyendo directamente una partición** con una
  sesión `app_user`, no solo por el padre. Es la diferencia entre «la RLS está
  declarada» y «la RLS aísla»: con la policy solo en el padre, la lectura por el
  padre pasa el test y la lectura directa de la partición devuelve las filas de
  todos los tenants. Verificado rompiendo la migración a propósito el 2026-08-01.

Dónde está aplicado: `tests/integration/test_rls_invariant.py` (`_introspect`) y
`tests/integration/test_partition_guardrail_events.py` (`_relkind`).
