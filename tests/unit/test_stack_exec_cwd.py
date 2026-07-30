"""ADR 0093 (2026-07-24) — ``stack_exec`` gains an optional ``cwd`` so a project
scaffolded under a subdirectory (e.g. CI4's ``ci4build/``) runs its toolchain
there instead of failing from the worktree root ("vendor/bin/phpunit: not
found"). Unit tests for the pure ``_apply_cwd`` helper: it prefixes ``cd`` when
a cwd is given, leaves the command untouched otherwise, and refuses to escape
the worktree (absolute paths made relative; ``..``/unsafe chars rejected)."""

from __future__ import annotations

import pytest
from workers.test_runtime import InvalidCwdError, _apply_cwd

pytestmark = pytest.mark.unit

_CMD = "vendor/bin/phpunit tests/E2E/HomeSmokeTest.php"


def test_no_cwd_leaves_command_unchanged() -> None:
    assert _apply_cwd(_CMD, None) == _CMD
    assert _apply_cwd(_CMD, "") == _CMD
    assert _apply_cwd(_CMD, "   ") == _CMD


def test_relative_cwd_prefixes_cd() -> None:
    assert _apply_cwd(_CMD, "ci4build") == f"cd ci4build && {_CMD}"
    assert _apply_cwd(_CMD, "packages/api") == f"cd packages/api && {_CMD}"


def test_absolute_path_is_forced_relative_to_worktree() -> None:
    # A leading slash is stripped → the command can never target the container's
    # real root; it stays inside the worktree.
    assert _apply_cwd(_CMD, "/ci4build") == f"cd ci4build && {_CMD}"
    assert _apply_cwd(_CMD, "/etc/") == f"cd etc && {_CMD}"


def test_parent_traversal_is_rejected() -> None:
    for bad in ("..", "../x", "a/../b", "ci4build/../../etc", "./x"):
        with pytest.raises(InvalidCwdError):
            _apply_cwd(_CMD, bad)


def test_unsafe_characters_are_rejected() -> None:
    for bad in ("foo; rm -rf /", "a b", "x$(id)", "a|b", "a&b", "a`b`"):
        with pytest.raises(InvalidCwdError):
            _apply_cwd(_CMD, bad)


def test_cwd_cannot_break_the_sh_quoting() -> None:
    # Even a value with a quote is rejected before it reaches the shell wrapper.
    with pytest.raises(InvalidCwdError):
        _apply_cwd(_CMD, "a' && rm -rf / #")
