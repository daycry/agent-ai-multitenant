"""agents.model_config: sanear el spec vacío {} legacy con el default seguro
(Plan 06.17 task_06_17_10 / ADR 0055).

El ``model_config`` de un agente (la pata SER: proveedor/modelo/temperatura)
nacía a menudo ``{}`` porque ningún diálogo de la UI lo enviaba. En dispatch ese
``{}`` se traducía a un spec de modelo vacío que podía hacer fallar el arranque
del run — un fallo tardío y opaco. El ADR 0055 (opción M-B) decide validar lo
nuevo (422 fuera de catálogo), rellenar un default explícito al crear, aplicar un
default seguro en dispatch para legacy ``{}``, y **sanear las filas existentes**
por esta migración: cada fila ``agents`` con ``model_config = {}`` recibe el
default seguro del catálogo cerrado del ADR 0021, de modo que el estado en BD deja
de tener specs vacíos.

El default literal aquí se mantiene en LOCKSTEP con
``api_server.db.platform_settings.DEFAULT_MODEL_CONFIG`` (la migración NO importa
el módulo de aplicación a propósito: una migración debe ser estable frente a
cambios futuros del default de código). El catálogo cerrado del ADR 0021
garantiza que ``claude_sdk`` es un proveedor válido y estable.

Reversible: el ``downgrade`` restaura ``{}`` EXACTAMENTE en las filas cuyo
``model_config`` es idéntico al default que el ``upgrade`` escribió (las que
saneó), sin tocar las que ya tenían un spec explícito. Solo afecta filas que
coinciden bit a bit con el default, así una fila que un usuario configuró
casualmente igual al default es un caso aceptado (vuelve a ``{}`` y el dispatch
le re-aplica el mismo default seguro — comportamiento idéntico).

Revision ID: 0081_model_config_sanitize
Revises: 0080_documents_indexed_empty
Create Date: 2026-06-04
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op

revision: str = "0081_model_config_sanitize"
down_revision: str | Sequence[str] | None = "0080_documents_indexed_empty"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Default seguro del catálogo cerrado (ADR 0021). LOCKSTEP con
# api_server.db.platform_settings.DEFAULT_MODEL_CONFIG. Ordenamos las claves para
# que el JSON escrito sea determinista (comparable bit a bit en el downgrade).
_DEFAULT_MODEL_CONFIG = {
    "provider": "claude_sdk",
    "model": "claude-sonnet-4",
    "temperature": 0.2,
}
_DEFAULT_JSON = json.dumps(_DEFAULT_MODEL_CONFIG, sort_keys=True)


def upgrade() -> None:
    # Sanea SOLO las filas con un spec vacío {} (no toca specs explícitos ni
    # specs incompletos no vacíos — esos los resuelve el default seguro en
    # dispatch). ``model_config = '{}'::jsonb`` compara el valor JSONB, no el
    # texto, así que es robusto frente al formato.
    op.execute(
        f"""
        UPDATE agents
        SET model_config = '{_DEFAULT_JSON}'::jsonb
        WHERE model_config = '{{}}'::jsonb
        """
    )


def downgrade() -> None:
    # Restaura {} en las filas que el upgrade saneó (las que coinciden bit a bit
    # con el default que escribimos). ``@>`` + ``<@`` comprueba igualdad de
    # contenido JSONB independientemente del orden de claves.
    op.execute(
        f"""
        UPDATE agents
        SET model_config = '{{}}'::jsonb
        WHERE model_config @> '{_DEFAULT_JSON}'::jsonb
          AND model_config <@ '{_DEFAULT_JSON}'::jsonb
        """
    )
