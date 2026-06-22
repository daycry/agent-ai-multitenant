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
    "REGLA ABSOLUTA — MODO PLANIFICACIÓN. Tu ÚNICO objetivo es PLANIFICAR, NO programar.\n"
    "PROHIBIDO TERMINANTEMENTE, sin excepción:\n"
    "- escribir código de cualquier lenguaje (PHP, JS, SQL, HTML…), definiciones de "
    "funciones/clases/métodos, o el contenido de archivos;\n"
    "- usar bloques de código (```), comandos de shell, ni snippets de implementación;\n"
    "- decir «voy a crear/hacer/implementar» o entregar la aplicación ya construida.\n"
    "Aunque el usuario pida «una app», «el código» o «impleméntalo», NO lo escribas: responde "
    "SIEMPRE con un PLAN en lenguaje natural estructurado (fases → tareas con dependencias y "
    "criterios de aceptación). Puedes razonar sobre el código existente SOLO para informar el "
    "plan. Serán los AGENTES quienes escriban el código al EJECUTAR las tareas; tú nunca, "
    "ahora. Si tu borrador contuviera código, reescríbelo como descripción de tareas antes "
    "de responder."
)

# Plantilla de salida del PLAN (la síntesis): fuerza estructura, no prosa con código.
_PLAN_TEMPLATE = (
    "Estructura tu respuesta EXACTAMENTE así (markdown, SIN bloques de código):\n"
    "## Plan\n"
    "_(1-2 frases de alcance: qué se construye y qué queda fuera)_\n\n"
    "### Fase 1 — <nombre>\n"
    "- **<título de tarea>** — _depende de_: <tareas previas o «ninguna»>; "
    "_criterio de aceptación_: <resultado observable>\n"
    "- … (más tareas)\n\n"
    "### Fase 2 — <nombre>\n"
    "- …\n\n"
    "Repite por fase. Tareas accionables y atómicas. Si falta información para planificar, "
    "termina con UNA pregunta concreta al usuario en vez de inventar."
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
    # A full structured plan (## Plan + several ### Fases with acceptance criteria)
    # easily exceeds 1024 tokens — that budget truncated multi-phase plans
    # mid-sentence. 8192 gives generous headroom and is supported across the closed
    # catalog (ADR 0021). pm_decide/specialist_speak use far less. NOTE: this is a
    # fixed default for now; making it operator-configurable (chat_model_config.
    # max_tokens) is the follow-up for projects that need an even larger preview.
    # The authoritative, unbounded plan is the STRUCTURED task draft (create_plan),
    # not this prose synthesis.
    max_tokens: int = 8192
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
            "Sintetiza la postura del equipo en UNA sola respuesta. Habla en primera persona "
            "del equipo.\n\n" + _PLAN_TEMPLATE + "\n\n"
            "Cuando el plan esté completo, deja claro que está listo para insertarse como "
            "tareas del proyecto."
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

    def pm_plan_draft(
        self, state: PlanningState, contributions: Sequence[SpecialistContribution]
    ) -> dict[str, Any]:
        """Structured plan ready to materialise as a Plan (fases/tareas DAG). Called
        when the PM decides ``finish_planning``. Returns ``{title, summary, tasks}``
        where each task is ``{id, title, description, role, depends_on:[ids]}`` —
        the shape ``PlanSpecification`` / ``POST /projects/{id}/plans`` expects."""
        roles = sorted(r.value for r in state.team_roles)
        system = (
            "Eres el PROJECT MANAGER cerrando la planificación. " + _PLAN_ONLY_RULE + "\n\n"
            "Formaliza el PLAN acordado como un objeto JSON, SIN texto alrededor, con esta "
            "forma EXACTA:\n"
            '{"title": "<título corto>", "summary": "<resumen: alcance, decisiones, '
            'riesgos>", "tasks": [{"id": "t1", "title": "<acción>", "description": '
            '"<qué hacer y criterio de aceptación>", "role": "<rol>", "depends_on": []}]}\n'
            "Reglas: ids únicos y cortos (t1, t2, …); `depends_on` referencia SOLO ids "
            "declarados; NO ciclos; ordena las tareas por dependencias; tareas accionables "
            "y atómicas. `role` ∈ los roles del equipo cuando aplique. NO implementes ni "
            f"escribas código: solo el plan. Roles del equipo: {roles or '(genérico)'}."
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
        return _normalise_plan_draft(_extract_json(self._complete(messages)))


def _normalise_plan_draft(obj: dict[str, Any]) -> dict[str, Any]:
    """Coerce an LLM plan object into a valid draft: string task ids (auto-filled),
    ``depends_on`` as a list of KNOWN ids (unknown/self refs dropped), no cycles
    relied upon downstream (the create-plan endpoint re-validates the DAG). Returns
    ``{title, summary, tasks}``; ``tasks=[]`` when nothing usable was produced."""
    raw_tasks = obj.get("tasks")
    raw_tasks = raw_tasks if isinstance(raw_tasks, list) else []
    tasks: list[dict[str, Any]] = []
    ids: list[str] = []
    for i, t in enumerate(raw_tasks):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"t{i + 1}").strip() or f"t{i + 1}"
        title = str(t.get("title") or t.get("name") or "").strip()
        if not title:
            continue
        ids.append(tid)
        tasks.append(
            {
                "id": tid,
                "title": title[:255],
                "description": str(t.get("description") or "").strip(),
                "role": str(t.get("role") or "").strip(),
                "depends_on": [str(d) for d in (t.get("depends_on") or []) if isinstance(d, str)],
            }
        )
    id_set = set(ids)
    for t in tasks:  # drop unknown / self references so the spec validates
        t["depends_on"] = [d for d in t["depends_on"] if d in id_set and d != t["id"]]
    return {
        "title": str(obj.get("title") or "Plan del proyecto").strip()[:255],
        "summary": str(obj.get("summary") or "").strip(),
        "tasks": tasks,
    }


__all__ = ["LLMPlanningModel"]
