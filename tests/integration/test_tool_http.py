"""Integration tests for the http_request builtin tool (task_02_17).

A real HTTP server runs on loopback; the tool's three rails — domain
allowlist, timeout and max body size — are each exercised against it.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from agent_runtime.http_tool import HttpRequestTool

pytestmark = pytest.mark.integration

_BIG_BODY = b"x" * 5000


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:  # silence access logging
        pass

    def do_GET(self) -> None:  # stdlib BaseHTTPRequestHandler API
        with contextlib.suppress(OSError):  # client may have walked away
            self._route()

    def _route(self) -> None:
        if self.path == "/ok":
            self._send(200, b"hello http")
        elif self.path == "/big":
            self._send(200, _BIG_BODY)
        elif self.path == "/slow":
            time.sleep(2.0)
            self._send(200, b"late")
        else:
            self._send(404, b"not found")

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def http_port() -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


def _tool(**kw: object) -> HttpRequestTool:
    kw.setdefault("allowed_domains", frozenset({"127.0.0.1"}))
    return HttpRequestTool(**kw)  # type: ignore[arg-type]


def test_request_to_an_allowed_domain_succeeds(http_port: int) -> None:
    result = _tool()({"url": f"http://127.0.0.1:{http_port}/ok"})
    assert result.ok is True
    assert result.output["status_code"] == 200
    assert result.output["body"] == "hello http"


def test_domain_not_in_allowlist_is_blocked(http_port: int) -> None:
    result = _tool()({"url": "http://malicious.example.com/steal"})
    assert result.ok is False
    assert "domain not allowed" in (result.error or "")


def test_non_2xx_status_is_reported(http_port: int) -> None:
    result = _tool()({"url": f"http://127.0.0.1:{http_port}/missing"})
    assert result.ok is False
    assert result.output["status_code"] == 404


def test_timeout_is_enforced(http_port: int) -> None:
    result = _tool(timeout_s=0.4)({"url": f"http://127.0.0.1:{http_port}/slow"})
    assert result.ok is False
    assert "timed out" in (result.error or "")


def test_max_body_size_is_enforced(http_port: int) -> None:
    result = _tool(max_body_bytes=1000)({"url": f"http://127.0.0.1:{http_port}/big"})
    assert result.ok is False
    assert "exceeds" in (result.error or "")


def test_a_body_within_the_cap_is_returned(http_port: int) -> None:
    result = _tool(max_body_bytes=10_000)({"url": f"http://127.0.0.1:{http_port}/big"})
    assert result.ok is True
    assert len(result.output["body"]) == 5000


def test_unsupported_scheme_is_rejected() -> None:
    result = _tool()({"url": "file:///etc/passwd"})
    assert result.ok is False
    assert "scheme" in (result.error or "")


def test_missing_url_is_rejected() -> None:
    assert _tool()({}).ok is False
    assert _tool()({"url": "  "}).ok is False
