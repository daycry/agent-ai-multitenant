"""Prefijos de claves de API en el enmascarado (prod-08 `task_prod08_shared_logging_08`).

El catálogo PII cubría email, IBAN, DNI/NIE, JWT y `Bearer …`. Fuera quedaba la
familia que este stack maneja a diario y que es LA credencial: las claves de API
con prefijo reconocible. Una traza de un 401 de proveedor, el `repr` de una
config o un mensaje de error de la librería del proveedor arrastran la clave
entera al log, y de ahí a `docker logs` — y, desde el ADR 0139, a Loki con 30
días de retención y buscador.

Las tres familias que este sistema tiene de verdad:

  * ``sk-…``    claves estilo OpenAI/Anthropic (Azure AI Foundry vía APIM).
  * ``ghu_…``   tokens de GitHub / Copilot (`ghp_`, `gho_`, `ghs_`, `ghr_`).
  * ``hvs.…``   tokens de servicio de HashiCorp Vault — la llave del baúl donde
                están TODAS las demás credenciales.

Cuidado deliberado con los falsos positivos: enmascarar de más también hace
daño. Si `sk-` se comiera cualquier palabra que empiece por esas letras, los
logs dejarían de ser legibles y el operador acabaría desactivando el masker.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

# Marcadores OPACOS, no material con forma de credencial real: lo único que
# importa es el prefijo + longitud suficiente para que el patrón enganche.
_FAKE_SK = "sk-" + "A" * 32
_FAKE_GH = "ghu_" + "B" * 36
_FAKE_VAULT = "hvs." + "C" * 24


@pytest.mark.parametrize("secret", [_FAKE_SK, _FAKE_GH, _FAKE_VAULT])
def test_api_key_prefixes_are_redacted(secret: str) -> None:
    from api_server.logging.pii import mask_pii_in_text

    masked = mask_pii_in_text(f"provider call failed with key={secret}")

    assert secret not in masked, f"la clave salió en claro: {masked}"
    assert "REDACTED" in masked


def test_the_prefix_alone_survives_so_the_log_still_says_which_family() -> None:
    """Enmascarar no es borrar: saber que la que falló era una `hvs.` (Vault) y
    no una `sk-` (proveedor LLM) es la mitad del diagnóstico."""
    from api_server.logging.pii import mask_pii_in_text

    masked = mask_pii_in_text(f"token={_FAKE_VAULT}")

    assert masked.startswith("token=hvs."), masked
    assert "C" * 24 not in masked


def test_a_key_inside_a_nested_structure_is_masked_too() -> None:
    """Los secretos rara vez llegan sueltos: llegan dentro del dict de config o
    del cuerpo de un error que alguien loguea entero."""
    from api_server.logging.pii import mask_pii_processor

    event = mask_pii_processor(
        None,
        "info",
        {"event": "llm.call_failed", "config": {"headers": [f"Authorization: {_FAKE_SK}"]}},
    )

    assert _FAKE_SK not in str(event), event


@pytest.mark.parametrize(
    "innocent",
    [
        "sk-",  # el prefijo solo, sin cuerpo
        "skydiving is fun",  # empieza por las mismas letras
        "the sk-1 branch",  # cuerpo demasiado corto para ser una clave
        "hvs.company.example",  # un HOSTNAME, no un token
    ],
)
def test_ordinary_text_is_not_mangled(innocent: str) -> None:
    """Enmascarar de más también hace daño: unos logs ilegibles acaban con el
    masker desactivado, y entonces no protege de nada."""
    from api_server.logging.pii import mask_pii_in_text

    assert mask_pii_in_text(innocent) == innocent


def test_the_older_patterns_still_work() -> None:
    """Regresión: el orden de sustitución importa (los patrones se aplican en
    cadena), así que añadir uno nuevo puede romper otro por delante."""
    from api_server.logging.pii import mask_pii_in_text

    masked = mask_pii_in_text("aviso para ana.perez@example.com con Bearer abc.def.ghi")

    assert "ana.perez@example.com" not in masked
    assert "a***@example.com" in masked
    assert "Bearer ***REDACTED***" in masked
