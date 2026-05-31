"""Integration tests for the pre-loaded E2E test templates (Plan 09 task_09_15).

Plan 09 Fase D ships a curated registry of ready-to-use Playwright E2E test
templates for the common web flows (login / signup / checkout / search /
form-submit). Each template is a parametrized, well-formed Playwright spec
skeleton the QA E2E Automator (task_09_14) — or a human — instantiates against
its own app: the URLs + selectors are declared parameters, not hard-coded.

These tests verify the contract the task names:
  * all builtin templates load AND validate (well-formed: valid semver, every
    placeholder declared, every declared parameter used);
  * each template declares its parameters;
  * the flagship flows login / signup / checkout are present (plus search +
    form-submit);
  * an instantiated template substitutes parameters correctly and leaves no
    ``{{...}}`` markers behind;
  * an unknown template name errors with the typed E2ETemplateError.

Multi-tenancy note: the registry is **platform-curated content** with no tenant
ownership (the same status as the GLOBAL Playwright listing — Phase A hybrid
model: catalog content is global, only installations are tenant-scoped). There
is therefore no RLS surface and no cross-tenant boundary here — the module is
pure, importable, no-I/O Python, so these tests need no DB.
"""

from __future__ import annotations

import re

import pytest
from api_server.marketplace.e2e_templates import (
    BUILTIN_E2E_TEMPLATES,
    E2ETemplateError,
    E2ETestTemplate,
    get_e2e_template,
    load_e2e_templates,
)

pytestmark = pytest.mark.integration

# The flows the task names explicitly (login/signup/checkout) + the couple of
# extra common ones the registry adds (search/form-submit).
EXPECTED_TEMPLATES = {"login", "signup", "checkout", "search", "form-submit"}
_PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")


# ---------------------------------------------------------------------------
# Load + validate
# ---------------------------------------------------------------------------
def test_all_builtin_templates_load_and_validate() -> None:
    """Every builtin template loads through the loader and validates well-formed."""
    templates = load_e2e_templates()
    assert templates, "registry is empty"
    assert set(templates) == set(BUILTIN_E2E_TEMPLATES)
    for template in templates.values():
        assert isinstance(template, E2ETestTemplate)
        # validate() raises on any malformation; reaching here means well-formed.
        template.validate()


def test_flagship_flows_are_present() -> None:
    """login / signup / checkout (named by the task) + search + form-submit."""
    templates = load_e2e_templates()
    assert set(templates) == EXPECTED_TEMPLATES
    for name in ("login", "signup", "checkout"):
        assert name in templates, f"missing flagship flow {name!r}"


def test_each_template_declares_its_parameters() -> None:
    """Each template declares a non-empty, uniquely-named parameter set, and
    every declared parameter is actually referenced by the body."""
    for name, template in load_e2e_templates().items():
        assert template.parameters, f"{name!r} declares no parameters"
        names = template.parameter_names
        assert len(names) == len(set(names)), f"{name!r} has duplicate parameter names"
        # Validation guarantees declared == referenced; assert the agreement.
        assert template.placeholders() == set(
            names
        ), f"{name!r} declared/referenced parameter mismatch"
        for param in template.parameters:
            assert param.name and param.name.strip()
            assert param.description and param.description.strip()
            assert param.example


def test_template_bodies_are_semver_versioned() -> None:
    """Every template carries a valid semver version (the registry is versioned)."""
    for template in load_e2e_templates().values():
        # validate() already enforces semver; assert the field is populated.
        assert template.version
        template.validate()


# ---------------------------------------------------------------------------
# Instantiate
# ---------------------------------------------------------------------------
def test_instantiation_substitutes_parameters_correctly() -> None:
    """Supplying values substitutes them into the body; no markers remain."""
    template = get_e2e_template("login")
    values = {
        "base_url": "https://app.example.test",
        "login_path": "/sign-in",
        "email_selector": "#email",
        "email_value": "ada@example.test",
        "password_selector": "#password",
        "password_value": "hunter2",
        "submit_selector": "#login-btn",
        "success_url_pattern": "/home",
        "success_selector": "#avatar",
    }
    rendered = template.instantiate(values)

    # Every supplied value is present, and no placeholder survives.
    for value in values.values():
        assert value in rendered
    assert not _PLACEHOLDER_RE.search(rendered), "an unresolved {{placeholder}} remained"
    # It is recognisably a Playwright spec.
    assert "@playwright/test" in rendered
    assert "test(" in rendered


def test_instantiation_falls_back_to_defaults() -> None:
    """Optional parameters (with a default) need not be supplied."""
    template = get_e2e_template("login")
    # Supply only the required params; defaulted ones (login_path,
    # success_url_pattern, base_url) fall back.
    rendered = template.instantiate(
        {
            "email_selector": "#email",
            "email_value": "ada@example.test",
            "password_selector": "#password",
            "password_value": "hunter2",
            "submit_selector": "#login-btn",
            "success_selector": "#avatar",
        }
    )
    assert not _PLACEHOLDER_RE.search(rendered)
    # The base_url default lands in the body.
    assert "http://localhost:3000" in rendered
    assert "/login" in rendered


def test_instantiation_missing_required_parameter_errors() -> None:
    """A required parameter (no default) left absent raises E2ETemplateError."""
    template = get_e2e_template("login")
    with pytest.raises(E2ETemplateError, match="missing required parameter"):
        template.instantiate({"base_url": "https://app.example.test"})


def test_instantiation_unknown_parameter_errors() -> None:
    """An undeclared key in the values mapping is rejected (no silent no-op)."""
    template = get_e2e_template("search")
    with pytest.raises(E2ETemplateError, match="unknown parameter"):
        template.instantiate({"not_a_real_param": "x"})


def test_checkout_instantiation_is_concrete() -> None:
    """The checkout flow instantiates into a concrete, marker-free spec."""
    template = get_e2e_template("checkout")
    rendered = template.instantiate(
        {
            "add_to_cart_selector": "#add",
            "checkout_selector": "#checkout",
            "card_selector": "#card",
            "card_value": "4242424242424242",
            "pay_selector": "#pay",
            "confirmation_selector": "#confirmed",
        }
    )
    assert not _PLACEHOLDER_RE.search(rendered)
    assert "4242424242424242" in rendered


# ---------------------------------------------------------------------------
# Unknown template
# ---------------------------------------------------------------------------
def test_unknown_template_errors() -> None:
    """get_e2e_template on an unknown name raises a typed, helpful error."""
    with pytest.raises(E2ETemplateError, match="unknown e2e template"):
        get_e2e_template("does-not-exist")


def test_validation_rejects_undeclared_placeholder() -> None:
    """A template whose body references an undeclared parameter fails validation."""
    bad = E2ETestTemplate(
        name="bad",
        description="references {{ghost}} which is not declared",
        version="1.0.0",
        flow="login",
        body="await page.goto('{{ghost}}');\n",
        parameters=(),
    )
    with pytest.raises(E2ETemplateError, match="undeclared parameter"):
        bad.validate()


def test_validation_rejects_dead_parameter() -> None:
    """A declared-but-unused parameter fails validation (no dead knob)."""
    from api_server.marketplace.e2e_templates import E2ETemplateParameter

    bad = E2ETestTemplate(
        name="bad",
        description="declares an unused parameter",
        version="1.0.0",
        flow="login",
        body="const x = 1;\n",
        parameters=(E2ETemplateParameter("unused", "never referenced", "x"),),
    )
    with pytest.raises(E2ETemplateError, match="unused parameter"):
        bad.validate()


def test_validation_rejects_bad_semver() -> None:
    """A non-semver version fails validation."""
    bad = E2ETestTemplate(
        name="bad",
        description="bad version",
        version="1.2",  # not a full MAJOR.MINOR.PATCH
        flow="login",
        body="const x = 1;\n",
        parameters=(),
    )
    with pytest.raises(E2ETemplateError, match="semver"):
        bad.validate()
