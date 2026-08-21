---
title: "Un test que siembra una fila de hace 100 días muere con «no partition of relation found for row»"
area: tests, postgresql, particionado
encountered: 2026-08-12
stack: PostgreSQL 16, asyncpg, pytest
---

## Síntoma

Un test que llevaba meses en verde empieza a fallar, y no en una aserción sino en
el INSERT de su propia siembra:

```
E   asyncpg.exceptions.CheckViolationError: no partition of relation "executions"
    found for row
E   DETAIL:  Partition key of the failing row contains (created_at) = (2026-05-04 12:27:46+00).
```

Nada en el mensaje menciona el test, ni la migración que lo rompió, ni el
particionado como decisión. El test no cambió: cambió la tabla debajo.

## Causa raíz

Las cinco tablas append-only del **ADR 0151** (`guardrail_events`,
`notification_logs`, `llm_usage_events`, `audit_log`, `executions`) pasaron a
particionado nativo por mes, y **a propósito no tienen partición `DEFAULT`**
(razonado en `apps/workers/src/workers/maintenance/partitions.py`: una `DEFAULT`
se traga las filas de meses sin partición y luego impide enganchar la partición
buena, convirtiendo un fallo ruidoso en corrupción silenciosa del plan).

Sobre una base recién migrada la cobertura es **«mes en curso + 3 por delante»**:
detrás no hay nada, porque detrás no hay datos.

Y ahí choca con una siembra perfectamente legítima: un test de **ventana
temporal** necesita una fila FUERA de la ventana para comprobar que queda
excluida, así que escribe `now - 100 días`. Ese mes no tiene partición.

## Fix

`tests/integration/_partitions.py`:

```python
from ._partitions import ensure_partition_for

await ensure_partition_for(dsn, "executions", now)   # antes del INSERT
```

Es idempotente y **reusa el DDL de producción** (`partition_statements`), con lo
que la partición nace con RLS activada, forzada y con su policy de aislamiento.

Necesita el DSN del owner (`migrations_pg_dsn`), no el de `app_user`: crear una
partición es DDL sobre la tabla padre.

## Lo que NO hay que hacer

**Crear la partición a mano en el test** con un `CREATE TABLE … PARTITION OF`
suelto. Esa partición no tiene RLS: los tests que siembren en ella pasarán el
aislamiento entre tenants **por no tenerlo activado**, que es exactamente el
fallo que los tests cross-tenant existen para cazar. El arnés nunca puede ser más
laxo que producción.

**Añadir una partición `DEFAULT` «solo para los tests»**. Cambia el
comportamiento que los tests de particionado verifican, y esconde el hueco de
cobertura que la alerta `PartitionCoverageGap` está para gritar.

## La clase de problema, que volverá

Convertir una tabla a particionada es un cambio que **el código de producción no
nota** —sólo inserta filas de hoy— y que **rompe el arnés** —que inserta filas de
cuando le conviene. La misma asimetría que con la contraseña de Redis
([redis-con-contrasena-rompe-la-integracion.md](redis-con-contrasena-rompe-la-integracion.md)):
producción absorbe el cambio, el arnés se lo come.

Regla práctica: **al particionar una tabla, busca quién escribe en ella con
fechas que tú no controlas** — `grep -rn "INSERT INTO <tabla>" tests/` cruzado con
`created_at=` o `timedelta(days=` — antes de dar la migración por terminada.

Ojo con el caso que parece a salvo y no lo está: un `-5 días` cae en el mes
anterior si hoy es día 3. Es un fallo que sólo aparece cinco días al mes.
