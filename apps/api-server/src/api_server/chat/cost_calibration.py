"""Estimaciones de tokens calibradas con el histórico real (`task_wf_33`).

El sistema cierra planes, escribe retrospectivas y guarda tokens reales en
`executions` desde hace tiempo — y la estimación seguía usando un mapa estático
cuyos números, según su propio comentario, «NO son empíricos para este
proyecto». El bucle no se cerraba: se medía todo y no se aprendía nada.

Qué se calibra, y qué NO
------------------------
**Solo los tokens.** Las horas humanas se quedan con el mapa estático, y no es
pereza: son horas-PERSONA en EUR, y lo único que el histórico tiene es
wall-clock de MÁQUINA. Calibrar una con la otra repetiría exactamente la mezcla
de magnitudes que ya se rechazó a propósito en el coste del plan — daría un
número que parece medido y no lo es, que es peor que uno que se sabe estimado.

Mediana y no media: un run que se fue a 300k tokens por un bucle no puede
arrastrar la estimación de todas las tareas «m» del proyecto.

Tres niveles de fallback —proyecto → tenant → mapa estático— porque un proyecto
nuevo no tiene histórico propio pero el tenant sí suele tenerlo, y sus stacks se
parecen más entre sí que a la media de la plataforma.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.cost import DEFAULT_COMPLEXITY_ESTIMATES, ComplexityTokenEstimate

# Runs mínimos por nivel para fiarse de la mediana. Con dos muestras la mediana
# es el punto medio de dos números cualesquiera; presentar eso como
# «calibrado» sería vender una precisión que no existe.
MIN_SAMPLES_PER_LEVEL = 5

# Ventana del histórico. Más allá de esto el modelo, los prompts y el propio
# stack del proyecto han cambiado lo bastante como para que el dato mida otra
# cosa.
CALIBRATION_WINDOW_DAYS = 90


@dataclass(frozen=True)
class CalibrationResult:
    """El mapa a usar y de dónde salió cada nivel."""

    estimates: dict[str, ComplexityTokenEstimate]
    # `project` | `tenant` | `default`, por nivel de complejidad. Es lo que la
    # UI necesita para no presentar como medido lo que sigue siendo un
    # placeholder.
    sources: dict[str, str]

    @property
    def calibrated(self) -> bool:
        return any(source != "default" for source in self.sources.values())

    def as_context(self) -> dict[str, Any]:
        """La forma que viaja en `PlanningState.project_context`.

        `pm_plan_draft` corre en un hilo SIN sesión de BD, así que el mapa se
        calcula fuera y se le inyecta ya hecho — no puede ir a buscarlo él.
        """
        return {
            "calibrated": self.calibrated,
            "sources": dict(self.sources),
            "tokens": {
                level: {
                    "input": est.base_input_tokens,
                    "output": est.base_output_tokens,
                }
                for level, est in self.estimates.items()
            },
        }


def _estimate_from_samples(level: str, samples: list[tuple[int, int]]) -> ComplexityTokenEstimate:
    """La estimación de un nivel a partir de sus muestras `(input, output)`."""
    return ComplexityTokenEstimate(
        complexity=level,
        base_input_tokens=int(median(s[0] for s in samples)),
        base_output_tokens=int(median(s[1] for s in samples)),
    )


async def _samples_by_complexity(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID | None
) -> dict[str, list[tuple[int, int]]]:
    """Tokens reales de runs COMPLETADOS, agrupados por complejidad de su tarea.

    Solo runs `done`: un run abortado a la mitad mide cuánto se gastó antes de
    romperse, no cuánto cuesta hacer la tarea — calibrar con eso sesgaría la
    estimación hacia abajo justamente en las tareas que más fallan.
    """
    from datetime import UTC, datetime, timedelta

    from api_server.db.domain import Execution, Task

    since = datetime.now(UTC) - timedelta(days=CALIBRATION_WINDOW_DAYS)
    stmt = (
        select(Task.estimated_complexity, Execution.total_tokens, Execution.steps_log)
        .join(Task, Task.id == Execution.task_id)
        .where(
            Execution.tenant_id == tenant_id,
            Execution.status == "done",
            Execution.created_at >= since,
            Execution.total_tokens > 0,
        )
    )
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)

    out: dict[str, list[tuple[int, int]]] = {}
    for complexity, total_tokens, steps in (await session.execute(stmt)).all():
        level = str(complexity or "").lower()
        if level not in DEFAULT_COMPLEXITY_ESTIMATES:
            continue
        # El desglose entrada/salida vive en los steps; el total en la fila. Si
        # los steps no lo traen se reparte con la proporción del mapa estático
        # en vez de descartar la muestra: el TOTAL sí es un dato real.
        tokens_in = sum(int(s.get("tokens_in") or 0) for s in (steps or []) if isinstance(s, dict))
        tokens_out = sum(
            int(s.get("tokens_out") or 0) for s in (steps or []) if isinstance(s, dict)
        )
        if tokens_in <= 0 and tokens_out <= 0:
            fallback = DEFAULT_COMPLEXITY_ESTIMATES[level]
            ratio_total = fallback.base_input_tokens + fallback.base_output_tokens
            if ratio_total <= 0:
                continue
            total = int(total_tokens or 0)
            tokens_in = int(total * fallback.base_input_tokens / ratio_total)
            tokens_out = total - tokens_in
        out.setdefault(level, []).append((tokens_in, tokens_out))
    return out


async def resolve_calibrated_estimates(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID | None
) -> CalibrationResult:
    """El mapa de estimaciones a usar para este proyecto.

    Nivel a nivel, no en bloque: un proyecto puede tener histórico de sobra en
    tareas «m» y ninguna «xl». Mezclar la mediana real de unas con el
    placeholder de otras es correcto —cada nivel usa el mejor dato que hay— y
    `sources` lo dice para que la UI no presente las dos igual.
    """
    project_samples = (
        await _samples_by_complexity(session, tenant_id=tenant_id, project_id=project_id)
        if project_id is not None
        else {}
    )
    tenant_samples = await _samples_by_complexity(session, tenant_id=tenant_id, project_id=None)

    estimates: dict[str, ComplexityTokenEstimate] = {}
    sources: dict[str, str] = {}
    for level, fallback in DEFAULT_COMPLEXITY_ESTIMATES.items():
        for source, samples in (("project", project_samples), ("tenant", tenant_samples)):
            level_samples = samples.get(level) or []
            if len(level_samples) >= MIN_SAMPLES_PER_LEVEL:
                estimates[level] = _estimate_from_samples(level, level_samples)
                sources[level] = source
                break
        else:
            estimates[level] = fallback
            sources[level] = "default"
    return CalibrationResult(estimates=estimates, sources=sources)
