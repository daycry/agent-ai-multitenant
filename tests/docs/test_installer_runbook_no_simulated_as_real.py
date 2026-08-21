"""Guarda estática del runbook de instalación: **nada simulado se presenta como real**.

Acredita el ítem 3 del test humano `human_prod01_04` del plan
`prod-01-despliegue-ejecutable` («El runbook ya no documenta como real ningún paso
simulado»). Sin Docker, sin red: solo lee `docs/06-runbooks/01-installation-from-scratch.md`
y el módulo del CLI del instalador.

## Las dos mitades de la guarda

**Negativa** — los *placeholders* que el instalador emite en modo simulación no
pueden aparecer en el runbook. Un runbook que pega `stub-admin-password` en un
bloque de salida está documentando una credencial FALSA como si fuera la real, que
es exactamente el modo de fallo del test humano. Y no se listan a mano: se leen de
:class:`installer_backend.cli.StubCredentialBuilder`, la fuente de verdad. Si
mañana los placeholders se renombran, la guarda los sigue sola en vez de envejecer
buscando cadenas que ya no existen.

**Positiva** — cada camino simulado tiene que estar **marcado** como tal. Solo con
la mitad negativa la guarda pasaría vacíamente el día que alguien borrara los
avisos de «SIMULACIÓN» (docs/03-guides/verificar-antes-de-implementar.md §4): no
habría placeholders, no habría avisos, y el runbook mentiría en verde. Así que se
exige, en el propio texto:

  * el **wizard HTTP** (Camino A) declarado simulación y desaconsejado como
    instalación real — hoy es un `FakeStepExecutor` hasta prod-09;
  * el `--dry-run` del CLI declarado simulación **con credenciales falsas**;
  * el **CLI sin `--dry-run`** identificado como el camino REAL;
  * y documentada la guarda ejecutable que lo respalda: un seam de simulación sin
    `--dry-run` **aborta** con código `PROVISION` (4)
    (``installer_backend.cli._assert_real_install_seams``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK = _REPO_ROOT / "docs" / "06-runbooks" / "01-installation-from-scratch.md"


def _runbook() -> str:
    return _RUNBOOK.read_text(encoding="utf-8")


def _stub_credential_literals() -> list[str]:
    """Los placeholders que el instalador emite en simulación, leídos del código."""
    from installer_backend.cli import StubCredentialBuilder

    stub = StubCredentialBuilder()
    return [stub.admin_password, stub.vault_root_token, *stub.vault_unseal_keys]


# --- la guarda no puede pasar vacíamente -----------------------------------


def test_runbook_exists_and_is_substantial() -> None:
    assert _RUNBOOK.is_file(), "falta docs/06-runbooks/01-installation-from-scratch.md"
    assert len(_runbook()) > 2000, "el runbook de instalación quedó vacío o truncado"


def test_stub_literals_are_discoverable_from_the_code() -> None:
    """Si esta lista se queda vacía, la mitad negativa de la guarda no mira nada."""
    literals = _stub_credential_literals()
    assert len(literals) >= 3, (
        "no se pudieron leer los placeholders de simulación de "
        f"StubCredentialBuilder (vio {literals!r}): la guarda del runbook "
        "estaría buscando cadenas inexistentes y pasaría vacíamente"
    )
    assert all(lit.strip() for lit in literals)


# --- mitad negativa: ningún placeholder de simulación en el runbook --------


def test_runbook_does_not_print_simulated_credentials() -> None:
    text = _runbook()
    leaked = sorted({lit for lit in _stub_credential_literals() if lit in text})
    assert not leaked, (
        "el runbook de instalación contiene placeholders que el instalador solo "
        f"emite en SIMULACIÓN ({leaked}): documenta como real una salida falsa. "
        "Si hay que mostrarlos, hay que marcarlos explícitamente como salida de "
        "`--dry-run`, no como credenciales de una instalación real"
    )


# --- mitad positiva: los caminos simulados están marcados ------------------


def test_wizard_path_is_flagged_as_simulation() -> None:
    """El Camino A (wizard HTTP) no aprovisiona: tiene que decirlo donde se lee."""
    text = _runbook()
    wizard_section = text.partition("## Camino A")[2].partition("## Camino B")[0]
    assert wizard_section.strip(), "no se encontró la sección «Camino A» del wizard"
    assert re.search(r"SIMULACI[ÓO]N", wizard_section), (
        "la sección del wizard (Camino A) no se declara SIMULACIÓN, pero su "
        "StepExecutor no aprovisiona nada (follow-up prod-09): un operador la "
        "leería como instalación real"
    )
    assert re.search(r"no\s+aprovisiona|falsas|FALSAS", wizard_section), (
        "la sección del wizard debe decir explícitamente que no aprovisiona / "
        "que las credenciales que muestra no son reales"
    )


def test_dry_run_is_flagged_as_simulation_with_fake_credentials() -> None:
    text = _runbook()
    assert "--dry-run" in text, "el runbook no menciona el modo --dry-run del CLI"
    assert re.search(r"--dry-run[^\n]*", text)
    # El bloque que explica real vs simulación debe nombrar las dos cosas.
    assert re.search(r"SIMULACI[ÓO]N", text), "el runbook no marca ninguna simulación"
    assert re.search(r"FALSAS|falsas", text), (
        "el runbook no advierte de que las credenciales del --dry-run son FALSAS"
    )


def test_runbook_names_the_cli_as_the_real_path() -> None:
    text = _runbook()
    assert re.search(r"camino\s+REAL", text, re.IGNORECASE), (
        "el runbook no identifica cuál es el camino REAL de instalación"
    )
    assert "scripts/install.sh" in text, "el runbook no nombra el camino real (scripts/install.sh)"


def test_runbook_documents_the_no_silent_stub_abort() -> None:
    """La promesa ejecutable del plan (deploy-1) tiene que estar en el runbook.

    Es la parte que convierte «no documentamos simulaciones como reales» en algo
    verificable por el operador: un seam de simulación sin `--dry-run` aborta con
    el código `PROVISION` (4), no instala a medias en silencio.
    """
    text = _runbook()
    assert "PROVISION" in text, (
        "el runbook no documenta el código de salida PROVISION del abort por "
        "seams de simulación sin --dry-run (deploy-1)"
    )
    assert re.search(r"abort[ae]", text), (
        "el runbook no dice que el CLI ABORTA si detecta seams de simulación sin "
        "--dry-run — sin eso, el lector no tiene forma de distinguir real de falso"
    )
    # Y que la guarda descrita exista de verdad en el código.
    from installer_backend.cli import _assert_real_install_seams

    assert callable(_assert_real_install_seams)
