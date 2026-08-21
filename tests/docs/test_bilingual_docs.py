"""Guarda estática: un documento declarado bilingüe no puede quedarse a medias.

Decisión del operador del **2026-08-21**: la documentación nueva —README,
changelog, sitio publicado— sale en inglés Y castellano, con el **inglés como
canónico**. La convención y su porqué están en
[`docs/03-guides/bilingual-docs.md`](../../docs/03-guides/bilingual-docs.md):
``foo.md`` es el canónico inglés y ``foo.es.md`` su traducción castellana.

## Qué vigila esto, y por qué cada cosa

El modo de fallo de una política bilingüe no es que alguien la contradiga: es que
se aplique a medias y nadie se entere. Un par roto no da error en ninguna parte —
el fichero inglés renderiza igual de bien sin su hermano, y el enlace del lector
castellano se convierte en un 404 que sólo ve él. Así que:

* **Los documentos de la RAÍZ son bilingües**, sin lista que mantener: la regla es
  la ubicación, no un inventario de nombres. Eso hace que el README de otro carril
  entre en el radio el día que aterrice, sin coordinación previa; y que la lista
  que sí hay a mano —``_ROOT_MONOLINGUAL_BY_DESIGN``— sólo pueda encogerse por
  accidente, nunca crecer por accidente.
* **Ninguna traducción huérfana**: un ``X.es.md`` sin su ``X.md``. Ése es el estado
  que rompe los enlaces entrantes, porque el nombre desnudo es la dirección
  estable a la que apunta todo el corpus.
* **Las dos mitades se enlazan en la cabecera.** Sin eso, el bilingüismo existe
  para quien ya sabe que existe.
* **Las dos mitades tienen la misma estructura de encabezados.** Una traducción
  que pierde una sección miente por omisión, y es lo primero que pasa cuando se
  añade algo a una mitad y no a la otra.
* **Los enlaces internos de los documentos de la raíz resuelven.**
  ``test_docs_internal_links.py`` sólo recorre ``docs/``, así que un ``CHANGELOG.md``
  de la raíz que apunte a un documento movido no lo detectaba nadie.

## Por qué NO es un inventario congelado de pares

El patrón de esta casa para la deuda es el inventario congelado
(``_DECLARED_TEST_DEBT_2026_08_19``, ``_GATE_DEBT_2026_07_29``). Aquí no encaja
para los PARES, y encaja para las EXENCIONES, que es al revés de lo que parece:

* congelar los pares obligaría a editar este fichero cada vez que se traduce un
  documento, y una guarda que hay que tocar para hacer lo correcto se convierte en
  peaje. Los pares se **descubren**: el día que aparece ``foo.es.md``, ese par
  queda validado desde ese commit.
* congelar las exenciones sí sirve, porque son la única puerta por la que el
  bilingüismo puede dejar de aplicarse, y tienen que costar una línea escrita con
  su motivo.

El corpus existente —160 ADR, 108 gotchas, el roadmap, las siete carpetas
canónicas: 324 documentos con ``docs_language: es``— **no** está en el radio y no
lo estará en esta ola. Se hace bilingüe documento a documento con el ``git mv``
que describe la política, sin tocar un solo enlace entrante. Esta guarda no ladra
por él y no debe: una guarda que denuncia 324 ficheros el primer día se desactiva
el segundo.

## No-vacuidad

``test_the_discovery_finds_the_bilingual_pairs`` afirma sobre el APARATO (que hay
pares y que hay markdown en la raíz), no sobre el resultado. Sin ella, el día que
el descubrimiento se rompa —una carpeta excluida de más, el sufijo cambiado— los
demás tests pasarían vacíos y verdes para siempre
(``docs/03-guides/verificar-antes-de-implementar.md`` §4).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# tests/docs/test_bilingual_docs.py -> raíz del repo.
_ROOT = Path(__file__).resolve().parents[2]

#: Sufijo de la mitad castellana. El canónico inglés vive en el nombre desnudo.
_ES_SUFFIX = ".es.md"

#: Árboles que no son documentación del repo: dependencias vendorizadas, cachés,
#: entornos virtuales y salidas de herramientas.
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".venv-lock",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".backups",
        ".dev",
        "node_modules",
        "test-results",
        "vault-init-output",
        "__pycache__",
    }
)

#: Documentos de la raíz que NO son bilingües a propósito, con su motivo. Añadir
#: uno es una decisión, no un descuido: son ficheros de trabajo interno, no
#: documentación pública, y su único lector es quien ya trabaja en el repo.
_ROOT_MONOLINGUAL_BY_DESIGN: dict[str, str] = {
    "CLAUDE.md": (
        "contexto operativo del agente y cabeza de la cadena de precedencia. Lo"
        " edita el operador a mano y una traducción en paralelo abriría la puerta"
        " a que las dos mitades manden cosas distintas."
    ),
    "CONTINUE_HERE.md": (
        "estado de trabajo efímero (rama, bloqueos, qué espera al operador). Se"
        " reescribe a diario; traducirlo sería trabajo perdido cada día."
    ),
}

#: Sufijo inglés EXPLÍCITO, que **no** forma parte de la convención: el canónico
#: inglés vive en el nombre desnudo. Se le pone nombre aquí porque un carril lo
#: usó antes de que la convención estuviera escrita, y porque la forma de que no
#: se convierta en una segunda convención es tener un test que lo diga.
_EN_SUFFIX = ".en.md"

#: Desviaciones de nombre conocidas el 2026-08-21, con su arreglo escrito. Es un
#: inventario congelado (el patrón de `test_declared_tests_exist.py`): impide que
#: aparezca la siguiente y obliga a retirar la entrada cuando ésta se arregle.
#:
#: **Vacío, y lo estuvo el mismo día.** La única entrada era
#: `docs/01-overview/03-diagrams.en.md`, nacida en otro carril y en la misma
#: jornada con la forma `X.en.md` + `X.es.md`; se realineó a
#: `docs/01-overview/03-diagrams.md` antes de comitear, reapuntando los dos
#: enlaces entrantes (`docs/01-overview/README.md`,
#: `docs/01-overview/02-architecture.md`). Se deja el conjunto —y no se borra el
#: mecanismo— porque lo que impide una segunda convención no es que esta vez no
#: haya desviaciones, sino que aparecer en
#: `test_no_new_document_invents_a_second_naming_convention` obligue a declararla
#: aquí con su arreglo escrito.
#:
#: Por qué el nombre desnudo y no `X.en.md`, que parece más explícito: con
#: `X.en.md` + `X.es.md` el nombre desnudo NO EXISTE, así que traducir un
#: documento del corpus existente rompería todos sus enlaces entrantes — que es
#: justo el coste que esta convención evita. Y en la raíz no cabe: `README.md`
#: tiene que llamarse `README.md` para que GitHub lo renderice.
_NAMING_DEVIATIONS_2026_08_21: frozenset[str] = frozenset()

#: Pares cuyo enlace cruzado lo pone el SITIO y no el documento, con su motivo.
#:
#: `docs/index.md` es la home del sitio MkDocs, y su plugin de i18n
#: (`mkdocs.yml`, `mkdocs-static-i18n` en modo `suffix`) sirve las dos mitades
#: como la misma página con un selector de idioma en la cabecera. Ahí un enlace
#: `[Español](index.es.md)` no añade nada al lector Y arriesga el build: el sitio
#: se construye con `--strict`, y en modo `suffix` la mitad castellana no es una
#: página distinta a la que enlazar. La exención vale sólo mientras el documento
#: le diga al lector cómo cambiar de idioma, y eso lo comprueba
#: `test_the_site_home_tells_the_reader_where_the_language_selector_is`.
_CROSS_LINK_PROVIDED_BY_THE_SITE = frozenset({"docs/index.md"})

#: Cuántas líneas del cuerpo cuentan como «la cabecera» para el enlace cruzado.
#: No se exige antes del primer título: los tres carriles que lo escribieron el
#: mismo día lo pusieron en tres sitios (antes del H1, justo después, y en una
#: cita), y las tres son legibles. Lo que importa es que se vea sin buscarlo.
_HEADER_WINDOW = 12

#: La política que este fichero hace cumplir.
_POLICY = _ROOT / "docs" / "03-guides" / "bilingual-docs.md"

#: Enlace Markdown `[texto](destino)` que NO sea imagen (el `(?<!!)` descarta
#: `![alt](src)`). Mismo criterio que `test_docs_internal_links.py`.
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: Anclas de cita de fuente (`#L120`, `#L120-L130`): no son travesías de docs.
_LINE_ANCHOR_RE = re.compile(r"^L\d+(?:-L\d+)?$")

_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


# ---------------------------------------------------------------------------
# Descubrimiento
# ---------------------------------------------------------------------------
def _walk_markdown() -> list[Path]:
    """Todos los `.md` del repo, PODANDO los árboles excluidos al descender.

    Con `rglob` el barrido entra en `.venv/` y `node_modules/` y luego filtra:
    aquí eso costaba ~85 s, y una guarda que tarda más que la suite entera se
    acaba sacando de la suite.
    """
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_PARTS]
        for name in filenames:
            if name.endswith(".md"):
                out.append(Path(dirpath) / name)
    return sorted(out)


def _translations() -> list[Path]:
    """Todas las mitades castellanas (`*.es.md`) del repo."""
    return [p for p in _walk_markdown() if p.name.endswith(_ES_SUFFIX)]


def _canonical_of(translation: Path) -> Path:
    """`foo.es.md` -> `foo.md`, que es donde vive el canónico inglés."""
    return translation.with_name(translation.name[: -len(_ES_SUFFIX)] + ".md")


def _pairs() -> list[tuple[Path, Path]]:
    """Los pares (canónico, traducción) cuyas DOS mitades existen.

    Incluye las desviaciones de nombre conocidas (`X.en.md` + `X.es.md`): dejarlas
    fuera las libraría de TODOS los demás controles —enlace cruzado, estructura de
    encabezados—, que es peor que la desviación misma.
    """
    out = []
    for es in _translations():
        en = _canonical_of(es)
        if not en.is_file():
            deviation = es.with_name(es.name[: -len(_ES_SUFFIX)] + _EN_SUFFIX)
            if deviation.is_file():
                en = deviation
            else:
                continue
        out.append((en, es))
    return out


def _root_markdown() -> list[Path]:
    """Los `.md` del primer nivel del repo (no recorre carpetas)."""
    return sorted(p for p in _ROOT.glob("*.md") if p.is_file())


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
def _body_lines(path: Path) -> list[str]:
    """Las líneas del documento sin el frontmatter YAML."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return lines[index + 1 :]
    return lines


def _heading_levels(path: Path) -> list[int]:
    """Niveles de los encabezados, ignorando frontmatter y bloques cercados.

    Ignorar los bloques importa: los ejemplos de esta misma política llevan
    comentarios `#` dentro de un ``` que un parseo ingenuo leería como H1.
    """
    levels: list[int] = []
    in_fence = False
    for line in _body_lines(path):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match is not None:
            levels.append(len(match.group(1)))
    return levels


def _header_window(path: Path) -> str:
    """Las primeras líneas del cuerpo: donde tiene que verse el enlace cruzado."""
    return "\n".join(_body_lines(path)[:_HEADER_WINDOW])


def _document_links(path: Path) -> list[str]:
    """Destinos de enlace interno **a documento** (`.md`) del fichero."""
    out: list[str] = []
    for match in _LINK_RE.finditer(path.read_text(encoding="utf-8")):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#", "<")):
            continue
        path_part, _, anchor = target.partition("#")
        if not path_part or not path_part.endswith(".md"):
            continue
        if _LINE_ANCHOR_RE.match(anchor):
            continue
        out.append(path_part)
    return out


# ---------------------------------------------------------------------------
# No-vacuidad
# ---------------------------------------------------------------------------
def test_the_discovery_finds_the_bilingual_pairs() -> None:
    """Si el descubrimiento se rompe, todo lo de abajo pasa vacío y verde."""
    pairs = _pairs()
    assert len(pairs) >= 2, (
        "el descubrimiento de pares bilingües dejó de encontrar nada"
        f" (vio {len(pairs)}). ¿Cambió el sufijo `{_ES_SUFFIX}`, o se excluyó de"
        " más en `_EXCLUDED_PARTS`?"
    )
    roots = _root_markdown()
    assert len(roots) >= 3, (
        f"esperaba varios `.md` en la raíz del repo y encontré {len(roots)}:"
        f" {[p.name for p in roots]}"
    )


# ---------------------------------------------------------------------------
# La regla: la raíz es bilingüe
# ---------------------------------------------------------------------------
def test_every_root_markdown_document_is_bilingual() -> None:
    """Un documento público de la raíz sin su mitad castellana."""
    missing = [
        _rel(path)
        for path in _root_markdown()
        if not path.name.endswith(_ES_SUFFIX)
        and path.name not in _ROOT_MONOLINGUAL_BY_DESIGN
        and not path.with_name(path.stem + _ES_SUFFIX).is_file()
    ]
    assert not missing, (
        "documentos de la raíz sin su mitad castellana: "
        + ", ".join(missing)
        + f"\n\nEscribe el hermano `<nombre>{_ES_SUFFIX}` con el enlace cruzado en"
        " la cabecera, o —si de verdad tiene que quedarse monolingüe— añádelo a"
        " `_ROOT_MONOLINGUAL_BY_DESIGN` con el motivo escrito. La política está en"
        f" {_rel(_POLICY)}."
    )


def test_the_root_exemption_list_has_no_dead_entries() -> None:
    """Una exención sobre un fichero que ya no existe describe un mundo que no está."""
    dead = sorted(name for name in _ROOT_MONOLINGUAL_BY_DESIGN if not (_ROOT / name).is_file())
    assert not dead, (
        "estas exenciones de `_ROOT_MONOLINGUAL_BY_DESIGN` apuntan a ficheros que"
        f" ya no existen en la raíz: {dead}. Bórralas."
    )
    assert _ROOT_MONOLINGUAL_BY_DESIGN, (
        "la lista de exenciones se quedó vacía: si de verdad ya no hay ninguna,"
        " retira también este test; si no, es que el descubrimiento se rompió."
    )


def test_no_new_document_invents_a_second_naming_convention() -> None:
    """Un `X.en.md` es un canónico mal nombrado, no una segunda convención.

    Dos formas de nombrar lo mismo es exactamente lo que impide que el sitio
    publicado resuelva un par sin saber de antemano quién lo escribió.
    """
    found = {_rel(p) for p in _walk_markdown() if p.name.endswith(_EN_SUFFIX)}
    new = found - _NAMING_DEVIATIONS_2026_08_21
    assert not new, (
        "documentos con sufijo `.en.md`, que NO es la convención de este repo: "
        + ", ".join(sorted(new))
        + "\n\nEl canónico inglés vive en el nombre DESNUDO (`foo.md`) y el"
        f" castellano en `foo{_ES_SUFFIX}`. Con `foo{_EN_SUFFIX}` el nombre desnudo"
        " no existe, así que traducir un documento del corpus existente rompería"
        f" todos sus enlaces entrantes. Política: {_rel(_POLICY)}."
    )


def test_the_naming_deviation_inventory_has_no_dead_entries() -> None:
    """Una desviación ya arreglada que sigue declarada describe un mundo que no está."""
    dead = sorted(name for name in _NAMING_DEVIATIONS_2026_08_21 if not (_ROOT / name).is_file())
    assert not dead, (
        "estas desviaciones de nombre ya están arregladas; retíralas de"
        f" `_NAMING_DEVIATIONS_2026_08_21`: {dead}"
    )


def test_no_translation_is_orphaned() -> None:
    """Un `X.es.md` sin su `X.md` rompe el enlace cruzado de su propia cabecera."""
    orphans = [
        _rel(es)
        for es in _translations()
        if not _canonical_of(es).is_file()
        and not es.with_name(es.name[: -len(_ES_SUFFIX)] + _EN_SUFFIX).is_file()
    ]
    assert not orphans, (
        "traducciones sin su mitad canónica en inglés: "
        + ", ".join(orphans)
        + "\n\nEl nombre desnudo es la dirección estable del documento: sin él, el"
        " enlace cruzado de la traducción es un 404 y ningún enlace entrante"
        " resuelve."
    )


# ---------------------------------------------------------------------------
# La regla: las dos mitades se encuentran y dicen lo mismo
# ---------------------------------------------------------------------------
def test_both_halves_cross_link_each_other() -> None:
    """El bilingüismo que no se ve en la cabecera sólo existe para quien ya lo sabe."""
    offenders: list[str] = []
    for en, es in _pairs():
        if _rel(en) in _CROSS_LINK_PROVIDED_BY_THE_SITE:
            continue
        for half, sibling in ((en, es), (es, en)):
            header = _header_window(half)
            if f"]({sibling.name})" not in header and f"](./{sibling.name})" not in header:
                offenders.append(
                    f"{_rel(half)} -> no enlaza a {sibling.name} en sus primeras"
                    f" {_HEADER_WINDOW} líneas"
                )
    assert not offenders, "mitades sin enlace cruzado en la cabecera:\n  " + "\n  ".join(offenders)


def test_the_site_home_tells_the_reader_where_the_language_selector_is() -> None:
    """La exención del enlace cruzado vale mientras el documento diga cómo cruzar.

    Si mañana el sitio deja de servir el selector —o alguien reescribe la home sin
    mencionarlo— la exención se queda protegiendo un documento del que no se sale.
    """
    for rel in sorted(_CROSS_LINK_PROVIDED_BY_THE_SITE):
        page = _ROOT / rel
        assert page.is_file(), (
            f"`_CROSS_LINK_PROVIDED_BY_THE_SITE` nombra {rel}, que ya no existe. Retira la entrada."
        )
        text = page.read_text(encoding="utf-8")
        assert "language selector" in text or "selector de idioma" in text, (
            f"{rel} está exento del enlace cruzado porque el selector de idioma del"
            " sitio hace ese trabajo, y ya no lo menciona: o lo vuelve a decir, o"
            " lleva el enlace cruzado como los demás."
        )
        assert (_ROOT / "mkdocs.yml").is_file(), (
            "la exención se apoya en el sitio MkDocs y `mkdocs.yml` no está: sin"
            f" sitio no hay selector, así que {rel} necesita su enlace cruzado."
        )


def test_both_halves_have_the_same_heading_structure() -> None:
    """Una traducción que pierde una sección miente por omisión.

    Se compara el recuento de encabezados POR NIVEL, no la secuencia: una
    traducción fiel tiene el mismo esqueleto, y comparar el orden exacto añadiría
    fragilidad sin cazar nada que esto no cace.
    """
    offenders: list[str] = []
    for en, es in _pairs():
        counts_en = {level: _heading_levels(en).count(level) for level in range(1, 7)}
        counts_es = {level: _heading_levels(es).count(level) for level in range(1, 7)}
        if counts_en != counts_es:
            diff = {
                f"H{level}": (counts_en[level], counts_es[level])
                for level in range(1, 7)
                if counts_en[level] != counts_es[level]
            }
            offenders.append(f"{_rel(en)} vs {_rel(es)}: {diff} (inglés, castellano)")
    assert not offenders, (
        "pares cuyas dos mitades no tienen la misma estructura de encabezados:\n  "
        + "\n  ".join(offenders)
        + "\n\nO le falta una sección a una mitad, o se añadió a una y no a la otra."
    )


# ---------------------------------------------------------------------------
# La regla: sus enlaces resuelven
# ---------------------------------------------------------------------------
def test_the_internal_links_of_the_root_documents_resolve() -> None:
    """`test_docs_internal_links.py` sólo recorre `docs/`; la raíz se quedaba fuera."""
    broken: list[str] = []
    scanned = 0
    for path in _root_markdown():
        for target in _document_links(path):
            scanned += 1
            if not (path.parent / target).resolve().is_file():
                broken.append(f"{_rel(path)} -> {target}")
    assert scanned >= 2, (
        f"esperaba enlaces `.md` en los documentos de la raíz y conté {scanned}:"
        " el regex o el descubrimiento se rompieron."
    )
    assert not broken, "enlaces internos rotos en los documentos de la raíz:\n  " + "\n  ".join(
        broken
    )


# ---------------------------------------------------------------------------
# La política existe y dice lo que esta guarda supone
# ---------------------------------------------------------------------------
def test_the_language_policy_is_documented_in_both_languages() -> None:
    """Una convención que sólo vive en un test es una convención que nadie encuentra."""
    spanish = _POLICY.with_name(_POLICY.stem + _ES_SUFFIX)
    for half in (_POLICY, spanish):
        assert half.is_file(), f"falta la política de idiomas en {_rel(half)}"
    text = _POLICY.read_text(encoding="utf-8")
    for needle in (_ES_SUFFIX, "canonical"):
        assert needle in text, (
            f"{_rel(_POLICY)} ya no menciona «{needle}»: la política y esta guarda"
            " dejaron de hablar de lo mismo."
        )


# ---------------------------------------------------------------------------
# Los documentos nuevos entran en la puerta de markdown
# ---------------------------------------------------------------------------
#: Rutas de prueba para el `files:` del hook: (camino, ¿debe casar?).
_MARKDOWN_GATE_CASES = (
    ("README.md", True),
    ("CHANGELOG.es.md", True),
    ("docs/03-guides/bilingual-docs.md", True),
    ("apps/admin-panel/vendor/README.md", False),
)


def test_the_markdown_gate_covers_the_root_documents() -> None:
    """Hasta el 2026-08-21 la raíz era el único markdown que no miraba ningún linter.

    Prettier corre en todas partes pero formatea; markdownlint aplica las reglas, y
    su glob era sólo `docs/**`. O sea que los dos documentos que lee primero un
    extraño —README y CHANGELOG— podían romper cualquier regla sin que CI lo dijera.
    Este test ata las DOS superficies, porque arreglar una y olvidar la otra es el
    modo de fallo que ya se pagó aquí con el pin de prettier.
    """
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = [line for line in ci.splitlines() if "markdownlint-cli@" in line]
    assert step, "el job «Lint Markdown» de CI ya no invoca markdownlint-cli"
    assert all('"*.md"' in line for line in step), (
        f'el markdownlint de CI ya no cubre los `.md` de la raíz (falta el glob "*.md"): {step}'
    )

    hook = re.search(
        r"id: markdownlint.*?^\s*files:\s*(\S+)",
        (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    assert hook is not None, "no encuentro el `files:` del hook markdownlint"
    pattern = re.compile(hook.group(1))
    wrong = [
        (path, expected)
        for path, expected in _MARKDOWN_GATE_CASES
        if bool(pattern.search(path)) is not expected
    ]
    assert not wrong, (
        f"el `files:` del hook markdownlint (`{hook.group(1)}`) no cubre lo que"
        f" debe: {wrong} (camino, ¿debía casar?)"
    )
