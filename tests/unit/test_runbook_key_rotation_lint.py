"""Every secret the platform holds has an entry in the rotation runbook (task_prod05_09).

The audit's gap2-6 was not "the runbook is thin". It was that the runbook omitted
**six of the eight** key families and described verifications the code could not
perform. Both halves are the same failure: prose that drifted away from the code
with nothing to notice.

A prose review cannot fix that, because the drift happens later — the day someone
adds a seventh `SecretStr` to `Settings` and nobody remembers this file exists. So
the runbook is linted against the CODE:

* every ``SecretStr`` setting in the three services must be named in the runbook,
  by env var, or be listed here as deliberately out of scope with a reason;
* the two env-only backup key variables (they are read by ``EnvSecretsProvider``,
  not by a ``Settings`` field, so no model discovers them) must be named too;
* the claims the previous version made and the code could not honour must be
  gone, and stay gone.

Every assertion is preceded by one that the discovery actually found something —
a lint that stops discovering passes vacuously forever (see
docs/03-guides/verificar-antes-de-implementar.md §4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK = _REPO_ROOT / "docs/06-runbooks/05-key-rotation.md"

#: (settings module, env prefix) for the three services that hold secrets.
_SERVICES = (
    ("api_server.config", "API_SERVER_"),
    ("workers.config", "WORKERS_"),
    ("notification_dispatcher.config", "NOTIFY_"),
)

#: Secrets that are read straight from the environment rather than through a
#: ``Settings`` field, so no model introspection can find them. Kept as an
#: explicit list precisely because they are invisible to the discovery — and the
#: backup key is the one whose loss is unrecoverable.
_ENV_ONLY_SECRETS = (
    "WORKERS_BACKUP_ENCRYPTION_KEY",
    "WORKERS_BACKUP_ENCRYPTION_KEYS",
)

#: Deliberately NOT in the runbook, each with the reason. Anything added here is a
#: decision, not an omission — which is the whole point of making the exception
#: list explicit rather than filtering by a clever pattern.
_OUT_OF_SCOPE: dict[str, str] = {}


def _runbook() -> str:
    assert _RUNBOOK.is_file(), f"the canonical rotation runbook is missing: {_RUNBOOK}"
    return _RUNBOOK.read_text(encoding="utf-8")


def _discovered_secret_env_vars() -> dict[str, str]:
    """Every ``SecretStr`` setting across the three services, as ENV VAR names."""
    import importlib

    discovered: dict[str, str] = {}
    for module_path, prefix in _SERVICES:
        settings_cls = importlib.import_module(module_path).Settings
        for name, field in settings_cls.model_fields.items():
            if "SecretStr" in str(field.annotation):
                discovered[f"{prefix}{name.upper()}"] = module_path
    return discovered


def test_the_discovery_finds_the_secrets_it_is_supposed_to_lint() -> None:
    """Guard on the guard. If the introspection breaks (a rename, a move to
    ``str``), every assertion below would pass on an empty set."""
    discovered = _discovered_secret_env_vars()
    assert len(discovered) >= 15, f"the discovery stopped finding settings: {sorted(discovered)}"
    # A few anchors: if any of these disappears from the scan, the scan is wrong,
    # not the platform.
    for anchor in (
        "API_SERVER_JWT_SECRET",
        "API_SERVER_SSO_ENCRYPTION_KEY",
        "NOTIFY_NOTIFICATION_ENCRYPTION_KEY",
    ):
        assert anchor in discovered, f"{anchor} vanished from the discovery"


def test_every_secret_setting_is_named_in_the_runbook() -> None:
    """gap2-6, made unrepeatable. A secret with no runbook entry is a secret
    nobody knows how to rotate — and the operator only finds out mid-incident."""
    runbook = _runbook()
    discovered = _discovered_secret_env_vars()
    missing = sorted(
        f"{var} (from {module})"
        for var, module in discovered.items()
        if var not in runbook and var not in _OUT_OF_SCOPE
    )
    assert not missing, (
        "these secrets have no entry in docs/06-runbooks/05-key-rotation.md. "
        "Add a row to the exhaustive table (with its blast radius and either a "
        "procedure or an explicit 'SIN CAMINO DE ROTACIÓN'), or justify the "
        "exception in _OUT_OF_SCOPE:\n  " + "\n  ".join(missing)
    )


def test_the_env_only_backup_keys_are_named_too() -> None:
    """No ``Settings`` field declares them, so nothing else would ever notice
    their absence — and they are the keys whose loss cannot be undone."""
    runbook = _runbook()
    for var in _ENV_ONLY_SECRETS:
        assert var in runbook, f"{var} is not documented in the rotation runbook"


def test_the_plural_ring_variables_are_documented_next_to_their_singular() -> None:
    """A runbook that only names ``*_KEY`` would send the operator to overwrite the
    key in place — which is the destructive operation the rings exist to replace.
    """
    runbook = _runbook()
    rings = [var for var in _discovered_secret_env_vars() if var.endswith(("_KEYS", "_SECRETS"))]
    assert len(rings) >= 6, f"the ring discovery found too few variables: {rings}"
    missing = sorted(var for var in rings if var not in runbook)
    assert not missing, f"ring variables absent from the runbook: {missing}"


def test_the_three_step_rotation_and_the_reencrypt_command_are_documented() -> None:
    """The procedure the code implements. Naming the command matters: step 2 is
    the one whose omission turns step 3 into data loss."""
    runbook = _runbook()
    for fragment in (
        "python -m api_server.cli reencrypt-secrets",
        "--dry-run",
        "--families",
    ):
        assert fragment in runbook, f"the runbook never mentions {fragment!r}"


def test_the_claim_the_code_cannot_honour_is_gone() -> None:
    """The previous runbook promised that services pick up a rotated secret
    "sin reinicio". Every ``get_settings`` in the three services is
    ``@lru_cache``-d and nobody reads Vault at runtime, so that sentence was
    false — and it is the sentence an operator would have trusted while planning
    a rotation with no maintenance window.

    Asserts the claim is absent EXCEPT where the runbook explicitly retracts it
    (the "lo que cambió" section says the old text was false, and must keep
    saying so).
    """
    lines = _runbook().splitlines()
    offenders = [
        line.strip()
        for line in lines
        if "sin reinicio" in line.lower()
        and "falso" not in line.lower()
        and "en caliente" not in line.lower()
    ]
    assert not offenders, (
        "the runbook claims a rotated secret takes effect without a restart "
        "again:\n  " + "\n  ".join(offenders)
    )


def test_the_emergency_revocation_no_longer_delegates_to_the_scheduled_job() -> None:
    """gap2-1's documentation half. Pointing emergency revocation at a weekly beat
    job is wrong even now that the job is real: it runs on a cadence and its
    result needs a manual propagation step to take effect."""
    runbook = _runbook()
    start = runbook.find("## Revocación de emergencia")
    assert start != -1, "the emergency-revocation section disappeared from the runbook"
    section = runbook[start:]
    assert "no es el camino de emergencia" in section or "sigue sin ser el camino" in section, (
        "the emergency-revocation section must say explicitly that the scheduled "
        "rotation job is NOT the emergency path"
    )


def test_the_keys_without_a_rotation_path_are_marked_as_such() -> None:
    """Postgres' static password has no mechanism. Saying so is the honest
    outcome; leaving it out of the table would read as "not applicable"."""
    runbook = _runbook()
    assert "SIN CAMINO DE ROTACIÓN" in runbook
    assert "API_SERVER_DATABASE_URL" in runbook


def test_the_backup_key_retention_rule_is_stated() -> None:
    """The one rule this codebase cannot enforce: a deleted backup key is a lost
    bundle. If it is not in the runbook, it exists nowhere the operator reads."""
    runbook = _runbook()
    assert "conserva toda clave de backup retirada" in runbook.lower()


def test_the_mfa_break_glass_is_documented_with_the_exact_setting() -> None:
    """The lockout it unblocks locks the admin out of the surface they would use
    to fix it, so the escape hatch has to be a literal env var, not advice."""
    runbook = _runbook()
    assert "API_SERVER_ADMIN_REQUIRE_MFA=false" in runbook
    assert "API_SERVER_ADMIN_REQUIRE_MFA=true" in runbook, (
        "the break-glass must also say to turn it back ON — that is the step " "people forget"
    )


def test_the_runbook_has_no_leftover_tool_markup() -> None:
    """The file shipped with a stray ``</content></invoke>`` at the end of a
    previous authoring session, committed and unnoticed. Cheap to pin."""
    runbook = _runbook()
    for marker in ("</content>", "</invoke>", "<function_calls", "<parameter"):
        assert marker not in runbook, f"leftover tool markup in the runbook: {marker}"
