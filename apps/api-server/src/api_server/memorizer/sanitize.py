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

#: ``<private>…</private>`` (task_mem_04): texto que el humano marca para que no
#: se memorice. La apertura admite atributos y espacios (``<private >``,
#: ``<private class="x">``, un salto de línea antes del ``>``), el cierre admite
#: espacios (``</private >``), sin distinguir mayúsculas y a través de saltos de
#: línea. Los bloques se anidan (una pila): el primer cierre NO termina el externo.
#: Una apertura SIN cierre se lleva todo lo que sigue: ante la duda, se calla (CWE-184).
_PRIVATE_TAG = re.compile(r"<private\b[^>]*>|</private\s*>", re.IGNORECASE)


def strip_private_tags(text: str) -> str:
    """Quita los bloques ``<private>…</private>`` (anidados y sin cerrar) del texto."""
    if not text or "private" not in text.lower():
        return text
    out: list[str] = []
    depth = 0
    pos = 0
    for tag in _PRIVATE_TAG.finditer(text):
        if depth == 0:
            out.append(text[pos : tag.start()])
        pos = tag.end()
        if tag.group().startswith("</"):
            depth = max(0, depth - 1)  # un cierre huérfano sólo se retira a sí mismo
        else:
            depth += 1
    if depth == 0:
        out.append(text[pos:])
    return "".join(out)


def sanitize_memory_content(text: str) -> tuple[str, int]:
    """``(texto redactado, número de redacciones)``. Sin hallazgos devuelve el
    texto intacto y 0."""
    if not text:
        return text, 0
    redactions = 0
    without_private = strip_private_tags(text)
    if without_private != text:
        text = without_private.strip()
        redactions += 1
    matches = _scanner.scan(text)
    if matches:
        text = redact_secrets(text, matches)
        redactions += len(matches)
    text, host_paths = _HOST_PROJECT_ROOT.subn(_HOST_PATH_MARKER, text)
    redactions += host_paths
    return text, redactions


__all__ = ["sanitize_memory_content", "strip_private_tags"]
