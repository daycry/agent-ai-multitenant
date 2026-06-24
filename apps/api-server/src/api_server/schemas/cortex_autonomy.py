"""Pydantic schemas de la autonomía del córtex (Córtex F4, ADR 0078).

Dan forma a ``GET/PUT /owner/cortex/autonomy`` (gated ``require_system_owner``): el
estado del KILL-SWITCH global, los caps de budget y el budget de búsquedas consumido
hoy. Copy honesto: la curiosidad es un comportamiento PROGRAMADO con límites de coste
auditables, no curiosidad consciente.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

#: Copy honesto NO removible (bilingüe) — la UI muestra el del idioma.
AUTONOMY_NOTE_ES = (
    "El córtex investiga temas por su cuenta dentro de límites de coste que tú "
    "controlas; es un comportamiento programado, no curiosidad consciente."
)
AUTONOMY_NOTE_EN = (
    "The cortex researches topics on its own within cost limits you control; this is "
    "a programmed behaviour, not conscious curiosity."
)


class CortexAutonomyBudget(BaseModel):
    """El budget de búsquedas de curiosidad: consumido hoy vs cap diario."""

    model_config = _BASE_CONFIG

    searches_today: int
    searches_cap: int


class CortexAutonomyResponse(BaseModel):
    """Estado de la autonomía del córtex: kill-switch + gates + budget consumido hoy."""

    model_config = _BASE_CONFIG

    autonomy_enabled: bool
    web_enabled: bool
    curiosity_drive_threshold: float
    circuit_breaker_open: bool
    budget: CortexAutonomyBudget
    note_es: str = AUTONOMY_NOTE_ES
    note_en: str = AUTONOMY_NOTE_EN


class CortexAutonomyUpdateRequest(BaseModel):
    """Flip del kill-switch global de los bucles autónomos (System Owner)."""

    model_config = _BASE_CONFIG

    autonomy_enabled: bool


__all__ = [
    "AUTONOMY_NOTE_EN",
    "AUTONOMY_NOTE_ES",
    "CortexAutonomyBudget",
    "CortexAutonomyResponse",
    "CortexAutonomyUpdateRequest",
]
