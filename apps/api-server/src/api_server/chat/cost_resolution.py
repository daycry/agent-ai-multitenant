"""Resolve the effective model + price catalog for a plan's cost breakdown.

The pure calculator (:func:`api_server.chat.cost.compute_ai_cost`) prices each
task against a model id. Historically the endpoint fed it a single hardcoded
``"gpt-4o"`` default, so EVERY task was priced as gpt-4o regardless of which
agent would actually run it.

This module bridges that gap with two DB-aware helpers, kept apart from the pure
calculator so the latter stays trivially unit-testable:

* :func:`resolve_plan_task_models` — for each spec task, find the team agent
  that owns the task's ``role`` and resolve its effective model through the
  inheritance chain **agent → team → project → platform** (ADR 0065, the SAME
  chain the orchestrator's dispatch uses). Returns ``{task_id: model_id}``;
  tasks without a role that maps to an agent are simply omitted (they fall back
  to the caller's ``default_model_id`` inside ``compute_ai_cost``).

* :func:`load_price_catalog` — build the cost :class:`PriceCatalog` from the
  open (current) rows of the ``model_prices`` table (Plan 11, the source of
  truth), layered over the in-code placeholder catalog so a freshly-installed
  platform with an empty table still prices the well-known models.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.cost import DEFAULT_AI_PRICE_CATALOG, ModelPrice, PriceCatalog
from api_server.chat.planning_graph import PlanningRole
from api_server.chat.responder import team_role_agents
from api_server.db.domain import Agent, Plan, Project, Team
from api_server.db.model_prices import ModelPrice as DBModelPrice
from api_server.db.platform_settings import (
    get_default_model_config,
    resolve_model_config_chain,
)


async def _team_model_config(session: AsyncSession, project: Project | None) -> dict[str, Any]:
    """``model_config`` del equipo del proyecto (nivel intermedio de la cadena de
    herencia). Vacío si el proyecto no tiene equipo. Mismo filtro por tenant que
    el dispatch como defensa en profundidad."""
    team_id = getattr(project, "team_id", None) if project else None
    if project is None or team_id is None:
        return {}
    team = (
        await session.execute(
            select(Team).where(Team.id == team_id, Team.tenant_id == project.tenant_id)
        )
    ).scalar_one_or_none()
    return dict(team.model_config or {}) if team is not None else {}


async def resolve_plan_task_models(session: AsyncSession, plan: Plan) -> dict[str, str]:
    """Map ``{spec_task_id: effective_model_id}`` for the tasks of ``plan``.

    For each task whose ``role`` maps to one of the team's agents, the effective
    model is resolved through the inheritance chain agent → team → project →
    platform (ADR 0065). Tasks without a role, or whose role has no team agent,
    are omitted from the result so the caller's ``default_model_id`` still
    applies to them. Returns ``{}`` when the project has no team (no agents to
    resolve against), which keeps legacy plans behaving exactly as before.
    """
    spec = plan.specification or {}
    tasks_raw = spec.get("tasks") or []
    if not tasks_raw:
        return {}

    project = (
        await session.execute(select(Project).where(Project.id == plan.project_id))
    ).scalar_one_or_none()

    role_agents = await team_role_agents(session, project)
    if not role_agents:
        return {}

    # Load every agent referenced by the role map once (model_config per agent).
    agent_ids = list(set(role_agents.values()))
    agent_rows = (
        await session.execute(select(Agent.id, Agent.model_config).where(Agent.id.in_(agent_ids)))
    ).all()
    agent_cfg_by_id = {aid: dict(cfg or {}) for aid, cfg in agent_rows}

    team_cfg = await _team_model_config(session, project)
    project_cfg = dict(getattr(project, "model_config", None) or {}) if project else {}
    platform_default = await get_default_model_config(session)

    resolved: dict[str, str] = {}
    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        role_str = str(task.get("role") or "").strip()
        if not task_id or not role_str:
            continue
        try:
            role = PlanningRole(role_str)
        except ValueError:
            continue
        agent_id = role_agents.get(role)
        if agent_id is None:
            continue
        effective = resolve_model_config_chain(
            agent_cfg_by_id.get(agent_id), team_cfg, project_cfg, platform_default
        )
        model_id = effective.get("model")
        if isinstance(model_id, str) and model_id:
            resolved[task_id] = model_id
    return resolved


async def load_price_catalog(session: AsyncSession) -> PriceCatalog:
    """Build the cost :class:`PriceCatalog` from the open ``model_prices`` rows.

    The DB catalog (Plan 11, USD, per-1M-token unit) is the source of truth; we
    layer it over the in-code placeholder so well-known models still price even
    on a platform whose ``model_prices`` table is empty (DB rows win on conflict).
    Only the current (``effective_to IS NULL``) text-modality rows are loaded.
    """
    rows = (
        (
            await session.execute(
                select(DBModelPrice).where(
                    DBModelPrice.effective_to.is_(None),
                    DBModelPrice.modality == "text",
                )
            )
        )
        .scalars()
        .all()
    )

    prices = dict(DEFAULT_AI_PRICE_CATALOG.prices)
    for row in rows:
        # The catalog is per-1M tokens by convention (the only unit the price
        # feed writes); skip any stray non-per-1M row rather than misprice it.
        if row.unit != "per_1m_tokens":
            continue
        prices[row.model_id] = ModelPrice(
            row.model_id,
            currency=row.currency,
            input_per_million=row.input_price,
            output_per_million=row.output_price,
        )
    return PriceCatalog(prices=prices)


__all__ = ["load_price_catalog", "resolve_plan_task_models"]
