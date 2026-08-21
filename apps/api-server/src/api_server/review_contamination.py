"""¿El revisor juzga o repite? — el detector de Goodhart (`task_gov_06`).

Plan `gov-01`, fase 3. **Esto produce un dato, no una feature**, y así hay que
leerlo: nada bloquea, nada cambia de comportamiento, nadie recibe un aviso.

El hecho que lo motiva está medido en el código, no supuesto. El revisor recibe
los tres últimos intentos del implementador —**el último verbatim**, ver
``_format_prior_outputs`` y ``_REVIEW_PRIOR_OUTPUTS`` en
``apps/orchestrator/src/orchestrator/dispatch.py``— y resuelve el modelo por la
MISMA cadena de herencia ADR 0055 que el implementador
(``_build_review_request`` reutiliza ``_resolve_model_spec`` a propósito). O sea
que el revisor hereda el encuadre entero del autor antes de opinar. Si además
repite su relato, «revisado» y «revisado a ojo» son indistinguibles desde fuera,
que es la definición de una métrica capturada por la ley de Goodhart.

La alternativa —una pasada de review **ciega**, sin el relato del autor— cuesta
4-6 días y duplica el coste de cada review, así que el operador decidió el
2026-08-12 **medir primero**. Si el número sale alto, la pasada ciega queda
justificada con evidencia; si sale bajo, nos hemos ahorrado pagarla. Cero tokens
extra: esto es post-proceso de texto que ya existe.

Qué se mide, y por qué así
--------------------------
``phrase_overlap``
    Qué fracción de los n-gramas del REVISOR aparece también en el relato del
    autor. Es **contención dirigida**, no similitud simétrica: el relato del
    autor es varias veces más largo, así que un Jaccard saldría bajo incluso
    cuando el revisor no ha aportado ni una frase propia. La dirección es la
    mitad de la medida.

``verbatim_share``
    Qué fracción de los tokens del revisor está cubierta por una tirada literal
    larga compartida con el autor. Separa «coincidimos en el fondo» (legítimo,
    y frecuente cuando la tarea está bien hecha) de «he copiado su párrafo».

``echoed_conclusion``
    Si el veredicto coincide con la autoevaluación del propio autor
    (``Execution.finish_status``). **Por sí solo no es contaminación** — un
    trabajo bien hecho y bien autoevaluado produce eco legítimo. Solo informa
    en agregado y contrastado con las otras dos. Es ``None`` cuando el autor no
    se autoevaluó: un dato ausente no es un dato negativo, y meterlo como
    ``False`` sesgaría la media de la semana sin que nadie lo supiera.

Determinista y puro: sin reloj, sin red, sin LLM y sin I/O. El llamante vive en
``apps/workers/src/workers/execution.py`` (``_record_review_contamination``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Versión de la FÓRMULA. Va en cada fila para que un agregado de meses no
#: mezcle dos definiciones distintas del mismo nombre. Es exactamente el fallo
#: que ya se pagó con `EvalRun.subject_prompt_version`: medir calidad sin poder
#: atribuirla a la versión que la produjo. Súbela al cambiar cualquier constante
#: de abajo o la tokenización.
METRIC_VERSION: int = 1

#: Ventana para el solapamiento de frases. Cinco palabras es lo bastante largo
#: para que coincidir sea significativo en castellano y lo bastante corto para
#: capturar una idea reformulada mínimamente.
PHRASE_NGRAM: int = 5

#: Ventana para la reutilización LITERAL. Doce palabras seguidas idénticas no
#: se producen por casualidad entre dos textos independientes sobre el mismo
#: tema: es copia.
VERBATIM_NGRAM: int = 12

#: Válvula de seguridad frente a una salida patológica. El algoritmo es lineal,
#: así que esto no se alcanza en la práctica; existe para que un `output` de
#: cientos de MB no convierta un post-proceso best-effort en un problema.
MAX_TOKENS: int = 200_000

#: Cómo se proyecta la autoevaluación del autor sobre el veredicto del revisor.
#: `partial` NO se proyecta: no equivale ni a aprobar ni a rechazar, y forzarlo
#: a uno de los dos inventaría eco donde no lo hay.
_SELF_REPORT_TO_VERDICT: dict[str, str] = {"success": "approve", "failed": "reject"}

#: Los dos veredictos que el revisor puede emitir (`reviewer_bridge`). Un
#: `unknown` no se compara con nada.
_COMPARABLE_VERDICTS = frozenset({"approve", "reject"})

#: Palabras: letras (con acentos y ñ) y dígitos. Todo lo demás —`**`, backticks,
#: viñetas, dos puntos, etiquetas `<verdict>`— es andamiaje que los dos textos
#: comparten por convención del prompt, no por copiarse. Contarlo inflaría la
#: medida con decorado.
_WORD_RE = re.compile(r"[0-9a-záéíóúüñ]+")


@dataclass(frozen=True, slots=True)
class ContaminationMetric:
    """El resultado de una medición. Objeto de valor, inmutable.

    Attributes:
        measured: ``False`` cuando no había texto suficiente para medir (alguno
            de los dos lados vacío o más corto que la ventana). Un ``0.0``
            silencioso en ese caso mentiría: diría «no hay contaminación»
            cuando lo cierto es «no se pudo mirar».
        phrase_overlap: contención dirigida revisor→autor, en ``[0, 1]``.
        verbatim_share: fracción de tokens del revisor cubierta por tiradas
            literales compartidas, en ``[0, 1]``.
        reviewer_tokens: tamaño del veredicto en palabras — pondera el agregado
            (un veredicto de dos líneas y otro de tres páginas no valen igual).
        author_tokens: idem para el relato del autor.
        echoed_conclusion: ``True``/``False``/``None``; ver el módulo.
        verdict: el veredicto tal cual, para poder segmentar el agregado.
        author_finish_status: la autoevaluación cruda del autor, o ``None``.
    """

    measured: bool
    phrase_overlap: float
    verbatim_share: float
    reviewer_tokens: int
    author_tokens: int
    echoed_conclusion: bool | None
    verdict: str
    author_finish_status: str | None

    def as_payload(self) -> dict[str, Any]:
        """Proyección JSON-segura para el evento de auditoría."""
        return {
            "metric_version": METRIC_VERSION,
            "measured": self.measured,
            "phrase_overlap": self.phrase_overlap,
            "verbatim_share": self.verbatim_share,
            "reviewer_tokens": self.reviewer_tokens,
            "author_tokens": self.author_tokens,
            "echoed_conclusion": self.echoed_conclusion,
            "verdict": self.verdict,
            "author_finish_status": self.author_finish_status,
        }


def _tokenise(text: str) -> list[str]:
    """El texto como lista de palabras normalizadas (ver :data:`_WORD_RE`)."""
    return _WORD_RE.findall(text.lower())[:MAX_TOKENS]


def _ngrams(tokens: list[str], size: int) -> set[tuple[str, ...]]:
    """Los n-gramas DISTINTOS de ``tokens``. Vacío si el texto no llega a uno."""
    if len(tokens) < size:
        return set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def _containment(source: list[str], reference: list[str], size: int) -> float:
    """Fracción de n-gramas de ``source`` presentes también en ``reference``.

    Dirigida a propósito (ver el docstring del módulo). ``0.0`` cuando
    ``source`` no da ni un n-grama — no hay nada que contener.
    """
    source_grams = _ngrams(source, size)
    if not source_grams:
        return 0.0
    reference_grams = _ngrams(reference, size)
    shared = sum(1 for gram in source_grams if gram in reference_grams)
    return shared / len(source_grams)


def _verbatim_coverage(source: list[str], reference: list[str], size: int) -> float:
    """Fracción de tokens de ``source`` dentro de alguna tirada literal compartida.

    Se marca cada posición cubierta por una ventana de ``size`` tokens que
    exista igual en ``reference``, y se devuelve la proporción cubierta. Mide
    superficie copiada, no número de coincidencias: dos tiradas largas y veinte
    cortas no deben puntuar igual.
    """
    if len(source) < size:
        return 0.0
    reference_grams = _ngrams(reference, size)
    if not reference_grams:
        return 0.0
    covered = bytearray(len(source))
    for start in range(len(source) - size + 1):
        if tuple(source[start : start + size]) in reference_grams:
            covered[start : start + size] = b"\x01" * size
    return sum(covered) / len(source)


def _echoed_conclusion(verdict: str, author_finish_status: str | None) -> bool | None:
    """¿Coincide el veredicto con lo que el autor dijo de sí mismo?

    ``None`` cuando falta la autoevaluación, cuando es ``partial`` (no se
    proyecta a un veredicto) o cuando el veredicto no es comparable.
    """
    if author_finish_status is None or verdict not in _COMPARABLE_VERDICTS:
        return None
    projected = _SELF_REPORT_TO_VERDICT.get(author_finish_status.strip().lower())
    if projected is None:
        return None
    return projected == verdict


def measure_review_contamination(
    *,
    reviewer_text: str,
    author_text: str,
    verdict: str,
    author_finish_status: str | None = None,
) -> ContaminationMetric:
    """Cuánto del veredicto del revisor venía ya en el relato del implementador.

    Args:
        reviewer_text: la salida del run de review (el veredicto y su prosa).
        author_text: la salida del run del implementador que se está juzgando.
        verdict: ``approve`` / ``reject`` / ``unknown``.
        author_finish_status: la autoevaluación del autor (``Execution.
            finish_status``), o ``None`` si no la reportó.

    Returns:
        Un :class:`ContaminationMetric`. Nunca lanza: es post-proceso de un
        veredicto YA aplicado, y romper aquí anularía una decisión correcta por
        un fallo de instrumentación.
    """
    reviewer_tokens = _tokenise(reviewer_text)
    author_tokens = _tokenise(author_text)
    measurable = len(reviewer_tokens) >= PHRASE_NGRAM and len(author_tokens) >= PHRASE_NGRAM

    phrase_overlap = 0.0
    verbatim_share = 0.0
    if measurable:
        phrase_overlap = round(_containment(reviewer_tokens, author_tokens, PHRASE_NGRAM), 4)
        verbatim_share = round(
            _verbatim_coverage(reviewer_tokens, author_tokens, VERBATIM_NGRAM), 4
        )

    return ContaminationMetric(
        measured=measurable,
        phrase_overlap=phrase_overlap,
        verbatim_share=verbatim_share,
        reviewer_tokens=len(reviewer_tokens),
        author_tokens=len(author_tokens),
        echoed_conclusion=_echoed_conclusion(verdict, author_finish_status),
        verdict=verdict,
        author_finish_status=author_finish_status,
    )


__all__ = [
    "MAX_TOKENS",
    "METRIC_VERSION",
    "PHRASE_NGRAM",
    "VERBATIM_NGRAM",
    "ContaminationMetric",
    "measure_review_contamination",
]
