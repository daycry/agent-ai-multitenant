"""Latido de shadow evals — `workers.run_shadow_evals` (`task_wf_52b`).

`record_shadow_eval` existía desde el Plan 14 (task_14_09) con su muestreador
determinista, su tabla y su dashboard… **y ningún llamante**. El mecanismo
estaba entero y nunca se disparaba: la señal de calidad continua no existía
porque nadie muestreaba.

Esto es ese llamante. Toma tareas reales ya completadas, decide cuáles entran
en la muestra y lanza para cada una una corrida en la sombra contra el dataset
`shadow` del tenant. **Nunca toca la tarea real** (decisión vinculante del
Plan 14): la señal se escribe en sus propias tablas, así que una corrida en la
sombra no puede bloquear ni alterar una ejecución de verdad.

El grifo, por defecto, está cerrado
-----------------------------------
Cada eval en la sombra cuesta llamadas de juez, así que encenderlo tiene que
ser un acto deliberado y visible. Hacen falta TRES condiciones, y cada una es
algo que un operador hace a propósito:

  1. tasa de muestreo > 0 (`EVAL_SHADOW_SAMPLE_RATE`, 5 % documentado);
  2. un modelo juez nombrado (`EVAL_JUDGE_MODEL`) — sin saber quién juzga no se
     puede juzgar, y además tiene que ser distinto del sujeto;
  3. que el tenant tenga un dataset de tipo `shadow` CON items.

Ninguna se cumple sola en una instalación nueva, así que instalar esto no
enciende gasto. Y las tres son inspeccionables: no hay un flag oculto.

Best-effort de principio a fin: un tenant que falle no impide los demás y nada
de esto puede tumbar el beat.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")

# Variable que nombra al modelo juez. Sin ella no hay shadow evals: un juez
# anónimo no se puede validar contra el sujeto (la regla anti-auto-aprobado
# compara los dos nombres).
JUDGE_MODEL_ENV_VAR = "EVAL_JUDGE_MODEL"

# Techo de corridas por latido. Cada una son N llamadas de juez; sin tope, un
# pico de tareas completadas se convertiría en un pico de factura.
MAX_SHADOW_EVALS_PER_BEAT = 5

# Solo tareas cerradas hace poco: la señal sirve para detectar deriva RECIENTE.
# Además evita que, al encender la feature, el primer latido intente muestrear
# el histórico entero de golpe.
CANDIDATE_WINDOW_HOURS = 24


def _subject_model_of(steps: list[Any] | None) -> str | None:
    """El modelo que de verdad corrió la tarea, sacado de su último `model_call`.

    No hay columna `model` en `executions`; el dato vive en los steps. Se toma
    el ÚLTIMO porque es el que produjo el entregable que se está juzgando.
    """
    for step in reversed(list(steps or [])):
        if isinstance(step, dict) and step.get("kind") == "model_call":
            model = str(step.get("model") or "").strip()
            if model:
                return model
    return None


@app.task(name="workers.run_shadow_evals")  # type: ignore[untyped-decorator]
def run_shadow_evals() -> dict[str, Any]:
    """Muestrea tareas completadas y lanza sus evals en la sombra.

    Idempotente: una tarea ya muestreada nunca se vuelve a muestrear (hay una
    fila `EvalShadowRecord` que la enlaza). Best-effort: nunca rompe el beat.
    """
    settings = get_settings()
    return asyncio.run(_run_shadow_evals_async(settings))


async def _resolve_seams(session: Any, judge_model: str, subject_model: str) -> tuple[Any, Any]:
    """Juez y sujeto sobre la MISMA capa de proveedores que el resto del sistema."""
    from api_server.chat.responder import _resolve_chat_provider, resolve_chat_model_config
    from api_server.evals.llm_judge import LLMJudgeModel, LLMSubjectModel
    from api_server.routers.llm_providers import get_provider_vault_store

    effective = await resolve_chat_model_config(session, project=None)
    provider, _kind, api_model = await _resolve_chat_provider(
        session, effective, get_provider_vault_store()
    )
    if provider is None:
        raise RuntimeError("no hay proveedor LLM activo para actuar de juez")
    return (
        LLMJudgeModel(provider=provider, model=judge_model),
        LLMSubjectModel(provider=provider, model=subject_model or api_model),
    )


async def _shadow_datasets(session: Any) -> dict[UUID, UUID]:
    """`tenant_id -> dataset_id` del dataset `shadow` de cada tenant, si tiene items.

    Un dataset sin items daría una corrida con veredicto `error` y cero señal;
    exigir items aquí evita generar ruido en la tabla de sombras.
    """
    from api_server.db.evals import EvalDataset, EvalDatasetItem, EvalDatasetKind
    from sqlalchemy import func, select

    rows = (
        await session.execute(
            select(EvalDataset.tenant_id, EvalDataset.id)
            .join(EvalDatasetItem, EvalDatasetItem.dataset_id == EvalDataset.id)
            .where(
                EvalDataset.kind == EvalDatasetKind.SHADOW.value,
                EvalDataset.deleted_at.is_(None),
            )
            .group_by(EvalDataset.tenant_id, EvalDataset.id)
            .having(func.count(EvalDatasetItem.id) > 0)
        )
    ).all()
    # Un tenant con varios datasets `shadow` se queda con el primero de forma
    # estable (orden por id): la alternativa —muestrear contra todos— multiplica
    # el coste sin que nadie lo haya pedido.
    out: dict[UUID, UUID] = {}
    for tenant_id, dataset_id in sorted(rows, key=lambda r: str(r[1])):
        out.setdefault(tenant_id, dataset_id)
    return out


async def _candidates(session: Any, tenant_id: UUID) -> list[tuple[UUID, UUID, Any, str | None]]:
    """Tareas `done` recientes del tenant que aún no se han muestreado."""
    from datetime import UTC, datetime, timedelta

    from api_server.db.domain import Execution, Task, TaskStatus
    from api_server.db.evals import EvalShadowRecord
    from sqlalchemy import select

    since = datetime.now(UTC) - timedelta(hours=CANDIDATE_WINDOW_HOURS)
    already = select(EvalShadowRecord.source_task_id).where(
        EvalShadowRecord.tenant_id == tenant_id,
        EvalShadowRecord.source_task_id.is_not(None),
    )
    stmt = (
        select(Task.id, Execution.id, Execution.steps_log, Execution.prompt_version)
        .join(Execution, Execution.task_id == Task.id)
        .where(
            Task.tenant_id == tenant_id,
            Task.status == TaskStatus.DONE.value,
            Execution.status == "done",
            Execution.created_at >= since,
            Task.id.not_in(already),
        )
        .order_by(Execution.created_at.desc())
        # Se leen más de las que se van a correr: el muestreador descarta la
        # mayoría (5 %), así que con un tope igual al de corridas casi nunca
        # saldría ninguna.
        .limit(MAX_SHADOW_EVALS_PER_BEAT * 100)
    )
    return list((await session.execute(stmt)).all())


async def _run_shadow_evals_async(settings: Settings) -> dict[str, Any]:
    """Núcleo async — dueño del ciclo de vida del engine."""
    from decimal import InvalidOperation

    from api_server.evals.shadow import (
        DeterministicSampler,
        record_shadow_eval,
        resolve_sample_rate,
    )

    judge_model = (os.environ.get(JUDGE_MODEL_ENV_VAR) or "").strip()
    if not judge_model:
        return {"status": "off", "reason": "no_judge_model", "sampled": 0}
    try:
        rate = resolve_sample_rate()
    except (ValueError, InvalidOperation) as exc:
        _log.warning("shadow_evals.bad_rate", error=str(exc))
        return {"status": "off", "reason": "bad_rate", "sampled": 0}
    if rate <= 0:
        return {"status": "off", "reason": "rate_zero", "sampled": 0}

    sampler = DeterministicSampler()
    engine = create_async_engine(settings.database_url)
    sampled = 0
    skipped_same_model = 0
    errors = 0
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            datasets = await _shadow_datasets(session)
            for tenant_id, dataset_id in datasets.items():
                if sampled >= MAX_SHADOW_EVALS_PER_BEAT:
                    break
                for task_id, execution_id, steps, prompt_version in await _candidates(
                    session, tenant_id
                ):
                    if sampled >= MAX_SHADOW_EVALS_PER_BEAT:
                        break
                    if not sampler.should_sample(str(task_id), rate):
                        continue
                    subject_model = _subject_model_of(steps)
                    if subject_model and subject_model == judge_model:
                        # Un modelo juzgándose a sí mismo se aprueba. Se salta
                        # la tarea en vez de registrar una señal inflada.
                        skipped_same_model += 1
                        continue
                    try:
                        seam_judge, seam_subject = await _resolve_seams(
                            session, judge_model, subject_model or ""
                        )
                        await record_shadow_eval(
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            source_task_id=task_id,
                            source_execution_id=execution_id,
                            judge=seam_judge,
                            subject_model=seam_subject.model,
                            sample_rate=rate,
                            subject=seam_subject,
                            subject_prompt_version=prompt_version,
                        )
                        await session.commit()
                        sampled += 1
                    except Exception as exc:  # - best-effort por tarea
                        await session.rollback()
                        errors += 1
                        _log.warning(
                            "shadow_evals.task_failed",
                            tenant_id=str(tenant_id),
                            task_id=str(task_id),
                            error=str(exc),
                        )
    finally:
        await engine.dispose()

    return {
        "status": "ok",
        "sampled": sampled,
        "skipped_same_model": skipped_same_model,
        "errors": errors,
        "rate": str(rate),
    }


__all__ = [
    "CANDIDATE_WINDOW_HOURS",
    "JUDGE_MODEL_ENV_VAR",
    "MAX_SHADOW_EVALS_PER_BEAT",
    "run_shadow_evals",
]
