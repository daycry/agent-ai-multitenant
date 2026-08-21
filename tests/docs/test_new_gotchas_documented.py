"""Los tres gotchas que el plan `docs-comprehensive-update` prometió, **usables**.

Acredita el ítem 3 del test humano `human_doc_01` («Los gotchas nuevos están
documentados (prettier/libuv, alembic rev-id, MinIO volumen)») y el `task_doc_03`
del plan, que exige «síntoma + causa + fix, sin duplicar» **+ actualizar su
README/índice**.

## Por qué no basta con `test -f`

El plan escribió su propio test como «`test -f` de los gotchas nuevos», y un
`test -f` no distingue un gotcha usable de un fichero con un título. La trampa se
descubre buscando: un gotcha sirve cuando quien pega el error en el buscador
encuentra el **síntoma**, entiende la **causa raíz** y aplica el **fix** — y
cuando el índice de la carpeta lo lista, porque `CLAUDE.md` manda buscar ahí
primero y nadie recorre 68 ficheros a mano. Un gotcha correcto y no indexado es un
gotcha que no existe.

Así que por cada uno de los tres se exige:

  1. el fichero, con frontmatter YAML y un `# título`;
  2. las tres secciones del contrato de la carpeta: **Síntoma**, **Causa raíz**,
     **Fix** (el propio `README.md` las declara obligatorias);
  3. contenido real bajo cada sección (no un encabezado vacío);
  4. su **fila en el índice** `docs/03-guides/gotchas/README.md`, enlazada;
  5. el **marcador del error** que un humano pegaría en el buscador — la cadena
     concreta por la que se llega al gotcha.

Se comprueba además que el contrato de secciones que este test da por bueno es el
que el README declara: si la carpeta cambiara de convención, el test avisa en vez
de seguir exigiendo la vieja.

## Dos cosas que este test deliberadamente NO exige

Al escribirlo salieron dos condiciones **preexistentes y de alcance mayor** que
este plan; encerrarlas aquí habría dejado un rojo permanente por motivos ajenos al
plan que se está acreditando, así que se reportan aparte en vez de fingirse:

  * **frontmatter parseable como YAML**: 20 documentos de `docs/` (17 de ellos
    gotchas) tienen un `title:` que empieza por backtick, carácter reservado en
    YAML, así que `yaml.safe_load` revienta. No rompe nada visible porque
    `docs_structure.language` traga el error por diseño — pero eso significa que
    esos 20 documentos figuran como «sin idioma declarado». Aquí se exige que la
    línea `title:` exista, no que el bloque parsee.
  * **índice completo de la carpeta**: 19 de los 68 gotchas no están listados en
    `README.md`. Los tres de este plan sí lo están, y eso es lo que se exige.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOTCHAS = _REPO_ROOT / "docs" / "03-guides" / "gotchas"
_INDEX = _GOTCHAS / "README.md"

#: Las tres secciones que el README de la carpeta declara obligatorias.
_REQUIRED_SECTIONS = ("Síntoma", "Causa raíz", "Fix")

#: (fichero, marcador del error que lleva a este gotcha). Los tres del plan
#: `docs-comprehensive-update` / `task_doc_03`.
_NEW_GOTCHAS: tuple[tuple[str, str], ...] = (
    ("prettier-all-files-libuv-windows.md", "UV_HANDLE_CLOSING"),
    ("alembic-revision-id-32-chars.md", "character varying(32)"),
    ("minio-dev-volume-xl-meta-version.md", "xl meta version"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return "", text
    return parts[0][len("---") :], parts[1].lstrip("\n")


def _section_body(body: str, heading: str) -> str:
    """El cuerpo de la sección `## heading` hasta el siguiente `## `."""
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", body, re.MULTILINE)
    if match is None:
        return ""
    rest = body[match.end() :]
    nxt = re.search(r"^##\s+", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


# --- el contrato que este test asume es el que la carpeta declara -----------


def test_index_declares_the_required_sections() -> None:
    """Si la convención de la carpeta cambia, este test debe enterarse."""
    index = _read(_INDEX)
    missing = [s for s in _REQUIRED_SECTIONS if s not in index]
    assert not missing, (
        "el README de gotchas/ ya no declara obligatorias estas secciones "
        f"{missing}: revisa el contrato antes de seguir exigiéndolas"
    )


# --- los tres gotchas, uno a uno -------------------------------------------


@pytest.mark.parametrize(("name", "marker"), _NEW_GOTCHAS, ids=[n for n, _ in _NEW_GOTCHAS])
def test_new_gotcha_file_exists_with_frontmatter(name: str, marker: str) -> None:
    path = _GOTCHAS / name
    assert path.is_file(), f"falta el gotcha prometido por task_doc_03: gotchas/{name}"
    frontmatter, body = _split_frontmatter(_read(path))
    assert frontmatter.strip(), f"gotchas/{name}: frontmatter ausente o no delimitado"
    # Se exige la línea `title:` con valor, no que el bloque parsee como YAML
    # (ver «Dos cosas que este test deliberadamente NO exige» arriba).
    title_line = re.search(r"^title:\s*(\S.*)$", frontmatter, re.MULTILINE)
    assert title_line is not None, f"gotchas/{name}: el frontmatter no declara 'title'"
    assert title_line.group(1).strip(), f"gotchas/{name}: 'title' vacío"
    assert re.search(r"^#\s+\S", body, re.MULTILINE), f"gotchas/{name}: falta el título H1"


@pytest.mark.parametrize(("name", "marker"), _NEW_GOTCHAS, ids=[n for n, _ in _NEW_GOTCHAS])
def test_new_gotcha_has_the_three_required_sections(name: str, marker: str) -> None:
    _, body = _split_frontmatter(_read(_GOTCHAS / name))
    for heading in _REQUIRED_SECTIONS:
        section = _section_body(body, heading)
        assert section.strip(), (
            f"gotchas/{name}: falta la sección obligatoria '## {heading}' "
            "(o está vacía) — sin ella el gotcha no es utilizable"
        )
        assert len(section.strip()) >= 40, (
            f"gotchas/{name}: la sección '## {heading}' tiene "
            f"{len(section.strip())} caracteres: es un encabezado, no una explicación"
        )


@pytest.mark.parametrize(("name", "marker"), _NEW_GOTCHAS, ids=[n for n, _ in _NEW_GOTCHAS])
def test_new_gotcha_carries_the_searchable_error_marker(name: str, marker: str) -> None:
    """La cadena por la que un humano llega al gotcha desde el error que ve."""
    text = _read(_GOTCHAS / name)
    assert marker in text, (
        f"gotchas/{name}: no contiene el marcador del error ({marker!r}) — quien "
        "pegue el error en el buscador no llegará aquí"
    )


@pytest.mark.parametrize(("name", "marker"), _NEW_GOTCHAS, ids=[n for n, _ in _NEW_GOTCHAS])
def test_new_gotcha_is_linked_from_the_index(name: str, marker: str) -> None:
    index = _read(_INDEX)
    assert re.search(rf"\]\(\.?/?{re.escape(name)}\)", index), (
        f"gotchas/README.md no enlaza a {name}: CLAUDE.md manda buscar en el "
        "índice primero, así que un gotcha no indexado es un gotcha que no existe"
    )
    # Y con descripción, no solo el enlace: la fila del índice es lo que se lee
    # al escanear la lista. Se exige texto en la misma línea o en la siguiente.
    row = re.search(
        rf"^-\s*\[[^\]]*\]\(\.?/?{re.escape(name)}\)(.*(?:\n(?!-\s*\[).*)*)", index, re.MULTILINE
    )
    assert row is not None and row.group(1).strip(), (
        f"gotchas/README.md enlaza a {name} pero sin una línea de descripción: "
        "la fila del índice es lo que se lee al escanear la carpeta"
    )
