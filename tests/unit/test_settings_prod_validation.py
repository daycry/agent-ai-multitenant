"""Fail-fast validation of production secrets (Plan 06.14 task_06_14_03).

Regression for secrets-config-1/2/3/5/7: the three services shipped
dev-only defaults for JWT/MinIO/DB credentials with no startup guard, so
a staging/prod deployment that forgot to override them would silently run
with publicly-known secrets. Each `Settings` now rejects a dev default
when `environment` is `staging`/`prod` (a Pydantic `model_validator`,
surfaced as `ValidationError`), while leaving `dev` untouched.

Init kwargs have the highest precedence in pydantic-settings, so passing
explicit values here makes the tests independent of any ambient env / .env.
"""

from __future__ import annotations

import pytest
from api_server.config import Settings as ApiSettings
from orchestrator.config import Settings as OrchSettings
from pydantic import ValidationError
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.unit

# Real-looking api-server secrets — none contains a dev marker.
_API_REAL = {
    "jwt_secret": "x" * 48,
    "review_url_signing_secret": "y" * 48,
    # SSO client-secret encryption key (Plan 08 task_08_01) — also guarded.
    "sso_encryption_key": "w" * 48,
    "minio_secret_key": "z" * 48,
    "minio_access_key": "prod-access-key",
    "database_url": "postgresql+asyncpg://app_user:S3cr3tP@db/agentic",
    "admin_database_url": "postgresql+asyncpg://migrations_user:S3cr3tM@db/agentic",
}
_REAL_DB = "postgresql+asyncpg://migrations_user:S3cr3tProd@db.internal/agentic"


# ---------------------------------------------------------------------------
# api-server (jwt, review-url, minio, db, admin-db)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", ["prod", "staging"])
def test_api_rejects_dev_jwt_secret(env: str) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(
            environment=env,
            **{**_API_REAL, "jwt_secret": "dev-only-jwt-secret-change-me"},
        )


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_api_rejects_dev_minio_and_db(env: str) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(
            environment=env,
            **{
                **_API_REAL,
                "minio_access_key": "minioadmin",
                "minio_secret_key": "changeme-dev-only",
                "database_url": "postgresql+asyncpg://app_user:changeme-app-dev-only@h/db",
            },
        )


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_api_rejects_dev_sso_encryption_key(env: str) -> None:
    # Plan 08 task_08_01: the OIDC client-secret encryption key is guarded
    # like every other secret — a dev default must not reach prod/staging.
    with pytest.raises(ValidationError):
        ApiSettings(
            environment=env,
            **{**_API_REAL, "sso_encryption_key": "dev-only-sso-encryption-key-change-me"},
        )


def test_api_accepts_real_secrets_in_prod() -> None:
    assert ApiSettings(environment="prod", **_API_REAL).environment == "prod"


def test_api_allows_dev_defaults_in_dev() -> None:
    # Dev defaults are fine when environment=dev (the common local case).
    assert ApiSettings(environment="dev", **{**_API_REAL, "jwt_secret": "dev-only-x"}).environment


# ---------------------------------------------------------------------------
# workers + orchestrator (database_url only — BYPASSRLS credentials)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", ["prod", "staging"])
def test_worker_rejects_dev_db_url(env: str) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            environment=env,
            database_url="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@h/db",
        )


def test_worker_accepts_real_db_url_in_prod() -> None:
    assert WorkerSettings(environment="prod", database_url=_REAL_DB).environment == "prod"


def test_worker_allows_dev_default_in_dev() -> None:
    assert WorkerSettings(environment="dev").environment == "dev"


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_orchestrator_rejects_dev_db_url(env: str) -> None:
    with pytest.raises(ValidationError):
        OrchSettings(
            environment=env,
            database_url="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only@h/db",
        )


def test_orchestrator_accepts_real_db_url_in_prod() -> None:
    assert OrchSettings(environment="prod", database_url=_REAL_DB).environment == "prod"


def test_orchestrator_allows_dev_default_in_dev() -> None:
    assert OrchSettings(environment="dev").environment == "dev"
