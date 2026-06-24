"""Unit tests for the public application base URL validator (ADR 0047).

`validate_public_base_url` normalises the operator-set public base URL — the
single origin the SSO callback / SAML ACS paths are appended to. It must be a
bare ``scheme://host[:port]`` (no path/query/fragment) so the router can append
the well-known paths cleanly.
"""

from __future__ import annotations

import pytest
from api_server.db.platform_settings import (
    InvalidApiPathPrefixError,
    InvalidPublicBaseUrlError,
    validate_api_path_prefix,
    validate_public_base_url,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://agentic-orchestrator.com", "https://agentic-orchestrator.com"),
        ("https://agentic-orchestrator.com/", "https://agentic-orchestrator.com"),
        ("  https://app.example.com  ", "https://app.example.com"),
        ("http://localhost:8001", "http://localhost:8001"),
        ("https://EXAMPLE.com:8443", "https://EXAMPLE.com:8443"),
    ],
)
def test_valid_urls_normalise(raw: str, expected: str) -> None:
    assert validate_public_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "ftp://example.com",  # wrong scheme
        "example.com",  # no scheme
        "https://",  # no host
        "https://example.com/auth/callback",  # carries a path
        "https://example.com?x=1",  # carries a query
        "https://example.com#frag",  # carries a fragment
    ],
)
def test_invalid_urls_raise(raw: str) -> None:
    with pytest.raises(InvalidPublicBaseUrlError):
        validate_public_base_url(raw)


# ---------------------------------------------------------------------------
# api_path_prefix (ADR 0069 — single-origin reverse proxy)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),  # no prefix (default / backward-compatible)
        ("   ", ""),
        ("/", ""),  # bare root = no prefix
        ("/api", "/api"),
        ("/api/", "/api"),  # trailing slash stripped
        ("  /api  ", "/api"),
        ("/api/v1", "/api/v1"),
        ("/api/v1/", "/api/v1"),
    ],
)
def test_valid_api_path_prefix_normalises(raw: str, expected: str) -> None:
    assert validate_api_path_prefix(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "api",  # no leading slash
        "http://example.com/api",  # carries a host
        "//example.com/api",  # protocol-relative host
        "/api?x=1",  # carries a query
        "/api#frag",  # carries a fragment
    ],
)
def test_invalid_api_path_prefix_raises(raw: str) -> None:
    with pytest.raises(InvalidApiPathPrefixError):
        validate_api_path_prefix(raw)
