"""G6a: the shell_exec base allowlist includes the read/text utilities.

Models reach for `sed -n`/`awk`/`cut`/… to page and inspect files; denying them
forced sterile retries (audit 2026-07-03, r1: `sed` denied live 2×). They add no
attack surface — the sandbox is the real boundary.

The base used to justify itself with «writing is already possible: rm/mv/cp are
here». Since the audit of 2026-09-01 that is no longer the argument: `rm` and
`mv` left the base because they were an unconditional side door around the
tracked-tree guard of ADR 0164 (see `test_agent_spec_sdk_shell_allowlist`).
"""

from __future__ import annotations

from workers.execution import _SDK_BASE_SHELL_COMMANDS


def test_read_text_utilities_are_allowed() -> None:
    for cmd in ("sed", "awk", "sort", "uniq", "cut", "tr", "echo"):
        assert cmd in _SDK_BASE_SHELL_COMMANDS, cmd


def test_tree_destroying_commands_are_not_in_the_base() -> None:
    # `delete_file` / `move_file` are the audited, guarded doors for these.
    for cmd in ("rm", "mv"):
        assert cmd not in _SDK_BASE_SHELL_COMMANDS, cmd


def test_non_destructive_file_commands_stay() -> None:
    # `cp` overwrites at most what `write_file` could; `mkdir`/`rmdir` cannot
    # take a tree away. Nothing here empties a deliverable in one call.
    for cmd in ("cp", "mkdir", "rmdir", "touch"):
        assert cmd in _SDK_BASE_SHELL_COMMANDS, cmd
