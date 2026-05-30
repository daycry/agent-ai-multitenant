"""Public v1 REST API (Plan 13 task_13_05, Fase B).

The ``/api/v1`` surface is the public, versioned REST contract a tenant's
external tooling (CI, CRM, issue trackers, monitoring) talks to. It is a
THIN, scope-checked FACADE over the existing domain (projects / plans /
tasks / conversations / knowledge bases) — it adds no business logic of
its own, it re-exposes the same rows the interactive UI sees but through a
DIFFERENT auth path:

  * authenticated by the Fase A ``X-API-Token`` HEADER dependency (NOT the
    JWT/session auth), which resolves the token to its tenant, enforces
    lifecycle / IP-allowlist / per-token rate limit, and yields a
    TENANT-SCOPED RLS session — a tenant-A token can never read or write
    tenant-B data (Plan 13 Decisiones Clave);
  * versioned in the PATH (``/api/v1/...``), not a header;
  * scope-checked: a ``read``-scope token may only GET; a ``write``-scope
    token may also create.

Every list endpoint is paginated (``limit``/``offset`` with ``ge``/``le``
bounds) so a tenant with thousands of rows cannot pull an unbounded
response.
"""

from __future__ import annotations

from api_server.routers.api_v1.openapi import api_v1_docs_router
from api_server.routers.api_v1.router import api_v1_router

__all__ = ["api_v1_docs_router", "api_v1_router"]
