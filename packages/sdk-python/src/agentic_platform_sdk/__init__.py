"""Official Python SDK for the Agentic Platform public v1 API (Plan 13).

Typed client over the published ``/api/v1`` OpenAPI 3.1 contract:

  * :class:`ApiClient` — construct with a base URL + an ``X-API-Token``,
    call the v1 endpoints, get back typed models.
  * The Pydantic v2 models (:mod:`agentic_platform_sdk.models`) are
    GENERATED from the committed ``openapi-v1.json`` via
    ``scripts/generate.py`` (datamodel-code-generator). Re-run that script
    whenever the public contract changes.

Quickstart::

    from agentic_platform_sdk import ApiClient, V1ProjectCreateRequest

    with ApiClient("https://platform.example.com", "tkn_...") as api:
        projects = api.list_projects(limit=50)
        new = api.create_project(V1ProjectCreateRequest(name="My project"))
"""

from __future__ import annotations

from agentic_platform_sdk.client import API_TOKEN_HEADER, ApiClient, ApiError
from agentic_platform_sdk.models import (
    ChatMode,
    ConversationResponse,
    KnowledgeBaseResponse,
    PlanResponse,
    PlanStatus,
    ProjectResponse,
    ProjectStatus,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    V1ConversationCreateRequest,
    V1KnowledgeBaseCreateRequest,
    V1PlanCreateRequest,
    V1ProjectCreateRequest,
    V1TaskCreateRequest,
)

__version__ = "0.1.0"

__all__ = [
    "API_TOKEN_HEADER",
    "ApiClient",
    "ApiError",
    "ChatMode",
    "ConversationResponse",
    "KnowledgeBaseResponse",
    "PlanResponse",
    "PlanStatus",
    "ProjectResponse",
    "ProjectStatus",
    "TaskPriority",
    "TaskResponse",
    "TaskStatus",
    "V1ConversationCreateRequest",
    "V1KnowledgeBaseCreateRequest",
    "V1PlanCreateRequest",
    "V1ProjectCreateRequest",
    "V1TaskCreateRequest",
    "__version__",
]
