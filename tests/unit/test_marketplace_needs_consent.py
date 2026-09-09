"""Una instalación no nace imposible de habilitar (`task_mk_14`, MK-17).

`needs_consent` mira el nivel de confianza Y si hay algo que consentir. Un listing
`community` sin permisos declarados —toda publicación privada nace `community` y un
`SKILL.md` sin bloque `permissions` pide cero— nacía `disabled` sin poder
habilitarse jamás: la pantalla de consentimiento exige al menos una decisión y el
despliegue exige `enabled`. La política de confianza queda intacta para quien sí
pide permisos.
"""

from __future__ import annotations

import pytest
from api_server.marketplace.consent import consent_required_for, needs_consent


@pytest.mark.parametrize("trust", ["community", "experimental"])
def test_un_listing_no_verificado_con_permisos_sigue_naciendo_disabled(trust: str) -> None:
    assert consent_required_for(trust) is True
    assert needs_consent(trust, [{"type": "allowed_domains", "value": ["a.example"]}]) is True


@pytest.mark.parametrize("trust", ["community", "experimental"])
@pytest.mark.parametrize("permissions", [[], None, "[]"[0:0]])
def test_sin_permisos_que_consentir_no_hay_consentimiento_que_esperar(
    trust: str, permissions: object
) -> None:
    """Es la regla que el backend ya usaba para habilitar (`all_granted` vacuo),
    aplicada también al nacimiento de la instalación."""
    assert needs_consent(trust, permissions) is False


def test_verified_nunca_pide_consentimiento() -> None:
    assert needs_consent("verified", [{"type": "allowed_paths", "value": ["/x"]}]) is False
