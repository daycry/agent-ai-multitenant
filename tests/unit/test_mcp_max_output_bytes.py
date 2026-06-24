"""MCPServerConfig.max_output_bytes must be configurable end-to-end
(auditoría zona 'tools-mcp-skills', hallazgo medium/gap).

Regression: the dataclass had ``max_output_bytes`` (per-server output cap) but
``MCPServerConfigModel`` (extra='forbid') rejected it with 422 and the converters
never propagated it → the cap was a fixed, unconfigurable 64 KiB.
"""

from __future__ import annotations

import pytest
from api_server.mcp.config import MCPServerConfigModel
from api_server.routers.mcp import _to_runtime_config
from pydantic import ValidationError


def _stdio(**kw: object) -> MCPServerConfigModel:
    base: dict[str, object] = {"name": "docling", "transport": "stdio", "command": "docling-mcp"}
    base.update(kw)
    return MCPServerConfigModel(**base)  # type: ignore[arg-type]


def test_model_accepts_max_output_bytes() -> None:
    assert _stdio(max_output_bytes=131072).max_output_bytes == 131072


def test_model_default_is_64kib() -> None:
    assert _stdio().max_output_bytes == 65536


def test_model_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        _stdio(max_output_bytes=10)  # below the floor


def test_converter_propagates_max_output_bytes() -> None:
    assert _to_runtime_config(_stdio(max_output_bytes=131072)).max_output_bytes == 131072


def test_converter_propagates_default() -> None:
    assert _to_runtime_config(_stdio()).max_output_bytes == 65536
