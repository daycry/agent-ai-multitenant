#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Bootstrap the local dev environment on Linux / macOS / WSL.
#
# Idempotent: safe to run repeatedly. Creates .venv/ at the repo root if
# missing, upgrades pip, installs everything in requirements-dev.txt, and
# registers the pre-commit git hook so future commits run the linters.
#
# Usage:
#   ./scripts/dev/bootstrap.sh
#
# After bootstrap, activate the venv for interactive work:
#   source .venv/bin/activate
# Or invoke tools directly without activating:
#   ./.venv/bin/pre-commit run --all-files
# -----------------------------------------------------------------------------
set -euo pipefail

# Resolve repo root (this script lives at <root>/scripts/dev/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="$REPO_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"

# 1) Ensure a usable system python.
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found on PATH. Install Python 3.12+ and retry." >&2
    exit 1
fi

# 2) Create venv if missing.
if [ ! -x "$VENV_PY" ]; then
    echo "==> Creating venv at .venv/"
    python3 -m venv "$VENV_DIR"
else
    echo "==> Reusing existing .venv/"
fi

# 3) Upgrade pip inside the venv.
echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip --quiet

# 4) Install dev dependencies.
echo "==> Installing requirements-dev.txt"
"$VENV_PY" -m pip install -r "$REPO_ROOT/requirements-dev.txt"

# 5) Install each app/ package editable, so `from <pkg> import ...` works
#    and pytest can discover modules. New apps must be added here.
APP_PACKAGES=("apps/api-server")
for pkg in "${APP_PACKAGES[@]}"; do
    if [ -f "$REPO_ROOT/$pkg/pyproject.toml" ]; then
        echo "==> pip install -e ${pkg}[dev]"
        "$VENV_PY" -m pip install -e "$REPO_ROOT/$pkg[dev]"
    fi
done

# 6) Register the pre-commit git hook.
echo "==> Installing pre-commit git hook"
"$VENV_PY" -m pre_commit install

echo ""
echo "OK. Dev environment ready."
echo "  Activate:        source .venv/bin/activate"
echo "  Run all hooks:   ./.venv/bin/pre-commit run --all-files"
echo "  Run a tool:      ./.venv/bin/ruff check ."
