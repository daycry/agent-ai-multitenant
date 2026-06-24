"""Seed the INITIAL tenant — `python -m api_server.seeds.init_tenant`.

The real installer (Plan prod-01 task_16, finding deploy-1) runs this at the
SEED_TENANT step to create the operator's first organization + its admin user +
a ``tenant_admin`` membership. Idempotent: re-running resolves to the same rows
and creates nothing new.

Security:
  * The admin password is read from the ``INIT_ADMIN_PASSWORD`` env var, NEVER
    from argv (so it can't leak via ``ps``/shell history) and never logged. It
    is stored only as an argon2id hash (``auth.passwords.hash_password``).
  * The first user on a fresh DB is promoted to ``is_system_admin`` (mirrors
    POST /auth/register), giving the operator the cross-tenant superpowers.

Caller must hold an AsyncSession bound to the BYPASSRLS admin engine — a tenant
session cannot write to ``organizations``/``users``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.passwords import hash_password

# Import the ORM aggregator FIRST so every mapper is registered before any
# flush triggers mapper configuration (mirrors seeds/__main__.py).
from api_server.db import models as _models  # noqa: F401
from api_server.db.models import (
    Organization,
    User,
    UserOrganizationMembership,
    UserRole,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging

_PASSWORD_ENV = "INIT_ADMIN_PASSWORD"


@dataclass(frozen=True)
class InitTenantResult:
    """Outcome of :func:`init_tenant` — IDs + what was newly created.

    Carries no secret material (no password / hash), so it is safe to log.
    """

    tenant_id: UUID
    user_id: UUID
    created_org: bool
    created_user: bool
    created_membership: bool
    is_system_admin: bool


async def init_tenant(
    session: AsyncSession,
    *,
    tenant_name: str,
    slug: str,
    admin_email: str,
    admin_password: str,
    full_name: str | None = None,
) -> InitTenantResult:
    """Create (idempotently) the initial organization + admin user + membership.

    Returns an :class:`InitTenantResult`. Safe to re-run: an existing org (by
    slug) / user (by email) / membership (by tenant+user) is reused, never
    duplicated, and the password of an existing user is left untouched.
    """

    slug = slug.strip().lower()
    email = admin_email.strip().lower()
    if not admin_password:
        raise ValueError("admin_password must not be empty")

    org = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    created_org = org is None
    if org is None:
        org = Organization(id=uuid7(), name=tenant_name, slug=slug)
        session.add(org)
        await session.flush()

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    created_user = user is None
    if user is None:
        # First user on a fresh DB → system admin (mirrors POST /auth/register).
        is_first_user = (
            await session.execute(select(User.id).limit(1))
        ).scalar_one_or_none() is None
        user = User(
            id=uuid7(),
            email=email,
            password_hash=hash_password(admin_password),
            full_name=full_name or f"{tenant_name} Admin",
            is_system_admin=is_first_user,
        )
        session.add(user)
        await session.flush()

    membership = (
        await session.execute(
            select(UserOrganizationMembership).where(
                UserOrganizationMembership.tenant_id == org.id,
                UserOrganizationMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    created_membership = membership is None
    if membership is None:
        membership = UserOrganizationMembership(
            id=uuid7(),
            tenant_id=org.id,
            user_id=user.id,
            role=UserRole.TENANT_ADMIN.value,
            is_active=True,
        )
        session.add(membership)
        await session.flush()

    return InitTenantResult(
        tenant_id=org.id,
        user_id=user.id,
        created_org=created_org,
        created_user=created_user,
        created_membership=created_membership,
        is_system_admin=user.is_system_admin,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m api_server.seeds.init_tenant",
        description="Seed the initial tenant + admin user (idempotent).",
    )
    parser.add_argument("--tenant-name", required=True, help="Display name of the org.")
    parser.add_argument("--slug", required=True, help="URL-safe org slug.")
    parser.add_argument("--admin-email", required=True, help="Initial admin email.")
    parser.add_argument("--full-name", default=None, help="Optional admin full name.")
    return parser.parse_args(argv)


async def _amain(argv: list[str]) -> int:
    configure_logging(service="init-tenant")
    log = structlog.get_logger("init-tenant")
    args = _parse_args(argv)

    password = os.environ.get(_PASSWORD_ENV)
    if not password:
        # Fail loud: never invent a password, never read from argv.
        log.error("init_tenant.missing_password", env=_PASSWORD_ENV)
        sys.stderr.write(
            f"ERROR: the admin password must be supplied via the {_PASSWORD_ENV} "
            "environment variable (never on the command line).\n"
        )
        return 2

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await init_tenant(
            session,
            tenant_name=args.tenant_name,
            slug=args.slug,
            admin_email=args.admin_email,
            admin_password=password,
            full_name=args.full_name,
        )

    log.info(
        "init_tenant.completed",
        tenant_id=str(result.tenant_id),
        user_id=str(result.user_id),
        created_org=result.created_org,
        created_user=result.created_user,
        created_membership=result.created_membership,
        is_system_admin=result.is_system_admin,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
