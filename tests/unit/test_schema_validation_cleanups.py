"""Unit tests for the input-validation cleanups (Plan 06.14 task_06_14_15).

Covers, purely at the Pydantic layer (no I/O):
  - api-routers-validation-7: LoginRequest password length now matches
    RegisterRequest (min 8, max 128).
  - api-routers-validation-2: GrantKBRequest typed body (UUID coercion,
    extra-field rejection).
  - api-routers-validation-6: oversized free-form JSON config blobs on
    project create/update are rejected.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.schemas.agents import GrantKBRequest
from api_server.schemas.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    LoginRequest,
    RegisterRequest,
)
from api_server.schemas.projects import ProjectCreateRequest, ProjectUpdateRequest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# api-routers-validation-7 — login/register password bounds parity
# ---------------------------------------------------------------------------
def _pw_bounds(model: type) -> tuple[int | None, int | None]:
    """Pull (min_length, max_length) off the password field's metadata,
    independent of which annotated-types class carries them."""
    field = model.model_fields["password"]
    min_len = max_len = None
    for meta in field.metadata:
        if hasattr(meta, "min_length"):
            min_len = meta.min_length
        if hasattr(meta, "max_length"):
            max_len = meta.max_length
    return min_len, max_len


def test_login_and_register_share_password_bounds() -> None:
    # Both schemas resolve to the same single source of truth.
    assert _pw_bounds(LoginRequest) == (PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH)
    assert _pw_bounds(RegisterRequest) == (PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH)


def test_login_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="alice@example.com", password="short")  # 5 chars < 8


def test_login_accepts_min_length_password() -> None:
    req = LoginRequest(email="alice@example.com", password="x" * PASSWORD_MIN_LENGTH)
    assert req.email == "alice@example.com"


def test_login_rejects_overlong_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="alice@example.com", password="x" * (PASSWORD_MAX_LENGTH + 1))


# ---------------------------------------------------------------------------
# api-routers-validation-2 — GrantKBRequest typed body
# ---------------------------------------------------------------------------
def test_grant_kb_request_coerces_uuid_string() -> None:
    kb_id = uuid4()
    req = GrantKBRequest(kb_id=str(kb_id))  # type: ignore[arg-type]
    assert req.kb_id == kb_id


def test_grant_kb_request_rejects_bad_uuid() -> None:
    with pytest.raises(ValidationError):
        GrantKBRequest(kb_id="not-a-uuid")  # type: ignore[arg-type]


def test_grant_kb_request_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        GrantKBRequest.model_validate({"kb_id": str(uuid4()), "tenant_id": str(uuid4())})


# ---------------------------------------------------------------------------
# api-routers-validation-6 — JSON config size cap
# ---------------------------------------------------------------------------
def test_project_create_rejects_oversized_worker_config() -> None:
    with pytest.raises(ValidationError, match="worker_config is too large"):
        ProjectCreateRequest(name="p", worker_config={"blob": "x" * 70_000})


def test_project_create_accepts_normal_config() -> None:
    req = ProjectCreateRequest(name="p", worker_config={"max_workers": 4})
    assert req.worker_config == {"max_workers": 4}


def test_project_update_rejects_oversized_repository_config() -> None:
    with pytest.raises(ValidationError, match="repository_config is too large"):
        ProjectUpdateRequest(repository_config={"blob": "y" * 70_000})


def test_project_update_allows_none_config() -> None:
    # None is fine -- the cap only bites populated blobs.
    req = ProjectUpdateRequest(repository_config=None)
    assert req.repository_config is None
