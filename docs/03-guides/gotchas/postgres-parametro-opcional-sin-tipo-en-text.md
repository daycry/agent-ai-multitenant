---
title: Un filtro opcional `:x IS NULL OR col = :x` en `text()` revienta, y el cast obvio lo empeora
area: postgres
encountered: 2026-08-19
stack: SQLAlchemy 2.x async, asyncpg 0.30, PostgreSQL 16
---

## Síntoma

Dos errores encadenados, y el segundo parece un arreglo del primero.

**Primero**, con el filtro opcional escrito de la forma natural:

```python
text("... WHERE (:project_id IS NULL OR t.project_id = :project_id)")
```

```
asyncpg.exceptions.AmbiguousParameterError: could not determine data type of parameter $3
```

**Después**, al castear con la sintaxis de Postgres (`:project_id::uuid`), el
error cambia de sitio y desconcierta: la query sale con **el parámetro sin
enviar**, y SQLAlchemy lista sólo los parámetros que sí reconoció.

```
[parameters: (['code', 'tests', ...],)]   # falta project_id, tenant_id, since
```

## Causa raíz

Son dos trampas distintas que se disparan seguidas:

1. **Postgres no infiere el tipo de un parámetro que aparece en `$n IS NULL`**,
   aunque el MISMO parámetro aparezca después en una comparación tipada
   (`col = $n`). No unifica ambos usos: falla en el primero. El patrón
   «filtro opcional en una query de `text()`» cae aquí siempre.
2. **El regex de bind params de `text()` no reconoce un parámetro seguido de
   `::`.** SQLAlchemy busca `:nombre` con un _negative lookahead_ de `:`, así que
   en `:project_id::uuid` no ve ningún parámetro — manda la query con `:project_id`
   literal y sin valor. El fallo se manifiesta lejos de la causa: parece que el
   parámetro «se perdió».

## Fix

`CAST(:x AS tipo)`, que resuelve las dos a la vez — le da el tipo a Postgres y
deja el `:x` en la forma que `text()` sí parsea:

```python
_SCOPE = """
 WHERE e.tenant_id = CAST(:tenant_id AS uuid)
   AND (CAST(:project_id AS uuid) IS NULL OR t.project_id = CAST(:project_id AS uuid))
   AND (CAST(:since AS timestamptz) IS NULL OR e.at >= CAST(:since AS timestamptz))
"""
```

Alternativas que también valen: `:x ::uuid` con espacio (el lookahead deja de
aplicar, pero es una sutileza que el siguiente lector borra sin saberlo), o
construir la query con el Core de SQLAlchemy y `sqlalchemy.cast()`.

## Cómo verificar el fix

`tests/integration/test_review_verdict_shape.py::test_no_rejections_is_an_empty_report_not_a_crash`
llama al agregado **sin** `project_id` ni `since`: es el caso que dispara el
`AmbiguousParameterError`, así que pasa sólo si los tres parámetros van
casteados. Y
`test_the_breakdown_can_be_scoped_to_one_project` prueba la otra rama —el
parámetro con valor— para que el cast no se «arregle» dejando el filtro inerte:

```bash
pytest tests/integration/test_review_verdict_shape.py -q -p no:randomly
```
