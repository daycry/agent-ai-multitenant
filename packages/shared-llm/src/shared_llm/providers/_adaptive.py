"""Adaptación reactiva a lo que cada modelo rechaza (ADR 0169 D2, `task_cm_05`).

Los modelos de razonamiento de OpenAI (GPT-5, serie o, Codex) rechazan cosas que el
resto acepta: ``max_tokens`` en vez de ``max_completion_tokens``, una ``temperature``
distinta de la de por defecto, un tope por encima de su máximo, o el propio endpoint
``/chat/completions``. Decidirlo por el nombre del modelo envejece —GPT-5.4 no lleva
«codex» en el nombre y también es sólo Responses—, así que se decide por lo que el
servidor CONTESTA:

1. el primer 400 reconocible produce un cuerpo corregido (`adapt_body`) o la señal de
   cambiar de transporte (`needs_responses_api`);
2. el proveedor reintenta una sola vez;
3. la corrección se guarda en `ModelAdaptations` y la llamada siguiente del mismo
   modelo ya sale bien.

Un 400 que no encaja con ninguno de esos casos no se toca: sigue siendo un error.
Módulo puro, sin red.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Lo que dicen Copilot y Azure cuando el modelo sólo se sirve por la Responses API.
_RESPONSES_ONLY_MARKERS = (
    "unsupported_api_for_model",
    "operationnotsupported",
    "chatcompletion operation does not work",
    "not accessible via the /chat/completions endpoint",
)
#: «supports at most 4096 completion tokens» / «… output tokens».
_MAX_CAP_RE = re.compile(r"at most (\d+) (?:completion|output) tokens", re.I)
#: «maximum context length is 8192 tokens … (808 in the messages, 8192 in the completion)».
_CONTEXT_RE = re.compile(r"maximum context length is (\d+) tokens", re.I)
#: Cada cifra «N in the <algo>» del paréntesis, salvo la de la completion.
_CONTEXT_PART_RE = re.compile(r"(\d+) in the (\w+)", re.I)
_CONTEXT_MARGIN = 64
_CONTEXT_MIN_CAP = 256
_CAP_KEYS = ("max_completion_tokens", "max_output_tokens", "max_tokens")


def needs_responses_api(error_text: str) -> bool:
    """¿Dice el servidor que este modelo sólo se sirve por la Responses API?"""
    lowered = error_text.lower()
    return any(marker in lowered for marker in _RESPONSES_ONLY_MARKERS)


def _rejects(lowered: str, param: str) -> bool:
    """¿El error nombra ``param`` como no soportado?"""
    if param not in lowered:
        return False
    return any(
        marker in lowered
        for marker in ("unsupported", "does not support", "only the default", "not supported")
    )


def adapt_body(body: dict[str, Any], error_text: str) -> dict[str, Any] | None:
    """El cuerpo corregido para ``error_text``, o ``None`` si el error no es adaptable."""
    lowered = error_text.lower()
    fixed = dict(body)
    changed = False
    if "max_tokens" in fixed and (
        "max_completion_tokens" in lowered or _rejects(lowered, "max_tokens")
    ):
        fixed["max_completion_tokens"] = fixed.pop("max_tokens")
        changed = True
    if "temperature" in fixed and _rejects(lowered, "temperature"):
        fixed.pop("temperature")
        changed = True
    match = _MAX_CAP_RE.search(error_text)
    if match:
        limit = int(match.group(1))
        for key in _CAP_KEYS:
            if key in fixed and int(fixed[key]) > limit:
                fixed[key] = limit
                changed = True
    context = _CONTEXT_RE.search(error_text)
    parts = [
        int(n) for n, what in _CONTEXT_PART_RE.findall(error_text) if what.lower() != "completion"
    ]
    if context and parts:
        room = int(context.group(1)) - sum(parts) - _CONTEXT_MARGIN
        if room >= _CONTEXT_MIN_CAP:
            for key in _CAP_KEYS:
                if key in fixed and int(fixed[key]) > room:
                    fixed[key] = room
                    changed = True
    return fixed if changed else None


@dataclass
class ModelAdaptations:
    """Lo aprendido para UN modelo, aplicado a sus llamadas siguientes."""

    use_completion_tokens: bool = False
    drop_temperature: bool = False
    cap_limit: int | None = None
    responses_api: bool = False

    def apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """``body`` con las correcciones ya aprendidas."""
        out = dict(body)
        if self.use_completion_tokens and "max_tokens" in out:
            out["max_completion_tokens"] = out.pop("max_tokens")
        if self.drop_temperature:
            out.pop("temperature", None)
        if self.cap_limit is not None:
            for key in _CAP_KEYS:
                if key in out and int(out[key]) > self.cap_limit:
                    out[key] = self.cap_limit
        return out

    def learn(self, before: dict[str, Any], after: dict[str, Any], error_text: str = "") -> None:
        """Registra qué cambió entre el cuerpo rechazado y el corregido.

        El tope de la ventana de contexto depende del prompt de ESA llamada: no se
        memoriza. Sólo el «at most N tokens» (propiedad del modelo) se guarda.
        """
        if "max_tokens" in before and "max_completion_tokens" in after:
            self.use_completion_tokens = True
        if "temperature" in before and "temperature" not in after:
            self.drop_temperature = True
        if not _MAX_CAP_RE.search(error_text):
            return
        for key in _CAP_KEYS:
            if key in before and key in after and int(after[key]) < int(before[key]):
                self.cap_limit = int(after[key])


@dataclass
class AdaptationMemo:
    """Las adaptaciones de un proveedor, por modelo, durante su vida."""

    _by_model: dict[str, ModelAdaptations] = field(default_factory=dict)

    def for_model(self, model: str) -> ModelAdaptations:
        return self._by_model.setdefault(model, ModelAdaptations())


__all__ = ["AdaptationMemo", "ModelAdaptations", "adapt_body", "needs_responses_api"]
