"""Retira del catálogo VIVO las cuatro tools `run_*` (F5, ADR 0093 D3).

`run_pytest` / `run_lint` / `run_typecheck` / `run_build` son `docker_command`, y
`DockerCommandTool` dentro del sandbox **falla siempre por diseño**: la imagen del
agent-runtime «carries NO Docker client» y no recibe socket. Se anunciaban al
modelo, se invocaban y morían — el mismo fallo B-04 de `send_notification`, con 62
grants vivos el día de la retirada y un turno quemado por invocación. La vía que
sí ejecuta el toolchain es `stack_exec`: el worker lo corre en el runtime-template
del proyecto.

El código ya salió del catálogo (`builtin_tools.py`) y del conjunto ejecutable
(`RUNTIME_WIRED_TOOL_NAMES`), pero **`seed_builtin_tools` solo hace upsert, nunca
poda**: sin esta migración las cuatro filas —y sus grants— sobrevivirían en toda
instalación existente.

## Por qué SOFT-delete y por qué NO se tocan los grants

Se marca `deleted_at` en las cuatro filas y **no se borra ni una fila de
`agent_tools`**. No es dejar el trabajo a medias, es lo que hace la retirada
reversible de verdad:

  * todos los caminos que importan ya filtran por `Tool.deleted_at IS NULL`
    (`serialize_agent_tool_specs`, `set_agent_tools`), así que los grants quedan
    **inertes** en el mismo momento en que la fila se marca borrada: no se
    anuncian al modelo ni se pueden reasignar;
  * borrar los grants sería **irreversible** — nadie guarda qué agente tenía qué,
    así que un `downgrade` no podría devolverlos. CLAUDE.md prohíbe promover una
    migración sin `downgrade` probado, y un downgrade que restaura las tools pero
    pierde sus asignaciones no es una vuelta atrás, es otra pérdida.

Así, `downgrade` restaura el estado ANTERIOR completo: limpia `deleted_at` y los
grants —que nunca se fueron— vuelven a estar operativos.

El precio es cosmético y consciente: quedan filas en `agent_tools` apuntando a
tools borradas. Si algún día molestan, purgarlas es un `DELETE` de operador con la
copia delante, no una migración.

Revision ID: 0122_retire_run_tools
Revises: 0121_execution_pending_guidance
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0122_retire_run_tools"
down_revision: str | Sequence[str] | None = "0121_execution_pending_guidance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Los nombres CANÓNICOS de las cuatro. Se filtra además por
#: `implementation_type = 'docker_command'` e `is_builtin = true` para no tocar
#: jamás una tool de tenant que se llame igual: el catálogo built-in vive en el
#: tenant de plataforma, pero un tenant pudo crear su propia `run_pytest` (que sí
#: se ejecuta, porque su tipo la cablea `register_tool_specs`).
_RETIRED = ("run_pytest", "run_lint", "run_typecheck", "run_build")


def upgrade() -> None:
    op.execute(
        """
        UPDATE tools
           SET deleted_at = now()
         WHERE is_builtin = true
           AND implementation_type = 'docker_command'
           AND deleted_at IS NULL
           AND name IN ('run_pytest', 'run_lint', 'run_typecheck', 'run_build')
        """
    )


def downgrade() -> None:
    # Reversible: las filas siguen ahí y sus grants nunca se borraron, así que
    # limpiar el marcador devuelve el estado anterior tal cual. El `IS NOT NULL`
    # evita resucitar una fila que un operador hubiera borrado por su cuenta
    # antes de esta migración… y sí, también la resucitaría; es el precio de no
    # guardar el timestamp previo, y es preferible a dejar el downgrade vacío.
    op.execute(
        """
        UPDATE tools
           SET deleted_at = NULL
         WHERE is_builtin = true
           AND implementation_type = 'docker_command'
           AND deleted_at IS NOT NULL
           AND name IN ('run_pytest', 'run_lint', 'run_typecheck', 'run_build')
        """
    )
