"""The http_request builtin tool (task_02_17).

An agent may reach the network only through this tool, and only on
three rails:

  * a per-project **domain allowlist** — the request host must be on it;
  * a **timeout** — a slow server cannot hang the agent;
  * a **max body size** — the response is streamed and aborted the
    moment it exceeds the cap, so a huge payload cannot exhaust memory.

Only http/https; the body is returned decoded as text.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from agent_runtime.tools import ToolResult

_ALLOWED_SCHEMES = ("http", "https")


@dataclass
class HttpRequestTool:
    """An `http_request` tool bound to one project's domain allowlist."""

    allowed_domains: frozenset[str]
    timeout_s: float = 10.0
    max_body_bytes: int = 1_000_000

    def _validate(self, args: dict[str, object]) -> tuple[str, str] | ToolResult:
        """Resolve the request into (method, url), or a failed ToolResult."""
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
        return str(args.get("method", "GET")).upper(), url

    def _request(self, method: str, url: str, args: dict[str, object]) -> ToolResult:
        headers = args.get("headers")
        request_headers = headers if isinstance(headers, dict) else None
        with (
            httpx.Client() as client,
            client.stream(method, url, timeout=self.timeout_s, headers=request_headers) as response,
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
        method, url = validated
        try:
            return self._request(method, url, args)
        except httpx.TimeoutException:
            return ToolResult(ok=False, error=f"request timed out after {self.timeout_s}s")
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
