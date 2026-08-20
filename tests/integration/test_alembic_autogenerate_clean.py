"""Tras migrar a head, el autogenerate propone SOLO los items inventariados.

Plan prod-16, ``auto_prod16_11_b``: «Verificar que `alembic` no detecta
diferencias de esquema tras el refactor (autogenerate vacío)».

Es la mitad de `task_prod16_11` que no se puede hacer offline. La otra —que el
troceo de `db/domain.py` no movió ni una columna del **modelo**— la cubre
``tests/unit/test_domain_models_package.py`` comparando el DDL compilado contra
el del monolito, sin base de datos. Aquí se comprueba la otra dirección: que lo
que el modelo declara sigue coincidiendo con **lo que las migraciones dejaron en
disco**.

## De «acotado a 17 tablas» a trinquete sobre el esquema ENTERO

Hasta el 2026-08-20 este fichero afirmaba algo mucho más flojo: que sobre las 17
tablas de `db/domain` el diff no crecía, y que fuera de ellas había «más de un
centenar de items» que se dejaban correr con una banda de tolerancia
(``50 <= total <= 250``). Había un motivo real —`migrations/env.py` importaba un
solo módulo de la capa de datos, así que `Base.metadata` veía **34 tablas de
84** y `alembic check` no daba veredicto: moría con `NoReferencedTableError`—,
pero la consecuencia era que la deriva ancha no la vigilaba nadie.

Con la metadata completa (commit `bc521ad4`) el veredicto salió: **162 items en
23 tablas**, y con la dirección peligrosa. Lo cerró una ola de cuatro carriles
declarando en el modelo los índices y las FK que las migraciones habían creado.
Hoy quedan **7 items en 4 tablas** —eran 22 al empezar la segunda ola del
2026-08-20, que cerró las familias (a) y (c)—, todos nombrados abajo, y este
fichero pasa de tolerar a **cerrar**: el inventario cubre el esquema entero y
**sólo puede menguar**. Los 7 que sobreviven son la única familia que NO se
arregla en el modelo: piden una migración que firme el operador.

Lo que se gana no es cosmético. El `alembic revision --autogenerate` que se
corría antes de la ola proponía, entre otras cosas::

    op.drop_index('ix_chunks_embedding_hnsw', ...)      # el índice HNSW del RAG
    op.drop_index('ix_chunks_content_fts', ...)
    op.drop_index('ix_memory_entries_embedding_hnsw', ...)
    op.drop_index('ix_memory_entries_content_fts', ...)

Quien autogenerase una migración para añadir una columna se llevaba de regalo el
borrado del índice vectorial, y si no la revisaba el RAG pasaba a búsqueda
secuencial **en silencio**. Hoy el diff no contiene ni un solo `remove_index`, y
:data:`REMAINING_DRIFT_2026_08_20` es lo que impide que vuelva a contenerlo.

## Por qué el test mide con el `include_object` del proyecto (y por qué importa)

La banda antigua era además una guarda que no podía fallar, en el sentido exacto
del §4 de `docs/03-guides/verificar-antes-de-implementar.md`. Este fichero
comparaba **sin** el `include_object` de :mod:`api_server.db.autogenerate_policy`,
así que veía 119 items donde `alembic check` veía 22: los 97 restantes eran las
particiones mensuales del ADR 0151 (76 índices y 21 tablas hijas, que aparecen y
se retiran solas cada mes) más la tabla de respaldo de la 0133. Es decir que el
suelo de «>= 50» lo sostenía **ruido**, y habría seguido en verde con toda la
deriva real arreglada.

De ahí las dos reglas de este fichero, que no son adorno:

* se compara con el `include_object` **del proyecto**, el mismo que usa
  `migrations/env.py`, para que el número que se vigila aquí sea el número que
  imprime `alembic check`; y
* la metadata se carga con :func:`api_server.db.model_registry.import_all_models`,
  también el mismo que usa `env.py`. Un recorrido propio se desincroniza del de
  la herramienta y entonces el test mide un esquema que nadie va a migrar.
"""

from __future__ import annotations

from typing import Any

import pytest

#: Los tests son SÍNCRONOS a propósito. El `env.py` de Alembic monta su propio
#: bucle con `asyncio.run()`, así que llamar a `command.upgrade` desde un test
#: async muere con «asyncio.run() cannot be called from a running event loop» —
#: un fallo que no habla de esquemas y cuesta un rato leer.
pytestmark = pytest.mark.integration


#: Las 17 tablas que DEFINE `api_server.db.domain`. Es el alcance de
#: `task_prod16_11` y por tanto el alcance de la guarda que ese plan pedía.
DOMAIN_TABLES = frozenset(
    {
        "agents",
        "agent_skills",
        "agent_tools",
        "approval_policy_templates",
        "approval_requests",
        "executions",
        "human_agent_config",
        "human_task_assignments",
        "human_work_sessions",
        "plans",
        "projects",
        "skills",
        "tasks",
        "task_dependencies",
        "teams",
        "team_members",
        "tools",
    }
)

#: **El trinquete.** Los items que el autogenerate todavía propone sobre el
#: esquema ENTERO —hoy **7**, y la cuenta la fija esta lista, no este párrafo—,
#: medidos el 2026-08-20 contra una BD limpia migrada a head, con el
#: `include_object` del proyecto. Eran 162 en 23 tablas antes de la ola, y 22 al
#: empezar la segunda.
#:
#: Este conjunto **sólo puede menguar**, y las dos direcciones están cerradas:
#: un item que no esté aquí pone el test rojo (la deriva no puede crecer), y una
#: entrada de aquí que ya no aparezca en el diff **también** (cuando alguien la
#: arregla, tiene que borrarla de la lista, para que el inventario no acabe
#: describiendo un mundo que ya no existe).
#:
#: Los tres grupos, que son tres decisiones distintas y conviene no mezclarlas.
#: Dos están CERRADOS y se dejan escritos a propósito: (a) porque la hipótesis
#: con la que se abrió era falsa y el arreglo evidente era el equivocado, y (c)
#: porque el argumento de por qué el modelo se equivocaba no vive en ningún otro
#: sitio.
#:
#: **(a) CERRADO el 2026-08-20 — las 11 `remove_fk`, y la hipótesis con la que
#: se abrieron era falsa.** Se apuntaron como «no falta la FK, falta su NOMBRE:
#: `TenantScopedMixin` declara el `ForeignKey` a `organizations` sin `name=`».
#: Las dos mitades de esa frase son mentira, y conviene que quede escrito porque
#: la primera lleva al arreglo equivocado:
#:
#:   - **El mixin no declara ninguna FK.** `TenantScopedMixin.tenant_id` es un
#:     `mapped_column(PG_UUID, nullable=False, index=True)` y nada más. Lo que
#:     faltaba era la constraint entera, no su etiqueta.
#:   - **El nombre no entra en la comparación.** Alembic empareja claves ajenas
#:     por la firma `unnamed` (`alembic/ddl/_autogen.py::_fk_constraint_sig`):
#:     tabla y columnas de origen, tabla y columnas de destino, `onupdate`,
#:     `ondelete` y el estado deferrable. El nombre NO está. Prueba viva: de las
#:     18 FK de `tenant_id` que hay en la BD, cinco se declaran en el modelo sin
#:     `name=` (`assistant_conversations`, `assistant_turns`,
#:     `cortex_conversations`, `guardrail_configs`, `tenant_settings`) y ninguna
#:     apareció nunca en este inventario. Y si el problema hubiera sido el
#:     nombre, el diff traería un `add_fk` emparejado con cada `remove_fk`; no
#:     traía ninguno.
#:
#: El arreglo fue declarar la constraint que la migración creó, tabla por tabla,
#: siguiendo la convención que ya usaban las diez tablas sanas: un
#: `ForeignKeyConstraint` a nivel de `__table_args__` (no en la columna, para no
#: reescribir el `tenant_id` del mixin) con el `ondelete` real, y con `name=`
#: **sólo si la migración lo puso** — tres de estas once las creó un
#: `create_table` sin nombrarlas, así que llevan el default de PostgreSQL
#: (`<tabla>_tenant_id_fkey`) y el modelo lo deja también sin nombrar en vez de
#: copiar a mano un default. Es aditivo y no tocó la base de datos.
#:
#: **Lo que NO se hizo, y es el motivo de que fueran once ediciones a mano:**
#: declarar la FK en el mixin. En el modelo hay **69 tablas con columna
#: `tenant_id` y sólo 18 con FK a `organizations` en la BD**, así que un
#: `ForeignKey` en el mixin habría cerrado once items y abierto **51 `add_fk`**
#: nuevos — deriva creciendo, no menguando. Tampoco cabía un
#: `naming_convention` en el `MetaData`: los nombres desplegados no siguen un
#: patrón único (ocho `fk_<tabla>_tenant` explícitos frente a tres defaults de
#: PostgreSQL), y una convención global además renombraría de golpe índices y
#: constraints de las 84 tablas.
#:
#: Las tres que no venían del mixin —`plans.conversation_id`,
#: `conversations.related_plan_id`, `messages.related_plan_id`— las promovió la
#: migración 0014 de soft-FK a reales con `op.create_foreign_key`, después de los
#: `create_table`, porque `plans` y `conversations` se apuntan mutuamente. Esa
#: pareja lleva `use_alter=True`: sin él `Base.metadata.sorted_tables` avisa de
#: un ciclo irresoluble («this warning may raise an error in a future release») y
#: **descarta las dos FK** al ordenar. `messages` no está en el ciclo y no lo
#: lleva.
#:
#: **(b) 7 `modify_nullable` — aquí el MODELO tiene razón y la BD está mal.** Es
#: la única familia que NO se arregla en el modelo: las migraciones 0108, 0109 y
#: 0112 crearon `created_at`/`updated_at` como
#: ``sa.Column(..., server_default=sa.text("now()"))`` **sin `nullable=False`**,
#: mientras `TimestampMixin` las declara obligatorias. Que el modelo acierte no
#: es una opinión: en el esquema hay **163 columnas `created_at`/`updated_at`
#: NOT NULL en 95 tablas** y sólo estas 7 nullables, y la propia migración 0135
#: diagnosticó el caso por escrito («La migración 0109 creó la columna […] sin
#: ``nullable=False``. El modelo ORM sí lo declara obligatorio») y declinó
#: endurecer `updated_at` sólo porque «endurecerla sería un cambio de esquema
#: colado» en una migración de particionado. Alinearlo tocando el modelo sería
#: relajar `TimestampMixin`, que comparten esas 95 tablas, para acomodar a cuatro
#: — o sea meter la mentira en el sitio donde más se lee. Cerrarlo pide una
#: migración que endurezca las 7 columnas, y **eso lo firma el operador**, no un
#: agente: ver el informe de la ola del 2026-08-20.
#:
#: **(c) CERRADO el 2026-08-20 — los 4 items que el modelo declaraba y la BD
#: nunca tuvo.** Eran los únicos que proponían CREAR, y ninguno pidió migración:
#: en los cuatro casos se equivocaba el MODELO, no la base de datos.
#:
#:   - Los tres `add_index` eran el `index=True` que `TenantScopedMixin` pone en
#:     `tenant_id` para todas sus tablas por igual. Las tres migraciones crearon
#:     en su lugar un compuesto cuya columna guía ya es `tenant_id`
#:     (`ix_mfa_totp_tenant_user`, `ix_webauthn_tenant_user` y el parcial
#:     `ix_user_invitations_tenant_pending`), así que el plano era un prefijo
#:     redundante: `index=False` local en cada modelo, con el argumento escrito
#:     al lado, igual que ya se hizo en `knowledge_bases` y `memory_entries`.
#:     NO se tocó el mixin — apagarlo ahí obligaría a declarar el `Index(...)` a
#:     mano en las ~35 tablas donde la BD sí lo tiene, que es mucha superficie
#:     para no ganar nada.
#:   - El `add_constraint` era un `UniqueConstraint` sobre las MISMAS columnas
#:     que la PK compuesta de `task_dependencies`. La migración 0002 lo declara y
#:     **jamás existió en ninguna base de datos**: PostgreSQL 16 descarta en
#:     silencio, dentro del mismo `CREATE TABLE`, un UNIQUE cuyas columnas son
#:     exactamente las de la PRIMARY KEY (verificado: el DDL que Alembic emite lo
#:     incluye y `pg_constraint` sólo devuelve `pk_task_dependencies`). No
#:     protegía nada que la PK no protegiera ya, así que se retiró del modelo.
REMAINING_DRIFT_2026_08_20: frozenset[str] = frozenset()
"""**VACÍO desde el 2026-08-20.** Los 162 items iniciales están cerrados.

Se deja el nombre con su fecha, y vacío, a propósito: dice cuándo se llegó a
cero, que es el dato que un `frozenset()` anónimo perdería. Y se deja el
mecanismo montado porque su valor no era la lista, era el trinquete de las dos
direcciones — hoy la primera mitad («ningún item nuevo») es la única que puede
disparar, y con el conjunto vacío equivale a exigir **diff vacío**.

Cómo se cerraron los últimos 22, que son tres historias distintas:

* **11 `remove_fk`**: la BD nombra la clave ajena y el modelo la dejaba anónima.
  Se declaró el nombre real tabla por tabla — no en `TenantScopedMixin`, porque
  en el modelo hay 69 tablas con columna `tenant_id` y sólo 18 con FK real a
  `organizations`: ponerla en el mixin habría cerrado 11 items y abierto **51
  `add_fk`** nuevos. Tampoco cabía un `naming_convention` global, porque los
  nombres desplegados no siguen un patrón único.
* **4 objetos que el modelo declaraba y la BD no tenía**: los tres
  `ix_*_tenant_id` venían del `index=True` que el mixin pone a todas sus tablas,
  y en las tres la BD ya tiene un compuesto cuya columna guía es `tenant_id`,
  así que el plano era un prefijo redundante. El cuarto,
  `uq_task_dependencies_pair`, **nunca existió en ninguna base de datos**:
  PostgreSQL descarta en silencio un UNIQUE cuyas columnas son exactamente las
  de la PRIMARY KEY dentro del mismo `CREATE TABLE`, sin error y sin NOTICE
  (ver `docs/03-guides/gotchas/postgres-unique-igual-a-la-pk-se-descarta-en-silencio.md`).
* **7 `modify_nullable`**: la única familia donde el modelo acertaba y la BD
  estaba mal. La cierra la migración `0144_timestamps_not_null`, con relleno
  previo y reversible.
"""


def _flatten(items: list[Any]) -> list[Any]:
    """`compare_metadata` anida listas para los cambios de columna."""
    out: list[Any] = []
    for item in items:
        if isinstance(item, list):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _table_of(item: tuple[Any, ...]) -> str:
    operation = item[0]
    if operation in ("add_table", "remove_table"):
        return str(getattr(item[1], "name", "?"))
    if operation in ("add_column", "remove_column") or operation.startswith("modify_"):
        return str(item[2])
    holder = getattr(item[1], "table", None)
    return str(getattr(holder, "name", "?"))


def _describe(item: tuple[Any, ...]) -> str:
    """Etiqueta corta y estable: ``<operación>:<tabla>.<detalle>``."""
    operation = item[0]
    table = _table_of(item)
    if operation in ("add_column", "remove_column"):
        return f"{operation}:{table}.{item[3].name}"
    if operation.startswith("modify_"):
        return f"{operation}:{table}.{item[3]}"
    if operation in ("add_index", "remove_index"):
        return f"{operation}:{table}.{getattr(item[1], 'name', '?')}"
    if operation in ("add_constraint", "remove_constraint", "add_fk", "remove_fk"):
        return f"{operation}:{table}.{getattr(item[1], 'name', None) or '<sin nombre>'}"
    return f"{operation}:{table}"


async def _autogenerate_diff(url: str) -> list[Any]:
    """El diff de autogenerate contra `url`, por asyncpg y con la política real.

    **Motor ASÍNCRONO a propósito**: este repo no instala `psycopg2`, así que un
    `create_engine("postgresql://…")` muere con `ModuleNotFoundError` — un fallo
    que parece de configuración y es sólo de driver. `compare_metadata` es
    síncrono, así que va dentro de `run_sync`.

    **Con el `include_object` del proyecto**, que es lo que hace comparable este
    número con el de `alembic check`. Sin él entran las particiones mensuales del
    ADR 0151 y el diff pasa de 22 items a 119, de los que 97 son ruido que
    ninguna persona puede cerrar (ver el docstring del módulo).
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from api_server.db.autogenerate_policy import make_include_object, partition_children
    from api_server.db.base import Base
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:

            def compare(sync_connection: Connection) -> list[Any]:
                include_object = make_include_object(partition_children(sync_connection))
                diff: list[Any] = compare_metadata(
                    MigrationContext.configure(
                        sync_connection,
                        opts={"compare_type": True, "include_object": include_object},
                    ),
                    Base.metadata,
                )
                return diff

            return await connection.run_sync(compare)
    finally:
        await engine.dispose()


def _diff_labels(alembic_config: object, url: str) -> list[str]:
    """Migra a head y devuelve las etiquetas del diff, ordenadas.

    La metadata se carga con el MISMO recorrido que usa `migrations/env.py`
    (`import_all_models`): 53 módulos, 84 tablas. Un recorrido propio aquí se
    desincronizaría del de la herramienta y el test mediría otro esquema.
    """
    import asyncio

    from alembic import command
    from api_server.db.model_registry import import_all_models

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]  # fixture sin tipar
    import_all_models()

    items = _flatten(asyncio.run(_autogenerate_diff(url)))
    return sorted(_describe(item) for item in items)


def test_the_comparison_actually_compared_something(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """No-vacuidad, y ahora hace falta de verdad.

    Mientras :data:`REMAINING_DRIFT_2026_08_20` tenía items, la aserción de
    «items del inventario que ya no aparecen» hacía de red: un `_diff_labels`
    roto que devolviera ``[]`` la disparaba. Con el inventario **vacío** esa red
    desaparece — un descubrimiento roto dejaría los otros tests en verde sin
    haber comparado un solo objeto, que es el §4 de
    `docs/03-guides/verificar-antes-de-implementar.md` y el mismo agujero por el
    que este fichero pasó semanas en verde con su suelo `total >= 50` sostenido
    por el ruido de las particiones.

    Así que se afirma sobre lo que SÍ tiene que ser no vacío aunque el diff lo
    sea: que la metadata cargó el esquema entero y que la base de datos migrada
    tiene sus tablas. Si mañana el diff vuelve a traer items, este test sigue
    valiendo igual — mide el aparato, no el resultado.
    """
    import asyncio

    from alembic import command
    from api_server.db.base import Base
    from api_server.db.model_registry import import_all_models
    from sqlalchemy import text
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]  # fixture sin tipar
    modulos = import_all_models()

    assert len(modulos) >= 50, (
        f"`import_all_models()` sólo importó {len(modulos)} módulos. El recorrido"
        " de `api_server.db` se rompió, y con la metadata a medias el diff de los"
        " otros tests no significa nada — de hecho un autogenerate propondría"
        " BORRAR todo lo que no cargó."
    )
    assert len(Base.metadata.tables) >= 84, (
        f"`Base.metadata` tiene {len(Base.metadata.tables)} tablas y el esquema"
        " son 84. Es exactamente el estado en que `alembic check` moría con"
        " `NoReferencedTableError` antes del 2026-08-20."
    )

    async def contar() -> int:
        engine = create_async_engine(admin_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:

                def leer(sync_connection: Connection) -> int:
                    filas = sync_connection.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables"
                            " WHERE table_schema = 'public'"
                        )
                    )
                    return int(filas.scalar_one())

                return await connection.run_sync(leer)
        finally:
            await engine.dispose()

    en_la_bd = asyncio.run(contar())
    assert en_la_bd >= 84, (
        f"la BD migrada a head sólo tiene {en_la_bd} tablas en `public`. O el"
        " `upgrade` no corrió, o esta fixture apunta a otra base — y entonces el"
        " diff compara el modelo contra un esquema vacío."
    )


def test_the_schema_drift_can_only_shrink(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """El trinquete: el diff son los items inventariados, ni uno más ni menos.

    Las dos direcciones importan:

    * **Un item nuevo** significa que alguien metió esquema por migración sin
      reflejarlo en el modelo (o al revés). El caso caro es un `remove_index`:
      el siguiente `alembic revision --autogenerate` propondría BORRAR ese
      índice, y si nadie revisa el fichero generado se pierde en silencio — que
      es exactamente lo que pasaba con el HNSW del RAG hasta el 2026-08-20.
    * **Un item del inventario que ya no aparece** significa que se arregló, y
      entonces hay que borrarlo de la lista. Sin esta mitad el inventario deja de
      medir y se convierte en prosa: diría 22 cuando queden 4.
    """
    labels = _diff_labels(alembic_config, admin_database_url)

    nuevos = sorted(set(labels) - REMAINING_DRIFT_2026_08_20)
    assert not nuevos, (
        "el autogenerate propone cambios NUEVOS que no están en el inventario — "
        "el modelo y las migraciones han divergido. Arréglalo en el MODELO (las "
        "migraciones son la verdad desplegada) y que no lo esconda nadie en "
        "`include_object`:\n" + "\n".join(f"  {n}" for n in nuevos)
    )

    arreglados = sorted(REMAINING_DRIFT_2026_08_20 - set(labels))
    assert not arreglados, (
        "estas divergencias del inventario YA no existen (alguien las arregló). "
        "Bórralas de REMAINING_DRIFT_2026_08_20 para que la cuenta siga siendo "
        "verdad:\n" + "\n".join(f"  {m}" for m in arreglados)
    )


def test_no_index_is_ever_proposed_for_deletion(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """Ni un `remove_index` en el diff. Es el objetivo entero de la ola.

    Se comprueba aparte del trinquete y con su propio mensaje porque es el fallo
    con peores consecuencias y el más fácil de no ver: un `drop_index` colado en
    una migración autogenerada no rompe nada al aplicarse — el RAG sigue
    respondiendo, sólo que con búsqueda secuencial. No hay error, no hay alerta,
    y la regresión se mide en latencia meses después.

    Los cuatro que motivaron la ola se nombran explícitamente: si alguna vez
    reaparecen, el mensaje debe decir cuáles son y por qué importan, no sólo
    «hay un item nuevo».
    """
    criticos = {
        "ix_chunks_embedding_hnsw",
        "ix_chunks_content_fts",
        "ix_memory_entries_embedding_hnsw",
        "ix_memory_entries_content_fts",
    }
    labels = _diff_labels(alembic_config, admin_database_url)

    borrados = sorted(label for label in labels if label.startswith("remove_index:"))
    vectoriales = sorted(label for label in borrados if label.split(".")[-1] in criticos)

    assert not vectoriales, (
        "el autogenerate propone BORRAR un índice del RAG o de la memoria. Si "
        "alguien genera una migración y no revisa el fichero, la búsqueda "
        "vectorial pasa a secuencial EN SILENCIO. Declara el índice en el modelo "
        "con sus kwargs (`postgresql_using`, `postgresql_ops`, `postgresql_with`, "
        "`postgresql_where`):\n" + "\n".join(f"  {v}" for v in vectoriales)
    )
    assert not borrados, (
        "el autogenerate propone borrar índices que las migraciones crearon. "
        "Declárralos en el modelo — no los excluyas en `include_object`, que "
        "dejaría el check verde el día que se borre uno de verdad:\n"
        + "\n".join(f"  {b}" for b in borrados)
    )


def test_the_split_did_not_move_the_domain_schema(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """Sobre las 17 tablas de `db/domain`, el autogenerate no propone nada más.

    Lo que protege del troceo de `task_prod16_11`: si al repartir `db/domain.py`
    en `db/domain/` se hubiera caído una columna, un `CheckConstraint` o un
    índice, la BD tendría algo que el modelo ya no declara y aparecería aquí.

    Desde el 2026-08-20 esta guarda se exige **EN VACÍO**, que es exactamente lo
    que pedía el enunciado del plan y lo que este fichero llevaba dos años sin
    poder afirmar: sobre las 17 tablas del dominio el autogenerate no propone
    nada. Los dos items que quedaban se cerraron ese día —
    `task_dependencies.uq_…_pair` (familia (c)) retirando del modelo un UNIQUE
    que la PK compuesta ya garantizaba, y `plans.fk_plans_conversation_id`
    (familia (a)) declarando la FK que la 0014 promovió—, así que `esperados`
    está vacío a propósito: cualquier item sobre una tabla del dominio es deriva
    nueva.

    Ojo con la lectura fácil: un conjunto vacío haría que `assert not nuevos`
    pasara también si la comparación no corriese. Eso NO lo cubre esta lista,
    lo cubren las dos guardas anti-vacío del final —que las 17 tablas están
    cargadas en la metadata, y que el diff global no está vacío mientras el
    inventario siga poblado.
    """
    esperados: set[str] = set()
    labels = _diff_labels(alembic_config, admin_database_url)
    domain = sorted(
        label for label in labels if label.split(":", 1)[1].split(".")[0] in DOMAIN_TABLES
    )

    nuevos = [label for label in domain if label not in esperados]
    assert not nuevos, (
        "el autogenerate propone cambios NUEVOS sobre tablas del dominio — el "
        "modelo y las migraciones han divergido:\n" + "\n".join(f"  {n}" for n in nuevos)
    )

    # La guarda anti-vacío del §4 de `verificar-antes-de-implementar`, pero
    # anclada a que la comparación CORRIÓ, no a que quede deriva en el dominio.
    # Anclarla a lo segundo ya sería un rojo en falso: el dominio está limpio
    # desde el 2026-08-20, o sea que ladraría precisamente por haber conseguido
    # el objetivo, y el arreglo tentador sería borrarla.
    from api_server.db.base import Base

    faltan = sorted(DOMAIN_TABLES - set(Base.metadata.tables))
    assert not faltan, (
        "la comparación no llegó a cargar las tablas del dominio, así que este "
        "test estaría pasando en vacío: " + ", ".join(faltan)
    )
    if REMAINING_DRIFT_2026_08_20:
        assert labels, (
            "el diff salió VACÍO con el inventario todavía poblado: la "
            "comparación no llegó a correr. (El día que el inventario quede "
            "vacío, el diff vacío es lo CORRECTO y lo exige el trinquete.)"
        )
