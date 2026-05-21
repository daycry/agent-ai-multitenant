"""Context-local request metadata bound to every log line via structlog.

`bind_request_context(...)` is called by the FastAPI middleware (next
task) at the start of each request. The bound values are stored in
contextvars so async tasks spawned from the request inherit them.
"""

from __future__ import annotations

from uuid import UUID

import structlog


def bind_request_context(
    *,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    project_id: UUID | None = None,
    request_id: str | None = None,
) -> None:
    """Attach per-request identifiers to every subsequent log line."""
    fields: dict[str, str | None] = {}
    if user_id is not None:
        fields["user_id"] = str(user_id)
    if tenant_id is not None:
        fields["tenant_id"] = str(tenant_id)
    if project_id is not None:
        fields["project_id"] = str(project_id)
    if request_id is not None:
        fields["request_id"] = request_id
    if fields:
        structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
