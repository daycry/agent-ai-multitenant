"""El `GITHUB_TOKEN` de Actions sólo publica en el namespace del dueño del repo.

Los dos workflows que publican en GHCR se autenticaban con `secrets.GITHUB_TOKEN`
contra `ghcr.io/agentic-platform`, una organización ajena a este repositorio. En
rama no publican —ese fue el disfraz—, así que el gate de siempre salía verde; la
primera vez que corrió en `master`, el 2026-08-21, los **catorce** builds murieron
con `denied: permission_denied: The requested installation does not exist`.

El coste no fue el rojo. Fue que durante las tres semanas anteriores
`runtime_images.json` siguió con `digests: {}`, es decir que **cada host seguía
construyendo su propia variante** de las 14 imágenes donde vive el aislamiento del
Principio Rector 2: exactamente el estado que el ADR 0148 se firmó para terminar.
El manifiesto lo declaraba como transitorio y era permanente, porque la única vía
que podía rellenarlo no podía funcionar nunca.

Esta guarda comprueba lo único comprobable sin red: si un workflow se autentica en
GHCR con el `GITHUB_TOKEN`, el namespace al que empuja tiene que derivarse de
`github.repository_owner`. Empujar a cualquier otro exige un PAT clásico de larga
vida guardado como secreto — peor cadena de suministro que el problema que arregla.
"""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

#: Un `ghcr.io/<algo>` escrito a mano. Excluye a propósito `ghcr.io/${{ … }}`,
#: que es la forma correcta y la que esta guarda quiere ver.
_LITERAL_NS = re.compile(r"ghcr\.io/(?!\$\{\{)([A-Za-z0-9._-]+)")

_OWNER_NS = "ghcr.io/${{ github.repository_owner }}"


def _publishes_with_the_actions_token(text: str) -> bool:
    return "registry: ghcr.io" in text and "secrets.GITHUB_TOKEN" in text


def _publishers() -> list[Path]:
    return sorted(
        p
        for p in _WORKFLOWS.glob("*.yml")
        if _publishes_with_the_actions_token(p.read_text(encoding="utf-8"))
    )


def test_ghcr_publishers_target_the_repository_owner_namespace() -> None:
    offenders: list[str] = []
    for wf in _publishers():
        for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            for ns in _LITERAL_NS.findall(line):
                offenders.append(f"{wf.name}:{n} -> ghcr.io/{ns}")
    assert not offenders, (
        "estos workflows empujan a un namespace de GHCR escrito a mano mientras se "
        f"autentican con el GITHUB_TOKEN, que no puede publicar ahí: {offenders}. "
        f"Usa {_OWNER_NS}."
    )


def test_every_ghcr_publisher_actually_uses_the_owner_derived_namespace() -> None:
    """No basta con no tener literales: hay que empujar a algún sitio."""
    for wf in _publishers():
        assert _OWNER_NS in wf.read_text(encoding="utf-8"), (
            f"{wf.name} publica en GHCR pero no deriva el namespace de github.repository_owner"
        )


def test_the_guard_actually_found_the_publishers() -> None:
    """Non-vacuidad: si el descubrimiento se rompe, esta guarda pasaría vacía."""
    found = {p.name for p in _publishers()}
    assert {"build-runtime-templates.yml", "release-images.yml"} <= found, (
        f"el descubrimiento de workflows publicadores no encontró los conocidos: {found}"
    )
