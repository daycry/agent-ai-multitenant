"""Qué objetos de la BD debe mirar el autogenerate de Alembic — y por qué no el resto.

## La diferencia entre configurar y tapar

Esto es un `include_object`, o sea un filtro sobre lo que `alembic check` y
`alembic revision --autogenerate` comparan. Un filtro así puede ser dos cosas
opuestas:

* **Configuración legítima**: el objeto existe en la BD a propósito y **no puede
  tener modelo**, así que compararlo produce un item de diff que nunca se va a
  poder cerrar. Dejarlo dentro convierte el veredicto en ruido permanente, que
  es la vía rápida a que nadie mire el veredicto.
* **Un ignore que tapa deriva**: el objeto sí debería estar en el modelo y se
  excluye para poner la comprobación en verde.

Todo lo de este módulo es del primer tipo, y **cada exclusión lleva escrito su
motivo** justo por eso: para que la siguiente persona pueda auditar la diferencia
sin arqueología. Añadir una entrada exige que el motivo diga por qué el objeto no
puede tener modelo — no basta con que hoy no lo tenga.

Lo que **NO** se excluye aquí, y hace falta decirlo porque es la tentación
obvia: los 39 índices y las 23 claves ajenas que las migraciones crearon y el
modelo no declara. Ésos son deriva de verdad (categoría (a) del inventario de
`tests/integration/test_alembic_autogenerate_clean.py`) y son **peligrosos**: un
`alembic revision --autogenerate` corrido hoy propone `DROP INDEX` sobre
`ix_chunks_embedding_hnsw` (el índice HNSW del RAG) y sobre `ix_chunks_content_fts`.
Esconderlos aquí dejaría `alembic check` verde el día que alguien borre de verdad
el índice vectorial.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

#: Tablas que viven en la BD **a propósito y sin modelo**. Clave → motivo.
#:
#: Requisito para añadir una entrada: explicar por qué la tabla no puede tener
#: modelo. «Todavía no lo tiene» no vale — eso es deriva, y va al inventario del
#: test de integración, no aquí.
TABLES_WITHOUT_A_MODEL: dict[str, str] = {
    "approval_policy_backfill_0133": (
        "Respaldo interno que crea la migración 0133 con la política de aprobación "
        "anterior de cada proyecto, para poder deshacer el backfill. La aplicación "
        "no lo consulta y NO debe poder consultarlo: la migración 0138 le retira "
        "todo acceso, y `tests/integration/conftest.py::_APP_REVOKED_TABLES` "
        "reproduce ese revoke en el arnés. Un modelo aquí sería un camino de "
        "lectura que el diseño quiere cerrado."
    ),
    "agent_tools_backfill_0145": (
        "Mismo caso que la de arriba, y con la misma pareja de garantías. La "
        "migración 0145 propaga `stack_exec` a las copias de tenant de los "
        "agentes built-in, y anota fila a fila lo que insertó para que el "
        "`downgrade` retire EXACTAMENTE eso y no lo recalcule — recalcular "
        "borraría también los grants que ya existían antes (los que un humano "
        "parcheó a mano en junio y julio). La aplicación no la consulta y NO "
        "debe poder: la propia 0145 le retira todo acceso al crearla, y "
        "`tests/integration/conftest.py::_APP_REVOKED_TABLES` reproduce ese "
        "revoke en el arnés. Un modelo aquí sería un camino de lectura hacia "
        "un respaldo que el diseño quiere cerrado."
    ),
    "agent_tools_backfill_0146": (
        "Gemela de la de arriba, con la misma pareja de garantías y por el mismo "
        "motivo estructural. La migración 0146 propaga `move_file` a las copias "
        "de tenant de los agentes built-in que ya tenían `write_file` Y "
        "`delete_file`, y anota fila a fila lo que insertó para que el "
        "`downgrade` retire EXACTAMENTE eso en vez de recalcularlo — recalcular "
        "se llevaría por delante los grants que un administrador del tenant "
        "pusiera después, indistinguibles de los de la migración. La aplicación "
        "no la consulta y NO debe poder: la propia 0146 le retira todo acceso al "
        "crearla, y `tests/integration/conftest.py::_APP_REVOKED_TABLES` "
        "reproduce ese revoke en el arnés. Un modelo aquí sería un camino de "
        "lectura hacia un respaldo que el diseño quiere cerrado."
    ),
    "agents_model_config_backfill_0147": (
        "Misma familia que las tres de arriba. La migración 0147 despinea "
        "`provider=anthropic` —un kind que no existe en el catálogo cerrado— en "
        "las copias de tenant de los agentes built-in, y guarda el `model_config` "
        "ENTERO anterior para que el `downgrade` lo restaure tal cual en vez de "
        "recalcularlo (una copia despineada por la migración y una que un "
        "administrador despineó a mano son indistinguibles después). La "
        "aplicación no la consulta y NO debe poder: la 0147 le retira todo acceso "
        "al crearla, y `tests/integration/conftest.py::_APP_REVOKED_TABLES` "
        "reproduce ese revoke en el arnés."
    ),
}

#: Consulta las relaciones que son **partición** de otra (tablas e índices).
#:
#: `relispartition` es la propiedad del catálogo, no un patrón de nombre: las
#: particiones las crea el planificador del ADR 0151 mes a mes
#: (`llm_usage_events_2026_11`, `audit_log_2026_12`, …), así que una lista o un
#: regex de nombres envejecería cada mes. El modelo declara los CINCO padres
#: particionados y no puede declarar los hijos, que aparecen y se retiran solos.
_PARTITION_CHILDREN_SQL = text("""
    SELECT c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema()
       AND c.relispartition
""")

#: Firma de `include_object` tal como la llama Alembic:
#: ``(object, name, type_, reflected, compare_to) -> bool``.
IncludeObject = Callable[[Any, str | None, str, bool, Any], bool]


def partition_children(connection: Connection) -> frozenset[str]:
    """Nombres de las relaciones que son partición de otra, en el esquema actual."""
    return frozenset(row[0] for row in connection.execute(_PARTITION_CHILDREN_SQL))


def make_include_object(partitions: frozenset[str]) -> IncludeObject:
    """El `include_object` de este proyecto, con `partitions` ya resuelto.

    Se construye con el conjunto ya calculado (en vez de consultarlo dentro) para
    que la decisión sea una función pura y se pueda probar sin base de datos.
    """

    def include_object(
        object_: Any,
        name: str | None,
        type_: str,
        reflected: bool,
        # Alembic lo pasa por posicion y esta politica no lo mira: la decision
        # depende del TIPO y del nombre del objeto, no de con que se compara.
        # Va con guion bajo porque la firma la fija Alembic y no se puede acortar.
        _compare_to: Any,
    ) -> bool:
        if type_ == "table":
            if name in partitions:
                return False
            # Sólo si viene de la BD: un MODELO con este nombre sí hay que
            # compararlo — sería alguien dándole modelo a una tabla que el
            # diseño quiere sin él, y eso debe salir en el diff.
            if reflected and name in TABLES_WITHOUT_A_MODEL:
                return False
        elif type_ == "index":
            if name in partitions:
                return False
            holder = getattr(object_, "table", None)
            if holder is not None and getattr(holder, "name", None) in partitions:
                return False
        return True

    return include_object
