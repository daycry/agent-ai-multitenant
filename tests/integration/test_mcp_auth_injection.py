"""Plan 05 task_05_05 — Vault auth injection at connect time.

The shape we pin here:

* ``MCPClient.connect`` accepts a ``vault_resolver=`` kwarg.
* When ``config.auth_ref`` is None, the client behaves exactly as
  before (resolver is never consulted).
* When ``config.auth_ref`` is set:
    - resolver missing  → MCPAuthError raised at connect time
    - resolver entry missing → MCPAuthError (no half-open session)
    - resolver returns ``{}`` → MCPAuthError (defensive)
    - resolver returns secret → merged into env (stdio) or headers
      (http) of a *new* config; the original frozen MCPServerConfig
      stays untouched (immutability).
* The agent-runtime adapter (``MCPToolRunner``) forwards the resolver
  through to the underlying client.
* End-to-end stdio: the toy server, spawned with a Vault-injected
  ``TOY_SECRET`` env var, can echo it via the ``secret_echo`` tool —
  proving the secret travelled from the resolver to the child process
  without ever being written to the on-disk config.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from agent_runtime.mcp_tools import MCPToolRunner
from shared_mcp import (
    MCPAuthError,
    MCPClient,
    MCPServerConfig,
    StaticVaultResolver,
    apply_vault_auth,
)

pytestmark = pytest.mark.integration


_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"


def _stdio_config(
    name: str = "toy",
    *,
    auth_ref: str | None = None,
) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        auth_ref=auth_ref,
        timeout_s=15.0,
    )


# ---------------------------------------------------------------------------
# apply_vault_auth — unit-ish: pure function, no I/O
# ---------------------------------------------------------------------------
def test_apply_vault_auth_no_auth_ref_returns_config_unchanged() -> None:
    cfg = _stdio_config()
    out = apply_vault_auth(cfg, resolver=None)
    assert out is cfg


def test_apply_vault_auth_with_auth_ref_but_no_resolver_raises() -> None:
    cfg = _stdio_config(auth_ref="vault:secret/data/toy")
    with pytest.raises(MCPAuthError, match="no VaultResolver"):
        apply_vault_auth(cfg, resolver=None)


def test_apply_vault_auth_unknown_pointer_raises() -> None:
    cfg = _stdio_config(auth_ref="vault:secret/data/missing")
    resolver = StaticVaultResolver(values={"vault:secret/data/other": {"X": "y"}})
    with pytest.raises(MCPAuthError, match="no secret registered"):
        apply_vault_auth(cfg, resolver=resolver)


def test_apply_vault_auth_empty_secret_raises() -> None:
    """A resolver that hands back an empty dict is almost certainly a
    bug — the original config thinks it has auth but we'd be silently
    opening an unauthenticated session. Fail loud."""
    cfg = _stdio_config(auth_ref="vault:secret/data/empty")
    resolver = StaticVaultResolver(values={"vault:secret/data/empty": {}})
    with pytest.raises(MCPAuthError, match="no key/value pairs"):
        apply_vault_auth(cfg, resolver=resolver)


def test_apply_vault_auth_stdio_merges_into_env() -> None:
    cfg = _stdio_config(auth_ref="vault:secret/data/gh")
    resolver = StaticVaultResolver(values={"vault:secret/data/gh": {"GITHUB_TOKEN": "ghp_abc"}})
    out = apply_vault_auth(cfg, resolver=resolver)
    assert out is not cfg  # new instance — original immutable
    assert out.env == {"GITHUB_TOKEN": "ghp_abc"}
    assert cfg.env == {}  # original untouched


def test_apply_vault_auth_http_merges_into_headers() -> None:
    cfg = MCPServerConfig(
        name="gh",
        transport="streamable_http",
        url="https://gh-mcp.example/mcp",
        auth_ref="vault:secret/data/gh",
    )
    resolver = StaticVaultResolver(values={"vault:secret/data/gh": {"Authorization": "Bearer xyz"}})
    out = apply_vault_auth(cfg, resolver=resolver)
    assert out.headers == {"Authorization": "Bearer xyz"}
    assert cfg.headers == {}


def test_apply_vault_auth_resolver_overrides_static_collision() -> None:
    """If the static config sets a key AND the resolver returns the
    same key, the Vault value wins (declaring both is a config smell;
    Vault is the source of truth)."""
    cfg = MCPServerConfig(
        name="x",
        transport="stdio",
        command="echo",
        env={"GITHUB_TOKEN": "from-static"},
        auth_ref="vault:secret/data/gh",
    )
    resolver = StaticVaultResolver(values={"vault:secret/data/gh": {"GITHUB_TOKEN": "from-vault"}})
    out = apply_vault_auth(cfg, resolver=resolver)
    assert out.env["GITHUB_TOKEN"] == "from-vault"


# ---------------------------------------------------------------------------
# MCPClient.connect — resolver threaded through to the transport
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def _no_resolver_marker() -> None:
    """No-op fixture used as a marker for tests that pin the
    `no-resolver` path. Kept separate so future fixtures (e.g. a
    real Vault dev server) can replace it per-test."""
    return None


@pytest.mark.asyncio
async def test_client_connect_without_auth_ref_does_not_need_resolver() -> None:
    """Backward-compat: configs without auth_ref must still work
    even when no resolver is passed."""
    async with MCPClient.connect(_stdio_config()) as session:
        result = await session.call_tool("echo", {"text": "hello"})
        assert "hello" in result.content


@pytest.mark.asyncio
async def test_client_connect_with_auth_ref_but_no_resolver_raises() -> None:
    cfg = _stdio_config(auth_ref="vault:secret/data/anything")
    with pytest.raises(MCPAuthError, match="no VaultResolver"):
        async with MCPClient.connect(cfg):
            pass  # pragma: no cover -- error must fire before the yield


@pytest.mark.asyncio
async def test_client_connect_injects_secret_into_stdio_env() -> None:
    """End-to-end: the resolver's secret reaches the child process'
    environment. Proven by calling the toy server's `secret_echo` tool
    with the env-var name and getting the secret value back."""
    resolver = StaticVaultResolver(
        values={"vault:secret/data/toy": {"TOY_SECRET": "s3cr3t-from-vault"}}
    )
    cfg = _stdio_config(auth_ref="vault:secret/data/toy")
    async with MCPClient.connect(cfg, vault_resolver=resolver) as session:
        result = await session.call_tool("secret_echo", {"env_var": "TOY_SECRET"})
        assert "s3cr3t-from-vault" in result.content


# ---------------------------------------------------------------------------
# MCPToolRunner — resolver forwarded through the sync bridge
# ---------------------------------------------------------------------------
def test_runner_without_resolver_works_when_no_auth_ref() -> None:
    """Default constructor (no resolver) must still drive a session
    whose config has no auth_ref."""
    with MCPToolRunner() as runner:
        tools = runner.connect(_stdio_config("plain"))
        assert {t.name for t in tools} == {"echo", "add", "secret_echo"}


def test_runner_raises_at_connect_when_auth_ref_set_and_no_resolver() -> None:
    """Same shape as the async client: connecting a server with
    auth_ref through a runner that has no resolver must fail at
    connect, not later at first tool call."""
    with (
        MCPToolRunner() as runner,  # no vault_resolver
        pytest.raises(MCPAuthError, match="no VaultResolver"),
    ):
        runner.connect(_stdio_config("needs-auth", auth_ref="vault:secret/data/x"))


def test_runner_injects_secret_via_resolver_end_to_end() -> None:
    """Full sync path: resolver → MCPClient → subprocess env →
    secret_echo tool → registry.call result."""
    resolver = StaticVaultResolver(
        values={
            "vault:secret/data/gh": {"GH_TOKEN_FAKE": "tok-from-vault"},
        }
    )
    with MCPToolRunner(vault_resolver=resolver) as runner:
        runner.connect(_stdio_config("gh", auth_ref="vault:secret/data/gh"))
        # `runner.call_tool` is sync — same path the agent loop uses.
        output = runner.call_tool("gh", "secret_echo", {"env_var": "GH_TOKEN_FAKE"})
        assert "tok-from-vault" in output


# ---------------------------------------------------------------------------
# StaticVaultResolver — resolver contract
# ---------------------------------------------------------------------------
def test_static_resolver_returns_a_copy_not_the_internal_dict() -> None:
    """If a caller mutates the returned dict, the resolver's internal
    state must not change. Critical for reuse across connect()s."""
    resolver = StaticVaultResolver(values={"vault:secret/data/x": {"K": "v"}})
    out = resolver.resolve("vault:secret/data/x")
    out["K"] = "MUTATED"
    out["NEW"] = "added"
    second = resolver.resolve("vault:secret/data/x")
    assert second == {"K": "v"}


def test_static_resolver_unknown_pointer_raises_mcp_auth_error() -> None:
    resolver = StaticVaultResolver(values={})
    with pytest.raises(MCPAuthError, match="no secret registered"):
        resolver.resolve("vault:secret/data/anything")
