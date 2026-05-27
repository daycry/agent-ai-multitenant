#!/usr/bin/env bash
# Build all fourteen runtime templates locally (Plan 06 task_06_02).
#
# Mirrors the loop in task_06_02's auto-test contract:
#
#   for t in <14 ids>; do docker build -t agent-runtime-${t}:v1 docker/agent-runtimes/${t}/ || exit 1; done
#
# The CI workflow (task_06_03) runs the same loop on a GitHub Actions
# runner. This script exists for local smoke-testing without going
# through Actions — operators run it once after pulling to make sure
# their Docker daemon can build every template.
#
# Usage:
#   ./scripts/dev/build-runtime-templates.sh           # build all
#   ./scripts/dev/build-runtime-templates.sh python-pytest node-jest  # subset
#
# Exit codes:
#   0  every requested template built
#   1  at least one build failed (script stops at the first failure)
#   2  docker not on PATH / daemon unreachable

set -euo pipefail

ALL_TEMPLATES=(
  python-pytest
  node-jest
  node-vitest
  node-playwright
  php-phpunit
  php-pest
  go-test
  java-maven
  java-gradle
  ruby-rspec
  rust-cargo
  dotnet-test
  generic-shell
  generic-http
)

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL - 'docker' is not on PATH" >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "FAIL - Docker daemon unreachable. Start Docker Desktop and retry." >&2
  exit 2
fi

if [ "$#" -gt 0 ]; then
  TEMPLATES=("$@")
else
  TEMPLATES=("${ALL_TEMPLATES[@]}")
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"

for t in "${TEMPLATES[@]}"; do
  dir="${REPO_ROOT}/docker/agent-runtimes/${t}"
  if [ ! -f "${dir}/Dockerfile" ]; then
    echo "FAIL - no Dockerfile at ${dir}" >&2
    exit 1
  fi
  echo
  echo "=== building agent-runtime-${t}:v1 ==="
  docker build -t "agent-runtime-${t}:v1" "${dir}/"
done

echo
echo "OK - built ${#TEMPLATES[@]} template(s)"
