"""Conversational personal assistant (Plan 10 task_10_14).

A specialised conversational agent that answers a Tenant Admin's
questions about the tenant's cross-project global state. It is NOT a new
LLM stack — it reuses the LangGraph + shared-llm seam introduced in
Plan 03 (see ``api_server.chat.planning_graph`` for the sibling pattern).

Access is doubly gated (binding constraints, see the plan):

  * role=admin of the tenant ONLY — a tenant_user / member is 403.
  * ``Organization.personal_assistant_enabled`` (DEFAULT false) — if the
    toggle is off, even a Tenant Admin is denied (403/disabled).

Every read tool is tenant-scoped (runs under the asking admin's
RLS-bound session) so a tool can NEVER return another tenant's data.
"""

from __future__ import annotations

from api_server.assistant.config import (
    DEFAULT_ENABLED_TOOLS,
    AssistantIdentity,
    get_assistant_identity,
    set_assistant_identity,
)
from api_server.assistant.graph import (
    AssistantModelClient,
    AssistantTurnResult,
    ScriptedAssistantModel,
    run_assistant_turn,
)
from api_server.assistant.tools import (
    ASSISTANT_TOOLS,
    AssistantToolContext,
    run_assistant_tool,
)

__all__ = [
    "ASSISTANT_TOOLS",
    "DEFAULT_ENABLED_TOOLS",
    "AssistantIdentity",
    "AssistantModelClient",
    "AssistantToolContext",
    "AssistantTurnResult",
    "ScriptedAssistantModel",
    "get_assistant_identity",
    "run_assistant_tool",
    "run_assistant_turn",
    "set_assistant_identity",
]
