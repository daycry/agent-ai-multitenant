"""Córtex F4/F5 — política de olvido y consolidación de memoria (ADR 0077).

Lógica **pura y determinista** (sin I/O) del olvido del córtex: el ``retention_score``
y la decisión de proteger/olvidar una memoria. El mantenimiento de fondo
(:mod:`workers.cortex_maintenance`) la aplica sobre la memoria EPISÓDICA del owner.

Invariantes NO negociables (ADR 0077):

  * **Protección dura**: ``metadata_.kind ∈ {identity, owner_model}`` NUNCA se
    auto-olvida — es el núcleo del autoconcepto y del modelo del owner. Tampoco las
    memorias semánticas de reflexión/aprendizaje (``kind ∈ {reflection, learning}``):
    el olvido autónomo de F4 se acota a la episódica de BAJA retención.
  * **Olvido reversible**: solo **soft-delete** (``deleted_at``, nunca delete
    físico — postura ADR 0059). El owner puede inspeccionar y restaurar.
  * **Conservador**: ``retention_score = importance * recency * recall_frequency``;
    empezamos con una ventana amplia para no enterrar long-tail útil.

El reloj entra como parámetro (``now``) para que el scoring sea 100% reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Vida media de la recencia (días): una memoria episódica pierde la mitad de su
#: "frescura" cada ~30 días. Conservador (ADR 0077: empezar amplio, medir, ajustar).
RECENCY_HALF_LIFE_DAYS: float = 30.0

#: Importancia por defecto cuando ``metadata_.importance`` no está fijada.
DEFAULT_IMPORTANCE: float = 0.5

#: Umbral por defecto: por debajo de este ``retention_score``, una memoria episódica
#: NO protegida es candidata a soft-delete. Bajo a propósito (olvido conservador).
DEFAULT_RETENTION_FORGET_THRESHOLD: float = 0.1

#: kinds del córtex que NUNCA se auto-olvidan (núcleo del autoconcepto, ADR 0077).
#: ``reflection``/``learning`` son semánticas (no episódicas) y quedan fuera del
#: barrido igualmente; se listan para que la protección sea explícita y auditable.
PROTECTED_KINDS: frozenset[str] = frozenset({"identity", "owner_model", "reflection", "learning"})


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def recency_factor(created_at: datetime, now: datetime) -> float:
    """Factor de recencia ∈ ``(0, 1]``: ``0.5 ** (edad_dias / half_life)``.

    Una memoria recién creada vale ~1.0; pierde la mitad cada
    :data:`RECENCY_HALF_LIFE_DAYS`. Determinista; tolerante a tz naïve/aware."""
    if created_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=created_at.tzinfo)
    elif created_at.tzinfo is None and now.tzinfo is not None:
        created_at = created_at.replace(tzinfo=now.tzinfo)
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


def importance_of(metadata: dict[str, Any] | None) -> float:
    """La importancia de la memoria (``metadata_.importance`` ∈ ``[0,1]``, default 0.5).

    Un valor no numérico / ausente cae al default; fuera de rango se recorta."""
    src = metadata or {}
    raw = src.get("importance")
    if isinstance(raw, bool) or raw is None:
        return DEFAULT_IMPORTANCE
    try:
        return _clamp(float(raw), 0.0, 1.0)
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE


def retention_score(
    *,
    created_at: datetime,
    now: datetime,
    metadata: dict[str, Any] | None,
    recall_frequency: float = 1.0,
) -> float:
    """``retention_score = importance * recency * recall_frequency`` (ADR 0077).

    ``recall_frequency`` por defecto 1.0 (la plataforma aún no instrumenta el
    contador de recalls; el factor queda preparado para cuando lo haga, sin cambiar
    la forma de la fórmula). Resultado ∈ ``[0,1]``, determinista dado ``now``."""
    importance = importance_of(metadata)
    recency = recency_factor(created_at, now)
    freq = _clamp(recall_frequency, 0.0, 1.0)
    return _clamp(importance * recency * freq, 0.0, 1.0)


def is_protected(metadata: dict[str, Any] | None) -> bool:
    """¿La memoria está PROTEGIDA del auto-olvido? (kind del núcleo, ADR 0077).

    True si ``metadata_.kind`` es identity/owner_model (núcleo del autoconcepto) o
    una semántica de reflexión/aprendizaje. Estas NUNCA se soft-deletean."""
    kind = str((metadata or {}).get("kind") or "").strip().lower()
    return kind in PROTECTED_KINDS


@dataclass(frozen=True)
class ForgetDecision:
    """El veredicto del olvido de UNA memoria: ``forget`` + ``score`` + ``reason``."""

    forget: bool
    score: float
    reason: str


def decide_forget(
    *,
    created_at: datetime,
    now: datetime,
    metadata: dict[str, Any] | None,
    memory_type: str,
    threshold: float = DEFAULT_RETENTION_FORGET_THRESHOLD,
    recall_frequency: float = 1.0,
) -> ForgetDecision:
    """¿Soft-deletear esta memoria? Aplica protección + tipo + umbral de retención.

    Reglas (en orden):
      1. PROTEGIDA (identity/owner_model/reflection/learning) → NUNCA olvidar.
      2. Solo la EPISÓDICA es candidata (la semántica destila reglas duraderas).
      3. Olvidar si ``retention_score < threshold``.

    Puro y determinista; el caller hace el soft-delete real (filtrando owner)."""
    if is_protected(metadata):
        return ForgetDecision(forget=False, score=1.0, reason="protected_kind")
    if memory_type != "episodic":
        return ForgetDecision(forget=False, score=1.0, reason="not_episodic")
    score = retention_score(
        created_at=created_at, now=now, metadata=metadata, recall_frequency=recall_frequency
    )
    if score < threshold:
        return ForgetDecision(forget=True, score=score, reason="low_retention")
    return ForgetDecision(forget=False, score=score, reason="retained")


__all__ = [
    "DEFAULT_IMPORTANCE",
    "DEFAULT_RETENTION_FORGET_THRESHOLD",
    "PROTECTED_KINDS",
    "RECENCY_HALF_LIFE_DAYS",
    "ForgetDecision",
    "decide_forget",
    "importance_of",
    "is_protected",
    "recency_factor",
    "retention_score",
]
