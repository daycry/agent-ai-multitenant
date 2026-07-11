"""C1 — iniciativa proactiva del córtex: la decisión y el mensaje (puros).

Todo el córtex era estrictamente REACTIVO: el surfacing de pursuits solo
disparaba dentro de un turno que iniciaba el owner — un «alguien» que jamás
escribe primero no parece vivo. Este módulo es la parte pura del beat
``workers.cortex_initiative`` (gated por ``cortex.autonomy_enabled``):

  * :func:`should_reach_out` — cuándo tiene sentido escribir primero: hay algo
    que contar (pursuits ``digested`` pendientes) + silencio largo (no
    interrumpir una conversación activa) + sin una iniciativa previa aún sin
    respuesta (no acosar) + nunca el primer contacto (el onboarding es del
    owner).
  * :func:`compose_initiative_message` — el mensaje, DETERMINISTA (sin LLM):
    reencuentro + el aprendizaje pendiente más antiguo. El digest procede de la
    web vía LLM ⇒ se incrusta como cita, no como instrucción; el mensaje entero
    es un turno del córtex, no un prompt.

El worker persiste el turno ``role='cortex'`` en una conversación nueva, marca
los pursuits como surfaced y notifica al owner (evento ``cortex_message``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from api_server.cortex.affective import Language
from api_server.cortex.self_context import PendingLearning

# Silencio mínimo antes de plantearse escribir primero (~20 h: deja pasar la
# cadencia diaria natural sin convertirse en spam de madrugada).
INITIATIVE_MIN_GAP_S = 20 * 3600
# Máximo de aprendizajes que el mensaje menciona (el resto espera al surfacing
# normal dentro de la conversación).
_MAX_TOPICS = 2
_DIGEST_CAP = 220


def should_reach_out(
    *,
    now: datetime,
    last_turn_at: datetime | None,
    has_pending_learnings: bool,
    unanswered_initiative: bool,
) -> bool:
    """¿Tiene sentido que el córtex escriba primero? (pura, sin I/O)."""
    if last_turn_at is None:
        # Sin historia previa no hay relación que retomar: el primer contacto
        # es del owner (onboarding), no del córtex.
        return False
    if unanswered_initiative:
        return False
    if not has_pending_learnings:
        return False
    gap = (now - last_turn_at).total_seconds()
    return gap >= INITIATIVE_MIN_GAP_S


def _gap_phrase(now: datetime, last_turn_at: datetime, language: Language) -> str:
    gap = max(0.0, (now - last_turn_at).total_seconds())
    days = int(gap // 86400)
    hours = int(gap // 3600)
    if language == "en":
        if days >= 1:
            return f"it's been {days} day(s) since we last spoke"
        return f"it's been {hours} hour(s) since we last spoke"
    if days >= 1:
        return f"hace {days} día(s) que no hablamos"
    return f"hace {hours} hora(s) que no hablamos"


def compose_initiative_message(
    learnings: Sequence[PendingLearning],
    *,
    now: datetime,
    last_turn_at: datetime,
    language: Language = "es",
) -> str | None:
    """El mensaje proactivo, determinista. ``None`` si no hay nada que contar."""
    topics = [entry for entry in learnings if entry.topic.strip()][:_MAX_TOPICS]
    if not topics:
        return None
    gap = _gap_phrase(now, last_turn_at, language)
    lines: list[str] = []
    if language == "en":
        lines.append(f"Hey — {gap}. While you were away I kept learning:")
        for entry in topics:
            digest = entry.digest.strip()[:_DIGEST_CAP]
            lines.append(f"• {entry.topic}" + (f": {digest}" if digest else ""))
        lines.append("Want me to go deeper into any of these?")
    else:
        lines.append(f"Hola — {gap}. Mientras no estabas seguí aprendiendo:")
        for entry in topics:
            digest = entry.digest.strip()[:_DIGEST_CAP]
            lines.append(f"• {entry.topic}" + (f": {digest}" if digest else ""))
        lines.append("¿Quieres que profundice en alguno?")
    return "\n".join(lines)
