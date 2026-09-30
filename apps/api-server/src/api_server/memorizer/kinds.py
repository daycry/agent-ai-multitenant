"""Vocabulario cerrado de `memory_entries.kind` (task_mem_04).

`type` (episodic / semantic) dice CUÁNTO dura una memoria; `kind` dice DE QUÉ
va: el resumen de una ejecución, una decisión, un bugfix, un patrón, una
trampa, una preferencia o un hecho suelto. Sirve para filtrar («sólo
decisiones») y para pintar el tipo en el panel. Las memorias anteriores a la
migración 0152 tienen ``kind = NULL`` («sin clasificar»).

Módulo PURO (sin BD). La normalización es tolerante a propósito: el destilado
lo escribe un LLM, y con un modelo de ~9B (ADR 0169) el ``kind`` llega en
mayúsculas, en español o inventado. Eso jamás debe costar las memorias.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

#: El vocabulario cerrado. El ``CHECK`` de la columna (migración 0152) es este.
MEMORY_KINDS: tuple[str, ...] = (
    "summary",
    "decision",
    "bugfix",
    "pattern",
    "gotcha",
    "preference",
    "fact",
)

SUMMARY_KIND = "summary"
FALLBACK_KIND = "fact"

#: Tipos con vocación de regla reutilizable (``semantic``); el resto son eventos.
_SEMANTIC_KINDS = frozenset({"decision", "pattern", "gotcha", "preference"})

_ALIASES: dict[str, str] = {
    # español
    "resumen": "summary",
    "decision": "decision",
    "decisiones": "decision",
    "error": "bugfix",
    "arreglo": "bugfix",
    "correccion": "bugfix",
    "solucion": "bugfix",
    "patron": "pattern",
    "trampa": "gotcha",
    "cuidado": "gotcha",
    "preferencia": "preference",
    "hecho": "fact",
    "dato": "fact",
    # inglés, variantes habituales
    "bug": "bugfix",
    "bug_fix": "bugfix",
    "bug-fix": "bugfix",
    "fix": "bugfix",
    "decisions": "decision",
    "patterns": "pattern",
    "gotchas": "gotcha",
    "pitfall": "gotcha",
    "preferences": "preference",
    "facts": "fact",
    "summaries": "summary",
}


def _fold(value: str) -> str:
    """Minúsculas, sin acentos y sin espacios de los bordes."""
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _canonical(raw: object) -> str | None:
    """El kind canónico de ``raw`` o ``None`` si no se reconoce (o no es texto)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    folded = _fold(raw)
    if folded in MEMORY_KINDS:
        return folded
    return _ALIASES.get(folded)


def normalise_kind(raw: object) -> str | None:
    """Lo que se persiste para el ``kind`` que devolvió el LLM.

    * ausente / vacío / no es texto → ``None`` (sin clasificar);
    * reconocido (en cualquier caja, con acentos o en español) → el canónico;
    * texto fuera del vocabulario → ``"fact"``: la memoria se conserva.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    canonical = _canonical(raw)
    if canonical is not None:
        return canonical
    # Ni texto reconocible ni ausente (p. ej. un número): también es un dato.
    return FALLBACK_KIND


def recognised_kind(raw: object) -> str | None:
    """Como :func:`normalise_kind` pero SIN el respaldo ``fact``: ``None`` si el
    valor no está en el vocabulario. Para inferir el ``type`` sólo de un kind real."""
    return _canonical(raw)


def type_for_kind(kind: str) -> str:
    """``type`` por defecto de un kind: el resumen y los sucesos son ``episodic``;
    decisiones, patrones, trampas y preferencias, ``semantic``."""
    return "semantic" if kind in _SEMANTIC_KINDS else "episodic"


def parse_kind_filter(raw: Iterable[object] | None) -> list[str]:
    """Filtro ``kinds`` de un recall: canónicos, sin duplicados y en orden.

    Lo desconocido se descarta (no cuenta como ``fact``: filtrar por «banana» no
    debe devolver los hechos). Una lista vacía significa «sin filtro»."""
    out: list[str] = []
    for item in raw or ():
        canonical = _canonical(item)
        if canonical is not None and canonical not in out:
            out.append(canonical)
    return out


__all__ = [
    "FALLBACK_KIND",
    "MEMORY_KINDS",
    "SUMMARY_KIND",
    "normalise_kind",
    "parse_kind_filter",
    "recognised_kind",
    "type_for_kind",
]
