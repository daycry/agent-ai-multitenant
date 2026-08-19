"""Tras migrar a head, el autogenerate no propone nada NUEVO sobre el dominio.

Plan prod-16, ``auto_prod16_11_b``: «Verificar que `alembic` no detecta
diferencias de esquema tras el refactor (autogenerate vacío)».

Es la mitad de `task_prod16_11` que no se puede hacer offline. La otra —que el
troceo de `db/domain.py` no movió ni una columna del **modelo**— la cubre
``tests/unit/test_domain_models_package.py`` comparando el DDL compilado contra
el del monolito, sin base de datos. Aquí se comprueba la otra dirección: que lo
que el modelo declara sigue coincidiendo con **lo que las migraciones dejaron en
disco**.

## El enunciado pedía «autogenerate vacío» y hoy NO lo está. Por qué

Se ejecutó, y el diff contra la BD recién migrada trae más de un centenar de
items. **Ninguno viene de este troceo** —lo demuestra el test unitario, que
compara DDL contra DDL, y la comparación por `ast.unparse` de las 39
definiciones— y casi todos son de dos familias que llevan años acumulándose:

- **Nombres de índice y de FK.** Las migraciones crearon
  `ix_review_sessions_plan_status`, `fk_task_audit_events_tenant`… con nombres
  que el modelo no declara, así que autogenerate quiere borrarlos y crear los
  suyos.
- **`TEXT` frente a `String(n)`.** Varias columnas se crearon como `TEXT` en su
  migración y el modelo las declara con longitud
  (`projects.default_runtime_template` es `String(64)`, la BD tiene `TEXT`).
  Equivalentes en PostgreSQL; distintas para `compare_type=True`.

Y una tercera, más reciente: las **cinco tablas particionadas** (ADR 0151)
tienen índices por partición (`llm_usage_events_2026_10_…`) que el modelo no
declara ni puede declarar.

Dejar el test «rojo hasta que alguien limpie todo eso» sería una suite que
siempre falla, o sea ninguna suite (§4 de `verificar-antes-de-implementar`).
Dejarlo pasando en vacío sería peor. Lo que hace es acotar la afirmación a lo que
esta tarea sí puede sostener: **sobre las 17 tablas que define `db/domain`, el
diff no crece**. Si el troceo hubiera perdido una columna, un `CheckConstraint` o
un índice, aparecería aquí un item nuevo y esto se pondría rojo.

## Y una nota sobre `env.py`, que es un hallazgo aparte

`migrations/env.py` importa **un solo módulo** de la capa de datos::

    from api_server.db import models as _models  # noqa: F401

`db/models.py` es el agregador de la fase 0 y arrastra córtex, marketplace,
invitaciones y LLM usage, pero **no importa `db/domain`**. Medido el 2026-08-19:
sólo con `db.models`, ``Base.metadata`` tiene **34** tablas; importando todo el
paquete, **83**. O sea que un `alembic revision --autogenerate` corrido hoy tal
cual no vería 49 tablas —`agents`, `projects`, `tasks`, `executions`…— y las
propondría **borrar**. Es anterior a este troceo (`db/domain.py` tampoco estaba
importado cuando era un fichero suelto) y se arregla con una línea, pero cambia
lo que genera la herramienta de migraciones para todo el mundo: merece su propio
cambio y su propia revisión, no colarse en un refactor. Este fichero lo rodea
cargando el modelo entero, y lo deja anotado con un test que se pondrá rojo el
día que se arregle.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

#: Los tests son SÍNCRONOS a propósito. El `env.py` de Alembic monta su propio
#: bucle con `asyncio.run()`, así que llamar a `command.upgrade` desde un test
#: async muere con «asyncio.run() cannot be called from a running event loop» —
#: un fallo que no habla de esquemas y cuesta un rato leer.
pytestmark = pytest.mark.integration


#: Las 17 tablas que DEFINE `api_server.db.domain`. Es el alcance de
#: `task_prod16_11` y por tanto el alcance de esta guarda.
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

#: Divergencias modelo↔BD que YA existían sobre tablas del dominio, medidas el
#: 2026-08-19 contra la BD migrada a head. **Este inventario sólo puede
#: menguar**: hay una aserción que se pone roja si sobra una entrada, para que la
#: lista no acabe describiendo un mundo que ya no existe.
KNOWN_DOMAIN_DRIFT_2026_08_19: frozenset[str] = frozenset(
    {
        # El modelo declara un UNIQUE que la migración 0002 no creó (el par es
        # ya la PK compuesta, así que en la práctica no falta nada).
        "add_constraint:task_dependencies.uq_task_dependencies_pair",
        # `TEXT` en la BD frente a `ARRAY(String)` / `String(n)` en el modelo.
        # Equivalentes en PostgreSQL; distintos para `compare_type=True`.
        "modify_type:projects.allowed_commands",
        "modify_type:projects.default_kb_grants",
        "modify_type:projects.default_runtime_template",
        "modify_type:projects.human_task_review_mode",
        # Índices y FK que crearon las migraciones con un nombre que el modelo
        # no declara: autogenerate quiere borrarlos para poner los suyos.
        "remove_fk:plans.fk_plans_conversation_id",
        "remove_index:executions.ix_executions_prompt_version",
        "remove_index:projects.ix_projects_team_id",
        "remove_index:projects.uq_projects_tenant_slug_live",
        "remove_index:skills.ix_skills_source_installation",
        "remove_index:tools.ix_tools_source_installation",
    }
)


def _import_every_db_module() -> None:
    """Carga TODOS los módulos de `api_server.db`, que es la metadata completa."""
    import api_server.db as db_package

    for module in pkgutil.iter_modules(db_package.__path__):
        importlib.import_module(f"api_server.db.{module.name}")


def _flatten(items: object) -> list:  # type: ignore[type-arg]
    """`compare_metadata` anida listas para los cambios de columna."""
    out = []
    for item in items:  # type: ignore[union-attr]
        if isinstance(item, list):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _table_of(item: tuple) -> str:  # type: ignore[type-arg]
    operation = item[0]
    if operation in ("add_table", "remove_table"):
        return str(getattr(item[1], "name", "?"))
    if operation in ("add_column", "remove_column") or operation.startswith("modify_"):
        return str(item[2])
    holder = getattr(item[1], "table", None)
    return str(getattr(holder, "name", "?"))


def _describe(item: tuple) -> str:  # type: ignore[type-arg]
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


async def _autogenerate_diff(url: str) -> list:  # type: ignore[type-arg]
    """El diff de autogenerate contra `url`, por asyncpg.

    **Motor ASÍNCRONO a propósito**: este repo no instala `psycopg2`, así que un
    `create_engine("postgresql://…")` muere con `ModuleNotFoundError` — un fallo
    que parece de configuración y es sólo de driver. `compare_metadata` es
    síncrono, así que va dentro de `run_sync`.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from api_server.db.base import Base
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: compare_metadata(
                    MigrationContext.configure(sync_connection, opts={"compare_type": True}),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def _domain_diff(alembic_config: object, url: str) -> tuple[list[str], int]:
    import asyncio

    from alembic import command

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    _import_every_db_module()

    items = _flatten(asyncio.run(_autogenerate_diff(url)))
    domain = sorted(_describe(item) for item in items if _table_of(item) in DOMAIN_TABLES)
    return domain, len(items)


def test_the_split_did_not_move_the_domain_schema(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """Sobre las 17 tablas de `db/domain`, el autogenerate no propone nada nuevo.

    Lo que protege del troceo de `task_prod16_11`: si al repartir `db/domain.py`
    en `db/domain/` se hubiera caído una columna, un `CheckConstraint` o un
    índice, la BD tendría algo que el modelo ya no declara y aparecería aquí un
    item que no está en el inventario.
    """
    domain, total = _domain_diff(alembic_config, admin_database_url)

    nuevos = [item for item in domain if item not in KNOWN_DOMAIN_DRIFT_2026_08_19]
    assert not nuevos, (
        "el autogenerate propone cambios NUEVOS sobre tablas del dominio — el "
        "modelo y las migraciones han divergido:\n" + "\n".join(f"  {n}" for n in nuevos)
    )

    muertos = sorted(KNOWN_DOMAIN_DRIFT_2026_08_19 - set(domain))
    assert not muertos, (
        "estas divergencias del inventario YA no existen (alguien las arregló). "
        "Bórralas de KNOWN_DOMAIN_DRIFT_2026_08_19:\n" + "\n".join(f"  {m}" for m in muertos)
    )

    assert total > 0, (
        "el autogenerate no devolvió NADA, ni siquiera la deriva conocida de "
        "índices/FK fuera del dominio: probablemente no llegó a comparar, y la "
        "guarda estaría pasando en vacío."
    )


def test_the_wider_schema_drift_is_written_down_not_forgotten(
    alembic_config: object,
    admin_database_url: str,
) -> None:
    """Que la deriva ancha exista NO es normal, y este test impide olvidarlo.

    Fuera del dominio hay más de un centenar de items —nombres de índice y de FK
    que las migraciones pusieron y el modelo no declara, `TEXT` frente a
    `String(n)`, e índices por partición de las cinco tablas del ADR 0151—.
    Limpiar eso es trabajo propio, no de este refactor. Lo que sí se puede hacer
    hoy es dejar el número medido y comprobar que no explota: si un día el diff se
    dispara, alguien metió una migración que el modelo no refleja.
    """
    _, total = _domain_diff(alembic_config, admin_database_url)

    assert total >= 50, (
        f"el diff bajó a {total} items: si alguien limpió la deriva, baja este "
        "número — y mira si ya se puede exigir el autogenerate vacío que pedía el "
        "enunciado de task_prod16_11"
    )
    assert total <= 250, (
        f"el diff subió a {total} items desde los ~150 medidos el 2026-08-19: "
        "alguien añadió esquema por migración sin reflejarlo en el modelo"
    )
