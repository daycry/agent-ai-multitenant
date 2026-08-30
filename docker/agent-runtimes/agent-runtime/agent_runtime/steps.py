"""The steps_log records (task_02_11 / task_02_12).

Every node run, every model call and every tool call appends one step
dict to `AgentState["steps"]`. The list is persisted verbatim as the
`executions.steps_log` JSONB column and drives the execution Timeline
UI (Fase E). Steps are plain dicts on purpose — JSONB-ready, no
serialisation layer.

Step kinds:
  node          a graph node ran (perceive, observe, reflect, …).
  model_call    an LLM call — carries token counts and cost.
  tool_call     a builtin tool ran — carries args and result.
  memory_read   a memory recall — placeholder until Plan 04.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any


class StepKind(enum.StrEnum):
    NODE = "node"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    MEMORY_READ = "memory_read"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _base(index: int, kind: StepKind, node: str, summary: str, status: str) -> dict[str, Any]:
    now = _now()
    return {
        "index": index,
        "kind": str(kind),
        "node": node,
        "status": status,
        "summary": summary,
        "started_at": now,
        "ended_at": now,
    }


def node_step(index: int, node: str, summary: str, *, status: str = "ok") -> dict[str, Any]:
    """A plain graph-node step."""
    return _base(index, StepKind.NODE, node, summary, status)


def check_declaration_step(
    index: int,
    node: str,
    *,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Con qué dijo el agente que se verifica cada criterio (ADR 0162, opción A).

    **Por qué vive en el ``steps_log`` y no en otro sitio.** Es el único canal por
    el que esta información sale hoy del contenedor efímero y se persiste: los
    pasos se guardan verbatim en ``executions.steps_log``, que es la misma
    columna de la que salieron las 180 ejecuciones que midió el ADR 0162. Una
    declaración que no llegue a ninguna parte no es una declaración: es el
    «estado que nadie produce ni consume» que el propio ADR denuncia.

    Lo que lleva —``criteria_total``, ``checks_without_declared_check_type`` y
    las declaraciones— es una MÉTRICA. El ADR descarta expresamente bloquear por
    porcentaje: se aprende a jugar enseguida y castiga a los proyectos que
    legítimamente tienen poco que automatizar. La diferencia con el estado
    anterior no es que se impida algo — es que antes ni siquiera se podía contar.

    ``status`` es siempre ``ok``: este paso REGISTRA, no juzga. Marcarlo de otro
    modo cuando hay criterios sin declarar sería empezar a pintar de rojo un run
    que terminó bien, que es la opción C por la puerta de atrás.
    """
    total = int(coverage.get("criteria_total") or 0)
    undeclared = int(coverage.get("checks_without_declared_check_type") or 0)
    step = _base(
        index,
        StepKind.NODE,
        node,
        f"Verificación declarada para {total - undeclared}/{total} criterios"
        + (f" — {undeclared} sin declarar" if undeclared else ""),
        "ok",
    )
    step.update(coverage)
    return step


def model_call_step(
    index: int,
    node: str,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    summary: str,
    status: str = "ok",
    provider: str | None = None,
    cache_read_tokens: int = 0,
) -> dict[str, Any]:
    """An LLM call step — token counts and cost are captured here.

    ``provider`` (AUD16-15) es el KIND del proveedor del run (claude_sdk/
    ollama/azure_foundry/copilot): sin él, el price-snapshot del api-server
    buscaba en el catálogo con provider="" y el coste facturable quedó NULL
    en el 100% de las executions.
    """
    step = _base(index, StepKind.MODEL_CALL, node, summary, status)
    step.update(
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        total_tokens=tokens_in + tokens_out,
        cost_usd=round(cost_usd, 6),
    )
    if provider:
        step["provider"] = provider
    # `task_wf_63`: cuántos tokens del prompt sirvió la CACHÉ del proveedor.
    # Viaja por iteración —no agregado— porque la pregunta que interesa es si
    # el prefijo se reutiliza a medida que crece el contexto, y eso solo se ve
    # turno a turno. `0` cuando el proveedor no lo reporta (Ollama local).
    if cache_read_tokens:
        step["cache_read_tokens"] = int(cache_read_tokens)
    return step


def tool_call_step(
    index: int,
    node: str,
    *,
    tool: str,
    args: dict[str, Any],
    result: dict[str, Any],
    summary: str,
    status: str = "ok",
) -> dict[str, Any]:
    """A builtin-tool invocation step."""
    step = _base(index, StepKind.TOOL_CALL, node, summary, status)
    step.update(tool=tool, args=args, result=result)
    return step


def memory_read_step(
    index: int,
    node: str,
    *,
    query: str,
    hits: int,
    summary: str,
    status: str = "ok",
    placeholder: bool = False,
) -> dict[str, Any]:
    """A memory recall step.

    ``placeholder=True`` marca honestamente un recall SIN cablear (bare run sin
    API interno) — desde 2026-07-03 el boot cablea el recall real contra
    ``/internal/agent/memory-recall`` y el default pasa a ``False``."""
    step = _base(index, StepKind.MEMORY_READ, node, summary, status)
    step.update(query=query, hits=hits)
    if placeholder:
        step["placeholder"] = True
    return step
