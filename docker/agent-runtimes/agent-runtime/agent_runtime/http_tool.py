"""The http_request builtin tool (task_02_17).

An agent may reach the network only through this tool, and only on
four rails:

  * a per-project **domain allowlist** — the request host must be on it;
  * the **SSRF guard** (prod-12 Fase A): the host is resolved ONCE, every
    address is validated against the internal-range denylist and the
    connection goes to the PINNED IP (Host + SNI preserved) — no DNS
    rebinding, no loopback/RFC1918/metadata, no literal IPs, and redirects
    are never followed;
  * a **timeout** — a slow server cannot hang the agent;
  * a **max body size** — the response is streamed and aborted the
    moment it exceeds the cap, so a huge payload cannot exhaust memory.

Only http/https; the body is returned decoded as text.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from agent_runtime.ssrf_guard import (
    PinnedDestination,
    Resolver,
    SsrfViolationError,
    pinned_url,
    validate_destination,
)
from agent_runtime.tools import ToolResult

_ALLOWED_SCHEMES = ("http", "https")


@dataclass
class HttpRequestTool:
    """An `http_request` tool bound to one project's domain allowlist."""

    allowed_domains: frozenset[str]
    timeout_s: float = 10.0
    max_body_bytes: int = 1_000_000
    # Test seams — production uses the real resolver + a fresh Client per call.
    resolver: Resolver = socket.getaddrinfo
    client: httpx.Client | None = None

    def _validate(self, args: dict[str, object]) -> tuple[str, str, PinnedDestination] | ToolResult:
        """Resolve the request into (method, url, pin), or a failed ToolResult."""
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return ToolResult(ok=False, error="http_request requires a 'url' string")
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return ToolResult(ok=False, error=f"unsupported URL scheme: '{parsed.scheme}'")
        host = parsed.hostname or ""
        if host not in self.allowed_domains:
            return ToolResult(
                ok=False,
                error=f"domain not allowed: {host}",
                output={"allowed": sorted(self.allowed_domains)},
            )
        # prod-12 Fase A (gap4-1): the textual allowlist match is NOT enough —
        # validate what the name actually resolves to, and pin it.
        try:
            pin = validate_destination(host, resolver=self.resolver)
        except SsrfViolationError as exc:
            return ToolResult(ok=False, error=f"destination rejected: {exc}")
        return str(args.get("method", "GET")).upper(), url, pin

    def _request(
        self, method: str, url: str, pin: PinnedDestination, args: dict[str, object]
    ) -> ToolResult:
        headers = args.get("headers")
        request_headers = dict(headers) if isinstance(headers, dict) else {}
        # Connect to the pinned IP; keep virtual-hosting + TLS verification on
        # the ORIGINAL hostname (gap4-3: explicit no-redirects — a 30x from an
        # allowed host must never walk the client onto an internal one).
        request_headers["Host"] = pin.host
        target = pinned_url(url, pin)
        client_ctx = _NoopExitClient(self.client) if self.client is not None else httpx.Client()
        with (
            client_ctx as client,
            client.stream(
                method,
                target,
                timeout=self.timeout_s,
                headers=request_headers,
                follow_redirects=False,
                extensions={"sni_hostname": pin.host},
            ) as response,
        ):
            total = 0
            chunks: list[bytes] = []
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > self.max_body_bytes:
                    return ToolResult(
                        ok=False, error=f"response body exceeds {self.max_body_bytes} bytes"
                    )
                chunks.append(chunk)
        body = b"".join(chunks).decode("utf-8", errors="replace")
        return ToolResult(
            ok=response.is_success,
            output={
                "status_code": response.status_code,
                "body": body,
                "headers": dict(response.headers),
            },
            error=None if response.is_success else f"HTTP {response.status_code}",
        )

    def __call__(self, args: dict[str, object]) -> ToolResult:
        validated = self._validate(args)
        if isinstance(validated, ToolResult):
            return validated
        method, url, pin = validated
        try:
            return self._request(method, url, pin, args)
        except httpx.TimeoutException:
            return ToolResult(ok=False, error=f"request timed out after {self.timeout_s}s")
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")


class _NoopExitClient:
    """Context manager that yields an injected client WITHOUT closing it (tests)."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *_exc: object) -> None:
        return None
