"""El tipo `string_list` del registro de ajustes, y la allowlist que lo estrena.

`task_mk_02` (ADR 0165 D6). El registro de ajustes de plataforma sólo sabía de
`bool`, `int`, `decimal`, `model_config` y `guardrails_config`; la allowlist de
hosts MCP remotos necesita una **lista de cadenas validada entrada a entrada**.

Se introduce **genérico** y no como `mcp_hosts` a propósito: la siguiente lista de
la plataforma no debería volver a pagar esto. Por eso el validador por elemento se
inyecta en la definición del ajuste (`item_validator`) en vez de vivir dentro del
tipo.

Dos cosas que estos tests fijan y que no son cosméticas:

- **Un tipo declarado sin rama en el validador revienta en runtime, no al
  importar**: la última línea de `validate_platform_setting_value` es un
  `unknown setting type`. Por eso hay un caso que lo ejercita de verdad.
- **El valor se guarda normalizado, ordenado y sin duplicados**. Lo que se
  almacena es lo que después renderiza el filtro; si el orden dependiese de cómo
  lo tecleó el operador, el `diff` de `filter.txt` dejaría de significar algo.
"""

from __future__ import annotations

import pytest
from api_server.platform_settings_registry import (
    PLATFORM_KNOWN_SETTINGS,
    platform_registry_to_dict,
    validate_platform_setting_value,
)

pytestmark = pytest.mark.unit

CLAVE = "egress.mcp_allowed_hosts"


# --------------------------------------------------------------- el registro


def test_la_allowlist_esta_en_el_registro_con_su_categoria() -> None:
    categorias = {
        nombre: cat for nombre, cat in PLATFORM_KNOWN_SETTINGS.items() if CLAVE in cat.settings
    }

    assert categorias, f"{CLAVE} no está en PLATFORM_KNOWN_SETTINGS"
    cat = next(iter(categorias.values()))
    assert cat.icon, "la categoría necesita un icono lucide: sin él la UI cae al genérico"
    assert cat.label_es != cat.label_en or not cat.label_es, "las dos caras del texto son distintas"


def test_la_definicion_dice_su_tipo_su_default_y_su_tope() -> None:
    sdef = next(
        cat.settings[CLAVE] for cat in PLATFORM_KNOWN_SETTINGS.values() if CLAVE in cat.settings
    )

    assert sdef.type == "string_list"
    assert sdef.default == []
    assert sdef.max_items == 100
    assert sdef.item_validator is not None, "sin validador por elemento entraría cualquier cosa"


def test_el_registro_serializado_lleva_el_tope() -> None:
    """El panel necesita el tope para no dejar guardar 101 y descubrirlo en el 422."""
    serializado = platform_registry_to_dict()
    entrada = next(
        cat["settings"][CLAVE] for cat in serializado.values() if CLAVE in cat["settings"]
    )

    assert entrada["type"] == "string_list"
    assert entrada["max_items"] == 100


# --------------------------------------------------------------- el validador


def test_una_lista_valida_se_guarda_normalizada_ordenada_y_sin_duplicados() -> None:
    guardado = validate_platform_setting_value(
        CLAVE, ["MCP.Atlassian.com", "api.github.com", "mcp.atlassian.com  "]
    )

    assert guardado == ["api.github.com", "mcp.atlassian.com"]


def test_la_lista_vacia_es_valida_y_es_el_default() -> None:
    assert validate_platform_setting_value(CLAVE, []) == []


@pytest.mark.parametrize("valor", ["mcp.atlassian.com", 42, {"host": "x"}, None, ["ok.com", 7]])
def test_lo_que_no_es_una_lista_de_cadenas_se_rechaza(valor: object) -> None:
    with pytest.raises(ValueError, match=r"lista de cadenas|list of strings"):
        validate_platform_setting_value(CLAVE, valor)


def test_una_entrada_invalida_se_rechaza_diciendo_cual_y_por_que() -> None:
    with pytest.raises(ValueError) as exc:
        validate_platform_setting_value(CLAVE, ["mcp.atlassian.com", "10.0.0.5"])

    mensaje = str(exc.value)
    assert "10.0.0.5" in mensaje, "el error no dice QUÉ entrada falló"
    assert "IP" in mensaje, "el error no dice POR QUÉ falló"


def test_pasarse_del_tope_se_rechaza_diciendo_el_tope() -> None:
    with pytest.raises(ValueError) as exc:
        validate_platform_setting_value(CLAVE, [f"h{i}.example.com" for i in range(101)])

    assert "100" in str(exc.value)


def test_los_duplicados_no_cuentan_para_el_tope() -> None:
    """Cien hosts repetidos son cien entradas tecleadas y un solo host permitido."""
    guardado = validate_platform_setting_value(CLAVE, ["mcp.atlassian.com"] * 100)

    assert guardado == ["mcp.atlassian.com"]
