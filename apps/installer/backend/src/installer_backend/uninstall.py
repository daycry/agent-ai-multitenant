"""Uninstall orchestration — ``uninstall.sh`` / the CLI (Plan 15 task_15_12).

Tearing down a *production* stack is irreversible, so the uninstall is gated by
a **double confirmation** and, separately, an **extra** confirmation before it
will ever wipe persistent data. This module owns that orchestration; every
host-touching action (``docker compose down``, deleting ``/data``) is an
injectable seam (mocked in tests, real binding only exercised by the plan's
Tests Humanos).

The teardown contract
---------------------
``uninstall`` does, in order:

    1. Confirmation gate — TWO independent confirmations are required before any
       destructive action: the operator must (a) type the exact deployment name
       (proving they know *which* stack they are tearing down — the analogue of
       GitHub's "type the repo name to delete") AND (b) give an explicit yes
       (``--yes`` on the CLI, or an interactive "y"). A single confirmation is
       NOT enough — both must pass or the whole run ABORTS with nothing removed.
    2. Stack teardown — ``docker compose -p <project> down`` stops + removes the
       containers + network. By DEFAULT the data volumes / bind-mounted data
       tree are PRESERVED (``down`` without ``-v``; the data root is left on
       disk) so a reinstall (task 15_13) can reuse them.
    3. Data purge (OPT-IN) — only when ``--purge-data`` is given AND its OWN
       extra confirmation passes does the uninstall wipe the data root
       (``/data/agent-platform``). Without that extra confirmation the purge is
       refused even if ``--purge-data`` was on the command line, so a fat-finger
       can never delete data.

Abort semantics
---------------
Any failed confirmation raises :class:`UninstallAbortedError` and NOTHING is touched
— the teardown seam is never called, the purge seam is never called. The data
root is never wiped unless BOTH the double confirmation AND the purge's extra
confirmation succeed. This is the whole point of the task: a destructive
production teardown must be hard to trigger by accident.

Nothing here performs real I/O. The concrete host bindings (subprocess to
``docker compose down``, ``shutil.rmtree`` of the data root) live behind the
Protocols below; the default stubs record the calls so the orchestration is
asserted with no Docker host and no writes to ``/data``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TextIO, runtime_checkable

# The generated stack's compose project name — the same constant the compose
# generator stamps as the top-level ``name:`` so ``docker compose -p`` targets
# exactly the stack the installer created.
from installer_backend.compose_generator import PROJECT_NAME


class UninstallAbortedError(Exception):
    """Raised when a required confirmation was not given.

    Carries an operator-facing message (never a secret). When this is raised
    NOTHING destructive has run: the stack is untouched and the data root is
    intact. The CLI maps it to the ``ABORTED`` exit code.
    """


# ---------------------------------------------------------------------------
# Injectable seams — everything that touches the host.
# ---------------------------------------------------------------------------
@runtime_checkable
class StackTeardown(Protocol):
    """Stops + removes the running stack (``docker compose down``).

    The real binding shells out to ``docker compose -p <project_name> down``
    (adding ``-v`` only when *remove_volumes* is true). The fake records the
    call so tests assert the stack was (or was NOT) torn down, and whether
    volumes were asked to be removed, without a Docker host.
    """

    def down(self, project_name: str, *, remove_volumes: bool) -> list[str]:
        """Tear down *project_name*; return the log lines produced.

        ``remove_volumes`` maps to ``docker compose down -v`` — it removes any
        *named* volumes. The platform's persistent data lives in the
        bind-mounted data root (deleted separately by :class:`DataPurger`), so
        the default teardown leaves *remove_volumes* false.
        """
        ...


@runtime_checkable
class DataPurger(Protocol):
    """Wipes the persistent data tree under the data root.

    The real binding ``shutil.rmtree``s ``/data/agent-platform`` (every PGDATA /
    MinIO / Vault / repos byte). The fake records the path it was asked to wipe
    so a test can assert the purge happened ONLY with both confirmations — and
    never otherwise.
    """

    def purge(self, data_root: str) -> list[str]:
        """Delete every byte under *data_root*; return the log lines produced."""
        ...


@runtime_checkable
class Confirmer(Protocol):
    """Supplies the operator confirmations the uninstall gates on.

    Two methods, one per confirmation kind:

    * :meth:`confirm_name` — the operator must type the exact deployment name
      (echoed back so the orchestration can compare). Returns what they typed.
    * :meth:`confirm_yes` — an explicit yes/no for *prompt* (the second of the
      double confirmation, and the purge's extra confirmation). Returns the
      boolean answer.

    The interactive binding reads from a TTY; the CLI's non-interactive binding
    derives the answers from flags (``--yes`` + the typed ``--confirm-name``).
    Tests inject a scripted fake so every gate branch is exercised without a
    terminal.
    """

    def confirm_name(self, prompt: str) -> str:
        """Ask the operator to type the deployment name; return what they typed."""
        ...

    def confirm_yes(self, prompt: str) -> bool:
        """Ask *prompt*; return True iff the operator explicitly confirmed."""
        ...


# ---------------------------------------------------------------------------
# In-memory fakes — the DEFAULT seams. Make the module import-safe + the CLI
# runnable with no Docker host, and the orchestration testable.
# ---------------------------------------------------------------------------
@dataclass
class StubStackTeardown:
    """Records teardown requests instead of touching Docker (test default)."""

    #: True once :meth:`down` has been called (the stack was torn down).
    torn_down: bool = False
    #: Whether the last teardown was asked to remove named volumes.
    removed_volumes: bool = False
    #: The project name the teardown targeted.
    project_name: str = ""

    def down(self, project_name: str, *, remove_volumes: bool) -> list[str]:
        self.torn_down = True
        self.removed_volumes = remove_volumes
        self.project_name = project_name
        return [
            f"docker compose -p {project_name} down" + (" -v" if remove_volumes else ""),
            "Stack detenido y eliminado.",
        ]


@dataclass
class StubDataPurger:
    """Records purge requests instead of deleting files (test default)."""

    #: True once :meth:`purge` has been called (the data root was wiped).
    purged: bool = False
    #: The data root the purge targeted.
    data_root: str = ""

    def purge(self, data_root: str) -> list[str]:
        self.purged = True
        self.data_root = data_root
        return [f"Eliminando todos los datos bajo {data_root}.", "Datos eliminados."]


@dataclass
class ScriptedConfirmer:
    """A scripted :class:`Confirmer` for tests + the CLI's flag-derived answers.

    ``name_answer`` is what :meth:`confirm_name` returns (the operator's typed
    deployment name); ``yes_answers`` is a queue popped one per :meth:`confirm_yes`
    call (so the second of the double confirmation and the purge's extra
    confirmation can each be scripted independently). When the queue is empty
    :meth:`confirm_yes` returns :attr:`default_yes` (default ``False`` — a missing
    confirmation is a *no*, the safe default for a destructive op).
    """

    name_answer: str = ""
    yes_answers: list[bool] = field(default_factory=list)
    default_yes: bool = False
    #: Records the prompts shown (so a test can assert what was asked).
    prompts: list[str] = field(default_factory=list)

    def confirm_name(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.name_answer

    def confirm_yes(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        if self.yes_answers:
            return self.yes_answers.pop(0)
        return self.default_yes


# ---------------------------------------------------------------------------
# The uninstall orchestration.
# ---------------------------------------------------------------------------
@dataclass
class UninstallRequest:
    """The parameters of one uninstall (what the CLI flags resolve to).

    ``deployment_name`` is the stack the operator intends to tear down (the
    compose project name by default); it must be typed back to pass the first
    confirmation. ``purge_data`` opts into wiping the data tree (still gated by
    an extra confirmation). ``data_root`` is the path that purge would delete.
    """

    deployment_name: str = PROJECT_NAME
    data_root: str = "/data/agent-platform"
    purge_data: bool = False


@dataclass
class UninstallResult:
    """Outcome of a completed uninstall (returned on success).

    ``data_preserved`` is the headline guarantee: True unless the data tree was
    actually purged. ``log`` is the secret-free operator log.
    """

    stack_removed: bool
    data_purged: bool
    log: list[str] = field(default_factory=list)

    @property
    def data_preserved(self) -> bool:
        """True iff the persistent data tree was left intact."""

        return not self.data_purged


@dataclass
class Uninstaller:
    """Runs the gated teardown: double-confirm → stack down → opt-in data purge.

    Construct with the injectable seams (defaults are the in-memory stubs so the
    orchestration is testable with no host): the :class:`StackTeardown`, the
    :class:`DataPurger`, the :class:`Confirmer` that supplies the gates, and the
    output stream.

    :meth:`run` enforces, in order:

      1. the DOUBLE confirmation — the operator must type the exact deployment
         name AND give an explicit yes. Either one missing/wrong ABORTS (raises
         :class:`UninstallAbortedError`) with NOTHING removed;
      2. the stack teardown — ``docker compose down`` (data preserved by
         default);
      3. the data purge — ONLY when ``purge_data`` is requested AND its own
         extra confirmation passes. Otherwise the data root is left intact.
    """

    teardown: StackTeardown
    purger: DataPurger
    confirmer: Confirmer
    out: TextIO
    #: The ordered phase names actually executed (for assertions).
    phases: list[str] = field(default_factory=list)

    def _log(self, message: str) -> None:
        """Emit one operator-facing log line. NEVER carries a secret."""

        print(message, file=self.out)

    def _confirm_double(self, req: UninstallRequest) -> None:
        """Enforce the double confirmation; abort if either part fails.

        First the operator must type the exact deployment name; then they must
        give an explicit yes. BOTH are required — a single one is not enough.
        On any failure NOTHING destructive has run yet (this gate is first).
        """

        self.phases.append("confirm")
        # (a) type-the-name confirmation.
        typed = self.confirmer.confirm_name(
            f"Escribe el nombre del despliegue para confirmar la desinstalación "
            f"({req.deployment_name}): "
        ).strip()
        if typed != req.deployment_name:
            raise UninstallAbortedError(
                "Desinstalación abortada: el nombre del despliegue no coincide "
                "(no se ha eliminado nada)."
            )
        # (b) explicit-yes confirmation — the SECOND of the double confirmation.
        if not self.confirmer.confirm_yes(
            f"Esto DETENDRÁ y ELIMINARÁ el stack '{req.deployment_name}'. " "¿Continuar? [y/N]: "
        ):
            raise UninstallAbortedError(
                "Desinstalación abortada: no se confirmó explícitamente "
                "(no se ha eliminado nada)."
            )

    def _maybe_purge(self, req: UninstallRequest) -> bool:
        """Purge the data tree ONLY with --purge-data AND its extra confirmation.

        Returns True iff the data was actually wiped. When ``purge_data`` is not
        requested the data is preserved (the default). When it IS requested the
        purge still needs its OWN extra confirmation — a refusal leaves the data
        intact and the stack already-removed (a safe partial outcome).
        """

        if not req.purge_data:
            self._log("[datos] Conservados por defecto. Usa --purge-data para eliminarlos.")
            return False

        self.phases.append("confirm_purge")
        # The purge's OWN extra confirmation (separate from the double confirm).
        if not self.confirmer.confirm_yes(
            f"--purge-data BORRARÁ DE FORMA IRREVERSIBLE todos los datos bajo "
            f"{req.data_root}. Esta acción NO se puede deshacer. "
            "¿Eliminar los datos? [y/N]: "
        ):
            self._log("[datos] Purga cancelada: no se confirmó. Los datos se conservan.")
            return False

        self.phases.append("purge_data")
        self._log(f"[datos] Eliminando todos los datos bajo {req.data_root}…")
        for line in self.purger.purge(req.data_root):
            self._log(f"[datos]   {line}")
        return True

    def run(self, req: UninstallRequest) -> UninstallResult:
        """Run the gated uninstall for *req*; return the :class:`UninstallResult`.

        Raises :class:`UninstallAbortedError` (with nothing removed) if the double
        confirmation fails. On success the stack has been torn down; the data is
        purged ONLY if both ``purge_data`` and its extra confirmation passed.
        """

        # 1. Gate FIRST: no destructive action until the double confirm passes.
        self._confirm_double(req)

        # 2. Tear down the stack (data preserved unless purge runs below).
        self.phases.append("teardown")
        self._log(f"[stack] Deteniendo y eliminando el stack '{req.deployment_name}'…")
        # Data lives in the bind-mounted data root, deleted separately by the
        # purger — so the compose teardown never removes named volumes here.
        for line in self.teardown.down(req.deployment_name, remove_volumes=False):
            self._log(f"[stack]   {line}")

        # 3. Opt-in data purge (its own extra confirmation).
        data_purged = self._maybe_purge(req)

        result = UninstallResult(
            stack_removed=True,
            data_purged=data_purged,
            log=[],
        )
        self._log("")
        self._log("Desinstalación completada.")
        self._log("Datos ELIMINADOS." if data_purged else "Datos CONSERVADOS (intactos).")
        return result


def build_default_uninstaller(
    out: TextIO,
    confirmer: Confirmer,
    *,
    dry_run: bool = False,
    compose_dir: str = "/data/agent-platform",
) -> Uninstaller:
    """Build an :class:`Uninstaller` with the REAL host bindings by default.

    ``dry_run=True`` wires the in-memory stub seams (import-safe, no Docker / no
    disk writes) for an explicitly-marked simulation. Otherwise it wires the real
    bindings: :class:`RealStackTeardown` (``docker compose down`` under
    *compose_dir*) + :class:`RealDataPurger` (``rmtree`` of the data tree). The
    caller supplies the :class:`Confirmer`; the double confirmation gates the
    real destruction. The real seams only touch the host when :meth:`Uninstaller.run`
    executes.
    """

    if dry_run:
        return Uninstaller(
            teardown=StubStackTeardown(),
            purger=StubDataPurger(),
            confirmer=confirmer,
            out=out,
        )
    from installer_backend.command_runner import SubprocessRunner
    from installer_backend.real_teardown import RealDataPurger, RealStackTeardown

    return Uninstaller(
        teardown=RealStackTeardown(compose_dir, SubprocessRunner()),
        purger=RealDataPurger(),
        confirmer=confirmer,
        out=out,
    )
