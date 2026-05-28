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

    with pytest.raises(UnknownSettingError, match="memories.'not-a-key'"):
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
