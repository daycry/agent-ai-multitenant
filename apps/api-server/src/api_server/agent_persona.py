"""Resolución server-side de la persona efectiva de un agente (P0-1).

La persona (`agents.system_prompt` + `model_config.system_prompts.{es,en}`)
existía pero se descartaba en ejecución: los runs corrían con el system genérico
del runtime. Este módulo la resuelve con la MISMA precedencia que el frontend
(`apps/admin-panel/lib/persona/persona.ts::resolvePromptSource`): bilingüe
es → en → campo plano legacy. El orquestador emite el resultado como
`request["agent_persona"]` y el runtime lo prepende al system prompt efectivo.

No hay preferencia de idioma por tenant/proyecto en BD (la plataforma es ES+EN,
principio 12); se prefiere ES de forma determinista y se cae a EN/plano — mejor
una persona real en el otro idioma que ninguna.
"""

from __future__ import annotations

from typing import Any

# Cap defensivo: las personas built-in más ricas rondan 2k chars; 8k deja sitio
# a personas de tenant generosas sin dejar que una persona-novela desplace la
# tarea del prompt. El runtime re-capa por su lado (defensa en profundidad).
PERSONA_MAX_CHARS = 8000

_TRUNCATION_MARKER = "\n[... persona truncated ...]"


def _bilingual_prompt(model_config: Any) -> str:
    if not isinstance(model_config, dict):
        return ""
    prompts = model_config.get("system_prompts")
    if not isinstance(prompts, dict):
        return ""
    for lang in ("es", "en"):
        value = prompts.get(lang)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_agent_persona(agent: Any) -> dict[str, str] | None:
    """La persona efectiva de ``agent`` como payload del run, o ``None``.

    Claves emitidas: ``prompt`` (siempre), ``role``/``name`` (si existen).
    ``None`` = el agente no tiene persona con contenido → clave ausente en el
    payload (backward-compat, mismo contrato que skill_prompt_fragments).
    """
    prompt = _bilingual_prompt(getattr(agent, "model_config", None))
    if not prompt:
        flat = getattr(agent, "system_prompt", None)
        prompt = flat.strip() if isinstance(flat, str) and flat.strip() else ""
    if not prompt:
        return None
    if len(prompt) > PERSONA_MAX_CHARS:
        prompt = prompt[:PERSONA_MAX_CHARS] + _TRUNCATION_MARKER

    persona: dict[str, str] = {"prompt": prompt}
    role = getattr(agent, "role", None)
    if role:
        persona["role"] = str(getattr(role, "value", role))
    name = getattr(agent, "name", None)
    if name:
        persona["name"] = str(name)
    return persona
