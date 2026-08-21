"""Seed para los tests humanos del Plan 06.9 — Agent-scoped KBs.

Re-usa el usuario `admin-06-8@example.com` del seed de 06.8 (si existe)
y monta el escenario que los 4 tests humanos necesitan:

  - `tenant-a` existe (lo dejaron seeds previos).
  - **KB nueva**: "API REST design principles" (la del test 01).
  - **Agente template del tenant**: `backend-dev-tenant` con
    `scope=global_tenant_template` — el sujeto del test 01.
  - **Agente built-in `project_manager`** ya existe vía
    `python -m api_server.seeds` (test 04).
  - **Dos proyectos** en tenant-a con stacks distintos (Python A y
    PHP B) — para verificar que el agente template ve la KB en los
    dos sin grants individuales (test 01).

Idempotente: re-ejecutable sin duplicar. Persiste ids +
credenciales en `scripts/.demo_state_06_9.json`.

Uso (desde la raíz del repo):

    .\\.venv\\Scripts\\python.exe scripts\\setup_demo_06_9.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from _demo_common import DB_URL, banner, resolve_tenant

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


STATE_FILE = Path(__file__).parent / ".demo_state_06_9.json"


async def _ensure_user(session: object, email: str, password: str) -> UUID:
    """Si el user del seed 06.8 ya existe, devuelve su id. Si no,
    lo crea y le da membership tenant_admin en el tenant activo."""
    from api_server.auth.passwords import hash_password
    from api_server.db.models import User
    from sqlalchemy import select

    existing = (
        await session.execute(select(User).where(User.email == email))  # type: ignore[attr-defined]
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id  # type: ignore[no-any-return]

    new_id = uuid4()
    session.add(  # type: ignore[attr-defined]
        User(
            id=new_id,
            email=email,
            full_name=email.split("@", 1)[0].replace("-", " ").title(),
            password_hash=hash_password(password),
            is_active=True,
        )
    )
    return new_id


async def _ensure_membership(session: object, *, tenant_id: UUID, user_id: UUID) -> None:
    from api_server.db.models import UserOrganizationMembership
    from sqlalchemy import select

    existing = (
        await session.execute(  # type: ignore[attr-defined]
            select(UserOrganizationMembership).where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.role = "tenant_admin"
        existing.is_active = True
        return
    session.add(  # type: ignore[attr-defined]
        UserOrganizationMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            role="tenant_admin",
            is_active=True,
        )
    )


async def _ensure_kb(session: object, *, tenant_id: UUID, name: str, description: str) -> UUID:
    """Crea (o reutiliza) una KB en el tenant — sin chunks. La
    visibilidad en runtime no requiere chunks; el test del modal de
    similares + el grant sí los reusa, pero no este test."""
    from api_server.db.knowledge import KnowledgeBase
    from sqlalchemy import select

    existing = (
        await session.execute(  # type: ignore[attr-defined]
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.name == name,
                KnowledgeBase.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id  # type: ignore[no-any-return]

    new_id = uuid4()
    session.add(  # type: ignore[attr-defined]
        KnowledgeBase(
            id=new_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
            embedding_model_id="nomic-embed-text-v1.5",
        )
    )
    return new_id


async def _ensure_agent_template(
    session: object,
    *,
    tenant_id: UUID,
    name: str,
    role: str,
    system_prompt: str,
) -> UUID:
    """Crea un agente template del tenant si no existe (scope=
    global_tenant_template). Es el sujeto del test 01."""
    from api_server.db.domain import Agent
    from sqlalchemy import select

    existing = (
        await session.execute(  # type: ignore[attr-defined]
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.name == name,
                Agent.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id  # type: ignore[no-any-return]

    new_id = uuid4()
    session.add(  # type: ignore[attr-defined]
        Agent(
            id=new_id,
            tenant_id=tenant_id,
            name=name,
            role=role,
            scope="global_tenant_template",
            agent_type="ai",
            system_prompt=system_prompt,
            is_template=True,
        )
    )
    return new_id


async def _ensure_project(session: object, *, tenant_id: UUID, name: str, description: str) -> UUID:
    from api_server.db.domain import Project
    from sqlalchemy import select

    existing = (
        await session.execute(  # type: ignore[attr-defined]
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.name == name,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id  # type: ignore[no-any-return]

    new_id = uuid4()
    session.add(  # type: ignore[attr-defined]
        Project(
            id=new_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
            status="active",
        )
    )
    return new_id


async def _find_builtin_pm(session: object) -> UUID | None:
    """Busca el agente built-in `project_manager` que siembra
    `python -m api_server.seeds`. Si no existe (no se ha corrido el
    seed), el test_04 indica que el operador corra el seed primero."""
    from api_server.db.domain import Agent
    from sqlalchemy import select

    row = (
        await session.execute(  # type: ignore[attr-defined]
            select(Agent.id).where(
                Agent.scope == "global_builtin",
                Agent.role == "project_manager",
                Agent.deleted_at.is_(None),
            )
        )
    ).first()
    return row[0] if row is not None else None


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    banner("setup demo 06.9 — Agent-scoped KBs (2 proyectos, 1 KB, 1 template)")
    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            tenant = await resolve_tenant(session)
            tenant_id: UUID = tenant.id
            print(f"  tenant: {tenant.slug} ({tenant_id})")

            # Reuse the admin from 06.8 seed (or create one if missing).
            admin_id = await _ensure_user(
                session, email="admin-06-8@example.com", password="demo-06-8-admin"
            )
            await _ensure_membership(session, tenant_id=tenant_id, user_id=admin_id)
            print("  user OK: admin-06-8@example.com (tenant_admin)")

            kb_id = await _ensure_kb(
                session,
                tenant_id=tenant_id,
                name="API REST design principles",
                description=(
                    "Principios de diseño de APIs REST: recursos, verbos HTTP, "
                    "status codes, versionado, paginación. Agnóstico de stack. "
                    "Sembrado por Plan 06.9 demo."
                ),
            )
            print(f"  KB OK: API REST design principles ({kb_id})")

            agent_id = await _ensure_agent_template(
                session,
                tenant_id=tenant_id,
                name="backend-dev-tenant",
                role="backend_dev",
                system_prompt=(
                    "Eres un desarrollador backend del tenant. Sigues los "
                    "principios de diseño REST y las convenciones del stack del "
                    "proyecto donde estés ejecutando."
                ),
            )
            print(f"  agent template OK: backend-dev-tenant ({agent_id})")

            project_python = await _ensure_project(
                session,
                tenant_id=tenant_id,
                name="Plan 06.9 demo — Proyecto Python",
                description="Stack Python+FastAPI. Sujeto del test 06_9_01.",
            )
            project_php = await _ensure_project(
                session,
                tenant_id=tenant_id,
                name="Plan 06.9 demo — Proyecto PHP",
                description="Stack PHP+Symfony. Sujeto del test 06_9_01.",
            )
            print(f"  project A (Python): {project_python}")
            print(f"  project B (PHP):    {project_php}")

            builtin_pm = await _find_builtin_pm(session)
            if builtin_pm is not None:
                print(f"  builtin PM disponible: {builtin_pm}")
            else:
                print("  ⚠ builtin PM NO encontrado — corre `python -m api_server.seeds`")

        state = {
            "tenant_id": str(tenant_id),
            "tenant_slug": tenant.slug,
            "kb_id": str(kb_id),
            "agent_template_id": str(agent_id),
            "project_python_id": str(project_python),
            "project_php_id": str(project_php),
            "builtin_pm_id": str(builtin_pm) if builtin_pm is not None else None,
            "admin": {"email": "admin-06-8@example.com", "password": "demo-06-8-admin"},
        }
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        print()
        print(f"  state: {STATE_FILE}")
        print()
        print("Login en http://localhost:3000/login:")
        print("  - admin-06-8@example.com  pwd: demo-06-8-admin  (tenant_admin)")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
