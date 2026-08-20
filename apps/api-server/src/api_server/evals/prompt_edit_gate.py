"""La eval que gatea la edición de un `system_prompt` (`task_gov_05`).

`task_gov_04` cerró la mitad de CI: el workflow que vigila **dos ficheros del
repo**. Esta es la otra mitad, y es la que de verdad usa un tenant —
``PUT /agents/{id}``, la pantalla de Agentes—, donde el prompt se cambia sin
pasar por ningún fichero versionado.

El contrato, decidido por el operador el 2026-08-12 (§Fase 2 del plan
`gov-01`):

  * al cambiar el prompt se lanza la eval contra el golden set del agente;
  * bajo preset ``production`` / ``customer-external`` un resultado peor que el
    umbral **rechaza la escritura**;
  * bajo ``development`` / ``sandbox`` se guarda y se avisa.

Cuatro decisiones que no son cosméticas
=======================================

**1. El mensaje dice QUÉ empeoró.** No «la eval falló»: los escenarios, por su
nombre. Un rechazo mudo no se puede accionar, y lo que no se puede accionar se
desactiva — y entonces la feature no existe. De ahí
:func:`scenario_label`, que saca del item dorado la etiqueta que un humano
reconoce, y :func:`rejection_message`, que la mete en el texto y no sólo en un
campo estructurado que nadie lee.

**2. Hay cuatro estados, no dos.** Tres son los de
:class:`~api_server.evals.ci_run.GateOutcome` —mismo vocabulario, a propósito:
las dos mitades del gate tienen que poder compararse en un informe— y el cuarto
es ``NOT_GATED``: *este agente no tiene golden set, así que no hay nada contra
lo que medir*. Es el análogo por configuración del ``--dry-run`` de CI: el
tenant no ha declarado que este agente se mida. Confundirlo con ``PASSED``
repetiría el defecto que `task_gov_04` acaba de quitar (un verde que no se ganó);
confundirlo con ``BLOCKED`` congelaría todos los prompts de todo tenant que aún
no haya sembrado un dataset, que es la forma más rápida de que alguien apague
el gate entero.

**3. La válvula de escape sólo abre lo INCONCLUSO.** Ver §Válvula abajo.

**4. La auditoría sobrevive al rechazo.** Cuando el gate rechaza, la transacción
del request se deshace: si la fila de auditoría viajara en ella, se iría con el
prompt. Por eso :func:`record_gate_audit` escribe en una sesión PROPIA
(``open_tenant_session``), que hace su commit aparte. Lo mismo vale para la
medición: el diff completo va dentro de ``audit_log.changes``, así que el
rechazo queda explicado aunque la corrida candidata no se persista.

Válvula de escape: quién, qué queda auditado, y por qué no es un agujero
=======================================================================

**El problema.** Un gate que bloquea cuando la infraestructura de evals no
responde deja al tenant-admin sin poder tocar un prompt por algo que no depende
de él. Eso es una llamada de soporte, y sobre todo es un **incentivo a apagar el
gate** — que es el único fallo del que no se vuelve.

**La válvula.** El `PUT` puede traer ``eval_gate_override: {reason: "…"}``. Sólo
tiene efecto sobre un resultado ``INCONCLUSIVE``.

* **Quién.** El mismo ``tenant_admin`` que ya autoriza el `PUT`. Y ésta es la
  parte que la hace defendible en vez de laxa: ese usuario **ya** puede abrir un
  agujero mucho mayor y permanente —cambiar el preset del proyecto a
  ``development``, donde el gate sólo avisa—. Negarle una válvula estrecha,
  puntual y auditada no le quita esa capacidad: le empuja a usarla. La válvula es
  ESTRICTAMENTE más pequeña que el bypass que ya tiene, y a diferencia de aquél
  deja una fila con su nombre.
* **Qué queda auditado.** Una fila en ``audit_log`` (append-only, particionada,
  con RLS por tenant) con ``action='prompt_eval_gate'``: quién, cuándo, qué
  agente, qué preset gobernaba, por qué el gate no pudo medir, el motivo escrito
  **verbatim**, y si la válvula se usó o no. Se audita también cuando venía y NO
  hizo falta, para que «adjuntar siempre el override» sea un patrón visible en la
  auditoría en vez de una costumbre invisible.
* **Por qué no es un agujero.** (a) NO abre un ``BLOCKED``: una regresión medida
  se rechaza con o sin override, y el mensaje lo dice. (b) Exige un motivo
  ESCRITO de al menos :data:`OVERRIDE_MIN_REASON_CHARS` caracteres — el mismo
  listón que `CLAUDE.md` le pone al ``gate_override`` del roadmap, y por la misma
  razón: sin él, el campo es la forma barata de saltarse el protocolo. (c) Es
  por-petición: no deja nada encendido detrás.

La otra mitad de la válvula es que **caduca sola**: en cuanto el tenant siembra
un baseline y hay proveedor, el resultado deja de ser ``INCONCLUSIVE`` y el
override no tiene sobre qué actuar.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.evals import EvalDataset, EvalDatasetItem, EvalRun, EvalRunStatus
from api_server.evals.ci_run import GateOutcome, gate_decision, resolve_threshold
from api_server.evals.diff import RunDiff
from api_server.seeds.builtin_approval_policies import (
    DEFAULT_APPROVAL_POLICY_PRESET,
    STRICT_PRESETS,
)

#: Longitud mínima del motivo escrito de la válvula. Es el listón que
#: `CLAUDE.md` §«La excepción al gate» le pone al ``gate_override`` del roadmap
#: (`test_gate_override_carries_a_written_justification`), y se copia aquí a
#: propósito: el problema es el mismo —una excepción sin justificación auditable
#: es la forma barata de saltarse la regla— así que el listón debe ser el mismo.
OVERRIDE_MIN_REASON_CHARS = 80

#: `action` de la fila de `audit_log`. Estable: es por lo que se busca.
AUDIT_ACTION = "prompt_eval_gate"
AUDIT_RESOURCE_TYPE = "agent"

#: Cuántos escenarios se nombran en el mensaje antes de resumir el resto. Un
#: mensaje con cuarenta títulos no se lee; uno con cero no se puede accionar.
MAX_NAMED_SCENARIOS = 5

#: Etiqueta de un escenario cuyo item dorado no da ningún nombre utilizable.
_UNNAMED_SCENARIO = "escenario sin título"

#: Claves del `input` del item dorado donde puede vivir su nombre, en orden de
#: preferencia. `title` es la que escribe `_build_input` al promocionar una task
#: real (`POST /tasks/{id}/promote-to-dataset`), que es el origen normal.
_LABEL_KEYS = ("title", "name", "prompt")

#: Corte del texto libre que se usa como etiqueta cuando no hay título.
_LABEL_MAX_CHARS = 80


class PromptGateOutcome(enum.StrEnum):
    """Qué puede decir el gate sobre una edición de prompt.

    Los tres primeros valores son **los mismos** que los de
    :class:`~api_server.evals.ci_run.GateOutcome` — mismo texto, misma
    semántica—, y ``tests/unit/test_prompt_edit_gate.py`` lo fija: las dos
    mitades del gate (CI y API) tienen que poder leerse en el mismo informe sin
    traducir. ``NOT_GATED`` no existe en CI porque allí el invocante nombra el
    dataset: si no lo hay, no se invoca.
    """

    PASSED = "passed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    #: El agente no tiene golden set: no hay nada contra lo que medir. NO es un
    #: aprobado (no se midió) ni un bloqueo (nadie pidió que se midiera).
    NOT_GATED = "not_gated"


class EvalUnavailableError(RuntimeError):
    """La eval no pudo medir: sin proveedor, sin baseline, o la corrida se cayó.

    Es la señal que convierte el resultado en ``INCONCLUSIVE`` — el único estado
    sobre el que la válvula de escape tiene efecto. Se distingue de un error de
    programación a propósito: un `TypeError` dentro del probe NO debe leerse como
    «la infraestructura está caída» y abrirle la puerta a la válvula.
    """


# =============================================================================
# El preset que gobierna esta edición
# =============================================================================
@dataclass(frozen=True)
class GateScope:
    """El preset bajo el que se juzga esta edición, y de dónde salió.

    ``project_id`` es ``None`` cuando ningún proyecto reclama al agente y el
    preset sale del default de plataforma; ``source`` lo explica en una línea
    para que el mensaje de rechazo no obligue a adivinar.
    """

    preset: str
    project_id: UUID | None
    source: str

    @property
    def blocking(self) -> bool:
        """¿Este preset RECHAZA la escritura, o sólo avisa?"""
        return self.preset in STRICT_PRESETS


async def resolve_gate_scope(session: AsyncSession, agent: Any) -> GateScope:
    """El preset que gobierna la edición del prompt de ``agent``.

    Dos casos, y el segundo es el que se hace mal si no se piensa:

    * **agente `project_local`** — manda el preset declarado por SU proyecto; si
      el proyecto no declara ninguno, el default de plataforma
      (``default_approval_policy_preset``), exactamente igual que resuelve el
      gate de aprobaciones en ``workers.execution``.
    * **plantilla de tenant** (``global_tenant_template`` / ``global_builtin``) —
      no tiene proyecto, pero **se ejecuta en los proyectos de sus equipos**. Si
      alguno de ellos es estricto, la edición se juzga como estricta. Tomar el
      camino cómodo («sin proyecto ⇒ sólo aviso») abriría la puerta trasera
      obvia: editar la plantilla en vez del agente del proyecto de producción.

    Entre varios proyectos gana el estricto, y entre varios estrictos el de
    ``id`` menor — un desempate arbitrario pero DETERMINISTA, para que el mensaje
    de rechazo no cambie entre dos peticiones idénticas.
    """
    from api_server.db.domain import Project
    from api_server.db.domain.teams import Team, TeamMember

    project_id = getattr(agent, "project_id", None)
    if project_id is not None:
        rows = (
            await session.execute(
                select(Project.id, Project.human_approval_policy).where(Project.id == project_id)
            )
        ).all()
    else:
        rows = (
            await session.execute(
                select(Project.id, Project.human_approval_policy)
                .join(Team, Team.id == Project.team_id)
                .join(TeamMember, TeamMember.team_id == Team.id)
                .where(
                    TeamMember.agent_id == agent.id,
                    Project.deleted_at.is_(None),
                    Team.deleted_at.is_(None),
                )
                .order_by(Project.id)
            )
        ).all()

    default_preset = await _platform_default_preset(session)
    if not rows:
        return GateScope(
            preset=default_preset,
            project_id=None,
            source="default de plataforma (el agente no pertenece a ningún proyecto)",
        )

    resolved = [(pid, declared_preset(policy) or default_preset) for pid, policy in rows]
    for pid, preset in sorted(resolved, key=lambda pair: str(pair[0])):
        if preset in STRICT_PRESETS:
            return GateScope(preset=preset, project_id=pid, source=f"preset del proyecto {pid}")
    pid, preset = resolved[0]
    return GateScope(preset=preset, project_id=pid, source=f"preset del proyecto {pid}")


def declared_preset(policy: Any) -> str | None:
    """El ``preset`` que una política DECLARA, o ``None``.

    Delegado en ``api_server.cli.approval_policy_audit.classify_preset`` para que
    haya UNA lectura de ese campo: allí está escrito por qué no se adivina el
    preset a partir del mapa de categorías (un calco del mapa sembrado parece
    evidencia y clasificarlo AFLOJARÍA el fail-closed de una política sin
    ``preset``).
    """
    from api_server.cli.approval_policy_audit import classify_preset

    return classify_preset(policy)


async def _platform_default_preset(session: AsyncSession) -> str:
    from api_server.db.platform_settings import get_platform_setting

    value = await get_platform_setting(
        session, "default_approval_policy_preset", default=DEFAULT_APPROVAL_POLICY_PRESET
    )
    return str(value)


# =============================================================================
# El seam de medición
# =============================================================================
@dataclass(frozen=True)
class PromptEvalRequest:
    """Todo lo que hace falta para medir un prompt candidato contra el baseline."""

    tenant_id: UUID
    agent_id: UUID
    agent_name: str
    dataset_id: UUID
    baseline_run_id: UUID
    #: El texto EFECTIVO del prompt nuevo — el que el modelo vería de verdad
    #: (`agent_persona.effective_prompt_text`), no el campo plano. Medir el crudo
    #: mediría algo que el sujeto puede no llegar a ver.
    candidate_prompt: str
    #: El modelo del SUJETO (el del agente) y el del JUEZ. El juez es **el mismo
    #: que usó la corrida base**, y no es un detalle: cambiarlo mediría al juez en
    #: vez de al prompt, que es como se obtiene una «regresión» que no existe.
    subject_model: str
    judge_model: str
    regression_threshold: Decimal


@runtime_checkable
class PromptEvalProbe(Protocol):
    """Produce el diff baseline↔candidato de una edición de prompt.

    Vive detrás de un ``Protocol`` por lo mismo que ``DiffProvider`` en
    :mod:`api_server.evals.ci_run`: la implementación real necesita proveedor LLM
    y sesión tenant-bound, y los tests tienen que poder recorrer el camino
    decisión→código de respuesta sin un LLM de verdad.

    Contrato de errores: **si no puede medir, levanta
    :class:`EvalUnavailableError`**. Devolver un diff vacío sería un aprobado que
    no se ganó, que es el defecto que `task_gov_04` acaba de retirar del gate de
    CI.
    """

    async def measure(self, request: PromptEvalRequest) -> RunDiff: ...


# =============================================================================
# Etiquetas de escenario y mensajes
# =============================================================================
def scenario_label(item_input: Any, item_id: UUID | None) -> str:
    """El nombre por el que un humano reconoce un item dorado.

    Se busca en las claves que escribe la promoción de una task real
    (``title`` → ``name`` → ``prompt``) y, si ninguna dice nada, se cae al id
    corto. Nunca se devuelve cadena vacía: un mensaje que dice «empeoraron: , , »
    es peor que uno que dice «empeoraron: 3 escenarios sin título», porque el
    segundo se puede investigar.
    """
    if isinstance(item_input, dict):
        for key in _LABEL_KEYS:
            value = item_input.get(key)
            if isinstance(value, str) and value.strip():
                text = " ".join(value.split())
                return text if len(text) <= _LABEL_MAX_CHARS else text[:_LABEL_MAX_CHARS] + "…"
    if item_id is not None:
        return f"{_UNNAMED_SCENARIO} ({str(item_id)[:8]})"
    return _UNNAMED_SCENARIO


async def regressed_scenarios(
    session: AsyncSession, diff: RunDiff, *, dataset_id: UUID
) -> tuple[str, ...]:
    """Los nombres de los escenarios que pasaban y ahora fallan.

    El orden es el del diff (que ya es determinista: el del run candidato), no el
    de la base — dos rechazos del mismo cambio deben producir el MISMO texto.
    """
    item_ids = [c.item_id for c in diff.regressions if c.item_id is not None]
    if not item_ids:
        return ()
    rows = (
        await session.execute(
            select(EvalDatasetItem.id, EvalDatasetItem.input).where(
                EvalDatasetItem.id.in_(item_ids),
                EvalDatasetItem.dataset_id == dataset_id,
            )
        )
    ).all()
    inputs: dict[UUID, Any] = {}
    for row_id, row_input in rows:
        inputs[row_id] = row_input
    return tuple(
        scenario_label(inputs.get(item_id), item_id)
        for item_id in item_ids
        # Un item que la consulta no devuelve es de otro dataset (o se borró):
        # nombrarlo mezclaría escenarios de otro golden set en el mensaje.
        if item_id in inputs
    )


def _enumerate(scenarios: Sequence[str]) -> str:
    if not scenarios:
        return "(ninguno con nombre)"
    shown = list(scenarios[:MAX_NAMED_SCENARIOS])
    text = "; ".join(f"«{s}»" for s in shown)
    rest = len(scenarios) - len(shown)
    return text if rest <= 0 else f"{text} y {rest} más"


def rejection_message(scenarios: Sequence[str], *, preset: str, drop: Decimal | None) -> str:
    """El texto del 409 por regresión. **Nombra los escenarios**, no dice «falló».

    Es el punto (1) del enunciado de `task_gov_05` y no es cosmético: un rechazo
    que no dice qué se rompió no se puede accionar, y lo que no se puede accionar
    se desactiva.
    """
    caida = f" (la tasa de acierto baja {drop})" if drop is not None else ""
    return (
        f"el prompt nuevo empeora la evaluación{caida} y el proyecto usa el preset "
        f"«{preset}», que no admite regresiones: empeoran "
        f"{_enumerate(scenarios)}. Corrige el prompt o, si el golden set ya no "
        f"describe lo que quieres, actualízalo antes de volver a guardar. La "
        f"válvula de escape NO aplica aquí: sólo cubre una eval que no puede medir."
    )


def advisory_message(scenarios: Sequence[str], *, preset: str, drop: Decimal | None) -> str:
    """El MISMO hallazgo bajo un preset que no bloquea. Se guarda y se avisa.

    Existe separado de :func:`rejection_message` porque reutilizar aquel texto
    en `development` diría dos cosas falsas —«no admite regresiones» y «la
    válvula no aplica»— sobre una escritura que acaba de guardarse. Un aviso que
    describe mal lo que ha pasado se aprende a ignorar, y a partir de ahí el
    preset estricto también se ignora.
    """
    caida = f" (la tasa de acierto baja {drop})" if drop is not None else ""
    return (
        f"CUIDADO: el prompt nuevo empeora la evaluación{caida} — empeoran "
        f"{_enumerate(scenarios)}. Se ha guardado igualmente porque el proyecto usa "
        f"el preset «{preset}», que sólo avisa; bajo «production» o "
        f"«customer-external» esta misma escritura se habría rechazado."
    )


def inconclusive_message(detail: str, *, preset: str) -> str:
    """El texto del 409 cuando la eval no pudo medir, con la salida escrita.

    Decir sólo «no se pudo evaluar» dejaría al tenant-admin sin saber que existe
    una salida — y un bloqueo sin salida visible es el incentivo a apagar el gate
    que el enunciado nombra.
    """
    return (
        f"la evaluación no pudo medir este cambio ({detail}) y el proyecto usa el "
        f"preset «{preset}», que no deja pasar lo que no se ha medido. Siembra el "
        f"golden set / lanza una corrida base (POST /eval-runs) y reintenta; si la "
        f"infraestructura de evals está caída y el cambio no puede esperar, repite "
        f"el PUT con «eval_gate_override.reason» explicando por qué — queda "
        f"auditado a tu nombre."
    )


# =============================================================================
# El veredicto
# =============================================================================
@dataclass(frozen=True)
class GateNotice:
    """Lo que el gate tiene que decir sobre esta edición.

    Se devuelve tanto cuando deja pasar como cuando avisa; el rechazo viaja como
    ``HTTPException`` y lleva esta misma información en su ``detail``.
    """

    outcome: PromptGateOutcome
    preset: str
    blocking: bool
    message: str
    scenarios: tuple[str, ...] = ()
    dataset_id: UUID | None = None
    baseline_run_id: UUID | None = None
    #: ``True`` cuando la válvula de escape fue lo que dejó pasar la escritura.
    overridden: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "preset": self.preset,
            "blocking": self.blocking,
            "message": self.message,
            "scenarios": list(self.scenarios),
            "dataset_id": str(self.dataset_id) if self.dataset_id else None,
            "baseline_run_id": str(self.baseline_run_id) if self.baseline_run_id else None,
            "overridden": self.overridden,
        }


async def golden_dataset_for_agent(session: AsyncSession, agent_id: UUID) -> EvalDataset | None:
    """El golden set que apunta a este agente, o ``None``.

    ``None`` significa **NO_GATED**, no «aprobado»: el tenant no ha declarado que
    este agente se mida. Se elige el más reciente cuando hay varios, para que
    sembrar un dataset nuevo sustituya al viejo sin tener que borrarlo.
    """
    return (
        await session.execute(
            select(EvalDataset)
            .where(
                EvalDataset.target_agent_id == agent_id,
                EvalDataset.deleted_at.is_(None),
            )
            .order_by(EvalDataset.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def baseline_run_for(session: AsyncSession, dataset_id: UUID) -> EvalRun | None:
    """La corrida COMPLETADA más reciente del dataset — el «antes» del diff.

    Sin ella no hay contra qué comparar, que es justo el aviso nº2 del plan: sin
    la fase 1 (versionado) una eval que bloquea rechaza el cambio y no puede decir
    contra qué comparaba. Una corrida a medias (`running`, `failed`) no sirve de
    baseline: sus métricas están sin cerrar.
    """
    return (
        await session.execute(
            select(EvalRun)
            .where(
                EvalRun.dataset_id == dataset_id,
                EvalRun.status == EvalRunStatus.COMPLETED.value,
            )
            .order_by(EvalRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _subject_model(agent: Any) -> str:
    model_config = getattr(agent, "model_config", None)
    model = model_config.get("model") if isinstance(model_config, dict) else None
    return str(model) if isinstance(model, str) and model.strip() else ""


def _missing_models(agent: Any, baseline: EvalRun) -> str | None:
    """Por qué NO se puede medir con estos modelos, o ``None`` si se puede.

    El juez tiene que ser **el mismo** que juzgó la corrida base. Con otro juez,
    el diff mide la diferencia entre dos jueces y la atribuye al prompt: se
    rechazarían cambios buenos y se dejarían pasar malos, las dos cosas sin que
    nadie pueda notarlo mirando el resultado.
    """
    subject = _subject_model(agent)
    judge = baseline.judge_model
    if not judge:
        return (
            "la corrida base no registró qué modelo juez usó, así que el "
            "candidato no se puede juzgar con el mismo y el diff no sería "
            "comparable"
        )
    if not subject:
        return "el agente no tiene «model» en su model_config, así que no hay sujeto que medir"
    if judge == subject:
        return (
            f"el juez de la corrida base («{judge}») es el mismo modelo que el del "
            "agente, y un modelo que se juzga a sí mismo se aprueba"
        )
    return None


async def evaluate_prompt_edit(
    session: AsyncSession,
    *,
    agent: Any,
    candidate_prompt: str,
    probe: PromptEvalProbe,
) -> GateNotice:
    """Mide la edición y devuelve el veredicto. **No decide qué hacer con él.**

    Separar medir de decidir es lo que permite que el mismo camino sirva para el
    preset que bloquea y para el que avisa: la diferencia entre los dos está en
    :func:`apply_gate_decision`, no aquí.
    """
    scope = await resolve_gate_scope(session, agent)
    dataset = await golden_dataset_for_agent(session, agent.id)
    if dataset is None:
        return GateNotice(
            outcome=PromptGateOutcome.NOT_GATED,
            preset=scope.preset,
            blocking=scope.blocking,
            message=(
                "este agente no tiene ningún golden set que lo apunte, así que no "
                "hay nada contra lo que medir el cambio. Crea un dataset con "
                "«target_agent_id» y promociona tareas aprobadas para que las "
                "próximas ediciones se evalúen."
            ),
        )

    baseline = await baseline_run_for(session, dataset.id)
    if baseline is None:
        return GateNotice(
            outcome=PromptGateOutcome.INCONCLUSIVE,
            preset=scope.preset,
            blocking=scope.blocking,
            message=inconclusive_message(
                f"el dataset «{dataset.name}» no tiene ninguna corrida completada "
                "que sirva de base",
                preset=scope.preset,
            ),
            dataset_id=dataset.id,
        )

    missing = _missing_models(agent, baseline)
    if missing is not None:
        return GateNotice(
            outcome=PromptGateOutcome.INCONCLUSIVE,
            preset=scope.preset,
            blocking=scope.blocking,
            message=inconclusive_message(missing, preset=scope.preset),
            dataset_id=dataset.id,
            baseline_run_id=baseline.id,
        )

    request = PromptEvalRequest(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        agent_name=agent.name,
        dataset_id=dataset.id,
        baseline_run_id=baseline.id,
        candidate_prompt=candidate_prompt,
        subject_model=_subject_model(agent),
        judge_model=str(baseline.judge_model),
        regression_threshold=resolve_threshold(None),
    )
    try:
        diff = await probe.measure(request)
    except EvalUnavailableError as exc:
        return GateNotice(
            outcome=PromptGateOutcome.INCONCLUSIVE,
            preset=scope.preset,
            blocking=scope.blocking,
            message=inconclusive_message(str(exc), preset=scope.preset),
            dataset_id=dataset.id,
            baseline_run_id=baseline.id,
        )

    # El MISMO `gate_decision` puro que usa la mitad de CI (`task_gov_04`): un
    # segundo criterio de «esto es una regresión» es como acaban divergiendo dos
    # gates que dicen medir lo mismo.
    decision = gate_decision(diff)
    if decision.outcome is not GateOutcome.BLOCKED:
        return GateNotice(
            outcome=PromptGateOutcome.PASSED,
            preset=scope.preset,
            blocking=scope.blocking,
            message=f"la evaluación no empeora: {decision.reason}",
            dataset_id=dataset.id,
            baseline_run_id=baseline.id,
        )

    scenarios = await regressed_scenarios(session, diff, dataset_id=dataset.id)
    delta = diff.pass_rate.delta
    drop = -delta if delta is not None else None
    # El hallazgo es el mismo; lo que cambia con el preset es lo que se hace con
    # él, y por tanto lo que hay que CONTAR. Reutilizar el texto del rechazo en
    # `development` afirmaría dos cosas falsas sobre una escritura que sí se
    # guardó, y un aviso que describe mal lo ocurrido se aprende a ignorar.
    render = rejection_message if scope.blocking else advisory_message
    return GateNotice(
        outcome=PromptGateOutcome.BLOCKED,
        preset=scope.preset,
        blocking=scope.blocking,
        message=render(scenarios, preset=scope.preset, drop=drop),
        scenarios=scenarios,
        dataset_id=dataset.id,
        baseline_run_id=baseline.id,
    )


__all__ = [
    "AUDIT_ACTION",
    "AUDIT_RESOURCE_TYPE",
    "MAX_NAMED_SCENARIOS",
    "OVERRIDE_MIN_REASON_CHARS",
    "EvalUnavailableError",
    "GateNotice",
    "GateScope",
    "PromptEvalProbe",
    "PromptEvalRequest",
    "PromptGateOutcome",
    "advisory_message",
    "baseline_run_for",
    "declared_preset",
    "evaluate_prompt_edit",
    "golden_dataset_for_agent",
    "inconclusive_message",
    "regressed_scenarios",
    "rejection_message",
    "resolve_gate_scope",
    "scenario_label",
]
