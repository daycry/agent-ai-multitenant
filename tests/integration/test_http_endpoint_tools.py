"""Tests for the `http_endpoint`-typed Tool executor (Plan 05 task_05_12).

`http_endpoint` Tools are pre-cooked per-project: the operator
declares ``Tool(implementation_type='http_endpoint',
implementation_ref='https://api.weather.example/?q={city}')`` and
the agent invokes it as a named tool, supplying only the placeholder
values that match the Tool's `input_schema`.

The executor (`HttpEndpointTool`) lives in agent-runtime, mirrors
the security envelope of the generic `http_request` builtin (allowlist
+ timeout + max body), and adds placeholder rendering with URL
encoding. We test all of that here using `httpx.MockTransport` so
no network hits CI.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from agent_runtime.http_endpoint_tool import (
    HttpEndpointTool,
    HttpEndpointToolSpec,
    build_http_endpoint_tool,
    render_url_template,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# render_url_template — placeholder safety
# ---------------------------------------------------------------------------
def test_render_substitutes_top_level_keys() -> None:
    out = render_url_template("https://x.example/{a}/{b}", {"a": "1", "b": "2"})
    assert out == "https://x.example/1/2"


def test_render_url_encodes_values() -> None:
    """Spaces, slashes, ampersands and other URL-special characters
    must be encoded — the operator declares the template, but the
    agent's input is arbitrary."""
    out = render_url_template("https://x.example/?q={query}", {"query": "a b/c&d"})
    assert out == "https://x.example/?q=a%20b%2Fc%26d"


def test_render_missing_key_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="missing"):
        render_url_template("https://x.example/{missing}", {"other": "v"})


def test_render_rejects_attribute_access_pattern() -> None:
    """An attacker-controlled template like '{__class__.__base__}'
    would walk Python attributes via str.format. Our regex only
    matches plain identifiers — the `.` makes the whole match fail,
    so the substring stays literal in the URL."""
    template = "https://x.example/{foo.__class__}"
    out = render_url_template(template, {"foo": "v"})
    # The placeholder stays literal because the regex didn't match.
    assert out == template


def test_render_rejects_indexing_pattern() -> None:
    template = "https://x.example/{foo[0]}"
    out = render_url_template(template, {"foo": "v"})
    assert out == template


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------
def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """A long-lived httpx.Client backed by a MockTransport — wrapped
    by `_NoopExitClient` in the executor so the test owns its lifetime."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_off_allowlist_domain_is_rejected_before_request() -> None:
    """The transport handler should never fire — the allowlist gate
    bails first."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"shouldnt": "happen"})

    tool = HttpEndpointTool(
        name="weather",
        url_template="https://forbidden.example/?q={q}",
        allowed_domains=frozenset({"api.weather.example"}),
        client=_mock_client(handler),
    )
    result = tool({"q": "Madrid"})
    assert result.ok is False
    assert "not allowed" in (result.error or "")
    assert "forbidden.example" in (result.error or "")
    assert calls == []  # the transport was never invoked


def test_non_http_scheme_is_rejected() -> None:
    tool = HttpEndpointTool(
        name="weird",
        url_template="ftp://x.example/?q={q}",
        allowed_domains=frozenset({"x.example"}),
    )
    result = tool({"q": "v"})
    assert result.ok is False
    assert "unsupported URL scheme" in (result.error or "")


# ---------------------------------------------------------------------------
# Successful round-trip — JSON + text bodies
# ---------------------------------------------------------------------------
def test_json_response_is_parsed_into_output() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.weather.example"
        assert str(req.url).endswith("/v1?q=Madrid")
        return httpx.Response(200, json={"temperature": 22, "unit": "C"})

    tool = HttpEndpointTool(
        name="weather",
        url_template="https://api.weather.example/v1?q={city}",
        allowed_domains=frozenset({"api.weather.example"}),
        client=_mock_client(handler),
    )
    result = tool({"city": "Madrid"})
    assert result.ok is True
    assert result.output["status_code"] == 200
    assert result.output["body"] == {"temperature": 22, "unit": "C"}


def test_text_response_is_kept_as_string() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello world", headers={"content-type": "text/plain"})

    tool = HttpEndpointTool(
        name="hello",
        url_template="https://x.example/",
        allowed_domains=frozenset({"x.example"}),
        client=_mock_client(handler),
    )
    result = tool({})
    assert result.ok is True
    assert result.output["body"] == "hello world"


# ---------------------------------------------------------------------------
# HTTP error codes → ToolResult.ok=False but output is preserved
# ---------------------------------------------------------------------------
def test_4xx_returns_failed_toolresult_with_body() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    tool = HttpEndpointTool(
        name="lookup",
        url_template="https://api.example.com/{id}",
        allowed_domains=frozenset({"api.example.com"}),
        client=_mock_client(handler),
    )
    result = tool({"id": "missing"})
    assert result.ok is False
    assert result.error == "HTTP 404"
    # Body still present — the agent can read the error detail.
    assert result.output["body"] == {"detail": "not found"}


# ---------------------------------------------------------------------------
# Body size cap
# ---------------------------------------------------------------------------
def test_oversized_response_is_aborted() -> None:
    big_body = "x" * 10_000

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big_body)

    tool = HttpEndpointTool(
        name="big",
        url_template="https://x.example/",
        allowed_domains=frozenset({"x.example"}),
        max_body_bytes=1024,
        client=_mock_client(handler),
    )
    result = tool({})
    assert result.ok is False
    assert "exceeds 1024 bytes" in (result.error or "")


# ---------------------------------------------------------------------------
# Static headers + query are forwarded
# ---------------------------------------------------------------------------
def test_static_headers_are_forwarded() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(req.headers)
        return httpx.Response(200, json={"ok": True})

    tool = HttpEndpointTool(
        name="hdr",
        url_template="https://x.example/",
        static_headers={"X-Api-Key": "static-value"},
        allowed_domains=frozenset({"x.example"}),
        client=_mock_client(handler),
    )
    tool({})
    assert captured["headers"]["x-api-key"] == "static-value"


def test_static_query_is_appended() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["query"] = dict(req.url.params)
        return httpx.Response(200, json={"ok": True})

    tool = HttpEndpointTool(
        name="q",
        url_template="https://x.example/v1",
        static_query={"format": "json", "v": "2"},
        allowed_domains=frozenset({"x.example"}),
        client=_mock_client(handler),
    )
    tool({})
    assert captured["query"] == {"format": "json", "v": "2"}


# ---------------------------------------------------------------------------
# Missing placeholder surfaces as a typed error
# ---------------------------------------------------------------------------
def test_missing_placeholder_returns_failed_toolresult() -> None:
    tool = HttpEndpointTool(
        name="weather",
        url_template="https://api.weather.example/?q={city}",
        allowed_domains=frozenset({"api.weather.example"}),
    )
    result = tool({"wrong_key": "Madrid"})
    assert result.ok is False
    assert "missing required placeholder" in (result.error or "")
    assert "city" in (result.error or "")


# ---------------------------------------------------------------------------
# build_http_endpoint_tool from Spec — convenience constructor
# ---------------------------------------------------------------------------
def test_build_from_spec_injects_allowlist() -> None:
    spec = HttpEndpointToolSpec(
        name="weather",
        url_template="https://api.weather.example/?q={city}",
        method="GET",
        static_headers={"X-Api-Key": "abc"},
        timeout_s=15.0,
    )
    tool = build_http_endpoint_tool(spec, allowed_domains=frozenset({"api.weather.example"}))
    assert tool.allowed_domains == frozenset({"api.weather.example"})
    assert tool.timeout_s == 15.0
    assert tool.static_headers == {"X-Api-Key": "abc"}
    # The Spec doesn't carry the allowlist — it's a runtime concern,
    # not a Tool-row field. Verify we didn't accidentally store it
    # on the Spec.
    assert not hasattr(spec, "allowed_domains")


# ---------------------------------------------------------------------------
# Exception → ToolResult, never raise into the agent loop
# ---------------------------------------------------------------------------
def test_httpx_error_is_folded_into_failed_toolresult() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    tool = HttpEndpointTool(
        name="dead",
        url_template="https://x.example/",
        allowed_domains=frozenset({"x.example"}),
        client=_mock_client(handler),
    )
    result = tool({})
    assert result.ok is False
    assert "ConnectError" in (result.error or "")
