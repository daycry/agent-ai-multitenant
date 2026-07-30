"""Guarda estática: `docs/` no tiene enlaces internos `.md` rotos.

Acredita el ítem 5 del test humano `human_doc_01` del plan
`docs-comprehensive-update` («No hay enlaces internos rotos en docs/») y el
criterio de cierre nº1 del mismo plan. Sin red, sin BD, sin reloj: solo lee el
Markdown del repo.

## Qué cuenta como enlace interno a documento

Se barre **todo** `docs/**/*.md` (excluyendo `node_modules/`) y de cada enlace
Markdown `[texto](destino)` se conserva únicamente el que apunta a un
**documento**: destino relativo que termina en `.md`. Quedan fuera, a propósito:

  * URLs (`http://`, `https://`, `mailto:`) — no son enlaces internos;
  * anclas puras (`#seccion`) — no cruzan de fichero;
  * enlaces a **código** (`apps/.../x.py`, `docker/...yml`) — la convención de
    los ADR es citar fuente por ruta+línea, y comprobar la existencia de un
    fichero de código con `#L120-L130` es otra guarda (otro plan);
  * y por lo mismo, cualquier destino con **ancla de línea** (`#L177-L179`):
    es una *cita de fuente*, no una travesía de documentación. El ADR 0132 cita
    así al 0022 entre citas a `plans.py#L812-L836`; tratarlo como enlace de
    documento mezclaría dos convenciones distintas.
  * imágenes (`![...](...)`) — no navegan.

## Por qué la aserción de «encontró algo»

Una guarda que busca infractores pasa **vacíamente** el día que el
descubrimiento deja de encontrar nada (docs/03-guides/verificar-antes-de-
implementar.md §4). Por eso el test afirma primero que barrió un volumen
plausible de enlaces; si el regex se rompe o la carpeta se mueve, falla por ahí
en vez de dar un verde silencioso.

Estado al escribirlo (2026-07-29): 1.017 enlaces `.md`→`.md`, **3 rotos**, los
tres entre ADRs (0065→0021, 0068→placeholder `0054-...md`, 0069→0047). Los tres
se arreglaron con este test en rojo delante.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

pytestmark = pytest.mark.unit

# tests/docs/test_docs_internal_links.py -> raíz del repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _REPO_ROOT / "docs"

#: Enlace Markdown `[texto](destino)`, con título opcional, que NO sea imagen
#: (el `(?<!!)` descarta `![alt](src)`).
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: Anclas de cita de fuente (`#L120`, `#L120-L130`): no son travesías de docs.
_LINE_ANCHOR_RE = re.compile(r"^L\d+(?:-L\d+)?$")

#: Suelo de descubrimiento: por debajo de esto la guarda dejó de mirar (regex
#: roto, carpeta movida) y su verde no significaría nada. Al escribirlo: 1.017.
_MIN_LINKS_SCANNED = 700


def _markdown_files() -> list[Path]:
    return sorted(p for p in _DOCS.rglob("*.md") if "node_modules" not in p.parts)


def _document_links(text: str) -> list[str]:
    """Los destinos del texto que son enlaces internos **a documento**."""
    out: list[str] = []
    for match in _LINK_RE.finditer(text):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#", "<")):
            continue
        path_part, _, anchor = target.partition("#")
        if not path_part or not path_part.endswith(".md"):
            continue
        if _LINE_ANCHOR_RE.match(anchor):
            # Cita de fuente por rango de líneas, no navegación entre docs.
            continue
        out.append(target)
    return out


def _scan() -> tuple[int, list[str]]:
    """Devuelve (enlaces barridos, infractores formateados `origen -> destino`)."""
    scanned = 0
    broken: list[str] = []
    for md in _markdown_files():
        text = md.read_text(encoding="utf-8", errors="replace")
        for target in _document_links(text):
            scanned += 1
            path_part = target.partition("#")[0]
            resolved = (md.parent / unquote(path_part)).resolve()
            if not resolved.is_file():
                broken.append(f"{md.relative_to(_REPO_ROOT)} -> {target}")
    return scanned, broken


def test_link_scan_actually_finds_links() -> None:
    """La guarda no puede pasar vacíamente: debe haber barrido enlaces de verdad."""
    scanned, _ = _scan()
    assert scanned >= _MIN_LINKS_SCANNED, (
        f"la guarda de enlaces solo vio {scanned} enlaces .md (esperaba "
        f">= {_MIN_LINKS_SCANNED}): el regex o la ruta de docs/ se rompieron, "
        "así que un verde aquí no significaría nada"
    )


def test_no_broken_internal_document_links() -> None:
    scanned, broken = _scan()
    assert not broken, (
        f"{len(broken)} enlace(s) interno(s) de docs/ apunta(n) a un .md que no "
        f"existe (de {scanned} barridos):\n  " + "\n  ".join(broken)
    )
