"""Validador de la config del despliegue + los dos campos nuevos del manifest.

`task_mkt2_02`. Dos mitades del mismo contrato:

**(a) `marketplace/config_schema.py`** — valida los VALORES de un despliegue
contra el `config_schema` de la versión desplegada. Puro: sin BD, sin red.

**(b) los parsers del manifest** — aceptan y validan `targets` y
`config_schema`, y un manifest **sin ellos sigue siendo válido**. Eso último no
es una cortesía: hay un catálogo publicado (Playwright entre otros) que se
rompería si los campos pasaran a obligatorios, y la retro-compatibilidad se
comprueba con un test explícito, no con buena voluntad.

El caso que muerde y que da nombre a la mitad (a): **un secreto en claro en un
campo `secret` se rechaza con un mensaje que NO contiene el valor**. Un error de
validación que imprime la contraseña la copia al log, que es exactamente el sitio
del que se quería sacar.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.marketplace.config_schema import (
    SECRET_MUST_BE_VAULT_POINTER,
    apply_defaults,
    apply_schema_migration,
    dropped_fields,
    validate_deployment_config,
)
from api_server.marketplace.playwright import PLAYWRIGHT_TOOL_YAML, config_schema
from api_server.marketplace.skill_format import SkillFormatError, parse_skill_md
from api_server.marketplace.tool_format import ToolFormatError, parse_tool_manifest

pytestmark = pytest.mark.unit


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "base_url": {"type": "string", "widget": "text"},
        "timeout_ms": {"type": "integer", "widget": "number", "minimum": 1, "default": 30000},
        "headless": {"type": "boolean", "widget": "toggle", "default": True},
        "mode": {"type": "string", "widget": "select", "enum": ["fast", "thorough"]},
        "browsers": {
            "type": "array",
            "widget": "multiselect",
            "items": {"enum": ["chromium", "firefox"]},
            "minItems": 1,
            "default": ["chromium"],
        },
        "api_token": {"type": "string", "widget": "text", "secret": True},
    },
    "required": ["base_url", "browsers"],
}


# ===========================================================================
# (a) validate_deployment_config
# ===========================================================================
def test_a_valid_config_produces_no_errors() -> None:
    assert (
        validate_deployment_config(
            _SCHEMA,
            {
                "base_url": "https://app-a.example",
                "timeout_ms": 5000,
                "headless": False,
                "mode": "fast",
                "browsers": ["chromium"],
                "api_token": "vault:projects/p1/playwright#token",
            },
        )
        == []
    )


def test_missing_required_field_without_default_is_reported() -> None:
    errors = validate_deployment_config(_SCHEMA, {"browsers": ["chromium"]})
    assert any("base_url" in e and "requerido" in e for e in errors), errors


def test_required_field_with_a_default_is_not_a_hole() -> None:
    """`browsers` es requerido Y tiene default: el default lo llena."""
    errors = validate_deployment_config(_SCHEMA, {"base_url": "https://x.test"})
    assert errors == [], errors


def test_unknown_field_is_rejected_not_ignored() -> None:
    """Un `base_ur1` ignorado en silencio produce un despliegue que apunta a otro sitio."""
    errors = validate_deployment_config(
        _SCHEMA, {"base_url": "https://x.test", "browsers": ["chromium"], "base_ur1": "https://y"}
    )
    assert len(errors) == 1
    assert "base_ur1" in errors[0] and "desconocido" in errors[0]


@pytest.mark.parametrize(
    ("field", "bad_value", "needle"),
    [
        ("base_url", 42, "string"),
        ("timeout_ms", "mucho", "integer"),
        ("headless", "sí", "boolean"),
        ("browsers", "chromium", "array"),
    ],
)
def test_wrong_type_is_reported(field: str, bad_value: Any, needle: str) -> None:
    values = {"base_url": "https://x.test", "browsers": ["chromium"], field: bad_value}
    errors = validate_deployment_config(_SCHEMA, values)
    assert any(field in e and needle in e for e in errors), errors


def test_a_boolean_does_not_pass_as_an_integer() -> None:
    """`bool` es subclase de `int` en Python: aceptarlo haría `timeout_ms: True` == 1."""
    errors = validate_deployment_config(
        _SCHEMA, {"base_url": "https://x.test", "browsers": ["chromium"], "timeout_ms": True}
    )
    assert any("timeout_ms" in e for e in errors), errors


def test_value_outside_enum_is_reported() -> None:
    errors = validate_deployment_config(
        _SCHEMA, {"base_url": "https://x.test", "browsers": ["chromium"], "mode": "turbo"}
    )
    assert any("mode" in e for e in errors), errors


def test_array_entry_outside_items_enum_is_reported() -> None:
    errors = validate_deployment_config(
        _SCHEMA, {"base_url": "https://x.test", "browsers": ["chromium", "netscape"]}
    )
    assert any("netscape" in e for e in errors), errors


def test_empty_array_below_min_items_is_reported() -> None:
    errors = validate_deployment_config(_SCHEMA, {"base_url": "https://x.test", "browsers": []})
    assert any("browsers" in e and "1" in e for e in errors), errors


def test_minimum_is_enforced() -> None:
    errors = validate_deployment_config(
        _SCHEMA, {"base_url": "https://x.test", "browsers": ["chromium"], "timeout_ms": 0}
    )
    assert any("timeout_ms" in e and ">=" in e for e in errors), errors


def test_config_for_a_capability_that_declares_no_schema_is_rejected() -> None:
    """Mandar valores a algo que no declara `config_schema` es un error, no un no-op."""
    assert validate_deployment_config(None, {}) == []
    assert validate_deployment_config({}, {}) == []
    errors = validate_deployment_config(None, {"base_url": "https://x.test"})
    assert len(errors) == 1 and "base_url" in errors[0]


def test_non_mapping_values_are_rejected() -> None:
    assert validate_deployment_config(_SCHEMA, ["base_url"]) != []  # type: ignore[arg-type]


# --- el caso que muerde: el secreto -----------------------------------------
def test_cleartext_secret_is_rejected() -> None:
    errors = validate_deployment_config(
        _SCHEMA,
        {"base_url": "https://x.test", "browsers": ["chromium"], "api_token": "ghp_supersecreto"},
    )
    assert len(errors) == 1, errors
    assert "api_token" in errors[0]
    assert SECRET_MUST_BE_VAULT_POINTER in errors[0]


def test_the_secret_value_never_appears_in_the_error_message() -> None:
    """Un mensaje de validación que ecoa el secreto lo copia al log."""
    secret = "ghp_ESTO-NO-DEBE-SALIR-EN-NINGUN-LOG"
    errors = validate_deployment_config(
        _SCHEMA, {"base_url": "https://x.test", "browsers": ["chromium"], "api_token": secret}
    )
    assert errors, "el secreto en claro tenía que rechazarse"
    joined = " ".join(errors)
    assert secret not in joined, "¡EL MENSAJE DE ERROR FILTRA EL SECRETO!"
    # Y tampoco un prefijo reconocible de él.
    assert "ghp_" not in joined


def test_vault_pointer_is_accepted_and_an_empty_one_is_not() -> None:
    base = {"base_url": "https://x.test", "browsers": ["chromium"]}
    assert validate_deployment_config(_SCHEMA, {**base, "api_token": "vault:p/x#k"}) == []
    assert validate_deployment_config(_SCHEMA, {**base, "api_token": "vault:"}) != []


def test_a_non_string_secret_is_rejected_without_echoing_it() -> None:
    errors = validate_deployment_config(
        _SCHEMA,
        {"base_url": "https://x.test", "browsers": ["chromium"], "api_token": {"raw": "s3cr3t"}},
    )
    assert errors and "s3cr3t" not in " ".join(errors)


# ===========================================================================
# (a2) defaults y la migración de esquema de la fase 4
# ===========================================================================
def test_apply_defaults_fills_only_absent_fields() -> None:
    out = apply_defaults(_SCHEMA, {"base_url": "https://x.test", "timeout_ms": 9})
    assert out["timeout_ms"] == 9, "un valor presente no se pisa con el default"
    assert out["headless"] is True
    assert out["browsers"] == ["chromium"]


def test_apply_defaults_respects_an_explicitly_emptied_field() -> None:
    """`headless: None` es una decisión del operador, no un hueco que rellenar."""
    out = apply_defaults(_SCHEMA, {"headless": None})
    assert out["headless"] is None


def test_schema_migration_adds_new_field_with_default() -> None:
    new_schema = {
        "properties": {
            "base_url": {"type": "string"},
            "retries": {"type": "integer", "default": 3},
        }
    }
    values, problems = apply_schema_migration({"base_url": "https://x.test"}, new_schema)
    assert problems == []
    assert values == {"base_url": "https://x.test", "retries": 3}


def test_schema_migration_drops_retired_fields() -> None:
    new_schema = {"properties": {"base_url": {"type": "string"}}}
    old = {"base_url": "https://x.test", "legacy_flag": True}
    values, problems = apply_schema_migration(old, new_schema)
    assert problems == []
    assert values == {"base_url": "https://x.test"}
    assert dropped_fields(old, new_schema) == ["legacy_flag"]


def test_schema_migration_flags_a_new_required_field_without_default() -> None:
    """El nodo irrenunciable: NO se inventa un valor ni se aplica a medias."""
    new_schema = {
        "properties": {"base_url": {"type": "string"}, "region": {"type": "string"}},
        "required": ["region"],
    }
    values, problems = apply_schema_migration({"base_url": "https://x.test"}, new_schema)
    assert len(problems) == 1
    assert "region" in problems[0]
    assert "region" not in values, "no se inventa un valor para un requerido nuevo"


def test_schema_migration_to_a_schemaless_version_discards_everything() -> None:
    values, problems = apply_schema_migration({"base_url": "https://x.test"}, None)
    assert (values, problems) == ({}, [])


# ===========================================================================
# (b) los parsers aceptan targets + config_schema, y siguen aceptando su ausencia
# ===========================================================================
_TOOL_MIN = """\
name: mkt2-tool
version: 1.0.0
description: Una tool de prueba.
entrypoint: mod:run
implementation:
  runtime: python
"""

_SKILL_MIN = """\
---
name: mkt2-skill
description: Una skill de prueba.
version: 1.0.0
---

Cuerpo de la skill.
"""


def test_tool_manifest_without_the_new_fields_is_still_valid() -> None:
    """Retro-compatibilidad: es la mitad del contrato que protege el catálogo vivo."""
    manifest = parse_tool_manifest(_TOOL_MIN)
    assert manifest.targets == ()
    assert manifest.config_schema == {}
    # Y el JSONB del listing no gana claves sintéticas.
    rendered = manifest.to_manifest_dict()
    assert "targets" not in rendered
    assert "config_schema" not in rendered


def test_skill_manifest_without_the_new_fields_is_still_valid() -> None:
    manifest = parse_skill_md(_SKILL_MIN)
    assert manifest.targets == ()
    assert manifest.config_schema == {}
    rendered = manifest.to_manifest_dict()
    assert "targets" not in rendered and "config_schema" not in rendered


def test_the_published_playwright_manifest_still_parses() -> None:
    """El listing estrella YA publicado pasa por el parser nuevo sin tocarlo."""
    manifest = parse_tool_manifest(PLAYWRIGHT_TOOL_YAML)
    assert manifest.name == "playwright"
    assert manifest.targets == ()


def test_tool_manifest_with_targets_and_config_schema() -> None:
    text = (
        _TOOL_MIN
        + """\
targets:
  - backend_dev
  - qa
  - backend_dev
config_schema:
  type: object
  properties:
    base_url:
      type: string
      widget: text
    api_token:
      type: string
      secret: true
  required:
    - base_url
"""
    )
    manifest = parse_tool_manifest(text)
    # De-dupe conservando el orden declarado.
    assert manifest.targets == ("backend_dev", "qa")
    assert manifest.config_schema["properties"]["api_token"]["secret"] is True
    rendered = manifest.to_manifest_dict()
    assert rendered["targets"] == ["backend_dev", "qa"]
    assert rendered["config_schema"]["required"] == ["base_url"]


def test_skill_manifest_with_targets() -> None:
    text = _SKILL_MIN.replace("version: 1.0.0", "version: 1.0.0\ntargets: [technical_writer]")
    manifest = parse_skill_md(text)
    assert manifest.targets == ("technical_writer",)


def test_a_bare_string_target_is_a_one_element_selection() -> None:
    manifest = parse_tool_manifest(_TOOL_MIN + "targets: qa\n")
    assert manifest.targets == ("qa",)


def test_unknown_agent_role_in_targets_is_rejected() -> None:
    """`backend-dev` por `backend_dev` no casa con ningún agente y no da error
    en ninguna otra capa: el despliegue «funciona» sin entregar nada."""
    with pytest.raises(ToolFormatError, match="unknown agent role"):
        parse_tool_manifest(_TOOL_MIN + "targets: [backend-dev]\n")
    with pytest.raises(SkillFormatError, match="unknown agent role"):
        parse_skill_md(_SKILL_MIN.replace("version: 1.0.0", "version: 1.0.0\ntargets: [inventado]"))


@pytest.mark.parametrize(
    "bad",
    [
        "config_schema: no-soy-un-mapping\n",
        "config_schema:\n  properties: [a, b]\n",
        "config_schema:\n  properties:\n    x: not-a-mapping\n",
        "config_schema:\n  properties:\n    x:\n      type: cuaternion\n",
        "config_schema:\n  properties:\n    x:\n      secret: quizas\n",
        "config_schema:\n  properties:\n    x:\n      type: string\n  required: [y]\n",
    ],
)
def test_malformed_config_schema_is_rejected(bad: str) -> None:
    with pytest.raises(ToolFormatError):
        parse_tool_manifest(_TOOL_MIN + bad)


def test_playwright_config_schema_validates_against_its_own_validator() -> None:
    """El puente entre lo viejo y lo nuevo: el `config_schema` que ya existe se
    valida con el validador genérico, y sus defaults son un despliegue válido.

    Si esto se rompiera, la fase 5 (mudar Playwright al modelo nuevo) partiría
    de una premisa falsa.
    """
    schema = config_schema()
    defaults = apply_defaults(schema, {})
    assert validate_deployment_config(schema, defaults) == [], defaults
    # Y un navegador inventado NO pasa.
    assert validate_deployment_config(schema, {**defaults, "browsers": ["netscape"]}) != []
