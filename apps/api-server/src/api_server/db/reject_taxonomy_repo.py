"""Agregado del rechazo por `target` x `class` (`task_gov_10`).

La casilla existe porque el veredicto de un rechazo era prosa y **no agregaba**.
Escribir el par acotado (`api_server.reviewer_bridge.apply_reviewer_verdict`) es
la mitad del trabajo; la otra es que alguien pueda LEERLO, porque en esta base el
patrón de fallo dominante es «mecanismo entregado, cero llamantes» — un dato que
nadie consulta no se distingue de un dato que no existe.

Esta es la lectura mínima que hace mecánicas las dos preguntas de la casilla:

  * «¿qué se rechaza más aquí?» -> `RejectBreakdown.targets`
  * «¿qué clase de defecto domina?» -> `RejectBreakdown.classes`

Y una tercera que no estaba pedida pero sin la cual las dos primeras engañan:
**cuántos rechazos no se pudieron clasificar** (`unlabelled`). Como no hay bucket
«otros» a donde tirar lo genérico (decisión de `task_gov_10`), ese número es la
cobertura real del dato: un `targets` limpísimo sobre 8 rechazos de 200 no dice
lo que parece decir. Un agregado que esconde su propia cobertura es la versión
numérica de la guarda que no puede fallar.

Alcance: sólo los rechazos del reviewer **IA**. El rechazo de un revisor humano
viaja como evento `peer_review_verdict` (`human_agents/review.py`), un `kind`
distinto que estas queries no miran — así que `unlabelled` no se infla con
rechazos a los que nadie ha pedido todavía que se clasifiquen. Si algún día la
bandeja humana ofrece los dos ejes, entra por ahí y se suma aquí.

Multi-tenancy: cada query filtra `tenant_id` explícitamente además de la RLS —
misma defensa en profundidad que `orchestrator.dispatch._read_prior_review_feedback`,
porque el orquestador corre con BYPASSRLS y este módulo puede llamarse desde ahí.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from shared_domain.reject_taxonomy import REJECT_CLASSES, REJECT_TARGETS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["RejectBreakdown", "reject_breakdown"]


@dataclass(frozen=True)
class RejectBreakdown:
    """El desglose de los rechazos de un tenant (o de un proyecto suyo).

    `targets` y `classes` van ordenados de más frecuente a menos, que es el orden
    en el que se lee la respuesta a «¿qué se rechaza más?».
    """

    #: Rechazos considerados (eventos `review_comment` que NO son aprobaciones).
    rejections: int = 0
    #: Rechazos con al menos una etiqueta en CUALQUIERA de los dos ejes.
    labelled: int = 0
    #: Rechazos sin ninguna etiqueta: los que el reviewer no clasificó, más los
    #: defensivos que sintetiza el worker cuando el veredicto no se pudo parsear.
    unlabelled: int = 0
    #: `label -> nº de rechazos que la llevan`, descendente.
    targets: dict[str, int] = field(default_factory=dict)
    classes: dict[str, int] = field(default_factory=dict)

    @property
    def top_target(self) -> str | None:
        """La respuesta literal a «¿qué se rechaza más?», o `None` sin datos."""
        return next(iter(self.targets), None)

    @property
    def top_class(self) -> str | None:
        """La respuesta literal a «¿qué clase de defecto domina?»."""
        return next(iter(self.classes), None)


# Los parámetros van casteados con `CAST(... AS ...)` y no a pelo: un `$n IS NULL`
# sin tipo es `AmbiguousParameterError` en Postgres aunque el mismo parámetro
# aparezca después en una comparación tipada (comprobado, no supuesto: el informe
# entero se caía con «could not determine data type of parameter $3»). Y `CAST`
# en vez de `:x::uuid` porque el regex de `text()` NO reconoce un bind seguido de
# `::` — con esa forma SQLAlchemy manda la query sin el parámetro y falla igual,
# sólo que más lejos del sitio donde se lee el porqué.
#
# Un rechazo es un `review_comment` que no es la constancia de una aprobación
# (`{"approved": true}`, que `apply_reviewer_verdict` escribe cuando el reviewer
# aprueba CON desglose por criterio — `task_wf_61`). Los eventos de escalada
# (`escalated`) SÍ cuentan como rechazo: la task no pasó, y al no llevar etiquetas
# engordan `unlabelled`, que es exactamente donde deben verse.
_IS_REJECTION = "(e.payload -> 'approved') IS DISTINCT FROM 'true'::jsonb"

_SCOPE = f"""
  FROM task_audit_events e
  JOIN tasks t ON t.id = e.task_id AND t.tenant_id = e.tenant_id
 WHERE e.tenant_id = CAST(:tenant_id AS uuid)
   AND e.kind = 'review_comment'
   AND (CAST(:project_id AS uuid) IS NULL OR t.project_id = CAST(:project_id AS uuid))
   AND (CAST(:since AS timestamptz) IS NULL OR e.at >= CAST(:since AS timestamptz))
   AND {_IS_REJECTION}
"""

# `jsonb_array_elements_text` sobre algo que no sea un array revienta la query,
# y el payload de un evento viejo (o de una escalada) no lleva la clave. El
# filtro `jsonb_typeof = 'array'` va en una subconsulta y no en el WHERE de
# arriba porque el LATERAL se evalúa junto al WHERE, no después: dejarlo fuera
# haría que la primera fila sin la clave tirase el informe entero.
#
# `label = ANY(:allowed)` es la mitad de LECTURA del cierre del value-set: lo
# que no esté en el enum no se cuenta. Sin ella, una fila escrita a mano (o por
# una versión anterior del escritor) ensancharía el vocabulario por la puerta de
# atrás, que es justo lo que un CHECK impediría si esto viviera en una columna.
_LABELS_SQL = f"""
SELECT label, count(*) AS n
  FROM (
        SELECT e.payload -> :key AS labels
        {_SCOPE}
          AND jsonb_typeof(e.payload -> :key) = 'array'
       ) src
 CROSS JOIN LATERAL jsonb_array_elements_text(src.labels) AS label
 WHERE label = ANY(:allowed)
 GROUP BY label
 ORDER BY n DESC, label ASC
"""

# `labelled` cuenta rechazos con al menos una etiqueta VÁLIDA, no con la lista no
# vacía: si contase lo segundo, un payload con basura saldría como «clasificado»
# y `unlabelled` —el número que mide la cobertura del dato— mentiría a la baja.
_LABEL_ARRAY = (
    "CASE WHEN jsonb_typeof(e.payload -> '{key}') = 'array'"
    " THEN e.payload -> '{key}' ELSE '[]'::jsonb END"
)
_COUNTS_SQL = """
SELECT count(*) AS rejections,
       count(*) FILTER (
           WHERE EXISTS (
               SELECT 1
                 FROM jsonb_array_elements_text({targets} || {classes}) AS l
                WHERE l = ANY(:allowed)
           )
       ) AS labelled
{scope}
""".format(
    targets=_LABEL_ARRAY.format(key="reject_targets"),
    classes=_LABEL_ARRAY.format(key="reject_classes"),
    scope=_SCOPE,
)


async def reject_breakdown(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID | None = None,
    since: datetime | None = None,
) -> RejectBreakdown:
    """Los rechazos del tenant agregados por los dos ejes.

    `project_id` y `since` acotan (proyecto concreto, ventana temporal); sin
    ellos es el tenant entero desde siempre. Las etiquetas que devuelve son
    SIEMPRE del vocabulario cerrado: se descartan las que no lo sean, porque una
    fila escrita por una versión anterior del escritor (o a mano) no puede
    ensanchar el value-set por la puerta de atrás — es la mitad de BD del cierre
    que `normalise_*` hace en la escritura, ya que este par vive en un `payload`
    JSONB y no en una columna con CHECK.
    """
    params: dict[str, object] = {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "since": since,
    }

    async def _labels(key: str, allowed: tuple[str, ...]) -> dict[str, int]:
        rows = (
            await session.execute(
                text(_LABELS_SQL), {**params, "key": key, "allowed": list(allowed)}
            )
        ).all()
        return {str(label): int(n) for label, n in rows}

    counts = (
        await session.execute(
            text(_COUNTS_SQL),
            {**params, "allowed": [*REJECT_TARGETS, *REJECT_CLASSES]},
        )
    ).one()
    rejections, labelled = int(counts[0]), int(counts[1])
    return RejectBreakdown(
        rejections=rejections,
        labelled=labelled,
        unlabelled=rejections - labelled,
        targets=await _labels("reject_targets", REJECT_TARGETS),
        classes=await _labels("reject_classes", REJECT_CLASSES),
    )
