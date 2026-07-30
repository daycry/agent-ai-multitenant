"""Pydantic schemas del Panel de Mente (Córtex F2, ADR 0075).

Dan forma a los payloads de ``/owner/cortex/mind``, ``/affect/timeseries`` y
``/episodes`` (todos gated por ``require_system_owner``, DB-authoritative).

> Honestidad (ADR 0075 §6): el bloque ``honesty`` rotula que esto es un **modelo
> computacional de afecto, NO sentimientos reales** — la UI lo muestra siempre.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

#: Copy honesto NO removible (ADR 0075 §6), bilingüe — la UI muestra el del idioma.
HONESTY_NOTE_ES = "Modelo computacional de afecto, no sentimientos reales."
HONESTY_NOTE_EN = "Computational model of affect, not real feelings."


class CortexDrives(BaseModel):
    """Los cuatro drives homeostáticos ∈ [0,1] (ADR 0075 §2)."""

    model_config = _BASE_CONFIG

    curiosity: float
    bonding: float
    coherence: float
    competence: float


class CortexHonesty(BaseModel):
    """Bloque de honestidad que la UI rotula siempre (ADR 0075 §6)."""

    model_config = _BASE_CONFIG

    note_es: str = HONESTY_NOTE_ES
    note_en: str = HONESTY_NOTE_EN


class CortexMindResponse(BaseModel):
    """El estado afectivo vivo del córtex (Redis con decay lazy + último mood/drives).

    ``valence/arousal/dominance/intensity`` son la **emoción** viva; ``mood_*`` la
    capa lenta; ``mood_label`` su etiqueta categórica derivada SOLO-UI."""

    model_config = _BASE_CONFIG

    valence: float
    arousal: float
    dominance: float
    intensity: float
    mood_valence: float
    mood_arousal: float
    mood_dominance: float
    mood_label: str
    drives: CortexDrives
    honesty: CortexHonesty = Field(default_factory=CortexHonesty)


class CortexAffectPoint(BaseModel):
    """Un punto de la serie temporal (un snapshot) para el gráfico de mood + 2D."""

    model_config = _BASE_CONFIG

    created_at: datetime
    valence: float
    arousal: float
    dominance: float
    intensity: float
    mood_valence: float
    mood_arousal: float
    mood_dominance: float
    mood_label: str
    drives: CortexDrives


class CortexEpisodeItem(BaseModel):
    """Una memoria episódica emocional del owner (mapa afectivo; hover = razón)."""

    model_config = _BASE_CONFIG

    id: UUID
    content: str
    created_at: datetime
    mood_label: str | None = None
    valence: float | None = None
    arousal: float | None = None
    dominance: float | None = None
    intensity: float | None = None
    appraisal_reason: str | None = None


class CortexJournalEntry(BaseModel):
    """Una entrada del diario del córtex (C4): narrativa versionada o memoria
    de reflexión/aprendizaje, en línea temporal única."""

    model_config = _BASE_CONFIG

    kind: str  # narrative | reflection | learning
    content: str
    reason: str | None = None
    created_at: datetime


__all__ = [
    "HONESTY_NOTE_EN",
    "HONESTY_NOTE_ES",
    "CortexAffectPoint",
    "CortexDrives",
    "CortexEpisodeItem",
    "CortexHonesty",
    "CortexJournalEntry",
    "CortexMindResponse",
]
