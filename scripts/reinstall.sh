#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Reinstall over an existing deployment (Plan 15 task_15_13).
#
# Thin wrapper over the Python reinstall orchestration. The REAL logic lives in
# `apps/installer/backend/src/installer_backend/reinstall.py` (driven by
# `python -m installer_backend.cli reinstall`) so it is unit-testable (the tests
# mock the detection / teardown / data-purge / existing-secret-load seams). This
# script is just the operator-facing entrypoint; it delegates to the CLI and
# does NOT re-implement any of it.
#
# Re-running the installer over a machine that already holds a deployment must
# FIRST decide what to do with the data already there:
#
#   * PRESERVE (default) — keep the data volumes + DB + object store, regenerate
#     config/compose, and REUSE the existing secrets + Vault unseal keys. Reusing
#     the existing material is mandatory: the kept Postgres/MinIO data and the
#     Vault-encrypted secret tree are bound to it, so regenerating secrets would
#     ORPHAN the existing encrypted data. No data is wiped; no confirmation needed.
#
#   * FRESH (--fresh) — wipe the existing data tree and reinstall from scratch
#     (fresh secrets, fresh Vault). Because this DESTROYS tenant data it is gated
#     by the SAME double confirmation as the uninstall: type the EXACT deployment
#     name (--confirm-name) AND confirm explicitly (--yes). A single one is NOT
#     enough; without both, NOTHING is wiped and the reinstall aborts.
#
#   * No prior install — degrades to a plain first install (fresh secrets, no
#     confirmation; there is no data to destroy).
#
# Usage (preserve — the safe default):
#   ./scripts/reinstall.sh
#
# Usage (fresh wipe-and-reinstall, headless):
#   ./scripts/reinstall.sh --fresh --confirm-name agentic-platform --yes
#
# Usage (interactive prompts for the FRESH confirmations):
#   ./scripts/reinstall.sh --fresh --interactive
#
# Exit codes (propagated from the Python CLI, see installer_backend.cli.ExitCode):
#   0  reinstall pre-install work completed (preserve / fresh / first install)
#   1  usage error (bad args)
#   5  aborted (a FRESH confirmation was not given, or PRESERVE could not reuse
#      the existing secrets; NOTHING was removed and the data is intact)
# -----------------------------------------------------------------------------
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"

# Pass every argument straight through to the CLI's `reinstall` subcommand so
# the shell wrapper never diverges from the Python entrypoint's flags.
exec "${PYTHON_BIN}" -m installer_backend.cli reinstall "$@"
