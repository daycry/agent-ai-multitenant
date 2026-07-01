"""Unit: the `stack_exec` command allowlist gate (ADR 0093).

`stack_exec` runs a command in the project's stack runtime via the worker. Before
running anything the worker gates the command against the project's
`allowed_commands` (deny-by-default, ADR 0045) — the same envelope as `shell_exec`.
"""

from __future__ import annotations

import pytest
from workers.tasks import _stack_command_allowed

pytestmark = pytest.mark.unit


def test_allowed_basename_passes() -> None:
    assert _stack_command_allowed("composer install --no-interaction", ["composer", "php"]) is None


def test_full_relative_token_passes() -> None:
    # The PHP preset stores `vendor/bin/phpunit` as a relative token, not a basename.
    assert _stack_command_allowed("vendor/bin/phpunit --testdox", ["vendor/bin/phpunit"]) is None


def test_basename_of_a_path_command_passes_when_basename_allowed() -> None:
    assert _stack_command_allowed("vendor/bin/phpunit", ["phpunit"]) is None


def test_disallowed_command_is_denied_by_basename() -> None:
    # The deny STARTS with the stable prefix (log asserts rely on it) and now also
    # lists the allowlist so the model can self-correct.
    msg = _stack_command_allowed("rm -rf /", ["composer"])
    assert msg is not None and msg.startswith("command not allowed: rm")
    assert "Allowed: ['composer']" in msg


def test_empty_allowlist_denies_everything() -> None:
    msg = _stack_command_allowed("php spark migrate", [])
    assert msg is not None and msg.startswith("command not allowed: php")
    assert "none configured" in msg


def test_empty_command_is_rejected() -> None:
    assert _stack_command_allowed("   ", ["php"]) == "empty command"


# --- actionable deny for shell chaining (2026-07-01) ---------------------------
# The agent kept trying `bash -lc "a && b"` to chain, got an opaque "not allowed:
# bash", then churned on reads. The deny now tells it to issue one command per call.
def test_bash_chaining_deny_is_actionable() -> None:
    msg = _stack_command_allowed('bash -lc "composer validate && composer audit"', ["composer"])
    assert msg is not None and msg.startswith("command not allowed: bash")
    assert "shell chaining" in msg and "separate call" in msg


def test_chaining_operators_get_the_hint_even_for_allowed_program() -> None:
    # `composer x && composer y` — composer IS allowed, but shlex parses the first
    # token as `composer`; the '&&' still surfaces the chaining guidance.
    msg = _stack_command_allowed("composer x && composer y", ["php"])
    assert msg is not None and "shell chaining" in msg


def test_single_allowed_command_still_passes_clean() -> None:
    # No false positive: an allowed single command is untouched (returns None).
    assert _stack_command_allowed("composer audit --locked", ["composer"]) is None
