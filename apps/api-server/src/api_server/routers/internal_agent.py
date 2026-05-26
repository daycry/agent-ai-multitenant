"""Router skeleton for `/internal/agent/*` (Plan 04.5 task_04_5_01).

This is the family of endpoints the agent-runtime sandbox calls back
into during a run — memory_recall, memory_store, rag_search, etc.
They share a dedicated auth dependency :func:`get_agent_principal`
that validates the sandbox-scoped JWT minted by the worker.

This task only ships the skeleton:

  - prefix `/internal/agent` so reverse proxies / network policies can
    block external traffic to the whole family with one rule
  - a single `GET /internal/agent/_health` endpoint that exercises
    the auth dep end-to-end (mint → request → 200)

The real tool endpoints land in task_04_5_03..05.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api_server.auth.internal_agent import AgentPrincipal, get_agent_principal

router = APIRouter(prefix="/internal/agent", tags=["internal-agent"])


@router.get("/_health")
async def health(
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> dict[str, str]:
    """Smoke endpoint that proves the agent-token auth dependency works.

    Returns the principal's `agent_id` and `tenant_id` so tests can
    assert the token was parsed correctly. No DB writes, no side
    effects — safe to keep enabled in all environments.
    """
    return {
        "status": "ok",
        "agent_id": str(principal.agent_id),
        "tenant_id": str(principal.tenant_id),
    }
