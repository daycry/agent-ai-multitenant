# api-server

FastAPI service for the agentic platform. Owns:

- Auth (register, login, logout, session, RBAC).
- Multi-tenant middleware (sets `app.tenant_id` per request so RLS applies).
- Admin endpoints (`/admin/tenants`, `/admin/users`, `/admin/system-health`).

Phase 0 (`docs/roadmap/00-fundaciones.md`) ships the scaffold; richer
domain endpoints arrive in phase 1.

## Layout

```
apps/api-server/
├── pyproject.toml
├── src/api_server/
│   ├── db/
│   │   ├── base.py         # DeclarativeBase + mixins
│   │   └── models.py       # Organization, User, Membership, Session, AuditLog
│   └── ...                 # routers, middleware, auth, schemas (later tasks)
└── tests/                  # owned by repo-root tests/ — this dir stays empty
```

Tests live under the repo-root `tests/` so a single `pytest` run from
the project root covers every app.

## Local install (already wired into scripts/dev/bootstrap.\*)

```bash
./.venv/bin/python -m pip install -e "apps/api-server[dev]"
```
