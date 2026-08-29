"""Chat mode catalog (Plan 03 task_03_06).

Each chat mode shapes two things the agent team needs every turn:

  - a **system prompt** the LLM gets prepended (sets the team's
    operating frame — "build a plan", "explore freely", "execute the
    approved plan");
  - a **tool whitelist** that constrains what tools the team can call
    in that mode (e.g. `execution` allows shell/file write, while
    `planning` keeps the sandbox light to focus on structure).

Built-in modes (planning / discussion / execution) ship as part of the
platform. Custom tenant-defined modes (task_03_08) reuse this same
config shape and are resolved by name. When a mode is `custom`, the
tenant config is loaded; if none is found, we fall back to a sensible
default (the planning preset with a relabelled prompt).

This module is pure Python — no DB, no I/O — so the agent loop, the
sub-graph dispatcher and the REST schema validator can share it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from api_server.db.conversation import ChatMode


# ---------------------------------------------------------------------------
# Built-in modes
# ---------------------------------------------------------------------------
class BuiltinChatMode(enum.StrEnum):
    """Subset of `ChatMode` excluding ``custom``.

    Custom modes are resolved by name against the tenant config in
    `resolve_mode_config`. Splitting the enum keeps the built-in
    set static so the dispatcher can pattern-match on it.
    """

    PLANNING = "planning"
    DISCUSSION = "discussion"
    EXECUTION = "execution"


@dataclass(frozen=True)
class ChatModeConfig:
    """Static configuration a chat mode injects into a turn."""

    name: str
    label_es: str
    label_en: str
    # The system prompt prepended to every LLM call while this mode
    # is active. The agent loop appends the team's role-specific
    # prompt + the chat history on top of this.
    system_prompt: str
    # Tool names (subset of the project's registry) that the agent is
    # allowed to call in this mode. Empty tuple = no tools (pure
    # discussion). The worker forwards this list to the agent-runtime in
    # the task spec; the runtime's `ToolRegistry` enforces it at tool-call
    # time — a tool outside the set is rejected before it runs
    # (task_06_14_07). This is a lightweight call-time allowlist, NOT the
    # full layered guardrail engine (pre_llm / post_llm / pre_tool /
    # post_tool), which lands in Plan 11.
    allowed_tools: tuple[str, ...] = ()
    # Whether the planning sub-graph (PM agent as portavoz, others
    # chiming in) is active. Only ``planning`` flips this on by
    # default; custom modes can opt in via this flag.
    planning_subgraph: bool = False


# ---------------------------------------------------------------------------
# Built-in catalog
# ---------------------------------------------------------------------------
# ATENCIÓN antes de tocar estos textos: desde el 2026-08-29 (ADR 0162) este
# catálogo es la ÚNICA fuente de los prompts de modo. Lo que hay aquí es lo que
# `GET /chat-modes` publica como «prompt efectivo» Y lo que `_simple_reply`
# (chat/responder.py) mete de verdad como mensaje `system`. Antes eran dos
# catálogos con textos distintos, así que la pantalla de auditoría enseñaba algo
# que no se enviaba. Si haces un segundo diccionario de prompts en otro módulo,
# reabres exactamente esa brecha —y `test_chat_mode_prompt_single_source.py`
# rompe para avisarlo.
#
# `_PLANNING_PROMPT` es la excepción y conviene saberlo: `planning` NO pasa por
# `_simple_reply`, va por el sub-grafo multi-agente, y los prompts que ese
# sub-grafo envía se componen por nodo en `chat/planning_llm.py`. Este texto
# describe el modo para la UI; no es el que recibe el LLM de planning.
_PLANNING_PROMPT = (
    "Estás en el modo PLANNING. El equipo construye un plan estructurado"
    " para resolver la petición del usuario. El Project Manager es el"
    " portavoz único; el resto del equipo interviene cuando aporta valor"
    " específico (arquitectura, riesgos, estimaciones). El objetivo de la"
    " conversación es producir un plan canónico con fases, tareas,"
    " dependencias y tests automáticos. NO ejecutes acciones todavía;"
    " usa solo herramientas de consulta o de comentario sobre el plan."
)

# Fusión de los dos catálogos (ADR 0162). Lo que se conservó del texto que vivía
# en `responder`: el encuadre de PORTAVOZ ÚNICO —`_simple_reply` es una sola
# llamada al LLM que produce UN mensaje firmado por el equipo, así que «cada
# agente puede intervenir libremente» empujaba al modelo a fingir un diálogo— y
# la petición explícita de markdown, que el frontend renderiza. Lo que se
# conservó de este módulo: el encuadre del modo y la guarda de no ejecutar
# acciones de escritura.
_DISCUSSION_PROMPT = (
    "Estás en el modo DISCUSSION y eres el PORTAVOZ ÚNICO del equipo del"
    " proyecto: respondes por todos en un solo mensaje, sin simular un"
    " diálogo entre agentes. El equipo explora ideas en torno a la petición"
    " del usuario, así que aporta perspectivas, alternativas y riesgos de"
    " forma clara y concisa, en markdown. NO se produce un plan estructurado"
    " en este modo; el resultado esperado es claridad sobre el problema y sus"
    " opciones. Mantén la conversación textual: no ejecutes acciones de"
    " escritura ni llamadas externas."
)

# Aquí la fusión además CORRIGE. El texto anterior ordenaba «registra avances en
# task_comment, actualiza estados vía kanban_update», y este canal no entrega
# ninguna tool: `respond_to_conversation` sólo bifurca hacia el sub-grafo en
# `planning`; discussion y execution caen los dos en `_simple_reply`, una llamada
# al LLM sin tools. Pedirle al modelo algo que no puede hacer no es inocuo: su
# incumplimiento se lee como que el equipo no hizo el trabajo. La ejecución real
# la arranca `POST /plans/{id}/start-execution`, y decirlo evita que el usuario
# espere aquí un trabajo que se lanza en otro sitio.
#
# Cablear `allowed_tools` por modo (que las haría ciertas) está DESCARTADO a
# propósito: encender una restricción que lleva tiempo siendo decorativa cambia
# el comportamiento de todos los agentes de golpe y pide su propio ADR.
_EXECUTION_PROMPT = (
    "Estás en el modo EXECUTION y eres el PORTAVOZ ÚNICO del equipo del"
    " proyecto: respondes por todos en un solo mensaje, en markdown. El plan"
    " ya está aprobado y sincronizado al Kanban, y el equipo lo ejecuta"
    " siguiendo el DAG de dependencias. Tu papel en ESTA conversación es"
    " coordinar y dar seguimiento por escrito: estado, siguientes pasos,"
    " bloqueos y decisiones, claro y conciso. Esta conversación no ejecuta"
    " trabajo ni mueve el Kanban por sí misma —la ejecución real se arranca"
    " sobre el plan aprobado—, y las acciones sensibles (deploy, git_push)"
    " siguen sujetas al motor de aprobación humana de cada proyecto."
)


_BUILTIN_PLANNING = ChatModeConfig(
    name=BuiltinChatMode.PLANNING.value,
    label_es="Planning",
    label_en="Planning",
    system_prompt=_PLANNING_PROMPT,
    # Planning needs to look at code and existing plans but not
    # mutate state. http_request gives the architect a way to fetch
    # docs/specs; the kanban tools are read-only via their args.
    allowed_tools=(
        "file_read",
        "file_list",
        "http_request",
        "task_comment",
        "agent_invoke",
        "kanban_update",
    ),
    planning_subgraph=True,
)

_BUILTIN_DISCUSSION = ChatModeConfig(
    name=BuiltinChatMode.DISCUSSION.value,
    label_es="Discusión",
    label_en="Discussion",
    system_prompt=_DISCUSSION_PROMPT,
    # Pure conversation — no tools. The agent_invoke escape hatch is
    # intentionally absent so a multi-agent loop cannot spin up here.
    allowed_tools=(),
    planning_subgraph=False,
)

_BUILTIN_EXECUTION = ChatModeConfig(
    name=BuiltinChatMode.EXECUTION.value,
    label_es="Ejecución",
    label_en="Execution",
    system_prompt=_EXECUTION_PROMPT,
    # Full tool surface. Effects tools (kanban_update, task_comment,
    # notify_user) and the worker-side tools (shell_exec, file_write,
    # http_request) are all live. The approval engine still gates
    # sensitive categories per the project policy.
    allowed_tools=(
        "shell_exec",
        "stack_exec",
        "file_read",
        "file_write",
        "file_list",
        "http_request",
        "kanban_update",
        "task_comment",
        "notify_user",
        "agent_invoke",
    ),
    planning_subgraph=False,
)


BUILTIN_MODES: dict[str, ChatModeConfig] = {
    BuiltinChatMode.PLANNING.value: _BUILTIN_PLANNING,
    BuiltinChatMode.DISCUSSION.value: _BUILTIN_DISCUSSION,
    BuiltinChatMode.EXECUTION.value: _BUILTIN_EXECUTION,
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CustomModeSpec:
    """Tenant-defined custom mode (Plan 03 task_03_08).

    Stored in `platform_settings` or per-tenant settings later; for
    now this is the in-memory shape callers pass to `resolve_mode_config`
    when a conversation's `current_mode='custom'`.
    """

    name: str  # the conversation's custom_mode_name
    label_es: str
    label_en: str
    system_prompt: str
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    planning_subgraph: bool = False

    def as_config(self) -> ChatModeConfig:
        return ChatModeConfig(
            name=self.name,
            label_es=self.label_es,
            label_en=self.label_en,
            system_prompt=self.system_prompt,
            allowed_tools=self.allowed_tools,
            planning_subgraph=self.planning_subgraph,
        )


@dataclass(frozen=True)
class ChatModeListing:
    """Una entrada del catálogo de modos para la UI (Plan 06.17 task_06_17_11).

    Lo consume la vista "prompt efectivo" de la sección Persona: combina el
    ``system_prompt`` del rol del agente con el del modo de chat seleccionado.
    El modo ``custom`` se lista con ``available=False`` ("No disponible aún"):
    los modos custom creables de extremo a extremo están diferidos (alcance del
    plan 06.17), así que la UI lo muestra pero no lo deja elegir — honestidad de
    estado (regla 4 del plan).
    """

    name: str
    label_es: str
    label_en: str
    system_prompt: str
    available: bool


def list_chat_modes() -> list[ChatModeListing]:
    """Catálogo de modos de chat para la UI, en orden de lectura.

    Los tres modos built-in (planning/discussion/execution) son
    ``available=True`` y traen su ``system_prompt`` real (fuente única: no se
    duplica en el frontend). El modo ``custom`` aparece al final marcado
    ``available=False`` — la UI lo muestra como "No disponible aún" en vez de
    fingir una capacidad que no existe todavía.
    """
    listings = [
        ChatModeListing(
            name=cfg.name,
            label_es=cfg.label_es,
            label_en=cfg.label_en,
            system_prompt=cfg.system_prompt,
            available=True,
        )
        for cfg in (_BUILTIN_PLANNING, _BUILTIN_DISCUSSION, _BUILTIN_EXECUTION)
    ]
    listings.append(
        ChatModeListing(
            name=ChatMode.CUSTOM.value,
            label_es="Personalizado",
            label_en="Custom",
            system_prompt="",
            available=False,
        )
    )
    return listings


def resolve_mode_config(
    current_mode: str,
    *,
    custom_mode_name: str | None = None,
    custom_modes: dict[str, CustomModeSpec] | None = None,
) -> ChatModeConfig:
    """Resolve a conversation's mode to its `ChatModeConfig`.

    ``current_mode`` is the string stored on `Conversation.current_mode`
    (one of "planning", "discussion", "execution", "custom").

    For a built-in mode this is a dict lookup. For ``custom`` we look
    up `custom_mode_name` in the tenant's `custom_modes` registry;
    if the name is missing we fall back to the planning preset with
    its label adjusted — that's safer than silently using execution.
    """
    if current_mode in BUILTIN_MODES:
        return BUILTIN_MODES[current_mode]

    if current_mode == ChatMode.CUSTOM.value:
        if not custom_mode_name:
            raise ValueError("custom current_mode requires custom_mode_name")
        registry = custom_modes or {}
        spec = registry.get(custom_mode_name)
        if spec is not None:
            return spec.as_config()
        # Unknown custom name: fall back to the planning preset under
        # the custom label so the UI still makes sense.
        fallback = BUILTIN_MODES[BuiltinChatMode.PLANNING.value]
        return ChatModeConfig(
            name=custom_mode_name,
            label_es=custom_mode_name,
            label_en=custom_mode_name,
            system_prompt=fallback.system_prompt,
            allowed_tools=fallback.allowed_tools,
            planning_subgraph=fallback.planning_subgraph,
        )

    raise ValueError(f"unknown chat mode: {current_mode!r}")


__all__ = [
    "BUILTIN_MODES",
    "BuiltinChatMode",
    "ChatModeConfig",
    "ChatModeListing",
    "CustomModeSpec",
    "list_chat_modes",
    "resolve_mode_config",
]
