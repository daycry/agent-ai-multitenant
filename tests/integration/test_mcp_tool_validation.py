"""Plan 06.14 task_06_14_12 — MCP arg validation + output cap.

Two hardening guards on the MCP→ToolRegistry adapter
(`agent_runtime.mcp_tools`):

  * **mcp-tools-1** — before a tool is invoked, its args are validated
    against the tool's advertised JSON Schema (`input_schema`). Invalid
    args fold into ``ToolResult(ok=False)`` with a clear message and the
    wire call is *never* made, so garbage never reaches the server.
  * **mcp-tools-2** — the tool's text output is capped at the owning
    server's ``MCPServerConfig.max_output_bytes`` (default 64 KiB) with a
    visible truncation marker before it is returned to the agent loop, so
    a chatty or malicious server cannot exhaust the LLM context window.

These mirror ``test_mcp_adapter.py``: the toy server (`_toy_mcp_server.py`)
exposes ``echo(text:str)`` + ``add(a:int,b:int)``; ``echo`` conveniently
returns its input verbatim, so a large ``text`` exercises the output cap
without a bespoke server tool. The runner uses a background thread so we
DELIBERATELY exercise it from sync test functions, matching the agent
loop's call pattern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent_runtime.mcp_tools import (
    _DEFAULT_MAX_OUTPUT_BYTES,
    MCPToolRunner,
    _truncate_output,
    _validate_args,
    register_mcp_server,
)
from agent_runtime.tools import ToolRegistry
from shared_mcp import MCPServerConfig

pytestmark = pytest.mark.integration


_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"

_ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}
_ADD_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
}


def _stdio_config(name: str = "toy", *, max_output_bytes: int | None = None) -> MCPServerConfig:
    kwargs: dict[str, object] = {}
    if max_output_bytes is not None:
        kwargs["max_output_bytes"] = max_output_bytes
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        timeout_s=15.0,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Pure-helper coverage (no I/O) — fast, deterministic edge cases.
# ---------------------------------------------------------------------------
def test_validate_args_accepts_valid_payload() -> None:
    assert _validate_args({"text": "hi"}, _ECHO_SCHEMA) is None
    assert _validate_args({"a": 1, "b": 2}, _ADD_SCHEMA) is None


def test_validate_args_rejects_missing_required_field() -> None:
    msg = _validate_args({}, _ECHO_SCHEMA)
    assert msg is not None
    assert "text" in msg


def test_validate_args_rejects_wrong_type() -> None:
    msg = _validate_args({"a": "not-an-int", "b": 2}, _ADD_SCHEMA)
    assert msg is not None
    # json_path points at the offending field.
    assert "a" in msg


def test_validate_args_skips_when_no_schema() -> None:
    """Tools that publish no schema (or an empty / type-less one) accept
    anything — we must not block them."""
    assert _validate_args({"anything": 1}, {}) is None
    assert _validate_args({"anything": 1}, {"title": "X"}) is None
    assert _validate_args({"anything": 1}, None) is None  # type: ignore[arg-type]


def test_validate_args_ignores_malformed_schema() -> None:
    """A server that publishes a broken schema must not brick its tools —
    validation is skipped (logged), not turned into a hard rejection."""
    bad_schema = {"type": "object", "properties": {"x": {"type": "not-a-real-type"}}}
    assert _validate_args({"x": 1}, bad_schema) is None


def test_truncate_output_passthrough_under_limit() -> None:
    assert _truncate_output("hello", 64) == "hello"


def test_truncate_output_marks_when_over_limit() -> None:
    out = _truncate_output("x" * 1000, 100)
    assert "[output truncated at 100 bytes]" in out
    # The head is at most `max_bytes` bytes of payload.
    head = out.split("\n…[output truncated", 1)[0]
    assert len(head.encode("utf-8")) <= 100


def test_truncate_output_byte_bounded_for_multibyte() -> None:
    """A cut mid-multibyte-sequence must not raise; the partial char is
    dropped (errors='ignore')."""
    text = "é" * 1000  # 2 bytes each in UTF-8
    out = _truncate_output(text, 101)  # odd boundary splits a char
    assert "[output truncated at 101 bytes]" in out


def test_truncate_output_nonpositive_limit_is_noop() -> None:
    assert _truncate_output("anything", 0) == "anything"


# ---------------------------------------------------------------------------
# mcp-tools-1 — arg validation through the live adapter (happy + denial)
# ---------------------------------------------------------------------------
def test_valid_args_round_trip_through_registry() -> None:
    """Happy path: valid args pass validation and the tool runs."""
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        register_mcp_server(registry, runner, "toy")

        result = registry.call("toy.add", {"a": 2, "b": 3})
        assert result.ok is True
        assert result.output == 5


def test_invalid_args_rejected_before_wire_call() -> None:
    """Denial path: args that violate the tool's schema are rejected with
    a clear ToolResult error — the server is never contacted."""
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        register_mcp_server(registry, runner, "toy")

        # `add` requires integers; pass a string for `a`.
        result = registry.call("toy.add", {"a": "oops", "b": 3})
        assert result.ok is False
        assert result.error is not None
        assert "invalid arguments for toy.add" in result.error


def test_missing_required_arg_rejected() -> None:
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        register_mcp_server(registry, runner, "toy")

        result = registry.call("toy.echo", {})  # `text` is required
        assert result.ok is False
        assert result.error is not None
        assert "invalid arguments for toy.echo" in result.error
        assert "text" in result.error


def test_validation_does_not_block_schema_less_string_call() -> None:
    """When registered via a bare name string, validation is skipped and
    the call still works (back-compat with callers that only know names)."""
    from agent_runtime.mcp_tools import _make_tool_fn

    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy"))
        registry.register("toy.echo", _make_tool_fn(runner, "toy", "echo"))
        result = registry.call("toy.echo", {"text": "hi"})
        assert result.ok is True
        assert result.output == "hi"


# ---------------------------------------------------------------------------
# mcp-tools-2 — output cap through the live adapter
# ---------------------------------------------------------------------------
def test_oversized_output_truncated_with_marker() -> None:
    """A tool returning more than `max_output_bytes` is truncated and the
    marker is present so the model knows data was omitted."""
    registry = ToolRegistry()
    cap = 512
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy", max_output_bytes=cap))
        register_mcp_server(registry, runner, "toy")

        big = "A" * 50_000
        result = registry.call("toy.echo", {"text": big})
        assert result.ok is True
        assert isinstance(result.output, str)
        assert f"[output truncated at {cap} bytes]" in result.output
        # Payload portion is capped near the limit (marker adds a little).
        assert len(result.output.encode("utf-8")) < cap + 100


def test_output_under_cap_returned_verbatim() -> None:
    """Output below the cap is untouched (no marker)."""
    registry = ToolRegistry()
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("toy", max_output_bytes=65536))
        register_mcp_server(registry, runner, "toy")

        small = "hello world"
        result = registry.call("toy.echo", {"text": small})
        assert result.ok is True
        assert result.output == small
        assert "truncated" not in str(result.output)


def test_per_server_cap_is_read_from_config() -> None:
    """The cap is the per-server `MCPServerConfig.max_output_bytes`, not a
    hardcoded constant — two servers with different caps truncate at their
    own configured size."""
    with MCPToolRunner() as runner:
        runner.connect(_stdio_config("tiny", max_output_bytes=128))
        runner.connect(_stdio_config("roomy", max_output_bytes=4096))
        assert runner.max_output_bytes("tiny") == 128
        assert runner.max_output_bytes("roomy") == 4096
        # Unknown / closed server falls back to the module default.
        assert runner.max_output_bytes("ghost") == _DEFAULT_MAX_OUTPUT_BYTES


def test_default_cap_matches_audit_64kib() -> None:
    """Regression guard: the documented default is 64 KiB (mcp-tools-2)."""
    cfg = _stdio_config("toy")
    assert cfg.max_output_bytes == 65536
    assert _DEFAULT_MAX_OUTPUT_BYTES == 65536
