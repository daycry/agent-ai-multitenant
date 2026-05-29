"""Language validator for canonical ``/docs`` Markdown (task_07_04).

Plan 07 Fase A enforces that a doc's *body* is written in the language its
frontmatter declares. The project scope (CLAUDE.md principle 12) supports
**Spanish and English only**, so this validator is a binary es-vs-en
classifier plus a thin checker that compares the detected language to the
frontmatter ``docs_language`` and reports confident mismatches.

Design constraints (from the task brief):

  * **Dependency-light + offline + deterministic.** No heavy ML / no
    network. The classifier is a stopword-frequency heuristic: count how
    many tokens of the body are high-frequency Spanish vs English
    function words, and pick the larger share. Stopwords are *disjoint*
    between the two languages here on purpose (we dropped ambiguous
    cognates like "no"/"a"/"e") so a hit is an unambiguous signal.
  * **Tolerant of code-heavy / short docs.** Markdown fenced code blocks,
    inline code, links/URLs and the YAML frontmatter are stripped before
    counting, because code identifiers and English-keyword-heavy snippets
    would otherwise drown out short prose. A doc that does not carry
    enough prose signal is reported as :data:`Language.UNKNOWN` and the
    checker treats it as a *pass* — we only ever flag a **confident**
    mismatch, never a guess.

This module mirrors :mod:`api_server.docs_structure.validator`:

  * pure functions, no I/O in the core (``detect_doc_language`` /
    ``validate_doc_language`` take text);
  * a file-level convenience (``check_doc_file``) for the CI gate;
  * the supported-language set lives in ONE place
    (:data:`SUPPORTED_LANGUAGES`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
import yaml

_log = structlog.get_logger("api_server.docs_structure.language")


class Language(str, Enum):
    """The languages this validator can decide between.

    ``ES`` / ``EN`` are the only *supported* doc languages (CLAUDE.md
    principle 12). ``UNKNOWN`` is the honest verdict for content that does
    not carry enough prose signal to classify (code-heavy or very short
    docs) — the checker never flags ``UNKNOWN`` as a mismatch.
    """

    ES = "es"
    EN = "en"
    UNKNOWN = "unknown"


#: The languages a doc may declare in ``docs_language``. Single source of
#: truth for "what is a supported language" across this module.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({Language.ES.value, Language.EN.value})

#: Frontmatter keys carrying the declared language, in priority order.
#: ``docs_language`` is the corpus-wide convention (and the roadmap plan
#: frontmatter key); ``lang`` is the alias the bootstrap README stub emits
#: (:func:`api_server.docs_structure.bootstrap._render_readme_stub`).
LANGUAGE_FRONTMATTER_KEYS: tuple[str, ...] = ("docs_language", "lang")

# --- detection thresholds (named, not magic) -------------------------------

#: Minimum number of stopword *hits* (es + en combined) below which we
#: refuse to classify — too little prose signal. Short docs and code-only
#: pages fall here and are tolerated (reported UNKNOWN ⇒ checker passes).
MIN_STOPWORD_HITS = 3

#: The winning language must hold at least this share of the combined
#: es+en stopword hits to be confident. 0.65 means a clear majority; a
#: near-even split (mixed-language or borderline) yields UNKNOWN.
MIN_CONFIDENCE = 0.65

#: Mismatch guard for bilingual / code-heavy docs. A real mislabel means
#: the declared language is essentially *absent* from the body. When the
#: declared language still holds at least this share of the es+en hits the
#: doc is genuinely bilingual (e.g. a Spanish roadmap that quotes lots of
#: English code/spec text) — present in force, not mislabelled — so the
#: checker does NOT flag it. Below this floor the declared language is too
#: scarce to be the body's language and a confident other-language verdict
#: is reported. This is the "only flag confident mismatches" guardrail.
MISMATCH_DECLARED_FLOOR = 0.20


# High-frequency Spanish function words that are NOT also common English
# words. Kept deliberately disjoint from the English set below so every
# hit is an unambiguous signal for one language.
_ES_STOPWORDS: frozenset[str] = frozenset(
    {
        "el",
        "los",
        "las",
        "una",
        "unos",
        "unas",
        "del",
        "al",
        "y",
        "o",
        "pero",
        "porque",
        "como",
        "cuando",
        "donde",
        "que",
        "qué",
        "quien",
        "cual",
        "esto",
        "esta",
        "este",
        "estos",
        "estas",
        "eso",
        "esa",
        "ese",
        "con",
        "sin",
        "por",
        "para",
        "sobre",
        "entre",
        "hasta",
        "desde",
        "hacia",
        "según",
        "su",
        "sus",
        "mi",
        "tu",
        "nuestro",
        "nuestra",
        "se",
        "le",
        "les",
        "lo",
        "más",
        "muy",
        "también",
        "tambien",
        "pueden",
        "puede",
        "debe",
        "deben",
        "tiene",
        "tienen",
        "hace",
        "hacer",
        "ser",
        "está",
        "están",
        "estan",
        "son",
        "fue",
        "será",
        "cada",
        "todo",
        "todos",
        "todas",
        "ello",
        "así",
        "asi",
        "ademas",
        "además",
        "mediante",
        "dentro",
        "fuera",
        "sino",
        "aunque",
    }
)

# High-frequency English function words that are NOT also common Spanish
# words. ("a", "no", "e", "as", "son" overlap with Spanish and are
# excluded on both sides.)
_EN_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "of",
        "and",
        "to",
        "in",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "for",
        "with",
        "without",
        "from",
        "this",
        "that",
        "these",
        "those",
        "which",
        "what",
        "when",
        "where",
        "while",
        "who",
        "whom",
        "whose",
        "they",
        "them",
        "their",
        "there",
        "here",
        "it",
        "its",
        "we",
        "our",
        "ours",
        "you",
        "your",
        "yours",
        "he",
        "she",
        "his",
        "her",
        "but",
        "because",
        "however",
        "therefore",
        "should",
        "would",
        "could",
        "must",
        "can",
        "may",
        "might",
        "will",
        "shall",
        "have",
        "has",
        "had",
        "does",
        "did",
        "doing",
        "about",
        "above",
        "below",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "each",
        "every",
        "any",
        "some",
        "many",
        "much",
        "more",
        "most",
        "such",
        "only",
        "also",
        "than",
        "then",
        "both",
        "either",
        "neither",
    }
)

# Ensure the two sets stay disjoint — an overlap would make a token count
# for both languages and corrupt the ratio. Caught at import time.
assert not (_ES_STOPWORDS & _EN_STOPWORDS), (
    "es/en stopword sets must be disjoint: " f"{sorted(_ES_STOPWORDS & _EN_STOPWORDS)}"
)


@dataclass(frozen=True)
class LanguageDetection:
    """Result of :func:`detect_doc_language`.

    ``language`` is the verdict (``ES``/``EN``/``UNKNOWN``).
    ``confidence`` is the winning language's share of combined es+en
    stopword hits in ``[0.0, 1.0]`` (``0.0`` when UNKNOWN for lack of
    signal). ``es_hits`` / ``en_hits`` expose the raw counts for tests and
    debug logging.
    """

    language: Language
    confidence: float
    es_hits: int
    en_hits: int


@dataclass(frozen=True)
class LanguageMismatch:
    """A confident body/declared-language disagreement found by the checker.

    ``declared`` is the ``docs_language`` from the frontmatter; ``detected``
    is the classifier verdict; ``confidence`` is the detection confidence;
    ``message`` is a human-readable line for a PR comment / CI log.
    """

    declared: str
    detected: Language
    confidence: float
    message: str


@dataclass(frozen=True)
class LanguageCheckResult:
    """Outcome of the checker: ok flag + optional mismatch + the detection.

    ``ok`` is ``True`` when there is no confident mismatch. A doc with no
    declared language, an unsupported declared language, or an
    inconclusive (``UNKNOWN``) body all pass — the validator only ever
    reports a *confident* contradiction. ``mismatch`` is set iff
    ``ok`` is ``False``.
    """

    declared: str | None
    detection: LanguageDetection
    mismatch: LanguageMismatch | None = field(default=None)

    @property
    def ok(self) -> bool:
        return self.mismatch is None


# --- markdown / frontmatter stripping --------------------------------------

_FRONTMATTER_RE = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INDENTED_CODE_RE = re.compile(r"(?m)^(?: {4}|\t).*$")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Markdown link/image: keep the visible [text], drop the (url) target so a
# URL's English-looking path segments do not pollute the prose count.
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
_BARE_URL_RE = re.compile(r"https?://\S+")
_WORD_RE = re.compile(r"[a-záéíóúñü]+", re.IGNORECASE | re.UNICODE)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a Markdown document into ``(frontmatter, body)``.

    The frontmatter is the YAML block delimited by ``---`` fences at the
    very start of the file (an optional leading BOM is tolerated). When no
    frontmatter is present the first element is ``""`` and the body is the
    whole input.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return "", text
    return match.group(1), text[match.end() :]


def parse_declared_language(frontmatter: str) -> str | None:
    """Extract the declared language from a YAML frontmatter block.

    Looks up :data:`LANGUAGE_FRONTMATTER_KEYS` in priority order and
    returns the value lower-cased and stripped, or ``None`` when no key is
    present (or the YAML is unparseable / not a mapping). Parsing never
    raises — a malformed frontmatter simply yields ``None`` so the checker
    treats the doc as "no declaration" rather than crashing CI.
    """
    if not frontmatter.strip():
        return None
    try:
        data: Any = yaml.safe_load(frontmatter)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    for key in LANGUAGE_FRONTMATTER_KEYS:
        value = data.get(key)
        if value is None:
            continue
        return str(value).strip().lower()
    return None


def _strip_markdown_noise(body: str) -> str:
    """Remove fenced/indented/inline code, link targets and bare URLs.

    These carry programming identifiers and English keywords that are not
    prose and would bias the classifier; stripping them is what makes the
    detector tolerant of code-heavy docs.
    """
    body = _FENCED_CODE_RE.sub(" ", body)
    body = _INDENTED_CODE_RE.sub(" ", body)
    body = _INLINE_CODE_RE.sub(" ", body)
    body = _LINK_TARGET_RE.sub("] ", body)
    body = _BARE_URL_RE.sub(" ", body)
    return body


def detect_doc_language(text: str) -> LanguageDetection:
    """Classify the prose language of a Markdown document as es / en / unknown.

    The frontmatter and Markdown code/link noise are stripped first, then
    word tokens are counted against the disjoint Spanish and English
    stopword sets. The verdict is:

      * :data:`Language.UNKNOWN` when the combined stopword-hit count is
        below :data:`MIN_STOPWORD_HITS` (not enough prose signal) or the
        winning share is below :data:`MIN_CONFIDENCE` (too close to call /
        mixed language);
      * :data:`Language.ES` / :data:`Language.EN` otherwise, for the
        majority side.

    Args:
        text: The full Markdown document (frontmatter + body), or just a
            body — either works, the frontmatter is stripped if present.

    Returns:
        A :class:`LanguageDetection` with the verdict, its confidence and
        the raw es/en hit counts.
    """
    _, body = split_frontmatter(text)
    prose = _strip_markdown_noise(body)

    es_hits = 0
    en_hits = 0
    for token in _WORD_RE.findall(prose.lower()):
        if token in _ES_STOPWORDS:
            es_hits += 1
        elif token in _EN_STOPWORDS:
            en_hits += 1

    total = es_hits + en_hits
    if total < MIN_STOPWORD_HITS:
        return LanguageDetection(Language.UNKNOWN, 0.0, es_hits, en_hits)

    if es_hits >= en_hits:
        winner, win_hits = Language.ES, es_hits
    else:
        winner, win_hits = Language.EN, en_hits
    confidence = win_hits / total

    if confidence < MIN_CONFIDENCE:
        return LanguageDetection(Language.UNKNOWN, confidence, es_hits, en_hits)
    return LanguageDetection(winner, confidence, es_hits, en_hits)


def _declared_share(declared: str | None, detection: LanguageDetection) -> float:
    """Share of the es+en stopword hits that belong to ``declared``.

    Returns ``1.0`` when there are no hits at all (a doc with zero prose
    signal can never be a confident mismatch — the caller's ``UNKNOWN``
    guard already short-circuits, but this keeps the ratio safe). Used by
    :func:`validate_doc_language` to spare genuinely bilingual / code-heavy
    docs where the declared language is present in force.
    """
    total = detection.es_hits + detection.en_hits
    if total == 0:
        return 1.0
    declared_hits = detection.es_hits if declared == Language.ES.value else detection.en_hits
    return declared_hits / total


def validate_doc_language(text: str) -> LanguageCheckResult:
    """Compare a doc's declared ``docs_language`` to its detected language.

    Steps:

      1. Split frontmatter, read the declared language
         (:func:`parse_declared_language`).
      2. Detect the body language (:func:`detect_doc_language`).
      3. Report a :class:`LanguageMismatch` **only** when all of:
         the declared language is one of :data:`SUPPORTED_LANGUAGES`; the
         detection is confident (not ``UNKNOWN``); the two differ; AND the
         declared language is essentially absent from the body — its share
         of the es+en hits is below :data:`MISMATCH_DECLARED_FLOOR`.

    A doc with no declaration, an unsupported declared language, an
    inconclusive body, or a genuinely bilingual / code-heavy body where
    the declared language is still present in force all pass (``ok`` stays
    ``True``) — the validator never flags a guess. This is the deliberate
    "only confident mismatches" contract from the task brief.

    Args:
        text: The full Markdown document (frontmatter + body).

    Returns:
        A :class:`LanguageCheckResult`.
    """
    frontmatter, _ = split_frontmatter(text)
    declared = parse_declared_language(frontmatter)
    detection = detect_doc_language(text)

    mismatch: LanguageMismatch | None = None
    if (
        declared in SUPPORTED_LANGUAGES
        and detection.language is not Language.UNKNOWN
        and detection.language.value != declared
        and _declared_share(declared, detection) < MISMATCH_DECLARED_FLOOR
    ):
        mismatch = LanguageMismatch(
            declared=declared,
            detected=detection.language,
            confidence=detection.confidence,
            message=(
                f"frontmatter declares docs_language={declared!r} but the body "
                f"reads as {detection.language.value!r} "
                f"(confidence {detection.confidence:.0%}, "
                f"es_hits={detection.es_hits}, en_hits={detection.en_hits})"
            ),
        )

    return LanguageCheckResult(declared=declared, detection=detection, mismatch=mismatch)


def check_doc_file(md_path: Path) -> LanguageCheckResult:
    """File-level convenience around :func:`validate_doc_language`.

    Reads ``md_path`` as UTF-8 and runs the validator. The single I/O
    boundary in this module so the CI gate can iterate over ``docs/**/*.md``
    while the core stays pure and trivially unit-testable.
    """
    text = md_path.read_text(encoding="utf-8")
    result = validate_doc_language(text)
    _log.info(
        "docs_structure.language_check",
        path=md_path.as_posix(),
        declared=result.declared,
        detected=result.detection.language.value,
        confidence=round(result.detection.confidence, 3),
        ok=result.ok,
    )
    return result


__all__ = [
    "LANGUAGE_FRONTMATTER_KEYS",
    "MIN_CONFIDENCE",
    "MIN_STOPWORD_HITS",
    "MISMATCH_DECLARED_FLOOR",
    "SUPPORTED_LANGUAGES",
    "Language",
    "LanguageCheckResult",
    "LanguageDetection",
    "LanguageMismatch",
    "check_doc_file",
    "detect_doc_language",
    "parse_declared_language",
    "split_frontmatter",
    "validate_doc_language",
]
