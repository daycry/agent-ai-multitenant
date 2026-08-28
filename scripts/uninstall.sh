#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Stack uninstall with DOUBLE confirmation (Plan 15 task_15_12).
#
# Thin wrapper over the Python uninstall orchestration. The REAL logic lives in
# `apps/installer/backend/src/installer_backend/uninstall.py` (driven by
# `python -m installer_backend.cli uninstall`) so it is unit-testable (the tests
# mock the `docker compose down` + data-purge seams). This script is just the
# operator-facing entrypoint; it delegates to the CLI and does NOT re-implement
# any of it.
#
# This tears down a PRODUCTION stack, so it is DESTRUCTIVE and gated:
#
#   1. DOUBLE confirmation before anything is removed — the operator must
#      (a) type the EXACT deployment name (--confirm-name) AND
#      (b) confirm explicitly (--yes). A single one is NOT enough.
#   2. Data is PRESERVED by default (`docker compose down`, the bind-mounted
#      data root under /data/agent-platform is left on disk so a reinstall can
#      reuse it), and so are the stack's named volumes.
#   3. --purge-data ALSO wipes the data root AND the named volumes (`down -v`,
#      which is what finally removes the multi-GB `whisper_models` voice cache),
#      but needs its OWN extra confirmation (--yes) — asked BEFORE anything is
#      destroyed, so a fat-finger can never delete data.
#   4. The purge REPORTS what it could not delete. A busy mount point or a
#      denied permission no longer comes back as "Datos ELIMINADOS": it exits 7.
#
# Usage (headless / automation):
#   ./scripts/uninstall.sh --confirm-name agentic-platform --yes
#   ./scripts/uninstall.sh --confirm-name agentic-platform --yes --purge-data
#
# Usage (interactive prompts from the terminal):
#   ./scripts/uninstall.sh --interactive
#
# Exit codes (propagated from the Python CLI, see installer_backend.cli.ExitCode):
#   0  uninstall completed
#   1  usage error (bad args)
#   5  aborted (a required confirmation was not given; NOTHING was removed)
#   7  INCOMPLETE — the purge ran but could not delete everything; data (possibly
#      the .env with every secret) is STILL on disk. Do NOT treat the machine as
#      clean: the log names each surviving path and why.
# -----------------------------------------------------------------------------
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"

# Pass every argument straight through to the CLI's `uninstall` subcommand so
# the shell wrapper never diverges from the Python entrypoint's flags.
exec "${PYTHON_BIN}" -m installer_backend.cli uninstall "$@"
