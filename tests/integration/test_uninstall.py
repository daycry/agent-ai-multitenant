"""Stack uninstall — uninstall.sh / python -m installer_backend.cli uninstall (task_15_12).

Exercises the gated uninstall orchestration (:mod:`installer_backend.uninstall`
+ the CLI's ``uninstall`` subcommand) with EVERY destructive action MOCKED
behind injectable seams — NO real ``docker compose down``, NO real deletion of
``/data``. The real bindings are exercised only by the plan's Tests Humanos.

The teardown of a production stack is DESTRUCTIVE, so it is doubly gated. This
suite pins the contract from the task:

  * WITHOUT confirmation        -> aborts; the stack is NOT torn down, data intact;
  * with a SINGLE confirmation  -> still blocked (the double confirm is required);
  * with BOTH confirmations     -> stack removed (mock) but data PRESERVED by default;
  * --purge-data wipes data ONLY with its OWN extra confirmation (and never
    otherwise — not even fat-fingered with both stack confirmations).
"""

from __future__ import annotations

import io

import pytest
from installer_backend.cli import ExitCode, FlagConfirmer, main, run_uninstall
from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.uninstall import (
    PurgeLeftover,
    PurgeReport,
    ScriptedConfirmer,
    StubDataPurger,
    StubStackTeardown,
    UninstallAbortedError,
    Uninstaller,
    UninstallRequest,
)

pytestmark = pytest.mark.integration

_DEPLOYMENT = PROJECT_NAME
_DATA_ROOT = "/data/agent-platform"


def _uninstaller(
    confirmer: ScriptedConfirmer,
) -> tuple[Uninstaller, StubStackTeardown, StubDataPurger, io.StringIO]:
    """Build an Uninstaller wired to recording fakes; return it + the seams + stdout."""

    teardown = StubStackTeardown()
    purger = StubDataPurger()
    out = io.StringIO()
    inst = Uninstaller(teardown=teardown, purger=purger, confirmer=confirmer, out=out)
    return inst, teardown, purger, out


def _request(*, purge_data: bool = False) -> UninstallRequest:
    return UninstallRequest(
        deployment_name=_DEPLOYMENT,
        data_root=_DATA_ROOT,
        purge_data=purge_data,
    )


# ---------------------------------------------------------------------------
# WITHOUT confirmation -> aborts, nothing removed.
# ---------------------------------------------------------------------------
def test_no_confirmation_aborts_nothing_removed() -> None:
    # Operator typed nothing for the name + did not say yes.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[])
    inst, teardown, purger, _out = _uninstaller(confirmer)

    with pytest.raises(UninstallAbortedError):
        inst.run(_request())

    # NOTHING destructive ran: stack untouched, data untouched.
    assert teardown.torn_down is False
    assert purger.purged is False
    # Only the (failed) confirm phase was recorded; no teardown/purge.
    assert inst.phases == ["confirm"]


def test_wrong_deployment_name_aborts() -> None:
    # The operator typed a different stack name — must not tear down ours.
    confirmer = ScriptedConfirmer(name_answer="some-other-stack", yes_answers=[True])
    inst, teardown, purger, _out = _uninstaller(confirmer)

    with pytest.raises(UninstallAbortedError):
        inst.run(_request())

    assert teardown.torn_down is False
    assert purger.purged is False


# ---------------------------------------------------------------------------
# SINGLE confirmation -> still blocked (double confirmation required).
# ---------------------------------------------------------------------------
def test_only_name_confirmation_is_still_blocked() -> None:
    # Correct name typed, but NO explicit yes -> the second confirm fails.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[False])
    inst, teardown, purger, _out = _uninstaller(confirmer)

    with pytest.raises(UninstallAbortedError):
        inst.run(_request())

    # Single confirmation is NOT enough — nothing was removed.
    assert teardown.torn_down is False
    assert purger.purged is False


def test_only_yes_confirmation_is_still_blocked() -> None:
    # Explicit yes, but the wrong/empty name -> the first confirm fails.
    confirmer = ScriptedConfirmer(name_answer="", yes_answers=[True])
    inst, teardown, purger, _out = _uninstaller(confirmer)

    with pytest.raises(UninstallAbortedError):
        inst.run(_request())

    assert teardown.torn_down is False
    assert purger.purged is False


# ---------------------------------------------------------------------------
# BOTH confirmations -> stack removed (mock) but data PRESERVED by default.
# ---------------------------------------------------------------------------
def test_both_confirmations_remove_stack_preserve_data() -> None:
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True])
    inst, teardown, purger, _out = _uninstaller(confirmer)

    result = inst.run(_request())

    # Stack torn down (mock) — targeting OUR project, without removing volumes.
    assert teardown.torn_down is True
    assert teardown.project_name == _DEPLOYMENT
    assert teardown.removed_volumes is False
    # Data PRESERVED by default — the purge seam was never called.
    assert purger.purged is False
    assert result.stack_removed is True
    assert result.data_purged is False
    assert result.data_preserved is True
    # Phase order: double-confirm, then teardown, no purge.
    assert inst.phases == ["confirm", "teardown"]


def test_purge_data_without_extra_confirmation_preserves_data() -> None:
    # Double confirm passes (one yes), but the purge's EXTRA confirmation is a
    # *no* -> the stack is removed yet the data is preserved.
    confirmer = ScriptedConfirmer(
        name_answer=_DEPLOYMENT,
        yes_answers=[True, False],  # double-confirm yes, purge extra-confirm no.
    )
    inst, teardown, purger, _out = _uninstaller(confirmer)

    result = inst.run(_request(purge_data=True))

    assert teardown.torn_down is True
    # The purge was REFUSED for lack of its own confirmation.
    assert purger.purged is False
    assert result.data_purged is False
    assert result.data_preserved is True
    # The purge-confirm gate ran, but the purge_data phase did not.
    assert "confirm_purge" in inst.phases
    assert "purge_data" not in inst.phases


# ---------------------------------------------------------------------------
# --purge-data WITH its extra confirmation -> data wiped (mock).
# ---------------------------------------------------------------------------
def test_purge_data_with_extra_confirmation_wipes_data() -> None:
    # Double confirm yes + purge extra-confirm yes -> data is wiped.
    confirmer = ScriptedConfirmer(
        name_answer=_DEPLOYMENT,
        yes_answers=[True, True],
    )
    inst, teardown, purger, _out = _uninstaller(confirmer)

    result = inst.run(_request(purge_data=True))

    assert teardown.torn_down is True
    assert purger.purged is True
    assert purger.data_root == _DATA_ROOT
    assert result.data_purged is True
    assert result.data_preserved is False
    # Toda confirmación va ANTES de cualquier destrucción. El orden anterior
    # (`confirm` → `teardown` → `confirm_purge`) preguntaba por los datos con el
    # stack ya eliminado, lo que era inocuo mientras el teardown no tocaba
    # volúmenes — pero ahora una purga confirmada se lleva también los volúmenes
    # nombrados, y decidirlo después de haberlos borrado sería destruir por
    # detrás de su propia puerta.
    assert inst.phases == ["confirm", "confirm_purge", "teardown", "purge_data"]


# ---------------------------------------------------------------------------
# The CLI surface — exit codes via run_uninstall() + main().
# ---------------------------------------------------------------------------
def test_run_uninstall_without_yes_aborts() -> None:
    # FlagConfirmer with the right name but no --yes -> ABORTED, nothing removed.
    teardown = StubStackTeardown()
    purger = StubDataPurger()
    out = io.StringIO()
    inst = Uninstaller(
        teardown=teardown,
        purger=purger,
        confirmer=FlagConfirmer(confirm_name_value=_DEPLOYMENT, yes=False),
        out=out,
    )
    # run_uninstall does NOT swallow the abort — it raises CliError carrying the
    # exit code; main() is what maps that to a process exit code.
    with pytest.raises(Exception) as exc:
        run_uninstall(
            deployment_name=_DEPLOYMENT,
            data_root=_DATA_ROOT,
            purge_data=False,
            confirm_name=_DEPLOYMENT,
            yes=False,
            uninstaller=inst,
            out=out,
            dry_run=True,  # injected stub seams: an explicit simulation
        )
    assert getattr(exc.value, "code", None) == ExitCode.ABORTED
    assert teardown.torn_down is False


def test_main_uninstall_without_confirmation_is_aborted_exit() -> None:
    # No --confirm-name / --yes -> the CLI returns the ABORTED exit code.
    out = io.StringIO()
    code = main(["uninstall"], out=out)
    assert code == int(ExitCode.ABORTED)


def test_main_uninstall_with_both_confirmations_succeeds() -> None:
    out = io.StringIO()
    # --dry-run: the default uninstall now wires the REAL teardown (docker compose
    # down); the dry run exercises the CLI gating + exit codes with stub seams.
    code = main(
        ["uninstall", "--confirm-name", _DEPLOYMENT, "--yes", "--dry-run"],
        out=out,
    )
    assert code == int(ExitCode.OK)
    # Data preserved by default — the success log says so.
    text = out.getvalue()
    assert "CONSERVADOS" in text


def test_main_uninstall_wrong_name_is_aborted_exit() -> None:
    out = io.StringIO()
    code = main(
        ["uninstall", "--confirm-name", "not-the-stack", "--yes"],
        out=out,
    )
    assert code == int(ExitCode.ABORTED)


def test_main_uninstall_purge_data_with_yes_wipes() -> None:
    # --purge-data + --yes covers BOTH the double confirm and the purge extra
    # confirm (the FlagConfirmer answers yes to every yes/no gate); --purge-data
    # is the explicit opt-in that lets the data gate run at all.
    out = io.StringIO()
    # --dry-run so the purge runs against the stub seam, not a real rmtree.
    code = main(
        ["uninstall", "--confirm-name", _DEPLOYMENT, "--yes", "--purge-data", "--dry-run"],
        out=out,
    )
    assert code == int(ExitCode.OK)
    assert "ELIMINADOS" in out.getvalue()


# ---------------------------------------------------------------------------
# Los volúmenes NOMBRADOS del stack (auditoría 2026-08-27, menor).
#
# `voice_mode` se deriva a «cpu» aunque el operador no ponga el campo, así que
# TODA instalación por defecto crea los servicios stt/tts y con ellos el volumen
# `whisper_models`, donde se descargan varios GB de modelos. El teardown pasaba
# `remove_volumes=False` SIEMPRE, con el comentario «los datos viven en el bind
# mount» — cierto para los datos, falso para este volumen. `docker volume ls` lo
# seguía mostrando después de un `--purge-data` que decía «Datos ELIMINADOS».
# ---------------------------------------------------------------------------
def test_confirmed_purge_also_removes_the_named_volumes() -> None:
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True, True])
    inst, teardown, purger, out = _uninstaller(confirmer)

    result = inst.run(_request(purge_data=True))

    assert teardown.removed_volumes is True, (
        "una purga confirmada tiene que llevarse `docker compose down -v`"
    )
    assert result.volumes_removed is True
    assert purger.purged is True
    # Y el operador lo LEE: un borrado que no se nombra es indistinguible de uno
    # que no ocurrió.
    assert "whisper_models" in out.getvalue()


def test_declined_purge_leaves_the_named_volumes_alone() -> None:
    # La otra mitad de la garantía: si la confirmación extra de la purga se
    # deniega, los volúmenes NO se tocan — la pregunta se hace antes del down.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True, False])
    inst, teardown, purger, out = _uninstaller(confirmer)

    result = inst.run(_request(purge_data=True))

    assert teardown.torn_down is True
    assert teardown.removed_volumes is False
    assert result.volumes_removed is False
    assert purger.purged is False
    assert "whisper_models" not in out.getvalue()


def test_uninstall_without_purge_keeps_volumes_for_a_later_reinstall() -> None:
    # El caso por defecto no cambia: sin --purge-data se conservan datos Y
    # volúmenes, que es lo que permite reinstalar encima.
    confirmer = ScriptedConfirmer(name_answer=_DEPLOYMENT, yes_answers=[True])
    inst, teardown, _purger, _out = _uninstaller(confirmer)

    result = inst.run(_request())

    assert teardown.removed_volumes is False
    assert result.volumes_removed is False


def test_run_uninstall_exits_with_an_error_when_the_purge_was_incomplete() -> None:
    # Lo que ve la automatización. El desinstalador ya NO dice «Datos
    # ELIMINADOS» si algo sobrevivió, pero devolver 0 igualmente dejaría a un
    # script de decomiso marcando la máquina como limpia con el .env dentro.
    class _PartialPurger:
        """Un purgador que borra la mitad, como un punto de montaje ocupado."""

        purged = False

        def purge(self, data_root: str) -> PurgeReport:
            self.purged = True
            return PurgeReport(
                lines=[f"base de datos: eliminada bajo {data_root}"],
                leftovers=(
                    PurgeLeftover(path=f"{data_root}/.env", reason="Device or resource busy"),
                ),
            )

    out = io.StringIO()
    inst = Uninstaller(
        teardown=StubStackTeardown(),
        purger=_PartialPurger(),
        confirmer=FlagConfirmer(confirm_name_value=_DEPLOYMENT, yes=True),
        out=out,
    )
    with pytest.raises(Exception) as exc:
        run_uninstall(
            deployment_name=_DEPLOYMENT,
            data_root=_DATA_ROOT,
            purge_data=True,
            confirm_name=_DEPLOYMENT,
            yes=True,
            uninstaller=inst,
            out=out,
            dry_run=True,
        )
    assert getattr(exc.value, "code", None) == ExitCode.INCOMPLETE
    assert ".env" in str(exc.value)
