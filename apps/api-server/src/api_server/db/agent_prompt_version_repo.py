"""Repositorio de `agent_prompt_versions` — el historial del prompt (`task_gov_02`).

**Append-only en la capa, como `task_audit_repo`**: este módulo ofrece
deliberadamente sólo tres operaciones, y ninguna escribe sobre una fila que ya
existe.

  * :func:`record_prompt_change` — INSERTa la(s) fila(s) de una edición.
  * :func:`list_prompt_versions` — SELECT del historial de un agente.
  * :func:`latest_prompt_version` — SELECT de la última fila, para el dispatch.

No hay UPDATE ni DELETE. El invariante se sostiene aquí, y la tabla lo respalda:
no tiene `updated_at` (ver el docstring de
:class:`~api_server.db.domain.agents.AgentPromptVersion`).

## La fila de base, y por qué el orden de los INSERT no es el de la prosa

El enunciado de `task_gov_02` dice «el `PUT` inserta una fila ANTES de escribir».
Lo que hace falta de verdad —y es lo que hace :func:`record_prompt_change`— es que
quede registrado el estado **anterior** a la escritura, no que el `INSERT` salga
antes por el cable: las dos sentencias viajan en la MISMA transacción, así que el
orden entre ellas no lo puede observar nadie, y lo que ordena el historial es la
columna `version`.

Registrar sólo el estado nuevo dejaría el historial cojo por el extremo que más
importa: la primera edición no tendría contra qué diffear, que es justo el caso en
el que alguien va a mirar («¿qué le hicieron a este agente?»). Así que la primera
vez que un agente cambia de prompt se escriben **dos** filas: la `version 1` con
el prompt que había —``changed_by`` a NULL, que es el autor honesto de algo que
nadie apuntó— y la `version 2` con el nuevo y su autor.

De ahí en adelante, una edición es una fila.

## Por qué la detección de cambio va sobre los valores CRUDOS

Se comparan `system_prompt` y `model_config.system_prompts` tal cual, no el texto
efectivo. Si se comparase el efectivo, dos ediciones no se registrarían nunca:
tocar el idioma que la precedencia NO prefiere (`en` cuando hay `es`), y tocar el
prompt más allá de `PERSONA_MAX_CHARS`. Las dos son ediciones reales que un
auditor querrá ver, y las dos dejan el texto efectivo idéntico.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.agent_persona import effective_prompt_hash, prompt_text_hash
from api_server.db.domain.agents import AgentPromptVersion


def raw_prompt_snapshot(agent: Any) -> tuple[str, dict[str, Any]]:
    """Los valores CRUDOS que versiona esta tabla: `(system_prompt, persona)`.

    ``persona`` es ``model_config.system_prompts`` (``{}`` si el agente no es
    bilingüe). El resto de `model_config` —proveedor, modelo, temperatura— NO
    entra: cambiar de modelo no es cambiar el prompt, y contarlo llenaría el
    historial de versiones que no mueven una palabra de lo que lee el modelo.
    """
    flat = getattr(agent, "system_prompt", None)
    system_prompt = flat if isinstance(flat, str) else ""
    model_config = getattr(agent, "model_config", None)
    persona: dict[str, Any] = {}
    if isinstance(model_config, dict):
        prompts = model_config.get("system_prompts")
        if isinstance(prompts, dict):
            persona = dict(prompts)
    return system_prompt, persona


async def latest_prompt_version(session: AsyncSession, agent_id: UUID) -> AgentPromptVersion | None:
    """La última versión registrada de ``agent_id``, o ``None`` si no hay ninguna.

    `ORDER BY version DESC LIMIT 1` sobre `uq_agent_prompt_versions_agent_version`:
    recorrido hacia atrás del índice, sin `Sort`. El filtro por tenant lo pone la
    RLS de la sesión — este módulo no la puentea nunca.
    """
    result = await session.execute(
        select(AgentPromptVersion)
        .where(AgentPromptVersion.agent_id == agent_id)
        .order_by(AgentPromptVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_prompt_version_number(session: AsyncSession, agent_id: UUID) -> int | None:
    """El número de la última versión de ``agent_id``, o ``None`` si no hay ninguna.

    Existe aparte de :func:`latest_prompt_version` porque el dispatch corre en el
    camino caliente de CADA run y sólo necesita el número: traer la fila entera
    arrastraría el `system_prompt` completo por el cable para tirarlo.
    """
    value = await session.scalar(
        select(func.max(AgentPromptVersion.version)).where(AgentPromptVersion.agent_id == agent_id)
    )
    return int(value) if value is not None else None


async def list_prompt_versions(
    session: AsyncSession, agent_id: UUID, *, limit: int | None = None
) -> list[AgentPromptVersion]:
    """El historial de ``agent_id``, **más reciente primero**.

    Ese orden es el de la pantalla y el del diff: cada fila se compara con la
    anterior en el tiempo, o sea con la SIGUIENTE de esta lista.
    """
    stmt = (
        select(AgentPromptVersion)
        .where(AgentPromptVersion.agent_id == agent_id)
        .order_by(AgentPromptVersion.version.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _next_version(session: AsyncSession, agent_id: UUID) -> int:
    current = await session.scalar(
        select(func.max(AgentPromptVersion.version)).where(AgentPromptVersion.agent_id == agent_id)
    )
    return int(current or 0) + 1


def _append(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID,
    version: int,
    system_prompt: str,
    persona: dict[str, Any],
    prompt_hash: str,
    changed_by: UUID | None,
    parent_version_id: UUID | None,
) -> AgentPromptVersion:
    row = AgentPromptVersion(
        tenant_id=tenant_id,
        agent_id=agent_id,
        version=version,
        system_prompt=system_prompt,
        persona=persona,
        prompt_hash=prompt_hash,
        changed_by=changed_by,
        parent_version_id=parent_version_id,
    )
    session.add(row)
    return row


async def record_prompt_change(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent: Any,
    before: tuple[str, dict[str, Any]],
    before_effective_prompt: str,
    changed_by: UUID | None,
) -> list[AgentPromptVersion]:
    """Registra la edición del prompt de ``agent``, ya aplicada sobre el objeto.

    ``before`` es :func:`raw_prompt_snapshot` de ANTES de la escritura y
    ``before_effective_prompt`` el texto efectivo de antes (que el llamante ya
    tiene resuelto y no merece recalcular). Devuelve las filas escritas: dos la
    primera vez —base + nueva—, una a partir de entonces.

    **No comprueba si hubo cambio**: eso lo decide el llamante, que es quien tiene
    el antes y el después. Llamarla sin cambio escribiría una versión igual a la
    anterior, que es exactamente el ruido que el historial no debe tener.
    """
    written: list[AgentPromptVersion] = []
    parent = await latest_prompt_version(session, agent.id)
    version = await _next_version(session, agent.id)

    if parent is None:
        # Primera edición de este agente: la fila de base con lo que había.
        # `changed_by=None` porque nadie apuntó quién escribió ese prompt —
        # atribuírselo a quien edita hoy sería inventar un autor.
        old_prompt, old_persona = before
        parent = _append(
            session,
            tenant_id=tenant_id,
            agent_id=agent.id,
            version=version,
            system_prompt=old_prompt,
            persona=old_persona,
            prompt_hash=prompt_text_hash(before_effective_prompt),
            changed_by=None,
            parent_version_id=None,
        )
        written.append(parent)
        # `flush` aquí y no al final: la fila nueva apunta a ésta por
        # `parent_version_id`, y sin el flush el id todavía es None.
        await session.flush()
        version += 1

    new_prompt, new_persona = raw_prompt_snapshot(agent)
    row = _append(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        version=version,
        system_prompt=new_prompt,
        persona=new_persona,
        prompt_hash=effective_prompt_hash(agent),
        changed_by=changed_by,
        parent_version_id=parent.id,
    )
    written.append(row)
    await session.flush()
    return written


async def record_initial_version(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent: Any,
    changed_by: UUID | None,
) -> AgentPromptVersion | None:
    """La `version 1` de un agente recién creado, con su autor de verdad.

    Existe para que el historial de los agentes nacidos a partir de hoy empiece
    con un autor conocido, en vez de con el NULL honesto de la fila de base que
    :func:`record_prompt_change` tiene que inventar para los que ya existían.

    Devuelve ``None`` —sin escribir— cuando el agente nace sin prompt ninguno: una
    `version 1` vacía no es historial, es una fila que hay que explicar. La
    escribirá la primera edición como fila de base.
    """
    system_prompt, persona = raw_prompt_snapshot(agent)
    if not system_prompt.strip() and not persona:
        return None
    row = _append(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id,
        version=1,
        system_prompt=system_prompt,
        persona=persona,
        prompt_hash=effective_prompt_hash(agent),
        changed_by=changed_by,
        parent_version_id=None,
    )
    await session.flush()
    return row
