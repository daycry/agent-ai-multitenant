"""Unit test for the health-check safe-detail mapping (Plan 06.14
task_06_14_15, error-obs-logging-6).

`/admin/system-health` must NOT echo raw exception text (which can leak
internal topology — schema names, Vault URL structure, socket paths) to
the client; it returns a generic failure class and logs the detail
server-side. These tests pin that contract without needing a live stack.
"""

from __future__ import annotations

import pytest
from api_server.routers.admin import _safe_detail

pytestmark = pytest.mark.unit


def test_timeout_maps_to_generic_timeout() -> None:
    assert _safe_detail("postgres", TimeoutError("connect timed out")) == "timeout"
    assert _safe_detail("redis", TimeoutError()) == "timeout"


def test_connection_error_maps_to_generic_connection_failed() -> None:
    assert _safe_detail("vault", ConnectionError("conn refused")) == "connection failed"
    assert _safe_detail("clamav", OSError("[Errno 111] /var/run/clamd.sock")) == "connection failed"


def test_other_exception_maps_to_generic_probe_failed() -> None:
    detail = _safe_detail("minio", ValueError("password authentication failed for schema acme"))
    assert detail == "probe failed"


def test_safe_detail_never_leaks_exception_text() -> None:
    secret = "host=internal-db.acme.local user=app_user dbname=tenant_secrets"
    detail = _safe_detail("postgres", RuntimeError(secret))
    assert secret not in detail
    assert "acme" not in detail
    assert detail == "probe failed"
