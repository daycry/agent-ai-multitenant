"""Every session-authenticated WebSocket goes through the Origin gate.

`tests/unit/test_ws_origin_gate.py` proves the PREDICATE is right. This proves
it is CALLED — the failure mode the ADR names («si se olvida, la migración a
cookie EMPEORA la postura del WebSocket») is not a wrong predicate, it is an
endpoint that quietly resolves the principal on its own.

The guard asserts it FOUND the handlers before asserting they are clean: a
static check that stops matching anything passes vacuously forever, which is
how this kind of test rots without anyone noticing.

``/ws/review/{id}/logs`` is deliberately out of scope: it authenticates with an
HMAC-signed URL and never touches the session, so an ambient cookie grants
nothing there.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROUTERS = Path(__file__).resolve().parents[2] / "apps/api-server/src/api_server/routers"

# The modules whose sockets authenticate with the USER SESSION.
_SESSION_WS_MODULES = ("ws.py", "cortex_ws.py", "cortex_voice.py", "assistant_voice.py")


def _websocket_handlers(module: Path) -> list[ast.AsyncFunctionDef]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    handlers: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            func = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(func, ast.Attribute) and func.attr == "websocket":
                handlers.append(node)
    return handlers


def _calls(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names


def test_every_session_websocket_authenticates_through_the_origin_gate() -> None:
    seen: list[str] = []
    offenders: list[str] = []

    for name in _SESSION_WS_MODULES:
        module = _ROUTERS / name
        assert module.exists(), f"{name} moved — update this guard, do not delete it"
        for handler in _websocket_handlers(module):
            seen.append(f"{name}::{handler.name}")
            called = _calls(handler)
            if "_authenticate_socket" not in called:
                offenders.append(f"{name}::{handler.name}")
            # Resolving the principal directly is the exact bypass: it skips
            # the Origin check that `_authenticate_socket` performs.
            if "_resolve_principal" in called:
                offenders.append(f"{name}::{handler.name} (calls _resolve_principal directly)")

    assert len(seen) >= 7, f"the guard stopped finding WS handlers (found {seen})"
    assert not offenders, f"WebSocket handlers bypassing the Origin gate: {offenders}"
