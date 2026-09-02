"""Lo que se memoriza no lleva secretos ni rutas de host (`task_cv_45`, E-11).

Auditoría 2026-09-01: el destilador corre en el worker, fuera de los cuatro
puntos del ciclo de guardrails (principio rector 10), y `memory_store`
persistía `payload.content` verbatim. Una memoria con un token o con la ruta
de un worktree del host se recuerda para siempre y se enseña a cualquier
agente del mismo scope. Aquí se redacta ANTES de embeber y de persistir, con
el mismo detector de secretos de `shared_guardrails`.
"""

from __future__ import annotations

import re

from shared_guardrails.checks.secret_leakage import SecretScanner, redact_secrets

#: Rutas de host del data-root de la plataforma (`/data/agent-platform/projects/<t>/<p>`).
_HOST_PROJECT_ROOT = re.compile(r"/data/agent-platform/projects/[^/\s]+/[^/\s]+")
_HOST_PATH_MARKER = "<project-root>"

_scanner = SecretScanner()


def sanitize_memory_content(text: str) -> tuple[str, int]:
    """``(texto redactado, número de redacciones)``. Sin hallazgos devuelve el
    texto intacto y 0."""
    if not text:
        return text, 0
    redactions = 0
    matches = _scanner.scan(text)
    if matches:
        text = redact_secrets(text, matches)
        redactions += len(matches)
    text, host_paths = _HOST_PROJECT_ROOT.subn(_HOST_PATH_MARKER, text)
    redactions += host_paths
    return text, redactions


__all__ = ["sanitize_memory_content"]
