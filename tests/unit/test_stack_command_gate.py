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
    assert _stack_command_allowed("rm -rf /", ["composer"]) == "command not allowed: rm"


def test_empty_allowlist_denies_everything() -> None:
    assert _stack_command_allowed("php spark migrate", []) == "command not allowed: php"


def test_empty_command_is_rejected() -> None:
    assert _stack_command_allowed("   ", ["php"]) == "empty command"
