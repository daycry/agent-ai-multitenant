"""Unit tests for the tenant-settings registry (Plan 06.7 task_06_7_02).

The registry is the source of truth for known (category, key) pairs +
their defaults/validators. These tests pin the contract the UI + the
PUT endpoint depend on.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_registry_contains_memories_and_costs() -> None:
    from api_server.settings_registry import KNOWN_SETTINGS

    assert "memories" in KNOWN_SETTINGS
    assert "costs" in KNOWN_SETTINGS
    assert KNOWN_SETTINGS["memories"].icon == "Brain"
    assert KNOWN_SETTINGS["costs"].icon == "Coins"


def test_memories_category_has_threshold_and_limit() -> None:
    from api_server.settings_registry import KNOWN_SETTINGS

    mem = KNOWN_SETTINGS["memories"]
    assert "similarity.threshold" in mem.settings
    assert "similarity.limit" in mem.settings
    assert mem.settings["similarity.threshold"].default == 0.85
    assert mem.settings["similarity.limit"].default == 5


def test_costs_category_has_external_page() -> None:
    """Costs is a legacy category — it links to the existing
    /admin/settings/hourly-rate page and has no editable settings of
    its own in the generic system."""
    from api_server.settings_registry import KNOWN_SETTINGS

    costs = KNOWN_SETTINGS["costs"]
    assert costs.external_page == "/admin/settings/hourly-rate"
    assert costs.settings == {}


def test_validate_setting_value_coerces_int_from_float() -> None:
    from api_server.settings_registry import validate_setting_value

    # JSON sends `5` and `5.0` interchangeably — both must work for int settings.
    assert validate_setting_value("memories", "similarity.limit", 5) == 5
    assert validate_setting_value("memories", "similarity.limit", 5.0) == 5


def test_validate_setting_value_rejects_fractional_for_int() -> None:
    from api_server.settings_registry import validate_setting_value

    with pytest.raises(ValueError, match="fractional"):
        validate_setting_value("memories", "similarity.limit", 5.5)


def test_validate_setting_value_enforces_min_max() -> None:
    from api_server.settings_registry import validate_setting_value

    with pytest.raises(ValueError, match="below minimum"):
        validate_setting_value("memories", "similarity.threshold", 0.1)
    with pytest.raises(ValueError, match="above maximum"):
        validate_setting_value("memories", "similarity.threshold", 1.5)
    with pytest.raises(ValueError, match="below minimum"):
        validate_setting_value("memories", "similarity.limit", 0)
    with pytest.raises(ValueError, match="above maximum"):
        validate_setting_value("memories", "similarity.limit", 100)


def test_validate_setting_value_accepts_boundaries() -> None:
    from api_server.settings_registry import validate_setting_value

    assert validate_setting_value("memories", "similarity.threshold", 0.5) == 0.5
    assert validate_setting_value("memories", "similarity.threshold", 0.99) == 0.99
    assert validate_setting_value("memories", "similarity.limit", 1) == 1
    assert validate_setting_value("memories", "similarity.limit", 20) == 20


def test_unknown_category_raises() -> None:
    from api_server.settings_registry import UnknownSettingError, validate_setting_value

    with pytest.raises(UnknownSettingError, match="category"):
        validate_setting_value("not-a-category", "x", 1)


def test_unknown_key_in_known_category_raises() -> None:
    from api_server.settings_registry import UnknownSettingError, validate_setting_value

    with pytest.raises(UnknownSettingError, match=r"memories.'not-a-key'"):
        validate_setting_value("memories", "not-a-key", 1)


def test_registry_to_dict_shape() -> None:
    """The UI consumes this dict to render forms — pin the shape."""
    from api_server.settings_registry import registry_to_dict

    payload = registry_to_dict()
    assert "memories" in payload
    mem = payload["memories"]
    assert mem["icon"] == "Brain"
    assert mem["label_es"] == "Memorias"
    assert mem["external_page"] is None
    assert mem["settings"]["similarity.threshold"]["type"] == "float"
    assert mem["settings"]["similarity.threshold"]["default"] == 0.85
    assert mem["settings"]["similarity.threshold"]["min_value"] == 0.5
    assert mem["settings"]["similarity.threshold"]["max_value"] == 0.99

    costs = payload["costs"]
    assert costs["external_page"] == "/admin/settings/hourly-rate"
    assert costs["settings"] == {}


# ---------------------------------------------------------------------------
# ES + EN — prod-16 `task_prod16_03`
#
# Este registry sirve los títulos y descripciones de `/admin/settings`. Mientras
# sólo tuvo `label_es`/`description_es`, dos pantallas del panel NO se podían
# migrar «sólo en el frontend»: traducir el marco y dejar el contenido en
# castellano es media pantalla sin traducir, que es justo el defecto que prod-16
# cierra. El principio 12 de CLAUDE.md fija ES + EN.
# ---------------------------------------------------------------------------
def test_every_category_and_setting_carries_both_languages() -> None:
    from api_server.settings_registry import KNOWN_SETTINGS

    assert KNOWN_SETTINGS, "el registry está vacío: este test no comprobaría nada"

    categorias = 0
    ajustes = 0
    for nombre, cat in KNOWN_SETTINGS.items():
        categorias += 1
        assert cat.label_es.strip(), f"{nombre}: sin `label_es`"
        assert cat.label_en.strip(), f"{nombre}: sin `label_en`"
        assert cat.label_es != cat.label_en or len(cat.label_es) <= 4, (
            f"{nombre}: `label_es` y `label_en` son idénticos ({cat.label_es!r})."
            " Puede ser legítimo en una palabra que se escribe igual, pero con"
            " esta longitud lo normal es que sea un copia-pega sin traducir."
        )
        if cat.description_es.strip():
            assert cat.description_en.strip(), f"{nombre}: descripción sólo en castellano"
        for clave, sdef in cat.settings.items():
            ajustes += 1
            ruta = f"{nombre}.{clave}"
            assert sdef.label_es.strip(), f"{ruta}: sin `label_es`"
            assert sdef.label_en.strip(), f"{ruta}: sin `label_en`"
            assert sdef.description_es.strip(), f"{ruta}: sin `description_es`"
            assert sdef.description_en.strip(), f"{ruta}: sin `description_en`"

    # No-vacuidad: si el descubrimiento se rompe, los bucles de arriba pasan
    # sin recorrer nada y el test diría que todo está bien.
    assert categorias >= 3, f"esperaba al menos 3 categorías, recorrí {categorias}"
    assert ajustes >= 2, f"esperaba al menos 2 ajustes, recorrí {ajustes}"


def test_registry_to_dict_serialises_both_languages() -> None:
    """De nada sirve tenerlo en el dataclass si el endpoint no lo emite."""
    from api_server.settings_registry import registry_to_dict

    payload = registry_to_dict()
    mem = payload["memories"]
    assert mem["label_en"] == "Memories"
    assert "How the system" in mem["description_en"]

    umbral = mem["settings"]["similarity.threshold"]
    assert umbral["label_en"] == "Similarity threshold"
    assert umbral["description_en"].startswith("Minimum cosine similarity")

    # Y las claves están en TODAS las entradas, no sólo en la que miramos.
    for nombre, cat in payload.items():
        assert "label_en" in cat and "description_en" in cat, nombre
        for clave, sdef in cat["settings"].items():
            assert "label_en" in sdef and "description_en" in sdef, f"{nombre}.{clave}"


def test_a_setting_without_its_english_pair_refuses_to_be_built() -> None:
    """La validación es al CONSTRUIR, o sea al importar el módulo.

    Comprobarlo a posteriori dejaría un hueco: el proceso ya habría arrancado
    sirviendo media pantalla en castellano.
    """
    from api_server.settings_registry import SettingDef

    with pytest.raises(ValueError, match="falta la variante `en`"):
        SettingDef(
            type="int",
            default=1,
            description_es="Descripción en castellano.",
            description_en="An English description.",
            label_es="Etiqueta",
            label_en="",
        )

    with pytest.raises(ValueError, match="falta la variante `en`"):
        SettingDef(
            type="int",
            default=1,
            description_es="Descripción en castellano.",
            description_en="   ",
            label_es="Etiqueta",
            label_en="Label",
        )


def test_a_category_with_a_spanish_description_and_no_english_one_refuses() -> None:
    from api_server.settings_registry import CategoryDef

    # Sin descripción en ninguno de los dos: legítimo.
    CategoryDef(label_es="Algo", label_en="Something", icon="Box")

    with pytest.raises(ValueError, match="falta la variante `en`"):
        CategoryDef(
            label_es="Algo",
            label_en="Something",
            icon="Box",
            description_es="Sólo en castellano.",
        )
