#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Unattended CLI install (Plan 15 task_15_10).
#
# Thin wrapper over the Python install orchestration. The REAL logic lives in
# `apps/installer/backend/src/installer_backend/cli.py` so it is unit-testable
# (the tests mock the prereq / provisioning / Vault / self-destruct seams). This
# script is just the operator-facing entrypoint; it delegates to the CLI and
# does NOT re-implement any of it.
#
# It runs the SAME orchestration as the wizard, headlessly, from a YAML config:
#   prereqs -> generate compose/.env/config -> docker compose up -> Vault
#   bootstrap -> seed tenant -> finalize (credentials shown ONCE).
#
# Usage:
#   ./scripts/install.sh --config install.yaml
#
# Profiles (Plan 15 task_15_11) live under scripts/install-profiles/; copy one
# and edit it, then pass it with --config.
#
# Exit codes (propagated from the Python CLI, see installer_backend.cli.ExitCode):
#   0  install completed
#   1  usage error (bad args / missing --config)
#   2  config error  (install.yaml malformed or failed validation; NO provisioning)
#   3  prereq error  (a required prerequisite failed; aborts BEFORE provisioning)
#   4  provision error (a provisioning step failed; stack may be half-up)
#   5  aborted (operator declined a destructive confirmation)
#
# Security: generated secrets + Vault unseal keys are printed to stdout EXACTLY
# ONCE. Capture them now — there is no recovery. They are never written to a log
# file by this tooling.
# -----------------------------------------------------------------------------
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"

# Pass every argument straight through to the CLI's `install` subcommand so the
# shell wrapper never diverges from the Python entrypoint's flags.
exec "${PYTHON_BIN}" -m installer_backend.cli install "$@"
