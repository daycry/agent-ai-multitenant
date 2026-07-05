"""G6a: the shell_exec base allowlist includes the read/text utilities.

Models reach for `sed -n`/`awk`/`cut`/… to page and inspect files; denying them
forced sterile retries (audit 2026-07-03, r1: `sed` denied live 2×). Since the
base allowlist already grants destructive `rm`/`mv`/`cp`, adding read-oriented
tools adds no attack surface — the sandbox is the real boundary.
"""

from __future__ import annotations

from workers.execution import _SDK_BASE_SHELL_COMMANDS


def test_read_text_utilities_are_allowed() -> None:
    for cmd in ("sed", "awk", "sort", "uniq", "cut", "tr", "echo"):
        assert cmd in _SDK_BASE_SHELL_COMMANDS, cmd


def test_destructive_commands_still_present() -> None:
    # These justify the "no added surface" argument: writing already possible.
    for cmd in ("rm", "mv", "cp"):
        assert cmd in _SDK_BASE_SHELL_COMMANDS
