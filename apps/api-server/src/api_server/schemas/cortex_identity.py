"""Pydantic schemas de la identidad del córtex (Córtex F3, ADR 0074/0077).

Dan forma a los payloads de ``GET /owner/cortex/identity`` y
``PUT /owner/cortex/identity`` (onboarding co-diseñado + override del owner).
Todos gated por ``require_system_owner`` (DB-authoritative).

Honestidad (copy): la identidad es un **modelo computacional**, NO consciencia.
El owner co-diseña ``name``/``core_values``/``narrative``/``language`` y fija
``learning_goals``; los rasgos Big-Five, el ``mood_baseline`` y el modelo del
owner los DERIVA la reflexión periódica de forma clampeada y versionada — el
owner NO los pisa a mano (guardrail de auto-modificación, ADR 0074).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

#: Idiomas soportados (ES + EN únicamente — Principio rector 12).
Language = str  # validado en el schema con un patrón; alias para legibilidad


class CortexTraits(BaseModel):
    """Rasgos Big-Five ∈ [0,1] (derivados por la reflexión, no editables por el owner)."""

    model_config = _BASE_CONFIG

    openness: float
    conscientiousness: float
    extraversion: float
    agreeableness: float
    neuroticism: float


class CortexBaseline(BaseModel):
    """Set-point PAD del mood (derivado por la reflexión; F2 lo lee como baseline)."""

    model_config = _BASE_CONFIG

    valence: float
    arousal: float
    dominance: float


class CortexIdentityResponse(BaseModel):
    """La identidad actual del córtex del owner (``GET /owner/cortex/identity``).

    ``onboarded_at`` NULL ⇒ onboarding pendiente (la UI lo muestra de forma
    prominente: "ponle nombre y valores a tu córtex")."""

    model_config = _BASE_CONFIG

    name: str | None = None
    core_values: list[str] = Field(default_factory=list)
    narrative: str = ""
    language: str = "es"
    learning_goals: list[str] = Field(default_factory=list)
    # Derivados por la reflexión (solo-lectura para el owner).
    traits: CortexTraits
    mood_baseline: CortexBaseline
    # "Lo que sé de mi owner" (relationship_model) — lo deriva la reflexión y lo
    # consume el self-context; el owner lo VE aquí (solo-lectura, como traits).
    relationship_model: dict[str, str] = Field(default_factory=dict)
    # Metadatos de versionado / estado de onboarding.
    version: int
    updated_by: str
    onboarded_at: datetime | None = None


class CortexIdentityUpdateRequest(BaseModel):
    """Onboarding / override del owner (``PUT /owner/cortex/identity``).

    SOLO campos editables por el owner: ``name``/``core_values``/``narrative``/
    ``language``/``learning_goals``. Un campo ``None`` no pisa el valor actual
    (PUT parcial). NO acepta ``traits`` ni ``mood_baseline`` (los deriva la
    reflexión, ADR 0074) — un payload que los incluya los ignora (``extra``
    prohibido los rechazaría con 422)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    core_values: list[str] | None = Field(default=None, max_length=20)
    narrative: str | None = Field(default=None, max_length=8000)
    # ES + EN únicamente (Principio rector 12).
    language: str | None = Field(default=None, pattern="^(es|en)$")
    learning_goals: list[str] | None = Field(default=None, max_length=20)


class CortexIdentityVersionItem(BaseModel):
    """Una versión del histórico (``GET /owner/cortex/identity/history``).

    El **timeline de versiones** del panel: qué cambió, cuándo, quién lo movió y por
    qué. El campo que da valor a la pantalla es ``diff`` — el
    ``{campo: {before, after}}`` que persiste ``cortex/identity.py::compute_diff``,
    solo con los campos que cambiaron. ``GET /owner/cortex/journal`` también lee
    ``cortex_identity_history``, pero aplana narrativas y DESCARTA el diff, así que
    hasta este endpoint la traza de qué tocó cada reflexión no era consultable.

    No se expone ``identity_state`` (el snapshot completo): la pantalla muestra
    cambios, y el estado vigente ya lo sirve ``GET /owner/cortex/identity``. Enviar
    el blob entero por versión multiplicaría el payload sin lector."""

    model_config = _BASE_CONFIG

    #: La versión que esta fila captura (monótona; 1 es el primer cambio real).
    version: int
    created_at: datetime
    #: ``reflection`` | ``owner_override`` | ``onboarding`` — quién movió la identidad.
    updated_by: str
    #: Resumen 1-línea del cambio (NULL si quien lo escribió no dejó motivo).
    reason: str | None = None
    #: ``{campo: {before, after}}`` — SOLO los campos que cambiaron.
    diff: dict[str, Any] = Field(default_factory=dict)


class CortexReflectResponse(BaseModel):
    """Resultado de disparar una pasada de reflexión (``POST /owner/cortex/reflect``).

    ``enqueued`` indica si la tarea de fondo se encoló (best-effort: un fallo del
    broker devuelve False sin romper). El ajuste real (narrativa/traits/baseline)
    lo aplica la tarea Celery de forma asíncrona y CLAMPEADA — este endpoint solo
    la dispara (la cadencia recurrente la agenda el beat de F4)."""

    model_config = _BASE_CONFIG

    enqueued: bool


__all__ = [
    "CortexBaseline",
    "CortexIdentityResponse",
    "CortexIdentityUpdateRequest",
    "CortexIdentityVersionItem",
    "CortexReflectResponse",
    "CortexTraits",
]
