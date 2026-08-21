"""El sello de embeddings guardado deja de ser una etiqueta que Ollama no conoce.

ADR 0155 (`task_audit14_05`). `knowledge_bases.embedding_model_id` guardaba
``'nomic-embed-text-v1.5'`` por *server default*, y ese string **no es un tag
válido de Ollama**: pedirlo devuelve «model not found». Lo que la plataforma
manda de verdad a ``/api/embed`` es ``nomic-embed-text`` — el valor de
``API_SERVER_EMBEDDING_MODEL``. O sea que la columna llevaba desde el Plan 04
guardando el nombre de un modelo con el que **nunca** se generó un solo vector.

El ADR 0155 ya cerró la parte que se ve: la API canoniza el sello **en lectura**
(`ingestion/embedding_contract.py`), así que la pantalla dejó de mentir el mismo
día. Lo que quedaba desfasado era el valor **almacenado**, que es lo que ve quien
abre la tabla con `psql` o restaura un backup. Esta migración lo alinea.

Medido antes de escribirla, contra la base de datos del stack: **14 filas** con
la etiqueta antigua y **cero chunks** detrás de ellas. Por eso el `UPDATE` es
seguro: no re-sella ningún vector existente, porque no hay ninguno. Re-sellar una
KB **con** chunks sigue siendo un 409 en la API, que es donde tiene que estar la
regla.

**El downgrade repone el default anterior pero NO deshace el `UPDATE`**, y es
deliberado: revertirlo volvería a escribir en las filas una etiqueta que no
identifica a ningún modelo servible. Un downgrade que restaura un dato roto no es
reversibilidad, es simetría mal entendida.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0141_kb_embedding_canonical"
down_revision: str | Sequence[str] | None = "0140_cortex_owner_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: La etiqueta que se guardaba y que Ollama no conoce.
_ALIAS_HEREDADO = "nomic-embed-text-v1.5"

#: El nombre real en el registro de Ollama, y el default de
#: ``API_SERVER_EMBEDDING_MODEL``.
_NOMBRE_CANONICO = "nomic-embed-text"


def upgrade() -> None:
    # Idempotente por el WHERE: correrla dos veces no toca nada la segunda.
    op.execute(
        "UPDATE knowledge_bases"
        f"    SET embedding_model_id = '{_NOMBRE_CANONICO}'"
        f"  WHERE embedding_model_id = '{_ALIAS_HEREDADO}'"
    )
    op.execute(
        "ALTER TABLE knowledge_bases"
        f"  ALTER COLUMN embedding_model_id SET DEFAULT '{_NOMBRE_CANONICO}'"
    )


def downgrade() -> None:
    # Sólo el default. Ver el docstring: reponer el alias en las FILAS sería
    # volver a escribir un modelo que no existe.
    op.execute(
        "ALTER TABLE knowledge_bases"
        f"  ALTER COLUMN embedding_model_id SET DEFAULT '{_ALIAS_HEREDADO}'"
    )
