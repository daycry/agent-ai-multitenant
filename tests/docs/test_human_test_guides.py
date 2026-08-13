"""Las guías de tests humanos están completas y son **usables**.

Acredita los cuatro ítems del test humano `human_htg_01` del plan
`docs-human-test-guides`:

  1. hay una guía por cada plan **del alcance declarado del plan**;
  2. cada guía «pendiente» lista precondiciones + pasos + resultado esperado por
     cada `human_*`;
  3. el índice README enlaza todas las guías, sin enlaces rotos;
  4. las guías referencian los setup scripts correctos donde existen.

Solo lee Markdown y el listado de `scripts/`: sin BD, sin red, sin Docker.

## Ítem 1: el alcance honesto, y por qué NO es «todos los planes»

Leído al pie de la letra, el ítem 1 («una guía por cada plan con bloque de tests
humanos») **falla hoy**: 59 planes del roadmap tienen bloque `human_*` y solo hay
31 guías. Pero esos 28 sin guía no son un incumplimiento del plan: son planes que
o se escribieron DESPUÉS (`prod-01`…`prod-18`, las remediaciones, el córtex) o
estaban fuera de su alcance. El plan enumera su alcance de forma cerrada en
§Alcance («Entra»), y las 31 guías que existen son exactamente esas 31.

Así que el test exige lo que el plan prometió — las 14 de prioridad + las 6 de
completitud + las 11 preexistentes — y no lo que nadie prometió. Forzar el verde
sobre la lectura literal habría pedido inventar 28 guías; declararlo rojo habría
dejado un rojo permanente que nadie puede cerrar. La **deuda residual** (28 planes
con bloque humano y sin guía) se reporta como hallazgo, no se esconde aquí; y
`test_every_guide_maps_to_a_real_roadmap_plan` impide la deriva en el otro sentido
(una guía huérfana de plan).

## Ítem 4: la trampa de las menciones NEGATIVAS

Un `grep setup_demo_` sobre las guías encuentra 16 nombres de script que NO
existen en `scripts/`, y sería tentador leerlo como «16 guías apuntan a scripts
inexistentes». Es al revés: **las 19 menciones a scripts inexistentes son frases
negativas deliberadas** («El Plan 00 **no tiene** `scripts/setup_demo_00.py`», «No
hay `setup_demo_11_2.py` ni launcher dedicado»), que es justo la información que
el operador necesita para no buscar un launcher que no hay.

Por eso el test clasifica cada mención por la frase que la contiene y solo exige
existencia a las **recomendaciones afirmativas**. La clasificación tiene sus
propios tests con entradas sintéticas en los dos sentidos, para que un fallo del
clasificador no se disfrace de verde.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROADMAP = _REPO_ROOT / "docs" / "roadmap"
_GUIDES = _REPO_ROOT / "docs" / "03-guides" / "human-tests"
_INDEX = _GUIDES / "README.md"
_SCRIPTS = _REPO_ROOT / "scripts"

# ---------------------------------------------------------------------------
# Alcance declarado del plan (§Alcance «Entra» + las 11 preexistentes del
# §Resumen). Los stems coinciden con el nombre del fichero de roadmap y de guía.
# ---------------------------------------------------------------------------

#: «Prioridad (pending_human_validation)» — las creó la Fase A del plan.
_PRIORITY_GUIDES: tuple[str, ...] = (
    "07-documentacion-visor",
    "08-sso-empresarial",
    "09-marketplace",
    "09.1-marketplace-seed-publish",
    "10-asistente-personal",
    "11-guardrails-precios",
    "11.2-llm-provider-admin-ui",
    "12-backup-restore",
    "13-api-publica-webhooks",
    "14-evals-estadisticas",
    "15-instalador-produccion",
    "16-human-agents",
    "06.15-agent-tools-assignment-ui",
    "06.16-polyglot-tool-catalog",
)

#: «Completitud (completed sin guía)» — Fase B, `task_htg_05`.
_COMPLETENESS_GUIDES: tuple[str, ...] = (
    "00-fundaciones",
    "01-dominio-minimo",
    "03-chat-planning-aprobacion",
    "06.10-kb-categories",
    "06.11-kb-ingestion-fixes",
    "06.13-kb-catalog-content",
)

#: Las 11 que ya existían cuando se escribió el plan (§Resumen).
_PREEXISTING_GUIDES: tuple[str, ...] = (
    "02-ejecucion-agentes",
    "04-memoria-rag-kbs",
    "04.5-agent-runtime-integration",
    "05-mcp-tools-avanzadas",
    "06-testing-revision-git",
    "06.6-admin-ui-gaps",
    "06.7-memory-dedup",
    "06.8-rbac-enforcement",
    "06.9-agent-scoped-kbs",
    "06.12-global-catalog-consistency",
    "06.14-hardening-auditoria",
)

_IN_SCOPE = _PRIORITY_GUIDES + _COMPLETENESS_GUIDES + _PREEXISTING_GUIDES

#: `- id: human_xxx` en el bloque «Tests humanos del Plan» de un roadmap.
_HUMAN_ID_RE = re.compile(r"^\s*-\s*id:\s*(human_[A-Za-z0-9_.]+)", re.MULTILINE)

#: `## human_xxx` (con o sin backticks) en una guía.
_GUIDE_SECTION_RE = re.compile(r"^##\s+`?(human_[A-Za-z0-9_.]+)`?", re.MULTILINE)

#: El triple que el ítem 2 exige por cada `human_*`.
_TRIPLE = ("**Precondiciones**", "**Pasos**", "**Resultado esperado**")

#: Menciones a un script de setup, con o sin `scripts/` (o `scripts\`) delante.
_SCRIPT_RE = re.compile(r"(setup_demo_[A-Za-z0-9_]*\.py)")

#: Marcadores de frase negativa («este plan NO tiene tal script»).
_NEGATIONS = ("no tiene", "no hay", "no existe", "no lleva", "no dispone", "tampoco")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    """Minúsculas sin acentos, para que 'No hay' y 'no habría' comparen igual."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# ---------------------------------------------------------------------------
# Clasificador de menciones a scripts (con sus propios tests, más abajo)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptMention:
    """Una mención a un `setup_demo_*.py` y si es una RECOMENDACIÓN."""

    script: str
    affirmative: bool
    sentence: str


def script_mentions(text: str) -> list[ScriptMention]:
    """Todas las menciones a `setup_demo_*.py`, clasificadas.

    Trabaja por párrafo y aplana los saltos de línea antes de partir en frases:
    las guías van envueltas a ~72 columnas, así que la negación («no tiene»)
    aparece a menudo en una línea distinta de la del script. Una mención es
    AFIRMATIVA cuando la frase que la contiene no lleva marcador de negación.
    """
    out: list[ScriptMention] = []
    for paragraph in re.split(r"\n\s*\n", text):
        flat = re.sub(r"\s+", " ", paragraph).strip()
        for match in _SCRIPT_RE.finditer(flat):
            before = flat[: match.start()]
            boundary = max(before.rfind(". "), before.rfind("! "), before.rfind("? "))
            sentence = before[boundary + 1 :] if boundary != -1 else before
            normalized = _norm(sentence)
            negated = any(marker in normalized for marker in _NEGATIONS)
            out.append(
                ScriptMention(
                    script=match.group(1),
                    affirmative=not negated,
                    sentence=sentence.strip()[-120:],
                )
            )
    return out


def _existing_setup_scripts() -> set[str]:
    return {p.name for p in _SCRIPTS.glob("setup_demo_*.py")}


def _guide_stems() -> set[str]:
    return {p.stem for p in _GUIDES.glob("*.md") if p.name.lower() != "readme.md"}


# ===========================================================================
# Ítem 1 — una guía por cada plan del alcance declarado
# ===========================================================================


def test_scope_constants_are_consistent() -> None:
    """La guarda no puede pasar vacíamente: el alcance tiene que estar poblado."""
    assert len(_IN_SCOPE) == 31, f"el alcance declarado del plan son 31 planes, no {len(_IN_SCOPE)}"
    assert len(set(_IN_SCOPE)) == len(_IN_SCOPE), "hay planes repetidos en el alcance"


@pytest.mark.parametrize("plan", _IN_SCOPE)
def test_in_scope_plan_has_a_guide(plan: str) -> None:
    guide = _GUIDES / f"{plan}.md"
    assert guide.is_file(), (
        f"falta docs/03-guides/human-tests/{plan}.md, que el plan "
        "docs-human-test-guides declara en su §Alcance"
    )
    assert len(_read(guide)) > 500, f"{plan}.md existe pero está prácticamente vacía"


@pytest.mark.parametrize("plan", _IN_SCOPE)
def test_in_scope_plan_exists_in_the_roadmap(plan: str) -> None:
    assert (_ROADMAP / f"{plan}.md").is_file(), (
        f"la guía {plan}.md no corresponde a ningún plan de docs/roadmap/"
    )


def test_every_guide_maps_to_a_real_roadmap_plan() -> None:
    """Deriva en el otro sentido: una guía huérfana de plan es documentación muerta."""
    stems = _guide_stems()
    assert stems, "no se encontró ninguna guía en docs/03-guides/human-tests/"
    orphans = sorted(s for s in stems if not (_ROADMAP / f"{s}.md").is_file())
    assert not orphans, f"guías sin plan correspondiente en docs/roadmap/: {orphans}"


# ===========================================================================
# Ítem 2 — precondiciones + pasos + resultado esperado por cada human_*
# ===========================================================================


@pytest.mark.parametrize("plan", _PRIORITY_GUIDES)
def test_priority_guide_covers_every_human_id_with_the_full_triple(plan: str) -> None:
    """Las guías «pendientes» (Fase A) llevan el triple por cada `human_*`.

    Los `human_*` no se listan a mano: se leen del bloque «Tests humanos del
    Plan» del propio roadmap, así que un test humano nuevo en el plan sale rojo
    aquí hasta que la guía lo cubra.
    """
    plan_text = _read(_ROADMAP / f"{plan}.md")
    human_ids = _HUMAN_ID_RE.findall(plan_text)
    assert human_ids, f"el roadmap {plan}.md no declara ningún human_* (¿cambió el formato?)"

    guide_text = _read(_GUIDES / f"{plan}.md")
    sections = {m.group(1): m for m in _GUIDE_SECTION_RE.finditer(guide_text)}

    problems: list[str] = []
    for human_id in human_ids:
        match = sections.get(human_id)
        if match is None:
            problems.append(f"{human_id}: la guía no tiene sección '## {human_id}'")
            continue
        nxt = guide_text.find("\n## ", match.end())
        body = guide_text[match.end() : nxt if nxt != -1 else len(guide_text)]
        for marker in _TRIPLE:
            if marker not in body:
                problems.append(f"{human_id}: falta {marker}")
    assert not problems, f"guía {plan}.md incompleta: {problems}"


# ===========================================================================
# Ítem 3 — el índice enlaza todas las guías, sin enlaces rotos
# ===========================================================================


def test_index_links_every_guide() -> None:
    index = _read(_INDEX)
    missing = sorted(f"{s}.md" for s in _guide_stems() if f"{s}.md" not in index)
    assert not missing, f"human-tests/README.md no lista estas guías de la carpeta: {missing}"


def test_index_has_no_broken_links() -> None:
    index = _read(_INDEX)
    targets = [
        t
        for t in re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", index)
        if not t.startswith(("http://", "https://", "mailto:", "#"))
    ]
    assert len(targets) >= len(_guide_stems()), (
        f"el índice solo tiene {len(targets)} enlaces internos para "
        f"{len(_guide_stems())} guías: el descubrimiento se rompió"
    )
    broken = sorted(t for t in targets if not (_INDEX.parent / t.split("#")[0]).resolve().is_file())
    assert not broken, f"enlaces rotos en human-tests/README.md: {broken}"


# ===========================================================================
# Ítem 4 — los setup scripts que se RECOMIENDAN existen
# ===========================================================================


def test_the_mention_classifier_reads_negative_sentences_as_negative() -> None:
    """Sin esto, el ítem 4 podría pasar por clasificar todo como negativo."""
    negatives = (
        "El Plan 00 **no tiene** `scripts/setup_demo_00.py` ni launcher\ndedicado.",
        "No hay `setup_demo_11_2.py` ni launcher dedicado para este plan.",
        "No existe un setup_demo_99.py para esto.",
        "Tampoco hay setup_demo_98.py.",
    )
    for text in negatives:
        mentions = script_mentions(text)
        assert len(mentions) == 1, f"no se detectó la mención en {text!r}"
        assert not mentions[0].affirmative, f"clasificada como recomendación: {text!r}"


def test_the_mention_classifier_reads_affirmative_sentences_as_affirmative() -> None:
    affirmatives = (
        ".\\.venv\\Scripts\\python.exe scripts\\setup_demo_project.py",
        "Ejecuta `setup_demo_06.py` — seedea dos bare repos remotos.",
        (
            "No hay `setup_demo_06_15.py` ni launcher dedicado para este plan. Los"
            " checklists reutilizan los sujetos de otros planes (los de"
            " `setup_demo_06_8.py`)."
        ),
    )
    expected_affirmative = ("setup_demo_project.py", "setup_demo_06.py", "setup_demo_06_8.py")
    for text, script in zip(affirmatives, expected_affirmative, strict=True):
        hits = [m for m in script_mentions(text) if m.script == script]
        assert hits, f"no se detectó {script} en {text!r}"
        assert hits[0].affirmative, (
            f"{script} clasificada como negativa en {text!r} — la segunda frase de"
            " un párrafo que empieza negando SÍ es una recomendación"
        )


def test_guides_only_recommend_setup_scripts_that_exist() -> None:
    existing = _existing_setup_scripts()
    assert existing, "no se encontró ningún scripts/setup_demo_*.py: el descubrimiento falló"

    affirmative = 0
    negative = 0
    offenders: list[str] = []
    for guide in sorted(_GUIDES.glob("*.md")):
        for mention in script_mentions(_read(guide)):
            if mention.affirmative:
                affirmative += 1
                if mention.script not in existing:
                    offenders.append(
                        f"{guide.name}: recomienda {mention.script} — «…{mention.sentence}»"
                    )
            else:
                negative += 1

    # §4 de verificar-antes-de-implementar: la guarda tiene que haber visto algo,
    # y de los DOS tipos — si el clasificador colapsara a «todo negativo», la
    # aserción de infractores pasaría vacíamente.
    assert affirmative >= 20, (
        f"solo {affirmative} recomendaciones afirmativas de setup script: el "
        "clasificador dejó de encontrarlas y este verde no significaría nada"
    )
    assert negative >= 10, (
        f"solo {negative} menciones negativas ('este plan no tiene launcher'): "
        "el clasificador dejó de distinguirlas"
    )
    # El mensaje va a una variable y no inline: como concatenación dentro del
    # `assert`, black y ruff-format la parten de formas distintas y se pelean en
    # bucle (docs/03-guides/gotchas/black-vs-ruff-format-chained-call-comment.md).
    detalle = "\n  ".join(offenders)
    assert not offenders, (
        f"guías que recomiendan un setup script que no existe en scripts/:\n  {detalle}"
    )
