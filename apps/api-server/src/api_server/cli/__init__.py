"""Administrative CLI for the api-server (`python -m api_server.cli <command>`).

Platform-global operations a System Admin runs INSIDE the api-server container —
things that are not a request, have no tenant, and must not be reachable over
HTTP. The first inhabitant is ``reencrypt-secrets`` (prod-05 task_prod05_02), the
middle step of every at-rest key rotation.

Why a CLI and not an admin endpoint: the operation walks EVERY tenant's rows and
rewrites them, it can take minutes, and it needs the BYPASSRLS engine. Exposing
that over HTTP would put "rewrite every stored secret on the platform" one auth
bug away from a tenant. The container shell is already the trust boundary for
this class of work (the same reasoning as ``python -m api_server.seeds``).
"""

from __future__ import annotations

__all__: list[str] = []
