"""`CONTINUE_HERE.md` es lo primero que se lee: no puede envejecer mintiendo.

Es el archivo con más riesgo de rot del repo — resume estado y el estado cambia.
Está escrito como PUNTERO (la verdad vive en el frontmatter del roadmap) justo
para reducirlo, pero quedan dos formas de que mienta y las dos se comprueban
aquí:

  1. **Enlaces rotos**: apunta a un fichero que se renombró o borró. Barato de
     detectar y caro de sufrir, porque lo lee quien acaba de llegar.
  2. **Contradecir al protocolo**: dice «0 fases in_progress» mientras hay dos.
     El frontmatter manda; este test avisa cuando se han separado.

Lo que NO se comprueba es la prosa: los recuentos y los hitos envejecen y eso se
acepta a cambio de que el archivo sea útil. El propio documento lo dice y trae
los comandos para regenerarlos.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "CONTINUE_HERE.md"


def _linked_paths() -> list[str]:
    """Rutas de fichero enlazadas en markdown, sin URLs ni anclas."""
    text = _DOC.read_text(encoding="utf-8")
    out: list[str] = []
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        out.append(target.split("#", 1)[0])
    return out


def test_the_entry_point_exists() -> None:
    assert _DOC.is_file(), "CONTINUE_HERE.md es la puerta de entrada y no está"


def test_every_path_it_points_at_exists() -> None:
    missing = [p for p in _linked_paths() if not (_ROOT / p).exists()]
    assert not missing, f"CONTINUE_HERE.md enlaza rutas que ya no existen: {missing}"
    # No vacuo: si el extractor deja de encontrar enlaces, el test dejaría de
    # vigilar en silencio. Holgado a propósito — pegado al número exacto daría
    # falsas alarmas al quitar un enlace, que es un cambio legítimo.
    assert len(_linked_paths()) >= 3


def test_claude_md_sends_the_reader_here() -> None:
    # Si CLAUDE.md deja de enlazarlo, nadie lo lee y el archivo deja de existir
    # a efectos prácticos.
    assert "CONTINUE_HERE.md" in (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_it_agrees_with_the_protocol_on_in_progress_phases() -> None:
    """El protocolo permite UNA fase `in_progress` como mucho.

    Si hay más, el roadmap está fuera de protocolo y el resumen miente; si hay
    una y el documento dice cero, el resumen está viejo. Las dos merecen aviso.
    """
    roadmap = _ROOT / "docs" / "roadmap"
    live = [
        f.name
        for f in roadmap.glob("*.md")
        if re.search(r"^status: *in_progress", f.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert len(live) <= 1, (
        f"{len(live)} fases in_progress a la vez, el protocolo permite una: {live}"
    )
    claims_zero = "| `in_progress`               |  0  |" in _DOC.read_text(encoding="utf-8")
    if claims_zero:
        assert not live, f"CONTINUE_HERE.md dice 0 fases in_progress pero hay {live} — actualízalo"
