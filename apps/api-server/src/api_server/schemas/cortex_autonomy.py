"""Pydantic schemas de la autonomía del córtex (Córtex F4, ADR 0078).

Dan forma a ``GET/PUT /owner/cortex/autonomy`` (gated ``require_system_owner``): el
estado del KILL-SWITCH global, los caps de budget y el budget consumido hoy en sus
dos dimensiones (búsquedas y dólares). Copy honesto: la curiosidad es un
comportamiento PROGRAMADO con límites de coste auditables, no curiosidad
consciente.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

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
    """El budget de curiosidad del día: consumido vs cap, en sus DOS dimensiones.

    Búsquedas y dólares son topes independientes que se aplican como AND (ADR
    0078): contar búsquedas acota el egress, pero no el dinero — una sola pasada
    con razonamiento profundo (``claude_sdk`` + WebSearch nativa, ADR 0076) puede
    costar más que veinte búsquedas baratas. Enseñar solo la primera dimensión
    dejaba al owner leyendo «3 de 5 búsquedas» con el cap de dólares agotado y la
    curiosidad parada sin explicación visible.
    """

    model_config = _BASE_CONFIG

    searches_today: int
    searches_cap: int
    #: Gasto real acumulado hoy (``INCRBYFLOAT`` de ``record_spend``) y su tope.
    #: Con un proveedor sin factura de API (Ollama local, ADR 0021) el gasto es 0
    #: legítimamente: 0 significa «no costó dinero», NO «no se sabe».
    cost_usd_today: float = 0.0
    cost_usd_cap: float = 0.0


class CortexAutonomyResponse(BaseModel):
    """Estado de la autonomía del córtex: kill-switch + gates + budget consumido hoy."""

    model_config = _BASE_CONFIG

    autonomy_enabled: bool
    web_enabled: bool
    # ADR 0080: el NAVEGADOR real (Playwright). Kill-switch aparte del de la web
    # —leer no es navegar— y, aun encendido, cada sesión la aprueba el owner.
    browser_enabled: bool = False
    curiosity_drive_threshold: float
    circuit_breaker_open: bool
    budget: CortexAutonomyBudget
    note_es: str = AUTONOMY_NOTE_ES
    note_en: str = AUTONOMY_NOTE_EN


class CortexAutonomyUpdateRequest(BaseModel):
    """Update PARCIAL de los gates del córtex (System Owner, desde la UI).

    Cada campo es opcional: ``autonomy_enabled`` flipa el kill-switch global de
    los bucles autónomos; ``web_enabled`` el gate de la web del córtex
    (``cortex.web_enabled``, ADR 0067 — deny-by-default). Un body sin ningún
    campo es un 422 honesto (no hay nada que escribir)."""

    model_config = _BASE_CONFIG

    autonomy_enabled: bool | None = None
    web_enabled: bool | None = None
    browser_enabled: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> CortexAutonomyUpdateRequest:
        if (
            self.autonomy_enabled is None
            and self.web_enabled is None
            and self.browser_enabled is None
        ):
            raise ValueError(
                "provide at least one of autonomy_enabled / web_enabled / browser_enabled"
            )
        return self


__all__ = [
    "AUTONOMY_NOTE_EN",
    "AUTONOMY_NOTE_ES",
    "CortexAutonomyBudget",
    "CortexAutonomyResponse",
    "CortexAutonomyUpdateRequest",
]
