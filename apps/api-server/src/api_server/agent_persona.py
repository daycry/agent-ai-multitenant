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

import hashlib
from collections.abc import Iterable
from typing import Any

# Cap defensivo: las personas built-in más ricas rondan 2k chars; 8k deja sitio
# a personas de tenant generosas sin dejar que una persona-novela desplace la
# tarea del prompt. El runtime re-capa por su lado (defensa en profundidad).
PERSONA_MAX_CHARS = 8000

_TRUNCATION_MARKER = "\n[... persona truncated ...]"


def _bilingual_prompt(model_config: Any, *, language: str | None = None) -> str:
    if not isinstance(model_config, dict):
        return ""
    prompts = model_config.get("system_prompts")
    if not isinstance(prompts, dict):
        return ""
    # `task_cv_35` (F-06): el idioma del proyecto manda; sin preferencia, ES.
    order = ("en", "es") if str(language or "").lower().startswith("en") else ("es", "en")
    for lang in order:
        value = prompts.get(lang)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_agent_persona(
    agent: Any, *, language: str | None = None, tool_slugs: Iterable[str] | None = None
) -> dict[str, str] | None:
    """La persona efectiva de ``agent`` como payload del run, o ``None``.

    Claves emitidas: ``prompt`` (siempre), ``role``/``name`` (si existen).
    ``None`` = el agente no tiene persona con contenido → clave ausente en el
    payload (backward-compat, mismo contrato que skill_prompt_fragments).
    """
    prompt = _bilingual_prompt(getattr(agent, "model_config", None), language=language)
    if not prompt:
        flat = getattr(agent, "system_prompt", None)
        prompt = flat.strip() if isinstance(flat, str) and flat.strip() else ""
    if not prompt:
        return None
    if len(prompt) > PERSONA_MAX_CHARS:
        prompt = prompt[:PERSONA_MAX_CHARS] + _TRUNCATION_MARKER
    if tool_slugs is not None:
        # `task_cv_33` (F-03): la guía de ejecución sigue a las tools EFECTIVAS
        # del run, no al texto horneado al sembrar (que las copias heredan
        # congelado). Va después del recorte para que siempre llegue entera.
        from api_server.seeds.tool_usage_guidance import with_execution_guidance

        prompt = with_execution_guidance(prompt, tool_slugs, language)

    persona: dict[str, str] = {"prompt": prompt}
    role = getattr(agent, "role", None)
    if role:
        persona["role"] = str(getattr(role, "value", role))
    name = getattr(agent, "name", None)
    if name:
        persona["name"] = str(name)
    return persona


def effective_prompt_text(agent: Any) -> str:
    """El texto que el runtime prepende de verdad, o ``""`` si no hay persona.

    Es :func:`resolve_agent_persona` sin el envoltorio: ya resuelto (es → en →
    plano), ya recortado y ya capado a :data:`PERSONA_MAX_CHARS`. Sellar el
    ``system_prompt`` crudo en su lugar sería sellar algo que el modelo puede no
    haber visto — dos personas que sólo difieren pasados los 8000 caracteres
    llegan idénticas al modelo, y dos agentes con el mismo campo plano pero
    distinto ``system_prompts.es`` llegan distintas.
    """
    persona = resolve_agent_persona(agent)
    return persona["prompt"] if persona is not None else ""


def prompt_text_hash(text: str) -> str:
    """sha256 hex del texto de un prompt. **Contrato compartido con el runtime.**

    El agent-runtime vive en otra imagen y no puede importar este módulo, así que
    tiene su propia copia de estas cuatro líneas
    (``agent_runtime.prompt_version.agent_prompt_seal``). Las dos tienen que
    producir el MISMO dígito para el mismo texto: el sello que el dispatch manda
    en el spec y el que el runtime calcula cuando no lo recibe son el mismo
    número, o dos runs del mismo prompt acabarían con etiquetas distintas según
    por qué rama entraron. Lo fija
    ``tests/unit/test_agent_prompt_seal_contract.py``.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def effective_prompt_hash(agent: Any) -> str:
    """Sello del texto efectivo de ``agent`` (`task_gov_02` / `task_gov_03`)."""
    return prompt_text_hash(effective_prompt_text(agent))
