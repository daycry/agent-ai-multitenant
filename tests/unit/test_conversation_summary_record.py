"""El registro estructurado del resumen y su pliegue determinista (task_wf_06 b).

La compresión jerárquica de PROSA degrada: cada piso vuelve a pasar por un modelo
el texto que ya era un resumen, y a los tres pisos un requisito enunciado al
principio se ha convertido en «el usuario mencionó varios requisitos». Por eso el
resumen lleva **doble representación**: prosa en `content` para el humano y un
`summary_record` estructurado en `attachments` para el pliegue.

La pieza clave es que el pliegue es **híbrido**: el LLM solo resume los mensajes
CRUDOS de la ventana; los `summary_record` que ya existían se fusionan de forma
determinista (concatenar + deduplicar). Un requisito registrado en el piso 1 se
copia literal hasta el piso 5 sin volver a pasar por un modelo, así que la
degradación queda acotada por completo.

Estos tests fijan las funciones puras de ese mecanismo. El recorrido end-to-end
(tres pisos reales sobre PostgreSQL) vive en
`tests/integration/test_conversation_compression.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api_server.db.conversation_compression import (
    SUMMARY_RECORD_KIND,
    SummaryRecord,
    aligned_window_size,
    estimate_tokens,
    record_of,
    render_record,
    split_window,
)

pytestmark = pytest.mark.unit


def _msg(
    content: str,
    *,
    author_kind: str = "user",
    attachments: list[dict[str, Any]] | None = None,
    is_summary: bool = False,
) -> Any:
    return SimpleNamespace(
        content=content,
        author_kind=author_kind,
        attachments=attachments or [],
        is_summary=is_summary,
    )


def _summary_msg(prose: str, record: SummaryRecord) -> Any:
    return _msg(
        prose,
        author_kind="system",
        attachments=[{"kind": "summary_replaces", "message_ids": []}, record.as_attachment()],
        is_summary=True,
    )


# ---------------------------------------------------------------------------
# El registro: forma, ida y vuelta
# ---------------------------------------------------------------------------
def test_record_round_trips_through_its_attachment() -> None:
    record = SummaryRecord(
        requisitos=("debe funcionar sin conexión",),
        decisiones=("usamos SQLite",),
        descartado=("nada de Electron",),
        abierto=("¿quién paga el certificado?",),
    )
    att = record.as_attachment()
    assert att["kind"] == SUMMARY_RECORD_KIND
    assert SummaryRecord.from_attachment(att) == record


def test_record_reads_only_its_own_attachment_kind() -> None:
    """Un mensaje lleva varios attachments (`summary_replaces`, directivas del
    grafo de planning…); el registro solo debe salir del suyo."""
    msg = _msg(
        "resumen",
        author_kind="system",
        attachments=[
            {"kind": "summary_replaces", "message_ids": ["x"]},
            {"kind": "planning_directive", "intent": "finish_planning"},
            SummaryRecord(requisitos=("R1",)).as_attachment(),
        ],
        is_summary=True,
    )
    assert record_of(msg) == SummaryRecord(requisitos=("R1",))


def test_message_without_record_has_none() -> None:
    """Distinguir «no hay registro» de «registro vacío» importa: el primero es un
    mensaje crudo que el LLM debe resumir, el segundo un resumen ya plegado."""
    assert record_of(_msg("hola")) is None
    assert record_of(_summary_msg("s", SummaryRecord())) == SummaryRecord()


def test_malformed_record_degrades_to_empty_not_crash() -> None:
    msg = _msg(
        "resumen",
        author_kind="system",
        attachments=[{"kind": SUMMARY_RECORD_KIND, "requisitos": "no es una lista"}],
        is_summary=True,
    )
    assert record_of(msg) == SummaryRecord()


# ---------------------------------------------------------------------------
# La fusión determinista
# ---------------------------------------------------------------------------
def test_merge_concatenates_preserving_order() -> None:
    a = SummaryRecord(requisitos=("R1", "R2"), decisiones=("D1",))
    b = SummaryRecord(requisitos=("R3",), abierto=("A1",))
    assert a.merged_with(b) == SummaryRecord(
        requisitos=("R1", "R2", "R3"),
        decisiones=("D1",),
        abierto=("A1",),
    )


def test_merge_deduplicates_keeping_the_first_occurrence() -> None:
    """Cada pliegue re-emite lo que ya sabía; sin deduplicar, el registro crecería
    linealmente con el número de pisos y acabaría desbordando él solo."""
    a = SummaryRecord(requisitos=("R1", "R2"))
    b = SummaryRecord(requisitos=("R2", "R1", "R3"))
    assert a.merged_with(b).requisitos == ("R1", "R2", "R3")


def test_merge_ignores_whitespace_only_entries() -> None:
    merged = SummaryRecord(requisitos=("R1",)).merged_with(SummaryRecord(requisitos=("", "   ")))
    assert merged.requisitos == ("R1",)


def test_merge_is_associative_over_three_floors() -> None:
    """El pliegue se aplica una vez por piso; el resultado no puede depender de
    cómo se agrupen las fusiones."""
    r1 = SummaryRecord(requisitos=("R1",), descartado=("X1",))
    r2 = SummaryRecord(requisitos=("R2",))
    r3 = SummaryRecord(descartado=("X2",), abierto=("A1",))
    assert r1.merged_with(r2).merged_with(r3) == r1.merged_with(r2.merged_with(r3))


# ---------------------------------------------------------------------------
# El pliegue híbrido: qué va al LLM y qué se copia literal
# ---------------------------------------------------------------------------
def test_split_window_sends_only_raw_messages_to_the_llm() -> None:
    raw_a = _msg("mensaje crudo 1")
    folded = _summary_msg(
        "prosa vieja que NO debe volver al modelo",
        SummaryRecord(requisitos=("R1",), descartado=("X1",)),
    )
    raw_b = _msg("mensaje crudo 2")

    to_summarise, inherited = split_window([raw_a, folded, raw_b])

    assert [m.content for m in to_summarise] == ["mensaje crudo 1", "mensaje crudo 2"]
    assert inherited == SummaryRecord(requisitos=("R1",), descartado=("X1",))


def test_split_window_never_reads_the_prose_of_a_folded_summary() -> None:
    """La prosa es para el humano. Si entrase como entrada del pliegue volveríamos
    a la compresión jerárquica de texto y a su degradación."""
    folded = _summary_msg("REQUISITO INVENTADO EN LA PROSA", SummaryRecord(requisitos=("R1",)))
    to_summarise, inherited = split_window([folded])
    assert to_summarise == []
    assert inherited.requisitos == ("R1",)
    assert all("INVENTADO" not in item for item in inherited.requisitos)


def test_split_window_merges_several_prior_records_in_order() -> None:
    first = _summary_msg("s1", SummaryRecord(requisitos=("R1",)))
    second = _summary_msg("s2", SummaryRecord(requisitos=("R2",), decisiones=("D1",)))
    _, inherited = split_window([first, second, _msg("crudo")])
    assert inherited == SummaryRecord(requisitos=("R1", "R2"), decisiones=("D1",))


def test_legacy_summary_without_record_is_treated_as_raw() -> None:
    """Los resúmenes que ya existan en BD (escritos antes de esta tarea) no llevan
    `summary_record`. Deben volver al modelo como texto, no perderse."""
    legacy = _msg(
        "resumen antiguo",
        author_kind="system",
        attachments=[{"kind": "summary_replaces", "message_ids": []}],
        is_summary=True,
    )
    to_summarise, inherited = split_window([legacy])
    assert [m.content for m in to_summarise] == ["resumen antiguo"]
    assert inherited == SummaryRecord()


# ---------------------------------------------------------------------------
# El render: lo que el modelo vuelve a leer
# ---------------------------------------------------------------------------
def test_render_puts_every_entry_verbatim_in_the_content() -> None:
    """`history_from_messages` solo pasa `content` al prompt: si el registro no se
    renderiza ahí, el modelo nunca lo ve y la garantía de supervivencia es falsa."""
    record = SummaryRecord(
        requisitos=("debe funcionar sin conexión",),
        decisiones=("usamos SQLite",),
        descartado=("nada de Electron",),
        abierto=("¿quién paga el certificado?",),
    )
    rendered = render_record(record)
    for entry in ("debe funcionar sin conexión", "usamos SQLite", "nada de Electron"):
        assert entry in rendered
    assert "¿quién paga el certificado?" in rendered


def test_render_of_an_empty_record_is_empty() -> None:
    assert render_record(SummaryRecord()) == ""


def test_render_omits_the_sections_that_have_nothing() -> None:
    rendered = render_record(SummaryRecord(requisitos=("R1",)))
    assert "R1" in rendered
    assert "Descartado" not in rendered


# ---------------------------------------------------------------------------
# La ventana no parte un turno por la mitad
# ---------------------------------------------------------------------------
def test_window_is_trimmed_back_to_the_last_turn_boundary() -> None:
    """Un turno son 6-10 mensajes (framing del PM + N especialistas + síntesis).
    Cortar en el 10 se llevaría el framing y cuatro especialistas dejando fuera la
    síntesis: el resumen contaría media discusión."""
    kinds = ["user"] + ["agent"] * 5 + ["user"] + ["agent"] * 5
    # Frontera de turno en 0 y en 6. Con ventana 10 la única frontera que cabe
    # (sin ser el arranque) es la 6 → se pliega el primer turno entero.
    assert aligned_window_size(kinds, 10) == 6


def test_window_takes_as_many_whole_turns_as_fit() -> None:
    kinds = ["user", "agent", "user", "agent", "user", "agent", "user", "agent"]
    assert aligned_window_size(kinds, 5) == 4  # dos turnos completos
    assert aligned_window_size(kinds, 100) == 6  # todos menos el último (en vuelo)


def test_a_turn_longer_than_the_window_is_folded_whole() -> None:
    """Si ni un turno entero cabe, recortar daría cero y la compresión no
    avanzaría nunca: se pliega ese turno completo aunque exceda la ventana."""
    kinds = ["user"] + ["agent"] * 20 + ["user", "agent"]
    assert aligned_window_size(kinds, 10) == 21


def test_without_turn_boundaries_the_plain_window_applies() -> None:
    """Caso degenerado (feed sin mensajes de usuario): no hay turnos que respetar."""
    assert aligned_window_size(["system"] * 30, 10) == 10


def test_the_last_turn_is_never_folded() -> None:
    """El turno más reciente es el que el equipo está contestando ahora mismo."""
    kinds = ["user", "agent", "user", "agent"]
    assert aligned_window_size(kinds, 100) == 2


# ---------------------------------------------------------------------------
# El techo por tokens (segunda guarda)
# ---------------------------------------------------------------------------
def test_token_estimate_grows_with_the_text() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 4000) == 1000


# ---------------------------------------------------------------------------
# Los umbrales con los que el chat de proyecto dispara la compresión
# ---------------------------------------------------------------------------
def test_chat_compression_thresholds() -> None:
    """El umbral tiene que quedar por DEBAJO de la ventana de contexto.

    Si no, el contador de `load_context_window` truncaría la conversación antes de
    que la compresión llegase a ejecutarse nunca: lo viejo se perdería en silencio
    en vez de resumirse, que es justo el bug A-01/A-02 con otro disfraz.
    """
    from api_server.chat.responder import (
        _CHAT_COMPRESSION_THRESHOLD,
        _CHAT_COMPRESSION_WINDOW,
        _PLANNING_CONTEXT_MESSAGES,
    )

    assert _CHAT_COMPRESSION_THRESHOLD < _PLANNING_CONTEXT_MESSAGES
    # Y la ventana por debajo del umbral: plegar todo lo descubierto de una vez
    # dejaría la conversación entera detrás de un solo resumen.
    assert _CHAT_COMPRESSION_WINDOW < _CHAT_COMPRESSION_THRESHOLD


def test_the_chat_window_spans_whole_turns() -> None:
    """Un turno de este chat son 6-10 mensajes; la ventana debe caber varios para
    que la compresión avance de verdad en cada pasada."""
    from api_server.chat.responder import _CHAT_COMPRESSION_WINDOW

    assert _CHAT_COMPRESSION_WINDOW >= 20
