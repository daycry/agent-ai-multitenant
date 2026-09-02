"""La valla de datos no confiables, compartida por todo lo que arma un prompt.

Vivía en `__main__` (preámbulos) y `providers` no podía importarla sin un
ciclo; `task_cv_27` (auditoría 2026-09-01, E-03) la necesita en los dos sitios
porque las memorias recuperadas también son texto de terceros.
"""

from __future__ import annotations

UNTRUSTED_OPEN = "<<<UNTRUSTED_DATA"
UNTRUSTED_CLOSE = "UNTRUSTED_DATA>>>"


def fence_untrusted(body: str) -> str:
    """Wrap ``body`` in the untrusted-data fence, neutralising embedded markers."""
    safe = body.replace(UNTRUSTED_OPEN, "«UNTRUSTED_DATA").replace(
        UNTRUSTED_CLOSE, "UNTRUSTED_DATA»"
    )
    return f"{UNTRUSTED_OPEN}\n{safe}\n{UNTRUSTED_CLOSE}"
