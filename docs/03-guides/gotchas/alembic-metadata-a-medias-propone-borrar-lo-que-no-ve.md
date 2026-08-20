---
title: "`alembic check` que muere con `NoReferencedTableError` no es un problema local: es la metadata incompleta"
area: postgres
encountered: 2026-08-20
stack: alembic 1.18.4, SQLAlchemy 2.x, PostgreSQL 16, pgvector
---

# Metadata a medias: `alembic check` no falla, revienta — y `--autogenerate` propone borrar lo que no ve

## Síntoma

Dos caras del mismo problema, y la segunda es la que sale caro.

**Cara 1 — el comando revienta.** `alembic check` no dice «hay deriva» ni «no hay
deriva». Muere con un traceback de SQLAlchemy que no habla de esquemas:

```
sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column
'incoming_webhook_configs.project_id' could not find table 'projects' with
which to generate a foreign key to target column 'id'
```

Se lee como un problema de configuración local de quien lo ejecuta —«me falta
algo en el entorno», «será cosa de mi venv»—, así que se rodea y se sigue. En
este repo se rodeó durante meses: **varios planes declaraban `alembic check` como
criterio de cierre** y uno de ellos ([`cortex-f2`](../../roadmap/cortex-f2-afectivo.md))
llegó a anotar que el comando «no es ejecutable» y a excusarse de él.

**Cara 2 — el comando calla y la herramienta miente.** Si las tablas que faltan
no encadenan ninguna FK con las que hay, no revienta nada: `alembic check` sale
en verde o con una deriva incompleta, y `alembic revision --autogenerate` genera
una migración cuyo `upgrade()` está lleno de borrados que nadie pidió:

```python
op.drop_index('ix_chunks_embedding_hnsw', ...)   # el índice HNSW del RAG
op.drop_index('ix_chunks_content_fts', ...)
op.drop_table('projects')                        # si la tabla tampoco se ve
```

Y esto es lo que hace caro el fallo: **aplicar ese `drop_index` no rompe nada
visible**. El RAG sigue respondiendo, sólo que con búsqueda secuencial. No hay
error, no hay alerta, no hay excepción. La regresión se mide en latencia, meses
después, y nadie la relaciona con «aquella migración que sólo añadía una
columna».

## Causa raíz

Un mapeador de SQLAlchemy se registra en `Base.metadata` **al importarse su
módulo**. `Base.metadata` no descubre nada: es el acumulado de lo que se importó.

`migrations/env.py` importaba **un solo módulo** de la capa de datos:

```python
from api_server.db import models as _models  # noqa: F401
```

`db/models.py` es el agregador de la fase 0 y arrastra córtex, marketplace,
invitaciones y LLM usage, pero **no importa `db/domain`** ni los módulos
posteriores. Medido: con `db.models` solo, `Base.metadata` tenía **34 tablas de
84**.

Y ahí está el mecanismo entero, en una frase: **para autogenerate, «no está en la
metadata» y «hay que borrarlo de la base de datos» son lo mismo.** La herramienta
compara lo que ve en la BD contra lo que ve en el modelo y propone alinear el
primero con el segundo. Una metadata a medias no produce un diff a medias:
produce un diff que **propone destruir la mitad que no cargó**.

Dos agravantes que conviene conocer porque los dos son silenciosos:

- **`pkgutil.walk_packages` se come los `ImportError`** si no le pasas `onerror`.
  Sin él, un módulo que no importe deja sus tablas fuera de la metadata sin una
  línea de log — o sea reproduce este mismo fallo por otra puerta.
- **`compare_indexes` de Alembic no compara `using`, `ops` ni `with`** (sólo
  nombre, unicidad y expresiones). Un HNSW declarado en el modelo sin
  `postgresql_ops={"embedding": "vector_cosine_ops"}` pone el check en verde y
  **no sirve para el coseno**. El verde de `alembic check` no basta para dar por
  bueno un índice vectorial.

## Fix

**1. Cargar la capa de modelos entera recorriendo el paquete, no listando
imports.** Una lista escrita a mano reproduce el mismo modo de fallo un piso más
arriba: envejece en cuanto llega `db/foo.py`, que es exactamente cómo envejeció
`db/models.py`. La fuente de verdad es el directorio
(`api_server/db/model_registry.py`):

```python
def import_all_models() -> tuple[str, ...]:
    names = discover_model_modules()   # walk_packages con onerror=_reraise
    for name in names:
        importlib.import_module(name)
    return names
```

Y en `env.py`, antes de `target_metadata = Base.metadata`:

```python
import_all_models()          # 53 módulos, 84 tablas
```

**2. Arreglar el MODELO, nunca la base de datos.** Las migraciones son la verdad
desplegada: llevan meses corriendo. Cuando el diff dice «la BD tiene un índice
que el modelo no declara», lo que falta es la declaración — con todos sus kwargs
(`postgresql_using`, `postgresql_ops`, `postgresql_with`, `postgresql_where`),
que son parte del índice y no adorno.

**3. Y sobre todo: no esconder la deriva en `include_object`.** Es la tentación
obvia y deja el veredicto verde el día que alguien borre el índice de verdad. La
línea está escrita en el docstring de `db/autogenerate_policy.py` y es la
diferencia entre configurar y tapar:

- **legítimo** — el objeto existe en la BD a propósito y **no puede** tener
  modelo (las particiones mensuales del ADR 0151, que aparecen y se retiran
  solas; la tabla de respaldo de la 0133, a la que el diseño quiere cerrado todo
  acceso). Cada exclusión lleva escrito por qué no puede tener modelo.
- **tapar** — el objeto sí debería estar en el modelo y se excluye para poner la
  comprobación en verde. Eso va al inventario del test, no al filtro.

## Cómo verificar el fix

Contra una BD **desechable** (nunca la del stack vivo), y midiendo antes y
después:

```bash
docker exec agentic-platform-postgres-1 psql -U postgres -c "CREATE DATABASE mi_bd_desechable;"
cd apps/api-server
pw=$(sed -n 's/^POSTGRES_PASSWORD=//p' ../../docker/.env)
export DATABASE_URL="postgresql+asyncpg://postgres:${pw}@localhost:15432/mi_bd_desechable"
../../.venv/Scripts/alembic.exe upgrade head
../../.venv/Scripts/alembic.exe check     # sale 1 mientras quede deriva, y la lista
```

Que la metadata esté completa lo vigila una guarda propia, para que no vuelva a
quedarse corta en silencio:

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_alembic_metadata_is_complete.py -q
```

Y que la deriva no vuelva a crecer, el trinquete de integración — que compara
**con el `include_object` del proyecto**, porque sin él el diff pasa de 22 items
a 119 y los 97 de diferencia son particiones que nadie puede cerrar:

```bash
.venv/Scripts/python.exe -m pytest tests/integration/test_alembic_autogenerate_clean.py -q -p no:randomly
```

**Si generas una sonda con `--autogenerate` para mirar el diff, bórrala.** Una
migración de sonda comiteada es un desastre, y mientras está en
`migrations/versions/` deja a cualquier otra sesión con «target database is not
up to date» sin saber por qué:

```bash
git status --porcelain --untracked-files=all -- apps/api-server/migrations/
# vacío, y `ls migrations/versions/*.py | wc -l` igual que antes
```

Ojo también al `.pyc`: el `__pycache__/` de `versions/` conserva el compilado de
la sonda aunque borres el `.py`. Está en `.gitignore`, así que no llega al repo,
pero confunde a un `grep` posterior.

## Relacionado

- [`alembic-round-trip-anclado-por-nombre.md`](./alembic-round-trip-anclado-por-nombre.md)
  — la otra forma de que un test de migraciones deje de probar lo que dice.
- [`partitioned-table-introspection.md`](./partitioned-table-introspection.md)
  — por qué las particiones del ADR 0151 no pueden tener modelo.
- §4 de [`verificar-antes-de-implementar.md`](../verificar-antes-de-implementar.md)
  — «una guarda que no puede fallar no es una guarda»: la banda de tolerancia
  `50 <= total <= 250` que este fichero tenía la sostenían 97 items de ruido de
  particionado, así que habría seguido en verde con toda la deriva real
  arreglada.
