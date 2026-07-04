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
from typing import Any, Literal, cast

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

# Convención ÚNICA de línea de tarea, compartida por las contribuciones de los
# especialistas y por la síntesis, para que TODO el chat se vea igual (prioridad
# de UX del operador). Una sola fuente de verdad evita formatos divergentes.
_TASK_LINE = (
    "- **<título de la tarea>** — _depende de_: <tareas previas o «ninguna»>; "
    "_criterio de aceptación_: <resultado observable>"
)

# Plantilla de salida del PLAN (la síntesis): fuerza estructura, no prosa con código.
_PLAN_TEMPLATE = (
    "FORMATO OBLIGATORIO (respétalo al pie de la letra; markdown, SIN bloques de código):\n"
    "## Plan\n"
    "_(1-2 frases de alcance: qué se construye y qué queda fuera)_\n\n"
    "### Fase 1 — <nombre>\n"
    f"{_TASK_LINE}\n"
    "- … (más tareas)\n\n"
    "### Fase 2 — <nombre>\n"
    "- …\n\n"
    "Repite por fase. Tareas accionables y atómicas. Si falta información para planificar, "
    "termina con UNA pregunta concreta al usuario en vez de inventar."
)

# Plantilla de la CONTRIBUCIÓN de un especialista. El encabezado de rol (p.ej.
# "**🏗️ Arquitecto**") lo antepone el responder, así que el cuerpo NO debe
# repetirlo. Mismo esqueleto + misma convención de tarea para TODOS los
# especialistas → salida uniforme y escaneable.
_CONTRIBUTION_TEMPLATE = (
    "FORMATO OBLIGATORIO (respétalo al pie de la letra; markdown, SIN bloques de código, "
    "SIN saludos y SIN repetir tu nombre/rol —se añade automáticamente):\n"
    "**Objetivo:** _(1 frase: tu foco desde este rol en este plan)_\n\n"
    "**Tareas propuestas:**\n"
    f"{_TASK_LINE}\n"
    "- … (entre 2 y 5 tareas atómicas)\n\n"
    "**Riesgos y dependencias:** _(1-3 viñetas; «ninguno» si no aplica)_"
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
        # mypy: el narrowing por `in` sobre una tupla no estrecha a Literal —
        # el mapeo explícito sí (error preexistente, destapado 2026-07-03).
        role: Literal["user", "assistant", "system"] = (
            "user"
            if raw_role not in ("user", "assistant", "system")
            else cast(Literal["user", "assistant", "system"], raw_role)
        )
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


# Deterministic discipline → planning-role hints (supervisor/router pattern): the
# model's pm_decide tends to answer alone, so we DETECT the disciplines a request
# touches from its text and nudge the PM to convene the matching specialists.
# Substring match on the lowercased request (ES + EN).
_DISCIPLINE_KEYWORDS: dict[PlanningRole, tuple[str, ...]] = {
    PlanningRole.ARCHITECT: (
        "arquitect",
        "architect",
        "multi-tenant",
        "multitenant",
        "multi tenant",
        "escalab",
        "microservic",
        "patrón",
        "pattern",
    ),
    PlanningRole.BACKEND_DEV: (
        "base de datos",
        "database",
        " orm",
        "doctrine",
        "entidad",
        "entit",
        "migracion",
        "migración",
        "migration",
        "modelo de datos",
        "endpoint",
        "api rest",
        " sql",
        "esquema",
        "schema",
        "repositor",
    ),
    PlanningRole.FRONTEND_DEV: (
        "frontend",
        "front-end",
        "front end",
        "panel de",
        "panel admin",
        " ui",
        "interfaz",
        "react",
        "vue",
        "pantalla",
        "dashboard",
    ),
    PlanningRole.QA: ("test", "prueba", " qa", "cobertura", "coverage", "e2e", "calidad"),
    PlanningRole.SECURITY: (
        "auth",
        "autentic",
        "login",
        "seguridad",
        "security",
        "jwt",
        "oauth",
        "permiso",
        "permission",
        "roles",
        "daycry/auth",
        "token",
    ),
    PlanningRole.DEVOPS: (
        "ci/cd",
        "ci cd",
        "pipeline",
        "despliegue",
        "deploy",
        "docker",
        "kubernetes",
        "release",
    ),
    PlanningRole.TECHNICAL_WRITER: (
        "documentación",
        "documentation",
        "openapi",
        "swagger",
        "readme",
    ),
    PlanningRole.REVIEWER: ("revisión de código", "code review", "quality gate", "revisor"),
}


def _suggest_specialists(text: str, available: frozenset[PlanningRole]) -> tuple[PlanningRole, ...]:
    """Disciplines a request touches → matching team specialist roles (deterministic).

    Substring detection so specialist collaboration doesn't hinge on the model's
    pm_decide judgment (which tends to answer alone). Returns the detected roles
    INTERSECTED with the team's available roles (PM never matches — no keywords),
    sorted for determinism."""
    low = text.lower()
    hits = {
        role
        for role, kws in _DISCIPLINE_KEYWORDS.items()
        if role in available and any(kw in low for kw in kws)
    }
    return tuple(sorted(hits, key=lambda r: r.value))


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
            "REGLA DE COLABORACIÓN: trabajas en EQUIPO. Si el plan abarca varias "
            "disciplinas (arquitectura, modelo de datos, seguridad/auth, frontend, "
            "pruebas, despliegue/CI, documentación), USA invite_specialists y convoca a "
            "TODOS los roles relevantes de los disponibles ANTES de sintetizar — no "
            "planifiques en solitario algo que les compete. Reserva speak_alone para "
            "aclaraciones triviales, ajustes menores, o cuando ya hubo una ronda de "
            "especialistas y solo falta cerrar.\n"
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

        # Deterministic collaboration nudge (supervisor/router pattern): the model
        # under-invites, so detect the disciplines THIS request touches and convene the
        # matching specialists instead of letting the PM plan solo. Only on a fresh,
        # multi-disciplinary turn (>=2 detected, no specialist has spoken yet); never
        # overrides ask_user / finish_planning.
        latest_user = next(
            (
                str(e.get("content", ""))
                for e in reversed(state.chat_history)
                if e.get("role") == "user"
            ),
            "",
        )
        suggested = _suggest_specialists(latest_user, state.team_roles)
        if intent == PMIntent.SPEAK_ALONE and len(suggested) >= 2 and not state.contributions:
            intent = PMIntent.INVITE_SPECIALISTS
            specialists = suggested
        elif intent == PMIntent.INVITE_SPECIALISTS and suggested:
            # Union the model's picks with the detected roles (don't miss obvious ones).
            specialists = tuple(sorted(set(specialists) | set(suggested), key=lambda r: r.value))

        return PMDirective(
            intent=intent,
            rationale=str(obj.get("rationale", "")),
            specialists=specialists,
        )

    def specialist_speak(self, role: PlanningRole, state: PlanningState) -> SpecialistContribution:
        system = (
            f"Eres el especialista «{role.value}» del equipo, en una sesión de "
            "PLANIFICACIÓN. " + _PLAN_ONLY_RULE + " Aporta tu análisis técnico para el PLAN "
            "de forma CONCISA y accionable desde tu rol. NO escribas código ni implementes. "
            "No te repitas ni saludes.\n\n" + _CONTRIBUTION_TEMPLATE
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
            'riesgos>", "phases": [{"title": "<fase>", "tasks": ["t1", "t2"]}], '
            '"tasks": [{"id": "t1", "title": "<acción>", "description": '
            '"<qué hacer>", "role": "<rol>", "complexity": "<xs|s|m|l|xl>", '
            '"depends_on": [], "acceptance_criteria": '
            '["<criterio verificable>", "<criterio verificable>"]}]}\n'
            "Reglas: ids únicos y cortos (t1, t2, …); `depends_on` referencia SOLO ids "
            "declarados; NO ciclos; ordena las tareas por dependencias; tareas accionables "
            "y atómicas. `role` ∈ los roles del equipo cuando aplique. `complexity` ∈ "
            "{xs, s, m, l, xl} estimando el esfuerzo de la tarea (usa `m` si dudas). "
            "Agrupa las tareas en `phases` ordenadas por dependencias; cada fase lista los "
            "ids de sus tareas y CADA tarea aparece en exactamente una fase. "
            "Cada tarea lleva 2-5 "
            "`acceptance_criteria`: condiciones CONCRETAS y VERIFICABLES que definen cuándo la "
            "tarea está HECHA (p.ej. 'composer audit no reporta vulnerabilidades pendientes', "
            "'el endpoint GET /hello responde 200 con el JSON acordado'). Son criterios "
            "comprobables redactados en lenguaje claro, NO comandos a ejecutar. NO implementes "
            f"ni escribas código: solo el plan. Roles del equipo: {roles or '(genérico)'}."
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


_MAX_ACCEPTANCE_CRITERIA = 8
_MAX_CRITERION_LEN = 300
#: Valid task-complexity buckets (mirror sync_to_kanban / the cost model). A
#: chat-planned task now carries its own estimate instead of defaulting to `m`
#: for everything, so the cost breakdown weights tasks differently (c11).
_VALID_COMPLEXITY = frozenset({"xs", "s", "m", "l", "xl"})
_DEFAULT_COMPLEXITY = "m"


def _clean_acceptance_criteria(raw: Any) -> list[str]:
    """Coerce a task's ``acceptance_criteria`` into a clean list of descriptive,
    verifiable strings — the agent's "definition of done" (rendered by
    ``providers._criterion_text``). Trims; flattens a ``{description}`` dict to its
    text; drops empties/non-strings; caps count and per-criterion length. NOT
    executable commands (those are out of the planner's scope — too unreliable)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        value = (
            (item.get("description") or item.get("text") or item.get("criterion") or "")
            if isinstance(item, dict)
            else item
        )
        if not isinstance(value, str):
            continue
        text = value.strip()[:_MAX_CRITERION_LEN].strip()
        if text:
            out.append(text)
        if len(out) >= _MAX_ACCEPTANCE_CRITERIA:
            break
    return out


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
        complexity = str(t.get("complexity") or "").strip().lower()
        if complexity not in _VALID_COMPLEXITY:
            complexity = _DEFAULT_COMPLEXITY
        ids.append(tid)
        tasks.append(
            {
                "id": tid,
                "title": title[:255],
                "description": str(t.get("description") or "").strip(),
                "role": str(t.get("role") or "").strip(),
                "complexity": complexity,
                "depends_on": [str(d) for d in (t.get("depends_on") or []) if isinstance(d, str)],
                "acceptance_criteria": _clean_acceptance_criteria(t.get("acceptance_criteria")),
            }
        )
    id_set = set(ids)
    for t in tasks:  # drop unknown / self references so the spec validates
        t["depends_on"] = [d for d in t["depends_on"] if d in id_set and d != t["id"]]
    return {
        "title": str(obj.get("title") or "Plan del proyecto").strip()[:255],
        "summary": str(obj.get("summary") or "").strip(),
        "phases": _normalise_phases(obj.get("phases"), id_set),
        "tasks": tasks,
    }


def _normalise_phases(raw: Any, valid_ids: set[str]) -> list[dict[str, Any]]:
    """Coerce the LLM ``phases`` into ``[{title, tasks: [known ids]}]`` (c6).

    Enables the ``phase`` sync scope for chat-planned plans, which previously
    lacked ``phases`` entirely. Drops task ids that don't exist (so
    ``sync_to_kanban`` never rejects a phase that references an unknown id) and
    empty phases. Returns ``[]`` when nothing usable was produced — the phase
    scope is then simply unavailable and ``total``/``selection`` still work.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, ph in enumerate(raw):
        if not isinstance(ph, dict):
            continue
        title = str(ph.get("title") or ph.get("name") or f"Fase {i + 1}").strip()[:255]
        phase_tasks: list[str] = []
        seen: set[str] = set()
        for t in ph.get("tasks") or []:
            if isinstance(t, str) and t in valid_ids and t not in seen:
                seen.add(t)
                phase_tasks.append(t)
        if phase_tasks:
            out.append({"title": title or f"Fase {i + 1}", "tasks": phase_tasks})
    return out


__all__ = ["LLMPlanningModel"]
