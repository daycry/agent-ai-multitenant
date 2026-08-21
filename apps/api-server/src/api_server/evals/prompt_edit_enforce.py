"""Qué se HACE con el veredicto del gate de prompts (`task_gov_05`).

:mod:`api_server.evals.prompt_edit_gate` mide y dictamina; aquí se traduce ese
dictamen a la única decisión que le importa al llamante —se guarda o no— y se
deja el rastro. Están separados a propósito: medir es puro y auditable, decidir
tiene efectos (un 409, una fila de auditoría, una sesión aparte).

Tres cosas que este módulo hace y conviene no deshacer:

**La auditoría va en su PROPIA sesión.** Cuando el gate rechaza, la transacción
del request se deshace entera: una fila de auditoría escrita en ella se iría con
el prompt que no llegó a guardarse, y el rechazo no dejaría rastro. Por eso
:func:`record_gate_audit` abre una ``open_tenant_session`` nueva, que hace su
propio commit. La sesión hereda el MISMO principal, así que la RLS del tenant
sigue puesta: esto no es un puente para escribir fuera del tenant.

**El fallo al auditar la válvula NO se traga.** En el camino del override, si la
fila de auditoría no se puede escribir, la escritura del prompt no debe pasar:
un override sin auditar es exactamente el agujero que la válvula existe para no
abrir, así que ese fallo sube y el `PUT` responde 500. En el camino del RECHAZO
el efecto es el mismo por otra vía: el fallo al auditar sustituye al 409 por un
500, y en los dos casos la escritura se queda fuera, que es lo que importa.

**La sonda viva juzga con el MISMO juez que la corrida base.** Ver
:class:`LiveEvalProbe`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import AuthPrincipal, open_tenant_session, require_tenant_admin
from api_server.db.evals import EvalResult, EvalRun
from api_server.evals.diff import RunDiff, diff_metrics
from api_server.evals.judge import JudgeResponseError, SameModelJudgeError, run_eval
from api_server.evals.metrics import compute_run_metrics
from api_server.evals.prompt_edit_gate import (
    AUDIT_ACTION,
    AUDIT_RESOURCE_TYPE,
    EvalUnavailableError,
    GateNotice,
    PromptEvalProbe,
    PromptEvalRequest,
    PromptGateOutcome,
    evaluate_prompt_edit,
)

#: Códigos de error estables del rechazo. Son contrato: un cliente los lee para
#: distinguir «arregla el prompt» de «arregla la infraestructura», que es la
#: MISMA distinción que `task_gov_04` hizo en CI entre el exit 1 y el exit 2.
ERROR_REGRESSION = "prompt_eval_regression"
ERROR_INCONCLUSIVE = "prompt_eval_inconclusive"


# =============================================================================
# Auditoría
# =============================================================================
async def record_gate_audit(
    principal: AuthPrincipal,
    *,
    agent_id: Any,
    notice: GateNotice,
    override_reason: str | None,
    override_used: bool,
    rejected: bool,
) -> None:
    """Deja la fila de `audit_log` de esta decisión, en una transacción propia.

    Se escribe cuando la decisión tiene consecuencias que alguien querrá auditar:
    un rechazo, un uso de la válvula, o **cualquier petición que traiga un
    override aunque no hiciera falta** — esto último para que «adjuntar siempre
    el override» sea un patrón visible en la auditoría en vez de una costumbre
    invisible. Un `PASSED` limpio no escribe nada: una fila por edición correcta
    sería ruido que entierra las que importan.
    """
    from api_server.db.models import AuditLog

    changes: dict[str, Any] = {
        **notice.to_json(),
        "rejected": rejected,
        "override_used": override_used,
    }
    if override_reason is not None:
        # VERBATIM. Resumirlo o recortarlo dejaría la auditoría diciendo lo que
        # el sistema entendió en vez de lo que la persona escribió.
        changes["override_reason"] = override_reason

    async with open_tenant_session(principal) as session:
        session.add(
            AuditLog(
                id=uuid7(),
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action=AUDIT_ACTION,
                resource_type=AUDIT_RESOURCE_TYPE,
                resource_id=agent_id,
                changes=changes,
            )
        )


def _http_rejection(notice: GateNotice) -> HTTPException:
    code = ERROR_REGRESSION if notice.outcome is PromptGateOutcome.BLOCKED else ERROR_INCONCLUSIVE
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": code,
            # El mensaje NOMBRA los escenarios (punto 1 del enunciado). Va también
            # suelto en `scenarios` para quien quiera pintarlos, pero el texto se
            # basta solo: un cliente que sólo enseñe `message` no pierde el qué.
            "message": notice.message,
            "scenarios": list(notice.scenarios),
            "preset": notice.preset,
            "outcome": notice.outcome.value,
            "dataset_id": str(notice.dataset_id) if notice.dataset_id else None,
            "baseline_run_id": str(notice.baseline_run_id) if notice.baseline_run_id else None,
        },
    )


# =============================================================================
# La decisión
# =============================================================================
async def enforce_prompt_edit_gate(
    session: AsyncSession,
    *,
    principal: AuthPrincipal,
    agent: Any,
    candidate_prompt: str,
    probe: PromptEvalProbe,
    override_reason: str | None = None,
) -> GateNotice:
    """Mide la edición y **rechaza la escritura** cuando el preset no la admite.

    Devuelve el aviso cuando deja pasar (para que el `PUT` lo devuelva y alguien
    lo LEA: un aviso que no llega a ninguna pantalla no avisa de nada) y levanta
    un ``409`` cuando no.

    La válvula (``override_reason``) sólo actúa sobre ``INCONCLUSIVE``. Sobre un
    ``BLOCKED`` no hace nada, y el mensaje del rechazo lo dice — si abriera una
    regresión medida, el gate sería opcional exactamente cuando funciona.
    """
    notice = await evaluate_prompt_edit(
        session, agent=agent, candidate_prompt=candidate_prompt, probe=probe
    )

    if not notice.blocking or notice.outcome in (
        PromptGateOutcome.PASSED,
        PromptGateOutcome.NOT_GATED,
    ):
        if override_reason is not None:
            await record_gate_audit(
                principal,
                agent_id=agent.id,
                notice=notice,
                override_reason=override_reason,
                override_used=False,
                rejected=False,
            )
        return notice

    if notice.outcome is PromptGateOutcome.BLOCKED:
        await record_gate_audit(
            principal,
            agent_id=agent.id,
            notice=notice,
            override_reason=override_reason,
            override_used=False,
            rejected=True,
        )
        raise _http_rejection(notice)

    # INCONCLUSIVE bajo preset estricto: aquí, y sólo aquí, abre la válvula.
    if override_reason is None:
        await record_gate_audit(
            principal,
            agent_id=agent.id,
            notice=notice,
            override_reason=None,
            override_used=False,
            rejected=True,
        )
        raise _http_rejection(notice)

    opened = replace(notice, overridden=True)
    # Sin `try`: si la auditoría no se puede escribir, la escritura NO pasa. Un
    # override sin rastro es el agujero que la válvula existe para no abrir.
    await record_gate_audit(
        principal,
        agent_id=agent.id,
        notice=opened,
        override_reason=override_reason,
        override_used=True,
        rejected=False,
    )
    return opened


# =============================================================================
# La sonda viva
# =============================================================================
@dataclass
class LiveEvalProbe:
    """Produce el diff de verdad: corre el golden set con el prompt CANDIDATO.

    Tres decisiones dentro:

    * **Sesión propia.** La corrida candidata se persiste en una
      ``open_tenant_session`` aparte, así que sobrevive al rechazo del `PUT`: el
      operador puede abrir esa corrida en el dashboard y ver por qué se le dijo
      que no. Si viajara en la transacción del request, el rechazo borraría la
      única evidencia de sí mismo.
    * **El juez es el de la corrida base.** Cambiarlo mediría al juez y lo
      atribuiría al prompt.
    * **El sujeto corre CON el prompt candidato.** Hasta esta tarea,
      ``LLMSubjectModel`` no mandaba ningún ``system``: dos corridas del mismo
      dataset con prompts distintos salían estadísticamente iguales, así que
      medir un cambio de prompt era imposible por construcción. Ver el docstring
      de :mod:`api_server.evals.llm_judge`.

    Todo fallo de infraestructura sale como :class:`EvalUnavailableError`, que es
    lo que el gate traduce a ``INCONCLUSIVE`` (y lo único que la válvula abre).
    """

    principal: AuthPrincipal

    async def measure(self, request: PromptEvalRequest) -> RunDiff:
        from api_server.evals.constants import MAX_SYNC_EVAL_CALLS

        async with open_tenant_session(self.principal) as session:
            planned = await _planned_calls(session, request.dataset_id)
            if planned == 0:
                raise EvalUnavailableError("el golden set no tiene items que juzgar")
            if planned > MAX_SYNC_EVAL_CALLS:
                raise EvalUnavailableError(
                    f"la corrida serían {planned} llamadas a modelo y el máximo dentro "
                    f"de una petición son {MAX_SYNC_EVAL_CALLS}; parte el dataset"
                )

            judge, subject = await _build_probe_seams(session, request)

            candidate = EvalRun(
                id=uuid7(),
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                # FK a `agents.id` mientras el request tiene esa fila con un
                # UPDATE pendiente: no hay bloqueo cruzado — un UPDATE que no toca
                # la clave toma `FOR NO KEY UPDATE`, compatible con el
                # `FOR KEY SHARE` que pide la comprobación de la FK.
                subject_agent_id=request.agent_id,
                subject_prompt_version=f"candidate:{request.agent_name}",
            )
            session.add(candidate)
            await session.flush()

            try:
                candidate_results = await run_eval(
                    session,
                    candidate,
                    judge=judge,
                    subject_model=request.subject_model,
                    subject=subject,
                )
            except (SameModelJudgeError, JudgeResponseError) as exc:
                raise EvalUnavailableError(str(exc)) from exc

            baseline_results = await _results_of(session, request.baseline_run_id)
            if not baseline_results:
                raise EvalUnavailableError(
                    "la corrida base no tiene resultados con los que comparar"
                )

            return diff_metrics(
                compute_run_metrics(baseline_results),
                compute_run_metrics(candidate_results),
                baseline_results,
                candidate_results,
                pass_rate_regression_threshold=request.regression_threshold,
            )


async def _planned_calls(session: AsyncSession, dataset_id: Any) -> int:
    """`items x (1 sujeto + N criterios)` — el mismo cálculo que `POST /eval-runs`."""
    from sqlalchemy import func

    from api_server.db.evals import EvalCriterion, EvalDatasetItem

    items = int(
        (
            await session.execute(
                select(func.count(EvalDatasetItem.id)).where(
                    EvalDatasetItem.dataset_id == dataset_id,
                    EvalDatasetItem.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )
    criteria = int(
        (
            await session.execute(
                select(func.count(EvalCriterion.id)).where(
                    EvalCriterion.dataset_id == dataset_id,
                    EvalCriterion.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )
    return items * (1 + max(criteria, 1))


async def _results_of(session: AsyncSession, run_id: Any) -> list[EvalResult]:
    return list(
        (await session.execute(select(EvalResult).where(EvalResult.run_id == run_id)))
        .scalars()
        .all()
    )


async def _build_probe_seams(session: AsyncSession, request: PromptEvalRequest) -> tuple[Any, Any]:
    """Juez + sujeto reales, con el prompt candidato puesto en el sujeto.

    Reutiliza la resolución de proveedor de ``POST /eval-runs`` (misma
    credencial de Vault, mismo catálogo del ADR 0021): un segundo camino de LLM
    que mantener es como acaban divergiendo las credenciales.
    """
    from fastapi import HTTPException as _HTTPException

    from api_server.routers.evals import _build_eval_seams

    try:
        judge, subject = await _build_eval_seams(
            session,
            request.judge_model,
            request.subject_model,
            subject_system_prompt=request.candidate_prompt,
        )
    except _HTTPException as exc:
        # `_build_eval_seams` levanta 503 cuando no hay proveedor activo. Dejarlo
        # salir haría que el `PUT` devolviera 503 en vez de pasar por el gate: la
        # decisión de si eso bloquea o avisa es del PRESET, no del proveedor.
        raise EvalUnavailableError(_detail_text(exc)) from exc
    return judge, subject


def _detail_text(exc: HTTPException) -> str:
    # `HTTPException.detail` está tipado `str` en Starlette pero en este repo se
    # levanta con un dict `{"error", "message"}` en media docena de sitios, así
    # que la anotación honesta aquí es `Any`: sin ella mypy declara muerto el
    # camino que de verdad se recorre.
    detail: Any = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


def get_prompt_eval_probe(
    principal: AuthPrincipal = Depends(require_tenant_admin),
) -> PromptEvalProbe:
    """La sonda que usa el `PUT`. Dependencia para que un test la sustituya.

    Mismo seam que ``DiffProvider`` en el gate de CI y por la misma razón: el
    camino decisión→código de respuesta tiene que poder recorrerse sin un LLM.
    """
    return LiveEvalProbe(principal=principal)


__all__ = [
    "ERROR_INCONCLUSIVE",
    "ERROR_REGRESSION",
    "LiveEvalProbe",
    "enforce_prompt_edit_gate",
    "get_prompt_eval_probe",
    "record_gate_audit",
]
