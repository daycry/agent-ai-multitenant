"""LLM-backed ``PlanningModelClient`` (the Plan 04 wiring that was missing).

The planning sub-graph (``chat/planning_graph.py``) was shipped with only a
``ScriptedPlanningModel`` test double; nothing ever plugged a real LLM behind its
``PlanningModelClient`` seam, so ``run_planning_turn`` had no production caller and
the project planning chat did nothing on a user message.

``LLMPlanningModel`` is that adapter: it drives the PM decision, the specialist
turns and the synthesis through any ``shared_llm.LLMProvider`` (ADR 0021 — the
SAME provider the tenant assigned to its assistant, so it is provider-agnostic).

The ``PlanningModelClient`` methods are SYNC (the graph nodes are sync), while the
provider is async; we bridge with ``asyncio.run`` per call. ``run_planning_turn``
is therefore run inside a worker thread (``asyncio.to_thread``) from the async
endpoint so this nested ``asyncio.run`` has its own loop.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from shared_llm.base import LLMProvider
from shared_llm.types import Message

from api_server.chat.planning_graph import (
    PlanningRole,
    PlanningState,
    PMDirective,
    PMIntent,
    SpecialistContribution,
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Modo PLANNING (decisión del operador): el chat de planificación SOLO planifica.
# NUNCA implementa, NUNCA escribe código, NUNCA dice "voy a crearlo". Su salida es un
# PLAN (fases/tareas con dependencias) que luego se inserta como tareas del proyecto y
# que ejecutan los agentes. Se inyecta en cada prompt del sub-grafo de planning.
_PLAN_ONLY_RULE = (
    "REGLA CLAVE — MODO PLANIFICACIÓN: tu ÚNICO objetivo es PLANIFICAR, no ejecutar. "
    "NUNCA implementes, NUNCA escribas código, NUNCA digas que «vas a crear/hacer» la "
    "aplicación. Puedes razonar sobre el código y los archivos existentes, pero solo para "
    "informar el plan. El resultado de esta sesión es un PLAN (fases/tareas con "
    "dependencias y criterios de aceptación) que se insertará como tareas del proyecto; "
    "serán los AGENTES quienes lo ejecuten después, no tú ahora."
)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort: parse the first ``{...}`` object out of an LLM reply."""
    try:
        bare = json.loads(text)  # the model obeyed and returned bare JSON
        if isinstance(bare, dict):
            return bare
    except (json.JSONDecodeError, TypeError):
        pass
    match = _JSON_RE.search(text or "")
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_intent(value: Any) -> PMIntent:
    try:
        return PMIntent(str(value))
    except ValueError:
        return PMIntent.SPEAK_ALONE  # unknown → safe default (PM answers directly)


def _history_messages(state: PlanningState) -> list[Message]:
    out: list[Message] = []
    for entry in state.chat_history:
        raw_role = str(entry.get("role", "user"))
        role = raw_role if raw_role in ("user", "assistant", "system") else "user"
        out.append(Message(role=role, content=str(entry.get("content", ""))))
    return out


def _context_note(state: PlanningState) -> Message | None:
    if not state.project_context:
        return None
    return Message(
        role="system",
        content=(
            "Contexto del proyecto (resumen): " + json.dumps(state.project_context, default=str)
        ),
    )


@dataclass
class LLMPlanningModel:
    """Adapt an ``LLMProvider`` to the planning sub-graph's ``PlanningModelClient``."""

    provider: LLMProvider
    model: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.4
    extra_call_kwargs: dict[str, Any] = field(default_factory=dict)

    def _complete(self, messages: list[Message]) -> str:
        resp = asyncio.run(
            self.provider.complete(
                messages,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                **self.extra_call_kwargs,
            )
        )
        return resp.content or ""

    def pm_decide(self, state: PlanningState) -> PMDirective:
        available = sorted(r.value for r in state.team_roles if r != PlanningRole.PROJECT_MANAGER)
        system = (
            "Eres el PROJECT MANAGER de un equipo de desarrollo en una sesión de "
            "PLANIFICACIÓN. " + _PLAN_ONLY_RULE + "\n\n"
            "Decides cómo afrontar ESTE turno del chat. Responde ÚNICAMENTE con un objeto "
            "JSON, sin texto alrededor:\n"
            '{"intent": "<speak_alone|invite_specialists|ask_user|finish_planning>", '
            '"rationale": "<breve motivo>", "specialists": ["<rol>", ...]}\n'
            "- speak_alone: respondes tú directamente (avanzas el plan).\n"
            "- invite_specialists: pides la opinión de especialistas (lista en "
            '"specialists", SOLO de los disponibles).\n'
            "- ask_user: necesitas más información del usuario para planificar.\n"
            "- finish_planning: el plan ya está claro y listo para formalizarse e "
            "insertarse como tareas del proyecto.\n"
            f"Especialistas disponibles: {available or '(ninguno)'}."
        )
        messages = [Message(role="system", content=system)]
        note = _context_note(state)
        if note:
            messages.append(note)
        messages.extend(_history_messages(state))
        obj = _extract_json(self._complete(messages))
        intent = _parse_intent(obj.get("intent"))
        specialists: tuple[PlanningRole, ...] = ()
        if intent == PMIntent.INVITE_SPECIALISTS:
            raw = obj.get("specialists")
            picked: list[PlanningRole] = []
            for item in raw if isinstance(raw, list) else []:
                try:
                    picked.append(PlanningRole(str(item)))
                except ValueError:
                    continue
            specialists = tuple(picked)
        return PMDirective(
            intent=intent,
            rationale=str(obj.get("rationale", "")),
            specialists=specialists,
        )

    def specialist_speak(self, role: PlanningRole, state: PlanningState) -> SpecialistContribution:
        system = (
            f"Eres el especialista «{role.value}» del equipo, en una sesión de "
            "PLANIFICACIÓN. " + _PLAN_ONLY_RULE + " Aporta tu análisis técnico para el PLAN "
            "de forma CONCISA y accionable: qué tareas harían falta desde tu rol, en qué "
            "orden, dependencias, riesgos y criterios de aceptación. NO escribas código ni "
            "implementes. No te repitas ni saludes."
        )
        messages = [Message(role="system", content=system)]
        note = _context_note(state)
        if note:
            messages.append(note)
        messages.extend(_history_messages(state))
        return SpecialistContribution(role=role, content=self._complete(messages))

    def pm_synthesise(
        self, state: PlanningState, contributions: Sequence[SpecialistContribution]
    ) -> str:
        system = (
            "Eres el PROJECT MANAGER. " + _PLAN_ONLY_RULE + "\n\n"
            "Redacta UNA sola respuesta (markdown) que sintetice la postura del equipo y "
            "haga avanzar el PLAN. Habla en primera persona del equipo. NUNCA digas que vas "
            "a implementar/crear nada: presentas un PLAN. Estructura el avance en fases y "
            "tareas accionables con sus dependencias y criterios de aceptación. Si falta "
            "información, termina con una pregunta concreta al usuario. Cuando el plan esté "
            "completo, deja claro que está listo para insertarse como tareas del proyecto."
        )
        messages = [Message(role="system", content=system)]
        note = _context_note(state)
        if note:
            messages.append(note)
        messages.extend(_history_messages(state))
        if contributions:
            joined = "\n".join(f"- [{c.role.value}] {c.content}" for c in contributions)
            messages.append(
                Message(role="system", content="Aportaciones de los especialistas:\n" + joined)
            )
        return self._complete(messages)


__all__ = ["LLMPlanningModel"]
