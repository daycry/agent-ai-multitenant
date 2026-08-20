"""Tras migrar a head, el autogenerate propone SOLO los 22 items inventariados.

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
Hoy quedan **22 items en 16 tablas**, todos nombrados abajo, y este fichero pasa
de tolerar a **cerrar**: el inventario cubre el esquema entero y **sólo puede
menguar**.

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

#: **El trinquete.** Los 22 items que el autogenerate todavía propone sobre el
#: esquema ENTERO, medidos el 2026-08-20 contra una BD limpia migrada a head, con
#: el `include_object` del proyecto. Eran 162 en 23 tablas antes de la ola.
#:
#: Este conjunto **sólo puede menguar**, y las dos direcciones están cerradas:
#: un item que no esté aquí pone el test rojo (la deriva no puede crecer), y una
#: entrada de aquí que ya no aparezca en el diff **también** (cuando alguien la
#: arregla, tiene que borrarla de la lista, para que el inventario no acabe
#: describiendo un mundo que ya no existe).
#:
#: Los tres grupos, que son tres decisiones distintas y conviene no mezclarlas:
#:
#: **(a) 11 `remove_fk` — la BD nombra la clave ajena y el modelo no.** No falta
#: la FK: falta su NOMBRE. `TenantScopedMixin` declara el `ForeignKey` a
#: `organizations` sin `name=`, así que autogenerate ve la `fk_*_tenant` de la
#: migración como sobrante y quiere poner la suya. Se arregla en el modelo
#: (declarar el nombre), es aditivo y no toca la BD. Las tres que no vienen del
#: mixin —`plans.conversation_id`, `conversations.related_plan_id`,
#: `messages.related_plan_id`— son FK explícitas con el mismo problema de nombre.
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
#: **(c) 4 items que el modelo declara y la BD nunca tuvo.** Los tres
#: `add_index` son el `index=True` que `TenantScopedMixin` pone en `tenant_id`
#: para todas sus tablas por igual y que estas tres migraciones no crearon; el
#: `add_constraint` es un UNIQUE que la 0002 no creó y que en la práctica no
#: falta, porque ese par YA es la PK compuesta. Ninguno es peligroso —proponen
#: CREAR, no borrar— y la salida limpia de los tres primeros es `index=False` en
#: el mixin más un `Index(...)` explícito en las ~35 tablas donde la BD sí lo
#: tiene, un cambio en fichero compartido que la ola dejó anotado sin hacer.
REMAINING_DRIFT_2026_08_20: frozenset[str] = frozenset(
    {
        # (a) La BD nombra la FK y el modelo no. Arreglo model-side, aditivo.
        "remove_fk:agent_prompt_versions.agent_prompt_versions_tenant_id_fkey",
        "remove_fk:api_tokens.fk_api_tokens_tenant",
        "remove_fk:conversations.fk_conversations_related_plan_id",
        "remove_fk:incoming_webhook_configs.fk_incoming_webhook_configs_tenant",
        "remove_fk:marketplace_deployments.marketplace_deployments_tenant_id_fkey",
        "remove_fk:messages.fk_messages_related_plan_id",
        "remove_fk:plans.fk_plans_conversation_id",
        "remove_fk:scim_tokens.fk_scim_tokens_tenant",
        "remove_fk:user_invitations.user_invitations_tenant_id_fkey",
        "remove_fk:user_mfa_totp.fk_user_mfa_totp_tenant",
        "remove_fk:webauthn_credentials.fk_webauthn_credentials_tenant",
        # (b) El modelo acierta y la BD está mal: pide MIGRACIÓN del operador.
        "modify_nullable:assistant_conversations.created_at",
        "modify_nullable:assistant_conversations.updated_at",
        "modify_nullable:assistant_turns.created_at",
        "modify_nullable:assistant_turns.updated_at",
        "modify_nullable:browse_sessions.created_at",
        "modify_nullable:browse_sessions.updated_at",
        "modify_nullable:llm_usage_events.updated_at",
        # (c) El modelo lo declara y la BD nunca lo tuvo. Proponen CREAR.
        "add_constraint:task_dependencies.uq_task_dependencies_pair",
        "add_index:user_invitations.ix_user_invitations_tenant_id",
        "add_index:user_mfa_totp.ix_user_mfa_totp_tenant_id",
        "add_index:webauthn_credentials.ix_webauthn_credentials_tenant_id",
    }
)


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


def test_the_schema_drift_can_only_shrink(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """El trinquete: el diff son los 22 items inventariados, ni uno más ni menos.

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

    A diferencia del trinquete de arriba, esta guarda ya casi se puede exigir
    **en vacío**, que es lo que pedía el enunciado del plan: de las 17 tablas del
    dominio quedan sólo dos items, de las familias (a) y (c) —
    `plans.fk_plans_conversation_id` y `task_dependencies.uq_…_pair`—, así que se
    nombran uno a uno y el resto tiene que estar limpio.
    """
    esperados = {
        "remove_fk:plans.fk_plans_conversation_id",
        "add_constraint:task_dependencies.uq_task_dependencies_pair",
    }
    labels = _diff_labels(alembic_config, admin_database_url)
    domain = sorted(
        label for label in labels if label.split(":", 1)[1].split(".")[0] in DOMAIN_TABLES
    )

    nuevos = [label for label in domain if label not in esperados]
    assert not nuevos, (
        "el autogenerate propone cambios NUEVOS sobre tablas del dominio — el "
        "modelo y las migraciones han divergido:\n" + "\n".join(f"  {n}" for n in nuevos)
    )

    assert domain, (
        "el diff del dominio salió VACÍO, ni siquiera los dos items conocidos: "
        "probablemente la comparación no llegó a correr y esta guarda estaría "
        "pasando en vacío (§4 de verificar-antes-de-implementar)."
    )
