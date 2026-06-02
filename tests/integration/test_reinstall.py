"""Reinstall over an existing deployment — reinstall.sh / CLI (task_15_13).

Exercises the reinstall orchestration (:mod:`installer_backend.reinstall` + the
CLI's ``reinstall`` subcommand) with EVERY host-touching action MOCKED behind
injectable seams — NO real detection of ``/data``, NO real ``docker compose
down``, NO real deletion, NO real ``.env`` read. The real bindings are exercised
only by the plan's Tests Humanos (``human_15_04``: "Reinstalación sobre datos
existentes").

Re-running the installer over a machine that may already hold a deployment must
decide what to do with the data already there. This suite pins the contract:

  * PRESERVE keeps the data AND reuses the existing secrets/Vault material (no
    data orphaning — regenerating secrets would orphan the kept encrypted data,
    so PRESERVE must reuse, never regenerate);
  * a FRESH reinstall wipes the data ONLY after the double confirmation (type the
    name + explicit yes); a single/failed confirmation wipes NOTHING;
  * detecting NO prior install behaves like a first install (fresh, no wipe, no
    confirmation — there is no data to destroy).
"""

from __future__ import annotations

import io

import pytest
from installer_backend.cli import ExitCode, FlagConfirmer, main, run_reinstall
from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.reinstall import (
    ExistingInstall,
    ReinstallAbortedError,
    Reinstaller,
    ReinstallMode,
    ReinstallRequest,
    StubExistingSecretLoader,
    StubInstallDetector,
)
from installer_backend.uninstall import (
    ScriptedConfirmer,
    StubDataPurger,
    StubStackTeardown,
)

pytestmark = pytest.mark.integration

_DEPLOYMENT = PROJECT_NAME
_DATA_ROOT = "/data/agent-platform"


def _reinstaller(
    *,
    data_dir_present: bool,
    stack_running: bool,
    confirmer: ScriptedConfirmer,
    secret_available: bool = True,
) -> tuple[
    Reinstaller,
    StubInstallDetector,
    StubExistingSecretLoader,
    StubStackTeardown,
    StubDataPurger,
    io.StringIO,
]:
    """Build a Reinstaller wired to recording fakes; return it + the seams + stdout."""

    detector = StubInstallDetector(data_dir_present=data_dir_present, stack_running=stack_running)
    loader = StubExistingSecretLoader(available=secret_available)
    teardown = StubStackTeardown()
    purger = StubDataPurger()
    out = io.StringIO()
    inst = Reinstaller(
        detector=detector,
        secret_loader=loader,
        teardown=teardown,
        purger=purger,
        confirmer=confirmer,
        out=out,
    )
    return inst, detector, loader, teardown, purger, out


def _request(*, preserve: bool) -> ReinstallRequest:
    return ReinstallRequest(
        preserve=preserve,
        deployment_name=_DEPLOYMENT,
        data_root=_DATA_ROOT,
    )


# ---------------------------------------------------------------------------
# PRESERVE -> data kept + existing secrets/Vault reused (no orphaning).
# ---------------------------------------------------------------------------
def test_preserve_keeps_data_and_reuses_existing_secrets() -> None:
    # Existing install present; operator preserves (no confirmation needed).
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    result = inst.run(_request(preserve=True))

    # The detector probed the right deployment.
    assert detector.probed == (_DATA_ROOT, _DEPLOYMENT)
    # PRESERVE mode: data kept, existing secrets REUSED (no regeneration).
    assert result.mode is ReinstallMode.PRESERVE
    assert result.data_preserved is True
    assert result.reused_existing_secrets is True
    # The existing material was actually loaded + carried for the install to reuse.
    assert loader.loaded is True
    assert result.existing_secrets is not None
    assert result.existing_secrets.vault_unseal_keys  # reused Vault keys present
    assert "POSTGRES_PASSWORD" in result.existing_secrets.env_values
    # The stack was stopped to apply the regenerated config, but WITHOUT removing
    # volumes — and the data purge seam was NEVER called (no orphaning, no wipe).
    assert teardown.torn_down is True
    assert teardown.removed_volumes is False
    assert purger.purged is False
    # Phase order: detect -> preserve -> teardown (no wipe).
    assert inst.phases == ["detect", "preserve", "teardown"]


def test_preserve_refuses_when_existing_secrets_unavailable() -> None:
    # Existing install present, but the old secrets can't be loaded -> a preserve
    # that minted new secrets would ORPHAN the encrypted data, so it must REFUSE
    # rather than silently regenerate.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True,
        stack_running=False,
        confirmer=confirmer,
        secret_available=False,
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=True))

    # It tried to load the existing secrets, found none, and refused — nothing
    # destructive ran and the stack was NOT torn down.
    assert loader.loaded is True
    assert teardown.torn_down is False
    assert purger.purged is False


# ---------------------------------------------------------------------------
# FRESH -> wipes data ONLY after the double confirmation.
# ---------------------------------------------------------------------------
def test_fresh_without_confirmation_wipes_nothing() -> None:
    # Existing install present; FRESH requested but NO confirmation -> abort.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=False))

    # NOTHING destructive ran: stack untouched, data intact.
    assert teardown.torn_down is False
    assert purger.purged is False
    # Only detect + the (failed) fresh-confirm phase ran; no teardown/wipe.
    assert inst.phases == ["detect", "confirm_fresh"]


def test_fresh_with_only_name_is_still_blocked() -> None:
    # Correct name typed, but NO explicit yes -> the double confirm fails.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[False])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=False))

    assert teardown.torn_down is False
    assert purger.purged is False


def test_fresh_wrong_name_is_blocked() -> None:
    # Explicit yes but a different stack name -> the first confirm fails.
    confirmer = ScriptedConfirmer(name_answer="some-other-stack", yes_answers=[True])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(ReinstallAbortedError):
        inst.run(_request(preserve=False))

    assert teardown.torn_down is False
    assert purger.purged is False


def test_fresh_with_both_confirmations_wipes_and_regenerates() -> None:
    # Name typed + explicit yes -> the data is wiped + secrets regenerated.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True])
    inst, _detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    result = inst.run(_request(preserve=False))

    assert result.mode is ReinstallMode.FRESH
    assert result.data_preserved is False
    # FRESH regenerates everything: the existing secrets are NOT reused/loaded.
    assert result.reused_existing_secrets is False
    assert result.existing_secrets is None
    assert loader.loaded is False
    # Stack removed (with its volumes) AND the data root wiped.
    assert teardown.torn_down is True
    assert teardown.removed_volumes is True
    assert purger.purged is True
    assert purger.data_root == _DATA_ROOT
    # Phase order: detect -> confirm_fresh -> teardown -> wipe_data.
    assert inst.phases == ["detect", "confirm_fresh", "teardown", "wipe_data"]


# ---------------------------------------------------------------------------
# No prior install -> behaves like a first install.
# ---------------------------------------------------------------------------
def test_no_prior_install_behaves_like_first_install() -> None:
    # Detector finds nothing; preserve flag is moot.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, loader, teardown, purger, _out = _reinstaller(
        data_dir_present=False, stack_running=False, confirmer=confirmer
    )

    result = inst.run(_request(preserve=True))

    assert result.mode is ReinstallMode.FIRST_INSTALL
    assert result.reused_existing_secrets is False
    assert result.existing_secrets is None
    # Nothing to preserve OR wipe — no secret load, no teardown, no purge.
    assert loader.loaded is False
    assert teardown.torn_down is False
    assert purger.purged is False
    assert inst.phases == ["detect", "first_install"]


def test_no_prior_install_even_when_fresh_requested() -> None:
    # --fresh over an empty machine: still just a first install, no wipe, no
    # confirmation gate (there is no data to destroy).
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, _detector, _loader, teardown, purger, _out = _reinstaller(
        data_dir_present=False, stack_running=False, confirmer=confirmer
    )

    result = inst.run(_request(preserve=False))

    assert result.mode is ReinstallMode.FIRST_INSTALL
    assert teardown.torn_down is False
    assert purger.purged is False
    assert "confirm_fresh" not in inst.phases


def test_stack_running_only_counts_as_present() -> None:
    # Data dir gone but the stack is still up -> still a prior install (present).
    existing = ExistingInstall(data_dir_present=False, stack_running=True)
    assert existing.present is True
    none = ExistingInstall(data_dir_present=False, stack_running=False)
    assert none.present is False


# ---------------------------------------------------------------------------
# The CLI surface — exit codes via run_reinstall() + main().
# ---------------------------------------------------------------------------
def test_run_reinstall_fresh_without_yes_aborts() -> None:
    # FRESH with the right name but no --yes -> ABORTED, nothing removed.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[False])
    inst, _detector, _loader, teardown, _purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(Exception) as exc:
        run_reinstall(
            deployment_name=_DEPLOYMENT,
            data_root=_DATA_ROOT,
            fresh=True,
            confirm_name=_DEPLOYMENT,
            yes=False,
            reinstaller=inst,
            out=out,
        )
    assert getattr(exc.value, "code", None) == ExitCode.ABORTED
    assert teardown.torn_down is False


def test_main_reinstall_preserve_default_succeeds() -> None:
    # Default reinstaller's detector reports "no prior install" -> first install,
    # OK. (Preserve is the default; no --fresh flag.)
    out = io.StringIO()
    code = main(["reinstall"], out=out)
    assert code == int(ExitCode.OK)
    assert "instalación desde cero" in out.getvalue().lower()


def test_run_reinstall_fresh_wrong_name_via_flagconfirmer_aborts() -> None:
    # The flag-derived path (FlagConfirmer) aborts a FRESH wipe when the typed
    # --confirm-name does not match the deployment, even with --yes set.
    confirmer = FlagConfirmer(confirm_name_value="wrong-name", yes=True)
    inst, _detector, _loader, teardown, purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    with pytest.raises(Exception) as exc:
        run_reinstall(
            deployment_name=_DEPLOYMENT,
            data_root=_DATA_ROOT,
            fresh=True,
            confirm_name="wrong-name",
            yes=True,
            reinstaller=inst,
            out=out,
        )
    assert getattr(exc.value, "code", None) == ExitCode.ABORTED
    assert teardown.torn_down is False
    assert purger.purged is False


def test_main_reinstall_fresh_with_both_confirmations_wipes() -> None:
    # FlagConfirmer with the matching name + --yes covers the double confirm.
    confirmer = FlagConfirmer(confirm_name_value=_DEPLOYMENT, yes=True)
    inst, _detector, _loader, teardown, purger, out = _reinstaller(
        data_dir_present=True, stack_running=True, confirmer=confirmer
    )

    code = run_reinstall(
        deployment_name=_DEPLOYMENT,
        data_root=_DATA_ROOT,
        fresh=True,
        confirm_name=_DEPLOYMENT,
        yes=True,
        reinstaller=inst,
        out=out,
    )
    assert code == ExitCode.OK
    assert teardown.torn_down is True
    assert purger.purged is True
