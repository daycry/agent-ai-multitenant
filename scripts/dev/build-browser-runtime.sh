#!/usr/bin/env bash
# Build browser-runtime:v1 locally (ADR 0080).
#
# El córtex no ejecuta un navegador: el worker lanza ESTE contenedor efímero
# por sesión de navegación aprobada (Playwright + Chromium headless, cap-drop
# ALL, root de solo lectura, red interna → solo el egress-proxy). Igual que
# agent-runtime, su contexto de build es la RAÍZ del repo (el Dockerfile COPYa
# docker/agent-runtimes/browser-runtime/browser_runtime).
#
# CI lo construye en .github/workflows/ci.yml; este script es para el smoke
# local y para el deploy en una sola máquina (Docker Compose) antes de
# `docker compose up` — el worker referencia la imagen por
# WORKERS_BROWSER_RUNTIME_IMAGE (default browser-runtime:v1).
#
# Uso:  ./scripts/dev/build-browser-runtime.sh
# Exit: 0 build ok · 1 fallo de build · 2 docker no disponible

set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL - 'docker' is not on PATH" >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "FAIL - Docker daemon unreachable. Start Docker Desktop and retry." >&2
  exit 2
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
TAG="${1:-browser-runtime:v1}"

echo "=== building ${TAG} (context: repo root) ==="
docker build \
  -f "${REPO_ROOT}/docker/agent-runtimes/browser-runtime/Dockerfile" \
  -t "${TAG}" \
  "${REPO_ROOT}"

echo
echo "=== smoke: sin egress-proxy la sesión debe RECHAZARSE (ADR 0080) ==="
out="$(docker run --rm --network none \
  -e BROWSE_SESSION_SPEC='{"steps":[{"action":"goto","url":"https://example.com"}]}' \
  "${TAG}" || true)"
echo "${out}"
if ! echo "${out}" | grep -q "browse.error"; then
  echo "FAIL - el navegador debería rechazar arrancar sin egress-proxy" >&2
  exit 1
fi

echo
echo "OK - ${TAG} construida y el smoke de seguridad pasa"
