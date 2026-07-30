"""prod-12 task_prod12_allow_02 — la cadena de allowlist HTTP, de punta a punta.

Proyecto con ``allowed_domains=["example.com"]`` → payload del dispatch →
``ExecutionRequest`` → ``_agent_spec`` → el parseo EXACTO del runtime
(``__main__``: ``frozenset(spec["allowed_domains"])``) → ``HttpRequestTool``.
Sin Docker ni red: el resolver y el transporte van con seams (el mismo camino
de código de producción decide, solo la E/S es fake), así el e2e corre en
cualquier runner.

Cubre: dominio permitido alcanzable (a su IP pineada), host no listado
rechazado con mensaje claro, IP literal rechazada, y dominio permitido que
RESUELVE a rango privado (rebinding) rechazado por el ssrf_guard.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "docker/agent-runtimes/agent-runtime"))

from agent_runtime.http_tool import HttpRequestTool  # noqa: E402
from workers.run_contract import ExecutionRequest  # noqa: E402
from workers.run_spec import _agent_spec  # noqa: E402

pytestmark = pytest.mark.e2e


def _resolver_for(*addrs: str) -> Any:
    def _resolve(host: str, _port: Any, **_kw: Any) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, None, None, "", (a, 0)) for a in addrs
        ]

    return _resolve


def _tool_from_project_allowlist(
    allowed_domains: list[str], *, resolver: Any, client: httpx.Client
) -> HttpRequestTool:
    """La cadena real: request del orchestrator → spec del worker → parseo del
    runtime (mismas expresiones que ``__main__``) → tool."""
    request = ExecutionRequest.from_dict(
        {
            "tenant_id": "t1",
            "task_id": "task1",
            "agent_id": "a1",
            "task": {"id": "task1", "title": "T", "description": ""},
            "model": {"kind": "azure_foundry", "model": "gpt"},
            # Lo que emite el builder común del dispatch desde projects.allowed_domains.
            "allowed_domains": allowed_domains,
        }
    )
    spec = _agent_spec(request, None)
    # Parseo EXACTO del runtime (__main__.py): frozenset de strings.
    parsed = frozenset(str(d) for d in (spec.get("allowed_domains") or []))
    return HttpRequestTool(allowed_domains=parsed, resolver=resolver, client=client)


def test_allowed_domain_is_reachable_via_its_pinned_ip() -> None:
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="hello")

    tool = _tool_from_project_allowlist(
        ["example.com"],
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    result = tool({"url": "https://example.com/page"})
    assert result.ok is True, result.error
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "example.com"


def test_unlisted_host_gets_a_clear_rejection() -> None:
    tool = _tool_from_project_allowlist(
        ["example.com"],
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
    )
    result = tool({"url": "https://otro.example.org/"})
    assert result.ok is False
    assert "domain not allowed" in (result.error or "")
    assert result.output == {"allowed": ["example.com"]}


def test_literal_ip_is_rejected() -> None:
    tool = _tool_from_project_allowlist(
        ["example.com"],
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
    )
    result = tool({"url": "http://169.254.169.254/latest/meta-data"})
    assert result.ok is False


def test_allowed_domain_resolving_to_private_range_is_rejected() -> None:
    tool = _tool_from_project_allowlist(
        ["rebind.example.com"],
        resolver=_resolver_for("127.0.0.1"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
    )
    result = tool({"url": "https://rebind.example.com/"})
    assert result.ok is False
    assert "destination rejected" in (result.error or "")


def test_empty_allowlist_is_deny_all() -> None:
    tool = _tool_from_project_allowlist(
        [],
        resolver=_resolver_for("93.184.216.34"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
    )
    result = tool({"url": "https://example.com/"})
    assert result.ok is False
    assert "domain not allowed" in (result.error or "")
