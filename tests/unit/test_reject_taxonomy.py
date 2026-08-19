"""El rechazo como `target` x `class`: vocabulario cerrado y contrato (`task_gov_10`).

Tres cosas se fijan aquí, y las tres son la razón de que la casilla exista:

1. **El vocabulario está CERRADO y no tiene bucket «otros».** Una etiqueta
   genérica se DESCARTA; el rechazo se queda sin clasificar y eso se ve en el
   agregado. Un `other` que se lleva el 60 % no informa de nada.
2. **Anuncio y parseo son la MISMA declaración.** El prompt del reviewer
   (`agent_runtime.review_contract`, y el seed `builtin_agents`) interpola lo que
   `shared_domain.reject_taxonomy` declara, y `api_server.reviewer_bridge`
   construye sus regex desde los mismos tags. Este es el fallo que ya nos costó
   dos incidentes —el tag `<verdict>` deletreado a mano en cinco prompts
   (hallazgo H3) y las 13 categorías de aprobación que no intersecaban con
   ninguna política (hallazgo g6)—, así que se prueba, no se confía.
3. **Un veredicto sin los tags sigue funcionando igual que antes.** El par es
   ADITIVO: el `<verdict>` manda, y un modelo que se lo salte no degrada nada.

No-vacuidad: los tests de descarte afirman CUÁNTAS etiquetas genéricas probaron.
Si un refactor vaciase `GENERIC_LABELS`, un `assert not colados` pasaría solo.
"""

from __future__ import annotations

import pytest
from agent_runtime import review_contract as rc
from api_server.reviewer_bridge import ReviewerVerdict, parse_reviewer_output
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from shared_domain import reject_taxonomy as rt
from shared_domain.reject_taxonomy import (
    GENERIC_LABELS,
    MAX_LABELS_PER_VERDICT,
    REJECT_CLASSES,
    REJECT_TARGETS,
    RejectClass,
    RejectTarget,
    describe_classes,
    describe_targets,
    normalise_classes,
    normalise_targets,
    reject_taxonomy_instruction,
)

# ---------------------------------------------------------------------------
# 1. El vocabulario: cerrado, corto y sin «otros»
# ---------------------------------------------------------------------------


def test_targets_are_the_four_of_the_casilla() -> None:
    """Los cuatro `target` del enunciado: código, tests, alcance, entregable."""
    assert REJECT_TARGETS == ("code", "tests", "scope", "deliverable")


def test_classes_are_closed_and_short() -> None:
    assert REJECT_CLASSES == (
        "incorrect",
        "incomplete",
        "unproven",
        "regression",
        "contract_drift",
        "overreach",
    )
    # «Corto» es parte del enunciado: un eje con veinte valores no se agrega, se
    # lee de uno en uno, que es el problema que veníamos a resolver.
    assert len(REJECT_CLASSES) <= 8


def test_neither_axis_has_a_generic_bucket() -> None:
    """Ni `other`, ni `misc`, ni `unknown` como VALOR del enum ni como alias.

    Los alias entran porque son la otra puerta: un alias `general -> code`
    reintroduciría el bucket «otros» sin tocar el enum, y el agregado volvería a
    tener una etiqueta que se lleva todo lo que el modelo no supo clasificar.
    """
    for value in (*REJECT_TARGETS, *REJECT_CLASSES):
        assert value not in GENERIC_LABELS, f"{value!r} es un bucket genérico disfrazado de valor"
    for alias in (*rt._TARGET_ALIASES, *rt._CLASS_ALIASES):
        assert alias not in GENERIC_LABELS, f"el alias {alias!r} es un bucket genérico"


def test_a_generic_word_cannot_sneak_in_as_an_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """El descarte de lo genérico se aplica ANTES de resolver alias y enum.

    Sin esta precedencia, `GENERIC_LABELS` sería decorativa: hoy `other` se cae
    igual porque no está en el vocabulario, así que el descarte explícito sólo se
    puede comprobar poniendo la trampa que viene a impedir — un alias genérico
    añadido por descuido. Es la diferencia entre una guarda y un comentario.
    """
    monkeypatch.setitem(rt._TARGET_ALIASES, "other", "code")
    assert normalise_targets("other") == ()
    # …y el alias legítimo de al lado sigue funcionando, o sea que la guarda no
    # está simplemente rompiendo la resolución de alias.
    assert normalise_targets("codigo") == ("code",)


def test_every_value_carries_a_gloss() -> None:
    """Un valor sin glosa es un valor que el modelo no sabe cuándo usar.

    `describe_*` recorre el enum e indexa las glosas, así que un valor nuevo sin
    glosa revienta aquí con `KeyError` en vez de llegar al prompt como una línea
    que falta.
    """
    targets, classes = describe_targets(), describe_classes()
    for value in REJECT_TARGETS:
        assert f"  - {value}: " in targets
    for value in REJECT_CLASSES:
        assert f"  - {value}: " in classes


# ---------------------------------------------------------------------------
# 2. La normalización: tope de tres, genérico descartado, forma tolerada
# ---------------------------------------------------------------------------


def test_valid_labels_survive_in_order() -> None:
    assert normalise_targets("code, tests") == ("code", "tests")
    assert normalise_classes("regression") == ("regression",)


def test_the_cap_is_three_per_axis() -> None:
    """Cuatro `target` válidos entran; sólo tres salen."""
    assert len(REJECT_TARGETS) > MAX_LABELS_PER_VERDICT  # si no, el tope no se probaría
    got = normalise_targets(", ".join(REJECT_TARGETS))
    assert got == tuple(REJECT_TARGETS[:MAX_LABELS_PER_VERDICT])
    assert len(got) == 3


def test_every_generic_label_is_discarded() -> None:
    """Ninguna de las formas de «otros» cuela, en ninguno de los dos ejes."""
    colados = [
        label
        for label in sorted(GENERIC_LABELS)
        if normalise_targets(label) or normalise_classes(label)
    ]
    assert not colados, f"etiquetas genéricas aceptadas: {colados}"
    # No-vacuidad: si alguien vaciase GENERIC_LABELS, el assert de arriba pasaría
    # solo y este test dejaría de proteger nada.
    assert len(GENERIC_LABELS) >= 10, f"la lista de genéricos se quedó en {len(GENERIC_LABELS)}"


def test_a_generic_label_does_not_displace_a_valid_one() -> None:
    """Lo genérico se cae y lo válido se queda: no se pierde el par entero."""
    assert normalise_classes("other, incomplete, misc") == ("incomplete",)


def test_unknown_labels_are_dropped_not_bucketed() -> None:
    assert normalise_targets("frontend") == ()
    assert normalise_classes("performance") == ()


def test_interpretive_synonyms_are_NOT_invented() -> None:
    """`bug` NO se mapea a `incorrect`.

    Es la frontera de la tolerancia: se perdona la FORMA (mayúsculas, guiones,
    plurales), no se adivina el VALOR. Un mapa de sinónimos sería el bucket
    genérico por la puerta de atrás, decidiendo por el modelo lo que el modelo no
    dijo — y contaminando justo el agregado que la casilla produce.
    """
    assert normalise_classes("bug") == ()
    assert normalise_classes("missing") == ()


@pytest.mark.parametrize(
    "raw",
    [
        "CODE",
        "  code  ",
        "- code",
        "code.",
        "[code]",
    ],
)
def test_form_drift_does_not_lose_a_valid_label(raw: str) -> None:
    """La deriva de redacción de los modelos no-Claude es un hecho medido.

    El tag `<verdict>` tuvo que pasar a parseo tolerante por esto mismo (F37):
    perder una etiqueta correcta por un espacio de más sería tirar el dato.
    """
    assert normalise_targets(raw) == ("code",)


def test_separators_a_model_actually_uses() -> None:
    assert normalise_targets("code; tests") == ("code", "tests")
    assert normalise_targets("code / tests") == ("code", "tests")
    assert normalise_targets("code tests") == ("code", "tests")
    assert normalise_targets(["code", "tests"]) == ("code", "tests")


def test_a_two_word_value_beats_its_own_split() -> None:
    """`contract drift` es UN valor, no dos palabras que se caen las dos."""
    assert normalise_classes("contract drift") == ("contract_drift",)
    assert normalise_classes("contract-drift") == ("contract_drift",)


def test_duplicates_collapse() -> None:
    assert normalise_targets("code, code, CODE") == ("code",)


def test_empty_input_is_empty_output_not_a_default() -> None:
    """Sin etiquetas NO se inventa una: el rechazo queda sin clasificar."""
    assert normalise_targets("") == ()
    assert normalise_classes("   ") == ()


# ---------------------------------------------------------------------------
# 3. El contrato: lo que el prompt anuncia es lo que el parser acepta
# ---------------------------------------------------------------------------


def test_the_prompt_advertises_exactly_the_parsed_vocabulary() -> None:
    """Ni un valor anunciado que el parser tire, ni uno aceptado sin anunciar."""
    instruction = rc.REJECT_TAXONOMY_INSTRUCTION
    for value in (*REJECT_TARGETS, *REJECT_CLASSES):
        assert value in instruction, f"{value!r} se acepta pero el prompt no lo ofrece"
    # Y al revés: cada línea de valor del anuncio se parsea de verdad.
    for line in instruction.splitlines():
        if not line.startswith("  - "):
            continue
        value = line[4:].split(":", 1)[0].strip()
        assert normalise_targets(value) or normalise_classes(value), (
            f"el prompt ofrece {value!r} y el parser lo descarta"
        )


def test_the_runtime_reexports_the_shared_declaration() -> None:
    """El runtime no tiene su propia copia del texto: reexporta la única.

    Si alguien re-teclea la instrucción en `review_contract`, esto cae — y esa
    es la forma exacta en que se rompió el contrato del `<verdict>`.
    """
    assert reject_taxonomy_instruction() == rc.REJECT_TAXONOMY_INSTRUCTION
    assert rc.REJECT_TARGET_OPEN == "<reject_target>"
    assert rc.REJECT_CLASS_OPEN == "<reject_class>"


def test_the_prompt_says_the_cap_it_enforces() -> None:
    assert f"at most {MAX_LABELS_PER_VERDICT} per axis" in rc.REJECT_TAXONOMY_INSTRUCTION


def test_the_prompt_forbids_inventing_a_value() -> None:
    """El «no hay bucket otros» tiene que estar DICHO, no sólo implementado."""
    text = rc.REJECT_TAXONOMY_INSTRUCTION.lower()
    assert "'other' bucket" in text
    assert "discarded" in text


def test_the_seeded_reviewer_prompt_carries_the_same_instruction() -> None:
    """Los dos prompts del reviewer piden lo mismo.

    Un system prompt que pide TRES campos de rechazo mientras el preámbulo del
    run pide CINCO son dos contratos en competencia: exactamente el fallo que
    arregló F1.6c en el reviewer.
    """
    reviewer = next(a for a in BUILTIN_AGENTS if a.slug == "reviewer")
    for prompt in (reviewer.system_prompt_es, reviewer.system_prompt_en):
        assert rc.REJECT_TAXONOMY_INSTRUCTION in prompt


# ---------------------------------------------------------------------------
# 4. El parseo del veredicto de punta a punta
# ---------------------------------------------------------------------------

_REJECTION = (
    "The retry path is not covered by any test.\n"
    f"{rc.VERDICT_REJECT}\n"
    "<rejection>"
    "<failed_criterion>retries are covered</failed_criterion>"
    "<what_to_fix>add the regression test</what_to_fix>"
    f"{rc.REJECT_TARGET_OPEN}tests, code{rc.REJECT_TARGET_CLOSE}"
    f"{rc.REJECT_CLASS_OPEN}unproven{rc.REJECT_CLASS_CLOSE}"
    "</rejection>"
)


def test_a_wire_shaped_rejection_yields_the_pair() -> None:
    verdict = parse_reviewer_output(_REJECTION)
    assert verdict.label == "reject"
    assert verdict.reject_targets == ("tests", "code")
    assert verdict.reject_classes == ("unproven",)
    # La prosa sigue intacta: el par es aditivo, no un reemplazo.
    assert verdict.failed_criterion == "retries are covered"


def test_the_plural_tag_is_honoured() -> None:
    """`<reject_targets>` (con s) es lo que emite media flota de modelos."""
    out = (
        f"{rc.VERDICT_REJECT}<rejection>"
        "<reject_targets>code</reject_targets>"
        "<reject_classes>incorrect</reject_classes>"
        "</rejection>"
    )
    verdict = parse_reviewer_output(out)
    assert verdict.reject_targets == ("code",)
    assert verdict.reject_classes == ("incorrect",)


def test_one_tag_per_label_is_read_whole() -> None:
    """Dos tags del mismo eje dicen lo mismo que uno con dos valores."""
    out = (
        f"{rc.VERDICT_REJECT}<rejection>"
        "<reject_target>code</reject_target><reject_target>tests</reject_target>"
        "</rejection>"
    )
    assert parse_reviewer_output(out).reject_targets == ("code", "tests")


def test_a_rejection_without_the_tags_still_parses_as_before() -> None:
    """El par es ADITIVO: sin los tags, el veredicto es el de siempre."""
    out = f"{rc.VERDICT_REJECT}\n<rejection><failed_criterion>c1</failed_criterion></rejection>"
    verdict = parse_reviewer_output(out)
    assert verdict.label == "reject"
    assert verdict.failed_criterion == "c1"
    assert (verdict.reject_targets, verdict.reject_classes) == ((), ())


def test_an_approve_carries_no_reject_labels() -> None:
    """Aunque el modelo los emita: no hay `target` de un rechazo que no hubo."""
    out = f"{rc.REJECT_TARGET_OPEN}code{rc.REJECT_TARGET_CLOSE}\n{rc.VERDICT_APPROVE}"
    verdict = parse_reviewer_output(out)
    assert verdict.label == "approve"
    assert verdict.reject_targets == ()


def test_garbage_in_the_tag_does_not_poison_the_pair() -> None:
    out = (
        f"{rc.VERDICT_REJECT}<rejection>"
        "<reject_target>everything, other, frontend, tests</reject_target>"
        "</rejection>"
    )
    assert parse_reviewer_output(out).reject_targets == ("tests",)


def test_the_defensive_reject_is_left_unclassified() -> None:
    """El rechazo que sintetiza el worker cuando no hay veredicto parseable
    (`workers.execution._apply_review_verdict`) NO se etiqueta.

    Etiquetarlo contaminaría el agregado con fallos de FORMATO del reviewer, que
    no son defectos del trabajo revisado: «lo que más se rechaza» pasaría a
    medir la obediencia del modelo al wire-format. Se cuenta como no clasificado,
    y ese número se lee aparte.
    """
    synthesised = ReviewerVerdict(
        label="reject",
        failed_criterion="reviewer produced no parseable verdict",
        what_to_fix="re-run the review and end with a <verdict>approve|reject</verdict> tag",
    )
    assert (synthesised.reject_targets, synthesised.reject_classes) == ((), ())
    # Y la prueba de que eso no es sólo el default del dataclass: el texto que el
    # worker mete ahí no contiene ninguna etiqueta del vocabulario que un parseo
    # posterior pudiera reinterpretar como clasificación.
    assert parse_reviewer_output(synthesised.what_to_fix).reject_targets == ()


def test_enum_members_are_their_own_values() -> None:
    """`StrEnum`: el enum se puede comparar y serializar sin `.value`."""
    assert RejectTarget.CODE == "code"
    assert RejectClass.CONTRACT_DRIFT == "contract_drift"
