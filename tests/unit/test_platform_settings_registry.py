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
