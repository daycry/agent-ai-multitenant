"""Helpers que aplican `Project.default_kb_grants` al crear un
proyecto desde una plantilla (Plan 06.9 task_06_9_07).

El flow real de "adoptar plantilla" lo cablea Plan 03 (wizard) o un
follow-up. Lo que **sí** vive aquí (y lo que el wizard tiene que
llamar) es:

  apply_template_kb_grants(session, *, template_id, new_project_id,
                            tenant_id, granted_by)

Que lee `default_kb_grants` del template (lista de slugs) → resuelve
cada slug al UUID de la KB built-in correspondiente → crea filas en
`kb_projects` para el nuevo proyecto. Idempotente: si el grant ya
existe (re-adopción) NO duplica.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds.builtin_kbs import kb_id_for_slug


async def apply_template_kb_grants(
    session: AsyncSession,
    *,
    template_id: UUID,
    new_project_id: UUID,
    tenant_id: UUID,
    granted_by: UUID | None = None,
) -> list[UUID]:
    """Lee `default_kb_grants` del template y crea los `kb_projects`
    para `new_project_id`. Devuelve la lista de KB ids granteadas.

    Slugs que NO resuelven a un KB existente se ignoran silenciosamente
    (no rompen la adopción si el catálogo built-in se ha re-seedeado
    parcialmente). El wizard puede comparar el retorno con la longitud
    esperada para detectar drift y avisar al operador.
    """
    row = (
        await session.execute(
            text("SELECT default_kb_grants FROM projects WHERE id = :tid AND is_template = true"),
            {"tid": str(template_id)},
        )
    ).first()
    if row is None or not row[0]:
        return []

    granted: list[UUID] = []
    for slug in row[0]:
        kb_id = kb_id_for_slug(slug)
        # Skip slugs whose KB row doesn't exist in this DB.
        kb_exists = (
            await session.execute(
                text("SELECT 1 FROM knowledge_bases WHERE id = :kid AND deleted_at IS NULL"),
                {"kid": str(kb_id)},
            )
        ).scalar_one_or_none()
        if kb_exists is None:
            continue

        # Idempotent — composite PK on kb_projects makes the conflict
        # handler trivial.
        await session.execute(
            text(
                "INSERT INTO kb_projects (kb_id, project_id, tenant_id, granted_by)"
                " VALUES (:kid, :pid, :tid, :gby)"
                " ON CONFLICT (kb_id, project_id) DO NOTHING"
            ),
            {
                "kid": str(kb_id),
                "pid": str(new_project_id),
                "tid": str(tenant_id),
                "gby": str(granted_by) if granted_by is not None else None,
            },
        )
        granted.append(kb_id)
    return granted


__all__ = ["apply_template_kb_grants"]
