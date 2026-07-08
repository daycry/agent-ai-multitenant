"""task_prod12_ssrf_01/02 — las tools HTTP validan el destino resuelto y
conectan a la IP pineada (Host + SNI preservados, redirects NO seguidos)."""

from __future__ import annotations

import socket
from typing import Any

import httpx
from agent_runtime.http_endpoint_tool import HttpEndpointTool
from agent_runtime.http_tool import HttpRequestTool


def _resolver_for(*addrs: str) -> Any:
    def _resolve(_host: str, _port: Any, **_kw: Any) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, None, None, "", (a, 0)) for a in addrs
        ]

    return _resolve


def _capture_transport(
    responses: list[httpx.Response],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    return httpx.MockTransport(_handler), seen


# --- HttpRequestTool ---------------------------------------------------------


def test_request_tool_rejects_host_resolving_to_private_range() -> None:
    tool = HttpRequestTool(
        allowed_domains=frozenset({"evil.example.com"}),
        resolver=_resolver_for("10.0.0.5"),
    )
    result = tool({"url": "https://evil.example.com/data"})
    assert result.ok is False
    assert "forbidden" in (result.error or "")


def test_request_tool_rejects_literal_ip_even_if_allowlisted() -> None:
    tool = HttpRequestTool(
        allowed_domains=frozenset({"169.254.169.254"}),
        resolver=_resolver_for("8.8.8.8"),
    )
    result = tool({"url": "http://169.254.169.254/latest/meta-data"})
    assert result.ok is False
    assert "literal IP" in (result.error or "")


def test_request_tool_connects_to_the_pinned_ip_with_host_and_sni() -> None:
    transport, seen = _capture_transport([httpx.Response(200, text="ok")])
    tool = HttpRequestTool(
        allowed_domains=frozenset({"api.example.com"}),
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=transport),
    )
    result = tool({"url": "https://api.example.com/v1/thing?q=1"})
    assert result.ok is True, result.error
    assert len(seen) == 1
    request = seen[0]
    # Conecta a la IP validada (no re-resuelve el nombre — anti-rebinding)...
    assert request.url.host == "93.184.216.34"
    assert request.url.params["q"] == "1"
    # ...preservando el virtual-host y la SNI del certificado.
    assert request.headers["host"] == "api.example.com"
    assert request.extensions.get("sni_hostname") == "api.example.com"


def test_request_tool_does_not_follow_redirects() -> None:
    transport, seen = _capture_transport(
        [
            httpx.Response(302, headers={"location": "http://10.0.0.5/internal"}),
            httpx.Response(200, text="MUST NOT ARRIVE"),
        ]
    )
    tool = HttpRequestTool(
        allowed_domains=frozenset({"api.example.com"}),
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=transport),
    )
    result = tool({"url": "https://api.example.com/redirige"})
    # Un solo request: la 302 se devuelve tal cual, jamás se sigue el Location.
    assert len(seen) == 1
    assert result.ok is False
    assert "302" in (result.error or "")


# --- HttpEndpointTool --------------------------------------------------------


def test_endpoint_tool_rejects_host_resolving_to_metadata() -> None:
    tool = HttpEndpointTool(
        name="get_thing",
        url_template="https://internal.example.com/api/{id}",
        allowed_domains=frozenset({"internal.example.com"}),
        resolver=_resolver_for("169.254.169.254"),
    )
    result = tool({"id": "42"})
    assert result.ok is False
    assert "forbidden" in (result.error or "")


def test_endpoint_tool_connects_to_the_pinned_ip_with_host_and_sni() -> None:
    transport, seen = _capture_transport([httpx.Response(200, json={"ok": True})])
    tool = HttpEndpointTool(
        name="get_thing",
        url_template="https://api.example.com/api/{id}",
        allowed_domains=frozenset({"api.example.com"}),
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=transport),
    )
    result = tool({"id": "42"})
    assert result.ok is True, result.error
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "93.184.216.34"
    assert request.url.path == "/api/42"
    assert request.headers["host"] == "api.example.com"
    assert request.extensions.get("sni_hostname") == "api.example.com"


def test_endpoint_tool_does_not_follow_redirects() -> None:
    transport, seen = _capture_transport(
        [
            httpx.Response(301, headers={"location": "http://127.0.0.1/"}),
            httpx.Response(200, text="MUST NOT ARRIVE"),
        ]
    )
    tool = HttpEndpointTool(
        name="get_thing",
        url_template="https://api.example.com/api/{id}",
        allowed_domains=frozenset({"api.example.com"}),
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=transport),
    )
    result = tool({"id": "42"})
    assert len(seen) == 1
    assert result.ok is False
