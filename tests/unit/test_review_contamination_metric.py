"""Detector de Goodhart: ¿el revisor juzga o repite? (`task_gov_06`).

Plan [`gov-01`](../../docs/roadmap/gov-01-precedencia-prompts-y-rigor.md), fase 3.

El hecho medido: el revisor recibe los tres últimos intentos del implementador,
**el último verbatim** (`orchestrator/dispatch.py`, `_format_prior_outputs` +
`_REVIEW_PRIOR_OUTPUTS = 3`), y resuelve el mismo modelo que él por la misma
cadena de herencia. O sea que hereda su encuadre entero antes de opinar. La
alternativa cara —una pasada ciega, 4-6 días y un ADR— se aplazó a propósito:
**primero el dato**. Este módulo produce ese dato, y no es una feature.

Lo que estos tests fijan, y por qué cada uno puede fallar de verdad:

* la **dirección** de la contención (del revisor hacia el autor, no al revés):
  el relato del autor es mucho más largo, así que una medida simétrica tipo
  Jaccard saldría siempre baja y diría «no hay contaminación» por construcción;
* que **copiar de verdad puntúe alto** y que decir lo mismo con otras palabras
  puntúe bajo — si las dos cosas dieran igual, el número no distinguiría nada;
* que un revisor que **contradice** al autor salga bajo, que es el caso que
  justifica NO pagar la pasada ciega;
* que el eco de conclusión sea `None` cuando el autor no se autoevaluó, en vez
  de un `False` que se agregaría como «no hubo eco» y sesgaría la media a la baja.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from api_server.review_contamination import (
    METRIC_VERSION,
    ContaminationMetric,
    measure_review_contamination,
)

pytestmark = pytest.mark.unit


# El relato del implementador: prosa larga, con conclusiones propias.
_AUTHOR = """
He implementado el gate de tenant en el repositorio de planes. El problema era
que la consulta de listado no filtraba por tenant_id cuando el llamante venía
del orquestador, porque esa ruta usa BYPASSRLS y confiaba en la RLS. He añadido
un predicado explícito de tenant_id en las tres consultas y un test que crea dos
tenants y comprueba que el segundo no ve las filas del primero. Los tests pasan:
14 verdes. Creo que la tarea queda completa y el criterio de aceptación se
cumple, porque el aislamiento cross-tenant está cubierto por el test nuevo.
"""

# El revisor que REPITE: reutiliza el relato casi literalmente y firma.
_REVIEWER_ECHO = """
El problema era que la consulta de listado no filtraba por tenant_id cuando el
llamante venía del orquestador, porque esa ruta usa BYPASSRLS y confiaba en la
RLS. Ha añadido un predicado explícito de tenant_id en las tres consultas y un
test que crea dos tenants y comprueba que el segundo no ve las filas del
primero. La tarea queda completa y el criterio de aceptación se cumple.
"""

# El revisor que JUZGA: mira otra cosa y concluye distinto, con vocabulario propio.
_REVIEWER_INDEPENDENT = """
Rechazo. El predicado nuevo cubre el listado pero deja fuera el endpoint de
detalle, que sigue resolviendo por identificador sin acotar el propietario. Un
usuario del tenant B puede pedir el recurso del tenant A si adivina el UUID.
Además la migración no lleva vuelta atrás y la suite no ejerce el camino de
borrado lógico. Falta cobertura del caso concurrente.
"""


def test_copying_the_author_verbatim_scores_high() -> None:
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_ECHO, author_text=_AUTHOR, verdict="approve"
    )
    assert metric.phrase_overlap > 0.7, (
        "un revisor que reescribe el relato del autor casi palabra por palabra "
        f"tiene que salir alto; salió {metric.phrase_overlap}"
    )
    assert metric.verbatim_share > 0.5, (
        f"la reutilización literal debería dominar el texto; salió {metric.verbatim_share}"
    )


def test_judging_independently_scores_low() -> None:
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_INDEPENDENT, author_text=_AUTHOR, verdict="reject"
    )
    assert metric.phrase_overlap < 0.15, (
        "un revisor que aporta hallazgos propios no debe puntuar como contaminado; "
        f"salió {metric.phrase_overlap}"
    )
    assert metric.verbatim_share == 0.0


def test_the_two_cases_are_actually_distinguished() -> None:
    """La propiedad entera del detector: separar repetir de juzgar.

    Sin esta comparación, los dos tests de arriba podrían pasar con umbrales
    que casaran por accidente con una métrica que no discrimina.
    """
    echo = measure_review_contamination(
        reviewer_text=_REVIEWER_ECHO, author_text=_AUTHOR, verdict="approve"
    )
    judged = measure_review_contamination(
        reviewer_text=_REVIEWER_INDEPENDENT, author_text=_AUTHOR, verdict="reject"
    )
    assert echo.phrase_overlap - judged.phrase_overlap > 0.5, (
        "el detector no separa las dos poblaciones que existe para separar: "
        f"eco={echo.phrase_overlap} vs juicio={judged.phrase_overlap}"
    )


def test_saying_the_same_thing_with_other_words_is_not_verbatim_reuse() -> None:
    """Coincidir en el fondo no es contaminarse: eso sería un falso positivo.

    Dos textos que concluyen lo mismo con vocabulario distinto comparten
    conceptos, no frases. Si esto puntuara alto, la métrica mediría «acuerdo» y
    la decisión que alimenta —pagar o no la pasada ciega— se tomaría con el
    número equivocado.
    """
    paraphrase = """
    Doy el visto bueno: el aislamiento entre inquilinos ya está garantizado en
    las rutas de consulta, y la cobertura automática ejercita el escenario de
    fuga entre organizaciones distintas.
    """
    metric = measure_review_contamination(
        reviewer_text=paraphrase, author_text=_AUTHOR, verdict="approve"
    )
    assert metric.verbatim_share == 0.0
    assert metric.phrase_overlap < 0.1


def test_containment_is_directional_not_symmetric() -> None:
    """Se mide cuánto del REVISOR venía del autor, no al revés.

    El relato del autor es varias veces más largo. Una medida simétrica diría
    «poco solapamiento» incluso cuando el revisor no ha aportado ni una frase
    propia — exactamente el caso que hay que detectar.
    """
    short_echo = "el aislamiento cross-tenant está cubierto por el test nuevo"
    forward = measure_review_contamination(
        reviewer_text=short_echo, author_text=_AUTHOR, verdict="approve"
    )
    backward = measure_review_contamination(
        reviewer_text=_AUTHOR, author_text=short_echo, verdict="approve"
    )
    assert forward.phrase_overlap == pytest.approx(1.0)
    assert backward.phrase_overlap < 0.2


def test_echoed_conclusion_is_unknown_when_the_author_did_not_self_report() -> None:
    """`None`, no `False`: un dato ausente no es un dato negativo.

    Si esto devolviera `False`, al agregar una semana de runs los que no usaron
    `submit_result` entrarían como «sin eco» y bajarían la media sin que nadie
    lo supiera.
    """
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_ECHO, author_text=_AUTHOR, verdict="approve"
    )
    assert metric.echoed_conclusion is None


@pytest.mark.parametrize(
    ("finish_status", "verdict", "expected"),
    [
        ("success", "approve", True),
        ("failed", "reject", True),
        ("success", "reject", False),
        ("failed", "approve", False),
        ("partial", "approve", None),  # 'partial' no se proyecta a un veredicto
        (None, "approve", None),
    ],
)
def test_echoed_conclusion_compares_self_report_with_verdict(
    finish_status: str | None, verdict: str, expected: bool | None
) -> None:
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_INDEPENDENT,
        author_text=_AUTHOR,
        verdict=verdict,
        author_finish_status=finish_status,
    )
    assert metric.echoed_conclusion is expected


def test_empty_inputs_do_not_blow_up_and_do_not_pretend_to_measure() -> None:
    """Sin texto no hay medida; un 0.0 silencioso sería peor que un `measured=False`."""
    for reviewer, author in (("", _AUTHOR), (_REVIEWER_ECHO, ""), ("", "")):
        metric = measure_review_contamination(
            reviewer_text=reviewer, author_text=author, verdict="approve"
        )
        assert metric.measured is False
        assert metric.phrase_overlap == 0.0
        assert metric.verbatim_share == 0.0


def test_a_text_shorter_than_the_ngram_window_is_not_measured() -> None:
    """Cuatro palabras no dan ni un 5-grama: medir ahí sería inventar un cero."""
    metric = measure_review_contamination(
        reviewer_text="lo apruebo", author_text=_AUTHOR, verdict="approve"
    )
    assert metric.measured is False


def test_the_payload_is_json_safe_and_carries_the_metric_version() -> None:
    """El agregado se lee meses después: sin versión, dos fórmulas se mezclan.

    Es el fallo que ya pagó `EvalRun.subject_prompt_version` — medir calidad sin
    poder atribuirla a una versión concreta.
    """
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_ECHO,
        author_text=_AUTHOR,
        verdict="approve",
        author_finish_status="success",
    )
    payload = metric.as_payload()
    assert payload["metric_version"] == METRIC_VERSION
    assert json.loads(json.dumps(payload)) == payload
    assert set(payload) == {
        "metric_version",
        "measured",
        "phrase_overlap",
        "verbatim_share",
        "reviewer_tokens",
        "author_tokens",
        "echoed_conclusion",
        "verdict",
        "author_finish_status",
    }


def test_scores_are_rounded_so_the_aggregate_is_readable() -> None:
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_ECHO, author_text=_AUTHOR, verdict="approve"
    )
    assert metric.phrase_overlap == round(metric.phrase_overlap, 4)
    assert 0.0 <= metric.phrase_overlap <= 1.0
    assert 0.0 <= metric.verbatim_share <= 1.0


def test_markdown_scaffolding_is_invisible_to_the_metric() -> None:
    """La MISMA frase vestida de markdown tiene que reconocerse como la misma.

    Es la propiedad que hace útil descartar el andamiaje, y se comprueba por su
    lado fuerte: si la tokenización contara `**`, backticks, viñetas o puntos,
    estas dos frases —idénticas palabra por palabra— puntuarían por debajo de 1
    y una copia literal disfrazada de formato pasaría por trabajo propio.

    Lo verifiqué rompiéndolo: con `\\S+` como tokenizador esto cae a 0.0.
    """
    plain = "el predicado de tenant id cubre las tres consultas del listado"
    dressed = "- El **predicado** de `tenant_id` cubre las *tres* consultas del listado."
    metric = measure_review_contamination(
        reviewer_text=dressed, author_text=plain, verdict="approve"
    )
    assert metric.phrase_overlap == pytest.approx(1.0), (
        "el andamiaje markdown está entrando como tokens y separando dos textos "
        f"que son la misma frase: {metric.phrase_overlap}"
    )


def test_metric_is_a_frozen_value_object() -> None:
    """Una medición no se retoca después de tomada.

    La escritura se intenta a través de una referencia `Any` a propósito: el
    tipado ya prohíbe asignar a un campo `frozen`, y lo que aquí se comprueba es
    que además lo prohíbe en EJECUCIÓN — es el mismo objeto que viaja al payload
    de auditoría, así que una mutación silenciosa quedaría persistida.
    """
    metric = measure_review_contamination(
        reviewer_text=_REVIEWER_ECHO, author_text=_AUTHOR, verdict="approve"
    )
    assert isinstance(metric, ContaminationMetric)
    escape_hatch: Any = metric
    with pytest.raises(FrozenInstanceError):
        escape_hatch.phrase_overlap = 0.0


# --- el cableado: sin llamante, esto sería el patrón dominante de la base ----
#
# `docs/03-guides/verificar-antes-de-implementar.md` §5: una y otra vez en este
# repo el mecanismo estaba construido entero y no lo llamaba nadie
# (`record_shadow_eval` desde el Plan 14 sin un solo llamante;
# `EvalRun.subject_prompt_version` que nadie poblaba). Una métrica de
# contaminación que nadie calcula es exactamente ese fallo, y encima uno
# invisible: el agregado del test humano `human_gov_03` saldría vacío y se
# leería como «no hay contaminación».
_WORKER_EXECUTION = (
    Path(__file__).resolve().parents[2] / "apps" / "workers" / "src" / "workers" / "execution.py"
)


def test_the_worker_actually_records_the_metric() -> None:
    source = _WORKER_EXECUTION.read_text(encoding="utf-8")
    assert len(source) > 10_000, (
        "la guarda dejó de encontrar el módulo del worker; sin sujeto pasaría vacía"
    )
    assert "measure_review_contamination" in source, (
        "workers/execution.py ya no usa la métrica: el detector de Goodhart "
        "quedaría construido y sin llamante"
    )
    # Se busca la INVOCACIÓN (`await …(`), no el nombre: contar apariciones daba
    # verde con la llamada borrada, porque el comentario que la introduce y la
    # propia `async def` ya suman dos. Lo comprobé quitando la llamada.
    assert "await _record_review_contamination(" in source, (
        "`_record_review_contamination` está DEFINIDA pero no la invoca nadie: "
        "el mecanismo existiría y el agregado saldría vacío"
    )


def test_the_metric_is_recorded_on_the_review_branch_only() -> None:
    """Se mide donde hay un veredicto que medir, no en cada run.

    Un implementador no produce veredicto: llamar ahí generaría filas sin
    significado que ensuciarían el agregado.
    """
    source = _WORKER_EXECUTION.read_text(encoding="utf-8")
    review_branch = source.split("if request.review:", 1)
    assert len(review_branch) == 2, "cambió la rama de review del worker; revisa la guarda"
    after = review_branch[1].split("\n        elif ", 1)[0]
    assert "_record_review_contamination(" in after, (
        "la llamada se ha salido de la rama `if request.review:`"
    )


def test_the_audit_event_kind_is_its_own_not_folded_into_review_comment() -> None:
    """Un APPROVE sin desglose de criterios NO emite `review_comment`.

    Colgar la métrica de ese evento la perdería justo en los approves limpios,
    que son la mitad de la población que interesa medir.
    """
    source = _WORKER_EXECUTION.read_text(encoding="utf-8")
    assert 'REVIEW_CONTAMINATION_EVENT_KIND = "review_contamination"' in source
    assert "kind=REVIEW_CONTAMINATION_EVENT_KIND" in source
