---
title: "Un UNIQUE con las columnas de la PK no se crea: PostgreSQL lo descarta dentro del `CREATE TABLE`"
area: postgres
encountered: 2026-08-20
stack: PostgreSQL 16.13, Alembic 1.18.4, SQLAlchemy 2.0.49
---

# Un `UniqueConstraint` sobre las columnas de la PRIMARY KEY nunca llega a existir

## Síntoma

Una migración declara las dos cosas sobre el mismo par de columnas:

```python
op.create_table(
    "task_dependencies",
    ...,
    sa.PrimaryKeyConstraint("task_id", "depends_on_task_id", name="pk_task_dependencies"),
    sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
)
```

La migración aplica **sin error ni warning**. Meses después, `alembic check`
propone crear ese UNIQUE en cada ejecución, para siempre:

```
Detected added unique constraint 'uq_task_dependencies_pair' on '('task_id', 'depends_on_task_id')'
```

Y se lee al revés de lo que es: parece que la base de datos ha perdido una
constraint, o que alguien la borró a mano. La reacción natural —escribir una
migración que la cree— añade un índice único idéntico al de la PK: coste de
escritura en cada `INSERT`, cero garantía nueva.

## Causa raíz

**PostgreSQL deduplica.** Dentro de un mismo `CREATE TABLE`, una constraint
UNIQUE cuyas columnas son exactamente las de la PRIMARY KEY no se crea: sólo
queda la PK. No hay error, no hay `NOTICE`, no hay rastro en el log.

Y no es que Alembic o SQLAlchemy se la coman por el camino — el DDL sale entero:

```bash
alembic upgrade 0001:0002 --sql | grep -A6 "CREATE TABLE task_dependencies"
#   CONSTRAINT pk_task_dependencies PRIMARY KEY (task_id, depends_on_task_id),
#   CONSTRAINT uq_task_dependencies_pair UNIQUE (task_id, depends_on_task_id),
```

Reproducible en tres líneas contra PostgreSQL 16.13:

```sql
CREATE TABLE zz (a uuid NOT NULL, b uuid NOT NULL,
                 CONSTRAINT pk_zz PRIMARY KEY (a,b),
                 CONSTRAINT uq_zz UNIQUE (a,b));
SELECT conname, contype FROM pg_constraint WHERE conrelid='zz'::regclass;
--  pk_zz | p        ← y nada más
```

De ahí la deriva permanente: el modelo declara un objeto que **jamás existió en
ninguna base de datos**, así que autogenerate lo propone en cada comparación.
`CREATE UNIQUE INDEX` explícito sí lo crearía (ése no se deduplica), que es la
única forma de que una migración «arregle» el diff — y es justo la que no hay que
escribir.

## Fix

**Retirarlo del modelo**, porque la unicidad no se pierde: la garantiza la PK.
La comprobación de que no se pierde nada es mirar quién se apoyaba en el nombre:

```bash
grep -rn "uq_task_dependencies_pair" apps/ tests/
```

En este repo el mapa de conflictos de `routers/_integrity.py` ya traducía
`pk_task_dependencies` → «Esa dependencia entre tareas ya existe», o sea que el
código nunca dependió del UNIQUE. Cero duplicados posibles antes y después.

Si el objeto que se retira estuviera en un snapshot de DDL
(`tests/unit/test_domain_models_package.py`), re-capturar el digest **y escribir
al lado por qué cambió**: un digest actualizado sin nota es indistinguible de un
descuido.

## Cómo verificar

`pg_constraint` es la autoridad, no el fichero de migración:

```bash
docker exec agentic-platform-postgres-1 psql -U postgres -d <bd> -c \
  "SELECT conname, contype FROM pg_constraint WHERE conrelid='<tabla>'::regclass;"
```

Y la lección general, que vale más que el caso: **una migración aplicada no
demuestra que el objeto exista**. Cuando el diff de autogenerate propone CREAR
algo que una migración vieja dice haber creado, el primer paso es mirar el
catálogo, no escribir la migración.

## Relacionado

- [`alembic-metadata-a-medias-propone-borrar-lo-que-no-ve.md`](./alembic-metadata-a-medias-propone-borrar-lo-que-no-ve.md)
  — la otra mitad: por qué el diff mentía en la dirección contraria (proponer
  borrar lo que no ve).
- §4 de [`verificar-antes-de-implementar.md`](../verificar-antes-de-implementar.md)
  — una guarda anclada a que quede deriva se vuelve un rojo en falso el día que
  se cierra; hay que anclarla a que la comparación **corrió**.
