"""Seed para los tests humanos del Plan 06.8 — RBAC enforcement.

Crea **tres usuarios** con roles distintos contra el tenant `tenant-a`
(override con DEMO_TENANT) más un proyecto sembrado para que los tres
tengan algo que ver desde el admin-panel:

  - `user-06-8@example.com`      tenant_user  (membership activa)
  - `admin-06-8@example.com`     tenant_admin (membership activa)
  - `sysadmin-06-8@example.com`  is_system_admin=true, sin membership

Persiste sus ids + un proyecto recién creado en
`scripts/.demo_state_06_8.json`. Las contraseñas van también en el
state — todo es local de dev, no hay riesgo de leakeo.

Re-ejecutable: si los usuarios ya existen, refresca su rol/flag y
re-imprime las credenciales sin duplicar memberships.

Uso (desde la raíz del repo):

    .\\.venv\\Scripts\\python.exe scripts\\setup_demo_06_8.py
    DEMO_TENANT=otro-slug python scripts/setup_demo_06_8.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

# Reuso el shim utf-8 + tenant resolver de _demo_common para mantener
# consistencia con los otros setup_demo_*.py.
from _demo_common import DB_URL, banner, resolve_tenant

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


STATE_FILE = Path(__file__).parent / ".demo_state_06_8.json"

USERS = [
    # (email, password, role-in-tenant, is_system_admin)
    ("user-06-8@example.com", "demo-06-8-user", "tenant_user", False),
    ("admin-06-8@example.com", "demo-06-8-admin", "tenant_admin", False),
    ("sysadmin-06-8@example.com", "demo-06-8-sysadmin", None, True),
]


async def _upsert_user(
    session: object,
    *,
    email: str,
    password: str,
    is_system_admin: bool,
) -> UUID:
    """Crea el usuario si no existe (con su hash argon2), o ajusta el
    flag `is_system_admin` si ya estaba. Devuelve siempre su id."""
    from api_server.auth.passwords import hash_password
    from api_server.db.models import User
    from sqlalchemy import select

    existing = (
        await session.execute(select(User).where(User.email == email))  # type: ignore[attr-defined]
    ).scalar_one_or_none()
    if existing is not None:
        existing.is_system_admin = is_system_admin
        return existing.id  # type: ignore[no-any-return]

    new_id = uuid4()
    session.add(  # type: ignore[attr-defined]
        User(
            id=new_id,
            email=email,
            full_name=email.split("@", 1)[0].replace("-", " ").title(),
            password_hash=hash_password(password),
            is_system_admin=is_system_admin,
            is_active=True,
        )
    )
    return new_id


async def _upsert_membership(
    session: object,
    *,
    tenant_id: UUID,
    user_id: UUID,
    role: str,
) -> None:
    """Crea o actualiza la membership user → tenant con el rol indicado.
    Activa la membership si estaba en `is_active=false`."""
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
        existing.role = role
        existing.is_active = True
        existing.deleted_at = None
        return

    session.add(  # type: ignore[attr-defined]
        UserOrganizationMembership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            is_active=True,
        )
    )


async def _ensure_demo_project(session: object, tenant_id: UUID) -> UUID:
    """Si no hay un proyecto-demo del 06.8 en el tenant, lo crea.

    Idempotente por nombre — busca uno con `name='Plan 06.8 demo'` antes
    de crear."""
    from api_server.db.domain import Project
    from sqlalchemy import select

    name = "Plan 06.8 demo"
    existing = (
        await session.execute(  # type: ignore[attr-defined]
            select(Project).where(Project.tenant_id == tenant_id, Project.name == name)
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
            description=(
                "Proyecto demo del Plan 06.8 — usado por los tests humanos para "
                "verificar que `tenant_user` ve la card pero no puede editarla."
            ),
            status="active",
        )
    )
    return new_id


async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    banner("setup demo 06.8 — RBAC: 3 usuarios + proyecto sembrado")
    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            tenant = await resolve_tenant(session)
            tenant_id: UUID = tenant.id
            print(f"  tenant: {tenant.slug} ({tenant_id})")

            seeded: list[dict[str, object]] = []
            for email, password, role, is_sys in USERS:
                user_id = await _upsert_user(
                    session,
                    email=email,
                    password=password,
                    is_system_admin=is_sys,
                )
                if role is not None:
                    await _upsert_membership(
                        session,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        role=role,
                    )
                seeded.append(
                    {
                        "email": email,
                        "password": password,
                        "user_id": str(user_id),
                        "role": role or "n/a",
                        "is_system_admin": is_sys,
                    }
                )
                tag = "system_admin" if is_sys else (role or "?")
                print(f"  user OK: {email}  ({tag})")

            project_id = await _ensure_demo_project(session, tenant_id)
            print(f"  project: Plan 06.8 demo ({project_id})")

        state = {
            "tenant_id": str(tenant_id),
            "tenant_slug": tenant.slug,
            "project_id": str(project_id),
            "users": seeded,
        }
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        print()
        print(f"  state: {STATE_FILE}")
        print()
        print("Credenciales (login en http://localhost:3000/login):")
        for u in seeded:
            print(f"  - {u['email']:<32}  pwd: {u['password']:<22}  rol: {u['role']}")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
