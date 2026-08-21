"""prod-12 task_prod12_img_01 (sandbox-6) — las imágenes de test-runtime son no-root.

Chequeo a nivel de fuente sobre TODOS los Dockerfiles del catálogo (el build
real de cada imagen es local/on-demand): cada template hornea el HOME
escribible de uid 1000 y arranca `USER 1000:1000` — la misma defensa en
profundidad que el agent-runtime. El `agent-runtime/` queda fuera (tiene su
propio hardening con entrypoint root→setpriv).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "docker" / "agent-runtimes"


def _template_dockerfiles() -> list[Path]:
    return sorted(
        d / "Dockerfile"
        for d in _TEMPLATES_ROOT.iterdir()
        if d.is_dir() and d.name != "agent-runtime" and (d / "Dockerfile").exists()
    )


def test_the_catalog_has_dockerfiles() -> None:
    assert len(_template_dockerfiles()) >= 14


@pytest.mark.parametrize("dockerfile", _template_dockerfiles(), ids=lambda p: p.parent.name)
def test_template_bakes_nonroot_user_and_writable_home(dockerfile: Path) -> None:
    text = dockerfile.read_text(encoding="utf-8")
    assert "USER 1000:1000" in text, f"{dockerfile.parent.name}: sin USER 1000:1000"
    assert "HOME=/home/agent" in text, f"{dockerfile.parent.name}: sin HOME=/home/agent"
    assert "chown -R 1000:1000 /home/agent" in text, (
        f"{dockerfile.parent.name}: /home/agent sin chown a uid 1000"
    )
