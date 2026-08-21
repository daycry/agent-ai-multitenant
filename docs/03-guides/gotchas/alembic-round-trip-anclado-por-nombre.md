---
title: Un round-trip de Alembic con `downgrade("-1")` deja de probar lo que probaba
area: postgres
encountered: 2026-08-18
stack: alembic 1.13, PostgreSQL 16, pytest
---

# `downgrade("-1")` significa «una por debajo de la CABEZA», no «deshaz mi migración»

## Síntoma

Un test de reversibilidad que llevaba semanas en verde empieza a fallar —en CI y
en local— sin que nadie haya tocado ni el test ni la migración que prueba:

```
tests/integration/test_guardrail_configs_table.py::test_downgrade_drops_the_table_and_upgrade_puts_it_back_with_rls
>       assert asyncio.run(_table_exists(migrations_pg_dsn)) is False
E       AssertionError: assert True is False
```

Y en el `stderr` capturado, la pista entera:

```
INFO  [alembic.runtime.migration] Running downgrade 0139_executions_steps_rollup -> 0138_revoke_backfill_grants
```

El test creía estar deshaciendo `0132_guardrail_configs`, y lo que deshizo fue
`0139`. La tabla, claro, seguía ahí.

## Causa raíz

`command.downgrade(cfg, "-1")` es **relativo a la cabeza del árbol de
migraciones**, no a la migración que el test tiene en mente. Mientras la
migración bajo prueba ES la cabeza —normalmente el día que se escribe el test—
las dos lecturas coinciden y el test parece correcto. En cuanto se apila otra
migración encima, `-1` apunta a otro sitio y el test:

- **deja de cubrir** la migración que decía cubrir, y
- **falla por una razón falsa**, apuntando a una migración inocente.

Lo segundo es lo que se ve; lo primero es lo caro: durante el intervalo en que
`-1` ya no apuntaba a la migración correcta pero el test todavía pasaba, la
reversibilidad no estaba probada por nadie.

## Fix

Anclar el round-trip a la revisión **inmediatamente anterior por NOMBRE**, que no
se mueve cuando crece el árbol:

```python
# La revisión anterior a la que crea la tabla. Se ancla por NOMBRE y nunca
# con "-1": "-1" es relativo a la cabeza, no a esta migración.
_REVISION_BEFORE = "0131_partition_guardrail_events"

command.downgrade(alembic_config, _REVISION_BEFORE)
```

Es la convención ya mayoritaria en el repo (`test_junction_tenant_rls.py`,
`test_marketplace_v2_migration.py`, `test_cortex_*`…) y está razonada en
`test_migrations.py::test_fk_cleanup_migration_is_reversible`.

**Efecto secundario deseable**: bajar por nombre deshace también todas las
migraciones apiladas encima. En este caso son ocho, cuatro de ellas las que
convierten tablas a particionadas del ADR 0151. Eso es exactamente el ida y
vuelta que CLAUDE.md exige comprobar antes de desplegar, así que el test más
lento es también el que prueba más.

## Cómo verificar el fix

```bash
TEST_PG_DB_NAME=agentic_gr .venv/Scripts/python.exe -m pytest \
  tests/integration/test_guardrail_configs_table.py -q -p no:randomly --timeout=900
```

Y para cazar el patrón en el resto del repo antes de que vuelva a pasar:

```bash
grep -rn 'command.downgrade(.*"-1"' tests/
```

Hoy sale **un** resultado legítimo:
`test_migrations.py::test_reversible_migration_preserves_row_data`, cuyo contrato
sí es relativo a la cabeza («sea cual sea la migración de arriba, su round-trip no
se come filas»). Cualquier otro resultado es un test que cree estar probando una
migración concreta y no la está probando: ánclalo por nombre.
