"""`http_endpoint`-typed Tool executor (Plan 05 task_05_12).

A Tool row with ``implementation_type='http_endpoint'`` carries:

  * ``implementation_ref``: a URL **template** with ``{placeholder}``
    slots that resolve from the call's input args. Example::

        implementation_ref = "https://api.weather.example/v1?q={city}"
        args = {"city": "Madrid"}
        # → GET https://api.weather.example/v1?q=Madrid

  * ``input_schema``: the JSON schema the agent sees; we use its
    required + properties to know what placeholders are valid.

Unlike the builtin :class:`HttpRequestTool` (`http_request`) — which
is one generic tool the agent uses to hit ANY URL on the allowlist —
this is one Tool row per pre-cooked URL, advertised to the agent
with the operator's display_name + description. The agent doesn't
choose the URL; it just supplies the placeholder values.

Security envelope (mirrors HttpRequestTool):

  * URL scheme is http/https only.
  * After rendering, the resolved host must be on the project's
    domain allowlist.
  * Body size capped (default 1 MB) — the response is streamed and
    aborted past the cap to avoid memory exhaustion.
  * Per-call timeout. Defaults to the Tool row's `timeout_seconds`.

Why placeholder rendering uses a regex instead of ``str.format``:
``str.format`` with attacker-controlled templates is unsafe — a
template like ``{foo.__class__.__init_subclass__}`` walks Python
attributes. Our renderer only accepts ``{identifier}`` shapes and
URL-encodes values, so a malicious template can't break out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from agent_runtime.tools import ToolResult

_ALLOWED_SCHEMES = ("http", "https")
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class _NoopExitClient:
    """Wraps an externally-managed `httpx.Client` so the executor's
    ``with client_ctx as client`` block doesn't close it on exit.

    Production path builds a fresh Client per call (it ``with``-closes
    naturally); the test path injects a long-lived MockTransport-backed
    Client across many calls. This wrapper makes both shapes uniform."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *_exc: object) -> None:
        # Deliberately do nothing — the test owns the client lifetime.
        pass


def render_url_template(template: str, args: dict[str, Any]) -> str:
    """Substitute ``{key}`` placeholders with URL-encoded values from ``args``.

    Only top-level identifier keys are supported. Attribute access
    (``{foo.bar}``) and indexing (``{foo[0]}``) are not — the regex
    rejects them before substitution. Missing keys raise
    :class:`KeyError`; the caller wraps to ToolResult.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in args:
            raise KeyError(key)
        return quote(str(args[key]), safe="")

    return _PLACEHOLDER_RE.sub(_replace, template)


@dataclass
class HttpEndpointTool:
    """One http_endpoint-typed Tool, bound to a project's allowlist.

    Instantiated once per Tool row at agent-loop boot and registered
    on :class:`ToolRegistry` under the Tool's ``name``.

    `client` is an injection seam: production passes ``None`` and we
    build a fresh :class:`httpx.Client` per call (one short-lived
    Client avoids cross-call connection-pool state). Tests pass a
    Client backed by an ``httpx.MockTransport`` so no real network
    hits CI.
    """

    name: str
    url_template: str
    method: str = "GET"
    static_headers: dict[str, str] = field(default_factory=dict)
    static_query: dict[str, str] = field(default_factory=dict)
    allowed_domains: frozenset[str] = frozenset()
    timeout_s: float = 30.0
    max_body_bytes: int = 1_000_000
    # Test seam — None = build a fresh httpx.Client() per call.
    client: httpx.Client | None = None

    def _render(self, args: dict[str, Any]) -> str | ToolResult:
        try:
            return render_url_template(self.url_template, args)
        except KeyError as exc:
            return ToolResult(
                ok=False,
                error=f"missing required placeholder: {exc.args[0]}",
            )

    def _validate_url(self, url: str) -> str | ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return ToolResult(ok=False, error=f"unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        if not host:
            return ToolResult(ok=False, error="URL has no host component")
        if host not in self.allowed_domains:
            return ToolResult(
                ok=False,
                error=f"domain not allowed: {host}",
                output={"allowed": sorted(self.allowed_domains)},
            )
        return url

    def _request(self, url: str, body: Any | None) -> ToolResult:
        # Merge static headers + static query string. Per-call headers
        # are NOT accepted — that's a deliberate choice: a pre-cooked
        # tool has a fixed contract; arbitrary headers belong to the
        # generic http_request builtin.
        headers = dict(self.static_headers)
        # When a Client is injected (tests), reuse it; otherwise
        # build a short-lived one.
        client_ctx = _NoopExitClient(self.client) if self.client is not None else httpx.Client()
        with (
            client_ctx as client,
            client.stream(
                self.method,
                url,
                timeout=self.timeout_s,
                headers=headers,
                params=self.static_query or None,
                json=body if body is not None else None,
            ) as response,
        ):
            total = 0
            chunks: list[bytes] = []
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > self.max_body_bytes:
                    return ToolResult(
                        ok=False,
                        error=f"response body exceeds {self.max_body_bytes} bytes",
                    )
                chunks.append(chunk)
        raw = b"".join(chunks)
        text = raw.decode("utf-8", errors="replace")
        # Try to parse JSON responses — saves the agent a `json.loads`
        # and gives the planner a structured output it can index into.
        output: Any = text
        if "application/json" in response.headers.get("content-type", "").lower():
            import json

            try:
                output = json.loads(text)
            except ValueError:
                # The server said JSON but sent garbage — keep the
                # text so the agent can still surface what went wrong.
                output = text
        return ToolResult(
            ok=response.is_success,
            output={
                "status_code": response.status_code,
                "body": output,
                "headers": dict(response.headers),
            },
            error=None if response.is_success else f"HTTP {response.status_code}",
        )

    def __call__(self, args: dict[str, Any]) -> ToolResult:
        rendered = self._render(args)
        if isinstance(rendered, ToolResult):
            return rendered
        validated = self._validate_url(rendered)
        if isinstance(validated, ToolResult):
            return validated
        url = validated
        body = args.get("body")
        try:
            return self._request(url, body)
        except httpx.TimeoutException:
            return ToolResult(ok=False, error=f"request timed out after {self.timeout_s}s")
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class HttpEndpointToolSpec:
    """Persisted shape of an http_endpoint Tool row, projected to
    what :class:`HttpEndpointTool` needs at construction time.

    The api-server hands this to the agent-runtime when it ships the
    project's tool catalog. Keeping the projection here (vs a dict)
    means the contract drift between Tool model and executor is
    visible at the type level."""

    name: str
    url_template: str
    method: str = "GET"
    static_headers: dict[str, str] = field(default_factory=dict)
    static_query: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 30.0


def build_http_endpoint_tool(
    spec: HttpEndpointToolSpec,
    *,
    allowed_domains: frozenset[str],
    max_body_bytes: int = 1_000_000,
) -> HttpEndpointTool:
    """Convenience constructor that propagates the per-project
    `allowed_domains` to the tool. The api-server doesn't know what
    domains a project allows; the agent-runtime does, so we bind
    them here at registration time."""
    return HttpEndpointTool(
        name=spec.name,
        url_template=spec.url_template,
        method=spec.method,
        static_headers=dict(spec.static_headers),
        static_query=dict(spec.static_query),
        allowed_domains=allowed_domains,
        timeout_s=spec.timeout_s,
        max_body_bytes=max_body_bytes,
    )


__all__ = [
    "HttpEndpointTool",
    "HttpEndpointToolSpec",
    "build_http_endpoint_tool",
    "render_url_template",
]
