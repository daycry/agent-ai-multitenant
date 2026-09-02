"""Ningún id de revisión de Alembic supera los 32 caracteres.

`alembic_version.version_num` es `varchar(32)`: un id más largo pasa todos los
tests unitarios y revienta en CI, al aplicar la migración, con
`StringDataRightTruncationError` (gotcha `alembic-revision-id-32-chars.md`,
y otra vez el 2026-09-02 con el id original de la 0147, `..._builtin_forks`, 34 chars).
Este test lo convierte en un fallo de la suite unitaria, que es donde se mira.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VERSIONS = Path(__file__).resolve().parents[2] / "apps" / "api-server" / "migrations" / "versions"
_REVISION_RE = re.compile(r'^revision(?:\s*:\s*str)?\s*=\s*"([^"]+)"', re.M)
_DOWN_RE = re.compile(r'^down_revision(?:\s*:[^=]+)?=\s*"([^"]+)"', re.M)
_MAX = 32


def _ids() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(_VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for regex in (_REVISION_RE, _DOWN_RE):
            for match in regex.finditer(text):
                found.append((path.name, match.group(1)))
    return found


def test_every_revision_id_fits_the_version_num_column() -> None:
    ids = _ids()
    assert ids, "no se encontró ninguna revisión: ¿cambió la ruta de migrations/versions?"
    too_long = [(name, rid) for name, rid in ids if len(rid) > _MAX]
    assert not too_long, (
        f"ids de revisión de más de {_MAX} caracteres (alembic_version.version_num es "
        f"varchar({_MAX})): {too_long}"
    )
