"""La huella de quién abrió qué host de egress (`task_mk_02`, ADR 0165 D5).

`platform_settings` guarda `updated_by` y `updated_at` del **último** escritor, y
nada más: cada cambio sobrescribe el rastro del anterior. Para un ajuste de tipo
umbral eso basta —lo que importa es cuánto vale hoy—, pero para una allowlist de
egress la pregunta de una revisión de seguridad es la contraria: *quién abrió este
host, cuándo, y a petición de quién*. Con sólo el último escritor, un host abierto
y cerrado tres veces en un mes deja una línea.

Así que el cambio escribe **además** una fila en `audit_log` con el delta. Este
módulo calcula ese delta, y es una función pura a propósito: la parte que se
equivoca de un registro de auditoría no es la escritura, es decidir qué cambió.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["ALLOWLIST_AUDIT_ACTION", "allowlist_delta"]

#: `action` de la fila. Corta (la columna es `String(64)`) y buscable.
ALLOWLIST_AUDIT_ACTION = "egress.mcp_allowlist.changed"


def allowlist_delta(antes: Iterable[str], despues: Iterable[str]) -> dict[str, list[str]] | None:
    """Qué entró y qué salió, o ``None`` si no cambió nada.

    El ``None`` importa: un PUT que reescribe el mismo valor no debe dejar fila.
    Si cada guardado escribiese una, la tabla dejaría de servir para lo único que
    se le pide —encontrar el cambio— y el ruido acabaría enseñando a no mirarla.
    """
    previos = {str(h) for h in antes}
    nuevos = {str(h) for h in despues}
    if previos == nuevos:
        return None
    return {
        "added": sorted(nuevos - previos),
        "removed": sorted(previos - nuevos),
    }
