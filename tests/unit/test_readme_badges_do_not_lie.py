"""Un badge de conteo del README no puede seguir diciendo un número que ya es falso.

El README es la primera cosa que lee alguien que llega. Si su primera pantalla
afirma «160 ADRs» y hay 174, o enlaza a un fichero que se renombró, el documento
entero pierde autoridad — y a diferencia de un badge vivo de shields.io, un
conteo escrito a mano **no se corrige solo**. Este fichero es la mitad que hace
que el número siga siendo cierto en noviembre.

Cubre las dos formas de mentir que tiene un README estático:

1. **Un conteo desfasado.** Cada badge de `_COUNTERS` declara su etiqueta y una
   función que cuenta lo mismo en el repositorio real. Si divergen, falla, y el
   mensaje dice exactamente qué número poner y en qué dos ficheros.
2. **Un enlace roto.** `tests/docs/test_docs_internal_links.py` barre `docs/**`,
   pero los README de la raíz caen **fuera de su radio** (y además enlazan a
   código y a carpetas, que aquella guarda excluye a propósito). Aquí se
   comprueba que todo destino relativo de los dos README existe en el árbol.

Los dos README se comprueban **a la vez y con la misma cifra**: la versión
castellana es una traducción, no una segunda fuente de verdad, y un contador que
sólo se actualiza en uno de los dos es el modo de fallo más probable.

## Por qué estos cuatro contadores y no el número de tests

Un badge cuyo guarda se rompe en cada commit acaba con el guarda debilitado, o
sea que se convierte en una fábrica de mentiras. El recuento de tests (9.536 el
2026-08-21) cambia con cualquier tarea, así que **no lleva badge**: el README da
el comando para medirlo en vez de un número que envejece en horas. Los cuatro que
sí llevan badge se mueven de uno en uno y con un commit que ya toca el sitio
donde está anotado.

## La aserción de «encontré algo»

Un contador que deja de encontrar ficheros —carpeta movida, glob roto— pasaría a
cero y el test sólo fallaría por el desajuste, que es un mensaje que despista.
Por eso cada contador declara un suelo de descubrimiento
(`docs/03-guides/verificar-antes-de-implementar.md` §4).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

pytestmark = pytest.mark.unit

# tests/unit/test_readme_badges_do_not_lie.py -> raíz del repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Los dos README de primer nivel. El inglés es el canónico (decisión del
#: operador del 2026-08-21); el castellano corre en paralelo.
_READMES: tuple[Path, ...] = (
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "README.es.md",
)


# ---------------------------------------------------------------------------
# Los contadores: cada uno mide en el repo lo que su badge afirma
# ---------------------------------------------------------------------------
def _count_adrs() -> int:
    """Documentos ADR: los ficheros `NNNN-*.md` de 05-architecture-decisions.

    Se cuentan FICHEROS, no números de ADR distintos: hay dos números duplicados
    (0053 y 0054, dos documentos cada uno) y uno ausente (0106), así que
    «el ADR más alto» y «cuántos ADR hay» no coinciden. El badge dice cuántos
    documentos hay, que es lo que se puede contar sin interpretar.
    """
    adrs = _REPO_ROOT / "docs" / "05-architecture-decisions"
    return len([p for p in adrs.glob("*.md") if re.match(r"^\d{4}-", p.name)])


def _count_migrations() -> int:
    """Revisiones de Alembic (un fichero .py por revisión)."""
    versions = _REPO_ROOT / "apps" / "api-server" / "migrations" / "versions"
    return len([p for p in versions.glob("*.py") if p.name != "__init__.py"])


def _count_test_runtimes() -> int:
    """Plantillas de test-runtime: la matriz que construye y publica CI.

    La fuente es el workflow, no `ls docker/agent-runtimes/`: esa carpeta
    contiene además `agent-runtime` (el sandbox del agente) y `browser-runtime`,
    que no son plantillas de ejecución de tests. Contar la carpeta daría 16 y el
    badge diría una cosa distinta de la que publica la release.
    """
    workflow = _REPO_ROOT / ".github" / "workflows" / "build-runtime-templates.yml"
    text = workflow.read_text(encoding="utf-8")
    marker = "        template:\n"
    assert marker in text, "la matriz `template:` del workflow cambió de forma"
    block = text.split(marker, 1)[1]
    entries: list[str] = []
    for line in block.splitlines():
        item = re.match(r"^          - ([a-z0-9-]+)\s*$", line)
        if item is None:
            break
        entries.append(item.group(1))
    return len(entries)


def _count_approval_categories() -> int:
    """Categorías de acción sensible con política de aprobación (principio 11)."""
    from shared_domain.approval_categories import APPROVAL_CATEGORIES

    return len(APPROVAL_CATEGORIES)


#: `(etiqueta del badge, cómo se cuenta de verdad, suelo de descubrimiento)`.
#:
#: La etiqueta es el texto EXACTO que precede al número dentro de la URL de
#: shields.io, ya percent-encoded (`%20` por espacio). Se busca así, y no por
#: el texto alt del badge, porque lo que se renderiza es la URL.
_COUNTERS: tuple[tuple[str, Callable[[], int], int], ...] = (
    ("ADRs", _count_adrs, 100),
    ("migrations", _count_migrations, 100),
    ("test%20runtimes", _count_test_runtimes, 10),
    ("gated%20action%20categories", _count_approval_categories, 10),
)

#: `https://img.shields.io/badge/<etiqueta>-<numero>-<color>.svg`
_BADGE_RE = r"img\.shields\.io/badge/{label}-(\d+)-"


def _readme_text(path: Path) -> str:
    assert path.is_file(), f"falta {path.name}: el README es el entregable, no un extra"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("label", "counter", "floor"), _COUNTERS, ids=lambda v: str(v)[:24])
def test_a_counter_badge_matches_what_the_repo_actually_has(
    label: str, counter: Callable[[], int], floor: int
) -> None:
    """El número del badge es el número real, y es el mismo en los dos README."""
    real = counter()
    assert real >= floor, (
        f"el contador de «{label}» sólo encontró {real} (suelo {floor}): el "
        "descubrimiento se rompió (carpeta movida o glob obsoleto), no es que "
        "hayan desaparecido. Arregla el contador antes de creerte el resto."
    )

    pattern = re.compile(_BADGE_RE.format(label=re.escape(label)))
    for readme in _READMES:
        found = pattern.findall(_readme_text(readme))
        assert found, (
            f"{readme.name} ya no tiene el badge de «{label}». Si se retiró a "
            "propósito, quita también su entrada de _COUNTERS; un contador sin "
            "badge vigila algo que nadie lee."
        )
        assert len(set(found)) == 1, (
            f"{readme.name} declara «{label}» con valores distintos: {sorted(set(found))}"
        )
        declared = int(found[0])
        assert declared == real, (
            f"{readme.name} dice «{label} = {declared}» y el repo tiene {real}. "
            f"Pon {real} en los DOS README (README.md y README.es.md) en el mismo "
            "commit que movió la cuenta."
        )


# ---------------------------------------------------------------------------
# La otra mitad: los conteos que van en PROSA, no en la URL de un badge
# ---------------------------------------------------------------------------
# El test de arriba sólo mira dentro de `img.shields.io/badge/<label>-<n>-`, así
# que la tabla «What makes it different» podía decir «199 ADRs» con el badge
# marcando 160 y la suite quedarse verde — comprobado rompiéndolo. Un número en
# prosa envejece exactamente igual que uno en un badge; lo que cambia es que
# nadie lo mira.
#
# El acoplamiento a la redacción es deliberado y va en las dos direcciones: si
# alguien reescribe la frase, la aserción de «encontré la frase» falla y le
# obliga a mover el patrón. Un patrón que deja de casar en silencio sería la
# misma mentira una capa más abajo.


def _count_policy_templates() -> int:
    """Plantillas de política de aprobación sembradas (principio 11)."""
    from api_server.seeds.builtin_approval_policies import BUILTIN_POLICIES

    return len({policy.slug for policy in BUILTIN_POLICIES})


#: `(qué se cuenta, cómo se cuenta, {README: patrón con un grupo numérico})`.
_PROSE_COUNTS: tuple[tuple[str, Callable[[], int], tuple[str, str]], ...] = (
    (
        "ADR en la tabla de diferenciadores",
        _count_adrs,
        (r"(\d+) ADRs, a precedence chain", r"(\d+) ADR, una cadena de precedencia"),
    ),
    (
        "imágenes de test-runtime",
        _count_test_runtimes,
        (r"(\d+) maintained test-runtime images", r"(\d+) imágenes de test-runtime mantenidas"),
    ),
    (
        "categorías de acción sensible",
        _count_approval_categories,
        (r"(\d+) categories of sensitive action", r"(\d+) categorías de acción sensible"),
    ),
    (
        "plantillas de política de aprobación",
        _count_policy_templates,
        # Sin el `×` que separa las dos cifras en la tabla: ruff lo marca
        # (RUF001, signo de multiplicación ambiguo) y silenciarlo con un `noqa`
        # para ganar un carácter no vale. `(Sandbox` ya hace único el patrón.
        (
            r"(\d+) policy templates \(Sandbox",
            r"(\d+) plantillas \(Sandbox",
        ),
    ),
)


@pytest.mark.parametrize(("what", "counter", "patterns"), _PROSE_COUNTS, ids=lambda v: str(v)[:32])
def test_a_number_written_in_the_readme_prose_is_also_the_real_one(
    what: str, counter: Callable[[], int], patterns: tuple[str, str]
) -> None:
    """El número que la prosa afirma coincide con el repositorio, en los dos idiomas."""
    real = counter()
    assert real > 0, f"el contador de «{what}» devolvió {real}: el descubrimiento se rompió"

    for readme, pattern in zip(_READMES, patterns, strict=True):
        found = re.findall(pattern, _readme_text(readme))
        assert found, (
            f"{readme.name}: la frase que afirma «{what}» ya no casa con "
            f"{pattern!r}. Si se reescribió, mueve el patrón en el mismo commit; "
            "un patrón que no casa deja el número sin vigilancia."
        )
        assert len(set(found)) == 1, f"{readme.name}: «{what}» con valores distintos: {found}"
        declared = int(found[0])
        assert declared == real, (
            f"{readme.name} dice «{what} = {declared}» en prosa y el repo tiene "
            f"{real}. El badge y la prosa se actualizan JUNTOS: un README que se "
            "contradice consigo mismo no vale más que uno desfasado."
        )


# ---------------------------------------------------------------------------
# Enlaces: los README de la raíz caen fuera de tests/docs/test_docs_internal_links
# ---------------------------------------------------------------------------
#: `[texto](destino)` que no sea imagen (`(?<!!)` descarta `![alt](src)`).
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: Suelo de descubrimiento del barrido de enlaces por README.
_MIN_LINKS_PER_README = 20


@pytest.mark.parametrize("readme", _READMES, ids=lambda p: p.name)
def test_every_relative_link_in_the_readme_resolves(readme: Path) -> None:
    """Ningún enlace relativo del README apunta a algo que no existe.

    Se aceptan destinos a fichero y a carpeta: el README enlaza a
    `apps/api-server/migrations/versions` y a `docker/agent-runtimes`, que en
    GitHub son travesías de árbol perfectamente válidas.
    """
    text = _readme_text(readme)
    targets = _LINK_RE.findall(text)
    assert len(targets) >= _MIN_LINKS_PER_README, (
        f"{readme.name}: el barrido sólo vio {len(targets)} enlaces "
        f"(suelo {_MIN_LINKS_PER_README}). El regex dejó de casar; su verde no "
        "significaría nada."
    )

    checked = 0
    broken: list[str] = []
    for raw in targets:
        if urlparse(raw).scheme or raw.startswith(("#", "//")):
            continue  # URL absoluta, mailto o ancla pura: no es travesía local.
        path_part = unquote(raw.split("#", 1)[0])
        if not path_part:
            continue
        checked += 1
        if not (_REPO_ROOT / path_part).exists():
            broken.append(raw)

    assert checked >= 15, (
        f"{readme.name}: sólo {checked} destinos relativos comprobados. El "
        "README dejó de enlazar al repositorio, o el filtro se rompió."
    )
    assert not broken, f"{readme.name}: enlaces relativos roscados a la nada: {sorted(broken)}"


#: Cuántas líneas de cabecera cuentan como «la primera línea» del documento.
#: El selector de idioma va inmediatamente bajo el H1; buscarlo en todo el
#: fichero no serviría, porque los dos README se citan también más abajo (en
#: §«Documentation language»), y un enlace enterrado a mitad de página no es un
#: selector de idioma: el lector que necesita el otro idioma se ha ido antes.
_HEADER_LINES = 5


@pytest.mark.parametrize(
    ("readme", "expected_link"),
    ((_READMES[0], "(README.es.md)"), (_READMES[1], "(README.md)")),
    ids=("en->es", "es->en"),
)
def test_the_language_switcher_sits_in_the_readme_header(readme: Path, expected_link: str) -> None:
    """El selector de idioma está en la cabecera, en los dos sentidos.

    El inglés es canónico; el castellano corre en paralelo. Si uno pierde el
    enlace al otro en la cabecera, la mitad de los lectores no encuentra su
    idioma — que es justo lo que la decisión bilingüe venía a resolver.
    """
    header = "\n".join(_readme_text(readme).splitlines()[:_HEADER_LINES])
    assert expected_link in header, (
        f"{readme.name} no lleva `{expected_link}` en sus primeras "
        f"{_HEADER_LINES} líneas. El selector de idioma va bajo el H1; más abajo "
        "no lo ve quien vino buscando su idioma."
    )


def test_no_readme_badge_claims_a_workflow_that_does_not_exist() -> None:
    """Un badge de estado sólo puede nombrar un workflow que está en el repo.

    Un badge de `actions/workflows/<x>.yml/badge.svg` cuyo fichero no existe
    renderiza un error en la primera pantalla del proyecto. Y el mismo riesgo lo
    corre un workflow que se renombra: el badge sobrevive al fichero.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    on_disk = {p.name for p in workflows_dir.glob("*.yml")}
    assert len(on_disk) >= 3, f"sólo {len(on_disk)} workflows encontrados: ¿se movió la carpeta?"

    badge_re = re.compile(r"actions/workflows/([A-Za-z0-9._-]+\.yml)/badge\.svg")
    seen = 0
    for readme in _READMES:
        named = badge_re.findall(_readme_text(readme))
        assert named, f"{readme.name} no declara ningún badge de estado de CI"
        seen += len(named)
        missing = sorted(set(named) - on_disk)
        assert not missing, (
            f"{readme.name} muestra el estado de {missing}, que no existe en "
            f".github/workflows/. Los que hay: {sorted(on_disk)}"
        )
    assert seen >= 2, "la guarda dejó de encontrar badges de workflow en los README"


def test_no_readme_badge_advertises_downloads_or_unpublished_coverage() -> None:
    """Nada publicado todavía: un badge de descargas o de cobertura mentiría.

    Ni el SDK de PyPI (`agentic-platform-sdk`) ni el de npm
    (`@agentic-platform/sdk`) están publicados, no hay releases ni imágenes en
    ghcr, y no hay servicio de cobertura conectado (CI aplica un suelo de
    ratchet local, ver `ci.yml`, job `test-unit`). Un badge de cualquiera de esas
    cosas renderizaría «not found» o un número que nadie publica.

    Si algún día se publica de verdad, este test es el sitio donde consta la
    condición: se retira la prohibición que corresponda **y se comprueba que el
    paquete existe**, no al revés.
    """
    forbidden = (
        "shields.io/pypi/dm",
        "shields.io/pypi/dw",
        "shields.io/pypi/dd",
        "shields.io/npm/dm",
        "shields.io/npm/dw",
        "shields.io/npm/dt",
        "shields.io/packagist/dt",
        "shields.io/github/downloads",
        "codecov.io",
        "coveralls.io",
        "shields.io/coveralls",
        "shields.io/codecov",
    )
    for readme in _READMES:
        text = _readme_text(readme)
        hits = [needle for needle in forbidden if needle in text]
        assert not hits, (
            f"{readme.name} muestra {hits}. Nada de eso está publicado, así que "
            "el badge renderizaría un error. Si ya se publicó, retira la entrada "
            "de este test y deja escrito dónde se comprobó."
        )
