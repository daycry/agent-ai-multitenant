---
title: Una migración que activa la RLS en un bucle es invisible para el guard estático
area: postgres
encountered: 2026-08-19
stack: alembic 1.13, PostgreSQL 16, pytest
---

# `ALTER TABLE {table} ENABLE ROW LEVEL SECURITY` no protege nada… a ojos del test

## Síntoma

Escribes una migración que activa la RLS de varias tablas con un bucle, que es lo
natural y lo que ya hacen otras migraciones del repo:

```python
for table in _TABLES:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
```

La migración es correcta —aplicada contra PostgreSQL hace exactamente lo que
dice— y sin embargo el guard de seguridad sigue en rojo, señalando esas mismas
tablas:

```
FAILED tests/security/test_pentest_findings.py::test_every_cortex_table_has_structural_rls
E   AssertionError: tablas del córtex SIN RLS estructural: cortex_turns, cortex_identity, …
```

## Causa raíz

`tests/security/test_pentest_findings.py` es un test **estático**: no ejecuta las
migraciones, lee su TEXTO. Su detector busca el nombre de la tabla dentro de la
sentencia:

```python
_RLS_LITERAL_RE = re.compile(
    r'ALTER\s+TABLE\s+["\']?([a-zA-Z0-9_]+)["\']?\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY',
    re.IGNORECASE,
)
```

En el fichero, una f-string en un bucle deja escrito `ALTER TABLE {table} ENABLE
ROW LEVEL SECURITY`. Ahí no hay ningún nombre de tabla: el detector no puede saber
sobre cuáles actúa sin ejecutar Python. Existe un segundo patrón
(`_RLS_TEMPLATED_RE`) para el caso del bucle, pero solo vale dentro de **la
migración que CREA** esas tablas —donde el bucle itera sobre la lista de la propia
migración—, no en una migración posterior que solo las endurece.

Lo caro no es el rojo, es el arreglo que sugiere. Ante «tabla X sin RLS» lo
tentador es añadir X a una allowlist de excepciones… que es exactamente eximir de
la RLS a la tabla que sí la tiene, y dejar el hueco abierto para la siguiente.

## Fix

Escribir las sentencias **literales**, una por tabla, en una tupla de SQL a nivel
de módulo:

```python
_UPGRADE_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE cortex_turns ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE cortex_turns FORCE ROW LEVEL SECURITY",
    "CREATE POLICY cortex_turns_owner_only ON cortex_turns FOR ALL"
    f" USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})",
    # …una tríada por tabla
)

def upgrade() -> None:
    for statement in _UPGRADE_STATEMENTS:
        op.execute(statement)
```

Es más verboso y es mejor por dos motivos, no solo por contentar al test: una
migración de seguridad que hay que **ejecutar** para saber qué protege es peor
migración, y el `grep` de «quién activa la RLS de esta tabla» vuelve a funcionar.

El `downgrade` sí puede seguir usando el bucle: nadie audita estáticamente lo que
se apaga.

**El corolario que hay que tener presente**: como el detector mira texto, la
frase `ALTER TABLE x ENABLE ROW LEVEL SECURITY` escrita en un **comentario o un
docstring** también cuenta como protección. Si documentas en una migración que
una tabla NO lleva RLS, no escribas la sentencia completa con su nombre —usa
`ALTER TABLE … ENABLE ROW LEVEL SECURITY`, como ya hacen las migraciones 0093-0095—
o crearás un falso verde. La comprobación que no se puede engañar así es la
funcional, contra el catálogo de PostgreSQL:
`tests/integration/test_rls_invariant.py`.

## Cómo verificar el fix

```bash
.venv/Scripts/python.exe -m pytest tests/security/test_pentest_findings.py -q -p no:randomly
```

Y para comprobar que el guard tiene dientes de verdad, borra una línea `ALTER
TABLE … ENABLE …` de tu migración y vuelve a correrlo: tiene que ponerse rojo
nombrando esa tabla.
