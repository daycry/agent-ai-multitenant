"""Platform settings registry — validation + serialization (no DB).

Pins the per-type validation/coercion (bool/int/decimal/model_config), bounds,
and the unknown-key error, plus the registry serialization shape the UI reads.
"""

from __future__ import annotations

import pytest
from api_server.platform_settings_registry import (
    PLATFORM_KNOWN_SETTINGS,
    UnknownPlatformSettingError,
    all_setting_keys,
    platform_registry_to_dict,
    validate_platform_setting_value,
)

pytestmark = pytest.mark.unit


def test_known_keys_present() -> None:
    keys = set(all_setting_keys())
    # The setting that motivated the feature + a couple of the others.
    assert "model.default_config" in keys
    assert "max_review_retries" in keys
    assert "rag.reranker_enabled" in keys


# ---------------------------------------------------------------------------
# Caducidad de aprobaciones (prod-03 task_prod03_05 / ADR 0016)
# ---------------------------------------------------------------------------
# Las dos palancas del sweep `workers.expire_stale_approvals` funcionaban y se
# leían de `platform_settings` desde prod-03, pero NO estaban en este registro:
# la única forma de cambiarlas era un INSERT a mano en la tabla. Un System Admin
# no podía ni ver que existían, y `approval_expiry_enabled` es precisamente el
# interruptor que se necesita a mano cuando el sweep está caducando solicitudes
# que no debería (aborta la ejecución al hacerlo).
def test_approval_expiry_settings_are_exposed() -> None:
    keys = set(all_setting_keys())
    assert "approval.timeout_hours" in keys
    assert "approval_expiry_enabled" in keys


# ---------------------------------------------------------------------------
# Sync programado de precios (Plan 11 task_11_18)
# ---------------------------------------------------------------------------
# Misma historia que las dos de arriba, encontrada el 2026-08-19: el beat
# `workers.sync_model_prices` leía `price_sync_enabled` en cada disparo y cuatro
# docstrings prometían que se cambiaba «desde el panel de administración», pero
# la clave no estaba en el registro. `PUT /admin/platform-settings/{key}` valida
# contra ÉL, así que respondía 404 y la palanca sólo se accionaba con un INSERT a
# mano. Un interruptor que sólo se acciona con SQL no es un interruptor.
def test_price_sync_lever_is_exposed() -> None:
    keys = set(all_setting_keys())
    assert "price_sync_enabled" in keys, (
        "el beat de precios lee esta clave en cada disparo; fuera del registro, "
        "el endpoint de escritura devuelve 404 y no hay forma de apagarlo"
    )


def test_price_sync_default_is_the_one_the_beat_task_reads() -> None:
    """El default del registro es EL MISMO que usa la lectura, no una copia.

    Si divergieran, la pantalla enseñaría «activado» mientras el job lee
    «desactivado» (o al revés) — el peor estado posible para un interruptor de
    emergencia, porque miente justo cuando se consulta con prisa.
    """
    from api_server.db.platform_settings import (
        DEFAULT_PRICE_SYNC_ENABLED,
        PRICE_SYNC_ENABLED_KEY,
    )
    from api_server.platform_settings_registry import PLATFORM_KNOWN_SETTINGS

    entry = PLATFORM_KNOWN_SETTINGS["mantenimiento"].settings[PRICE_SYNC_ENABLED_KEY]
    assert entry.type == "bool"
    assert entry.default is DEFAULT_PRICE_SYNC_ENABLED


def test_approval_timeout_defaults_and_bounds_match_the_read_path() -> None:
    """Los límites del registro son LOS MISMOS que clampa `approval_repo`.

    Si divergen, la UI aceptaría un valor que el sweep silenciosamente
    reinterpreta — el operador creería haber configurado 1000 h y el job usaría
    720. Se importan de la fuente en vez de copiarse.
    """
    from api_server.db.approval_repo import (
        DEFAULT_APPROVAL_EXPIRY_ENABLED,
        DEFAULT_APPROVAL_TIMEOUT_HOURS,
        MAX_APPROVAL_TIMEOUT_HOURS,
        MIN_APPROVAL_TIMEOUT_HOURS,
    )
    from api_server.platform_settings_registry import PLATFORM_KNOWN_SETTINGS

    entries = {
        key: sdef
        for cat in PLATFORM_KNOWN_SETTINGS.values()
        for key, sdef in cat.settings.items()
        if key in {"approval.timeout_hours", "approval_expiry_enabled"}
    }
    timeout = entries["approval.timeout_hours"]
    assert timeout.min_value == MIN_APPROVAL_TIMEOUT_HOURS
    assert timeout.max_value == MAX_APPROVAL_TIMEOUT_HOURS
    assert float(timeout.default) == DEFAULT_APPROVAL_TIMEOUT_HOURS

    enabled = entries["approval_expiry_enabled"]
    assert enabled.type == "bool"
    assert enabled.default is DEFAULT_APPROVAL_EXPIRY_ENABLED


def test_approval_timeout_accepts_fractional_hours() -> None:
    """El suelo del rango es 0.25 h (15 min), así que un `int` no sirve: el tipo
    tiene que preservar fracciones."""
    assert validate_platform_setting_value("approval.timeout_hours", 0.5) == "0.5"
    assert validate_platform_setting_value("approval.timeout_hours", "24") == "24"


def test_approval_timeout_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        validate_platform_setting_value("approval.timeout_hours", 0.1)  # bajo el suelo
    with pytest.raises(ValueError):
        validate_platform_setting_value("approval.timeout_hours", 1000)  # sobre el techo


def test_approval_expiry_enabled_is_a_strict_bool() -> None:
    assert validate_platform_setting_value("approval_expiry_enabled", False) is False
    with pytest.raises(ValueError):
        validate_platform_setting_value("approval_expiry_enabled", "off")


def test_unknown_key_raises() -> None:
    with pytest.raises(UnknownPlatformSettingError):
        validate_platform_setting_value("does.not.exist", 1)


# ---------------------------------------------------------------------------
# bool / int / decimal
# ---------------------------------------------------------------------------
def test_bool_validation() -> None:
    assert validate_platform_setting_value("rag.reranker_enabled", True) is True
    with pytest.raises(ValueError):
        validate_platform_setting_value("rag.reranker_enabled", "yes")


def test_int_coercion_and_bounds() -> None:
    assert validate_platform_setting_value("max_review_retries", 5) == 5
    assert validate_platform_setting_value("max_review_retries", 5.0) == 5  # lossless float ok
    with pytest.raises(ValueError):
        validate_platform_setting_value("max_review_retries", 5.5)  # fractional
    with pytest.raises(ValueError):
        validate_platform_setting_value("max_review_retries", 99)  # above max (10)
    with pytest.raises(ValueError):
        validate_platform_setting_value("max_review_retries", -1)  # below min (0)


def test_decimal_validation() -> None:
    key = "plan_approval_double_signature_threshold"
    assert validate_platform_setting_value(key, "10.5") == "10.5"
    assert validate_platform_setting_value(key, 0) == "0"
    with pytest.raises(ValueError):
        validate_platform_setting_value("plan_approval_double_signature_threshold", "abc")
    with pytest.raises(ValueError):
        validate_platform_setting_value("plan_approval_double_signature_threshold", "-1")  # below 0


# ---------------------------------------------------------------------------
# model_config (the agent default)
# ---------------------------------------------------------------------------
def test_model_config_valid() -> None:
    cfg = {"provider": "ollama", "model": "qwen3-coder:480b", "temperature": 0.2}
    assert validate_platform_setting_value("model.default_config", cfg) == cfg


def test_model_config_invalid_provider_is_value_error() -> None:
    with pytest.raises(ValueError):
        validate_platform_setting_value(
            "model.default_config", {"provider": "openai", "model": "gpt-4o"}
        )


def test_model_config_must_be_object() -> None:
    with pytest.raises(ValueError):
        validate_platform_setting_value("model.default_config", "ollama/qwen3-coder")


# ---------------------------------------------------------------------------
# Registry serialization
# ---------------------------------------------------------------------------
def test_registry_to_dict_shape() -> None:
    reg = platform_registry_to_dict()
    assert "modelos" in reg
    model_entry = reg["modelos"]["settings"]["model.default_config"]
    assert model_entry["type"] == "model_config"
    # model_config entries inline the closed catalogue's provider kinds.
    assert "ollama" in model_entry["provider_kinds"]
    # An int entry carries its bounds.
    exec_cat = reg["ejecucion"]["settings"]["max_review_retries"]
    assert exec_cat["min_value"] == 0
    assert exec_cat["max_value"] == 10


def test_every_category_has_label_and_icon() -> None:
    for cat in PLATFORM_KNOWN_SETTINGS.values():
        assert cat.label_es
        assert cat.icon


# ---------------------------------------------------------------------------
# ES + EN — prod-16
#
# Mismo defecto que tenia `settings_registry` y misma cura: este registry sirve
# los titulos y descripciones de `/admin/platform-settings`, asi que con solo
# `label_es` la pantalla queda a medias en cuanto alguien mueve el selector de
# idioma. Principio 12 de CLAUDE.md.
# ---------------------------------------------------------------------------
def test_every_platform_category_and_setting_carries_both_languages() -> None:
    from api_server.platform_settings_registry import PLATFORM_KNOWN_SETTINGS

    assert PLATFORM_KNOWN_SETTINGS, "el registry esta vacio: este test no comprobaria nada"

    categorias = 0
    ajustes = 0
    for nombre, cat in PLATFORM_KNOWN_SETTINGS.items():
        categorias += 1
        assert cat.label_es.strip(), f"{nombre}: sin `label_es`"
        assert cat.label_en.strip(), f"{nombre}: sin `label_en`"
        if cat.description_es.strip():
            assert cat.description_en.strip(), f"{nombre}: descripcion solo en castellano"
        for clave, sdef in cat.settings.items():
            ajustes += 1
            ruta = f"{nombre}.{clave}"
            assert sdef.label_es.strip(), f"{ruta}: sin `label_es`"
            assert sdef.label_en.strip(), f"{ruta}: sin `label_en`"
            assert sdef.description_es.strip(), f"{ruta}: sin `description_es`"
            assert sdef.description_en.strip(), f"{ruta}: sin `description_en`"

    # No-vacuidad: si el descubrimiento se rompe, los bucles pasan sin recorrer
    # nada y el test diria que todo esta bien.
    assert categorias >= 7, f"esperaba al menos 7 categorias, recorri {categorias}"
    assert ajustes >= 12, f"esperaba al menos 12 ajustes, recorri {ajustes}"


def test_platform_registry_to_dict_serialises_both_languages() -> None:
    from api_server.platform_settings_registry import platform_registry_to_dict

    payload = platform_registry_to_dict()
    assert payload["ejecucion"]["label_en"] == "Execution"
    assert payload["ejecucion"]["settings"]["max_review_retries"]["label_en"]

    for nombre, cat in payload.items():
        assert "label_en" in cat and "description_en" in cat, nombre
        for clave, sdef in cat["settings"].items():
            assert "label_en" in sdef and "description_en" in sdef, f"{nombre}.{clave}"


def test_a_platform_setting_without_its_english_pair_refuses_to_be_built() -> None:
    """Se valida al CONSTRUIR: una entrada nueva sin ingles no arranca el proceso."""
    from api_server.platform_settings_registry import PlatformCategoryDef, PlatformSettingDef

    with pytest.raises(ValueError, match="falta la variante `en`"):
        PlatformSettingDef(
            type="bool",
            default=True,
            label_es="Etiqueta",
            label_en="",
            description_es="Descripcion.",
            description_en="A description.",
        )

    with pytest.raises(ValueError, match="falta la variante `en`"):
        PlatformCategoryDef(
            label_es="Algo",
            label_en="Something",
            icon="Box",
            description_es="Solo en castellano.",
        )
