"""Despinea `provider="anthropic"` en las COPIAS de tenant de los agentes built-in.

## Qué se rompió, medido

Auditoría del 2026-09-01 (F-01). Once agentes core del catálogo
(`seeds/builtin_agents.py`) llevaban `model_config.provider = "anthropic"`, un
kind que NO existe en `LLMProviderKind` (`claude_sdk` / `copilot` /
`azure_foundry` / `ollama`, ADR 0021). La auditoría del 2026-07-16 lo anotó como
inocuo —«ningún run los usa directamente»— y la premisa era falsa: al ADOPTAR un
equipo, `routers/teams.py` copia `model_config` verbatim a las copias de tenant;
la cadena de herencia del ADR 0055 (`resolve_model_config`) se salta porque el
agente «pinea» provider+model; y el worker (`model_resolver.py`) no encuentra
ninguna fila de `llm_providers` de kind `anthropic` → `model_unresolved` antes de
arrancar. Explica por qué el recorrido E2E sólo ha prosperado con el equipo
CodeIgniter: es el único que no pinea.

El seed deja de pinear desde este mismo commit y el refresco de arranque
reescribe los built-ins. Pero **un seed no toca los datos de tenant**: las copias
adoptadas viven en la tabla `agents` de cada tenant y ningún re-seed las
reescribe. Sin esta migración el arreglo no llegaría a ningún equipo ya adoptado.

## La condición que hace esto seguro: sólo lo heredado de fábrica, y sólo el pin

  * Sólo COPIAS de un built-in (`forked_from_agent_id` → `scope =
    'global_builtin'`). Un agente que el tenant creó desde cero, o copió de una
    plantilla suya, es suyo entero aunque pinee `anthropic` — se respeta.
  * Sólo si el pin es EXACTAMENTE el de fábrica (`provider = 'anthropic'`). Una
    copia que un administrador re-pineó a `claude_sdk`/`ollama` a mano no se toca:
    ese pin es una decisión suya y funciona.
  * Sólo se retiran las tres claves del pin (`provider`, `model`, `temperature`);
    `system_prompts` y cualquier otra clave se conservan. La copia pasa a HEREDAR
    (proyecto → equipo → plataforma), que es lo que hace CI4 y lo que el ADR 0055
    quiere.

Cada `model_config` anterior se guarda ENTERO en `agents_model_config_backfill_0147`
y el `downgrade` lo restaura tal cual, fila a fila. No se recalcula: después del
`upgrade`, una copia despineada por esta migración y una que un administrador
despineó a mano son indistinguibles.

La tabla de respaldo nace SIN acceso para la aplicación (`REVOKE`), como la 0133,
la 0145 y la 0146: los default privileges de `docker/postgres/init/02-roles.sh`
alcanzan a toda tabla que Alembic cree, y una tabla sin `tenant_id` ni RLS
nacería legible cross-tenant.

Idempotente: re-ejecutar no encuentra a nadie con `provider = 'anthropic'`.

Revision ID: 0147_unpin_anthropic_builtin_forks
Revises: 0146_move_file_builtin_forks
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0147_unpin_anthropic_builtin_forks"
down_revision: str | Sequence[str] | None = "0146_move_file_builtin_forks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_TABLE = "agents_model_config_backfill_0147"
_BACKFILL_COMMENT = (
    "Respaldo de la 0147: el model_config ENTERO que tenia cada copia de agente "
    "built-in antes de despinear provider=anthropic. El downgrade lo restaura tal "
    "cual. Sin ella la migracion es irreversible."
)
_INVALID_PROVIDER = "anthropic"
_APPLICATION_ROLES = ("app_user", "service_user")


def _revoke_backfill_from_app() -> None:
    for role in _APPLICATION_ROLES:
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
                   AND to_regclass('public.{BACKFILL_TABLE}') IS NOT NULL THEN
                    EXECUTE 'REVOKE ALL ON TABLE public.{BACKFILL_TABLE} FROM {role}';
                END IF;
            END $$;
            """)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {BACKFILL_TABLE} (
            agent_id     uuid        NOT NULL PRIMARY KEY,
            model_config jsonb       NOT NULL,
            unpinned_at  timestamptz NOT NULL DEFAULT now()
        )
        """)
    op.execute(f"COMMENT ON TABLE {BACKFILL_TABLE} IS '{_BACKFILL_COMMENT}'")
    _revoke_backfill_from_app()

    # 1) Respaldo ENTERO de lo que se va a tocar, antes de tocarlo.
    op.execute(f"""
        INSERT INTO {BACKFILL_TABLE} (agent_id, model_config)
        SELECT a.id, a.model_config
          FROM agents a
          JOIN agents src ON src.id = a.forked_from_agent_id
         WHERE a.deleted_at IS NULL
           AND src.scope = 'global_builtin'
           AND a.model_config ->> 'provider' = '{_INVALID_PROVIDER}'
        ON CONFLICT (agent_id) DO NOTHING
        """)

    # 2) Retirar SÓLO el pin de fábrica; el resto del model_config se conserva.
    op.execute(f"""
        UPDATE agents a
           SET model_config = (a.model_config - 'provider' - 'model' - 'temperature')
          FROM {BACKFILL_TABLE} b
         WHERE a.id = b.agent_id
           AND a.model_config ->> 'provider' = '{_INVALID_PROVIDER}'
        """)


def downgrade() -> None:
    """Restaura EXACTAMENTE el model_config que tenía cada copia, desde el respaldo."""
    op.execute(f"""
        UPDATE agents a
           SET model_config = b.model_config
          FROM {BACKFILL_TABLE} b
         WHERE a.id = b.agent_id
        """)
    op.execute(f"DROP TABLE IF EXISTS {BACKFILL_TABLE}")
