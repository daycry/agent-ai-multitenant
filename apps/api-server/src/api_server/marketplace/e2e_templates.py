"""Pre-loaded E2E test templates — a curated registry (Plan 09 task_09_15).

Plan 09 Fase D ships, alongside the flagship Playwright tool (task_09_13) and
the QA E2E Automator agent (task_09_14), a curated set of **ready-to-use
Playwright E2E test templates** for the common user flows every web app shares:
login, signup, checkout, search and form-submit. Each template is a
*parametrized, well-formed Playwright spec skeleton* the QA agent — or a human —
instantiates against their own app: the selectors and URLs are **parameters**,
not hard-coded, so one skeleton drives many apps.

This module is the single source of truth for that registry, in three layers
that reuse the Phase A-D substrate rather than inventing a parallel concept:

  1. :class:`E2ETemplateParameter` + :class:`E2ETestTemplate` — the typed,
     immutable model. A template declares its ``name`` / ``description`` /
     ``version`` (semver, validated by the SAME shared helper the skill + tool
     formats use) / a list of declared :class:`E2ETemplateParameter` (each with
     a name, description, example and optional default) / and a ``body`` — the
     Playwright spec skeleton with ``{{param}}`` placeholders. Validation
     (:meth:`E2ETestTemplate.validate`) proves the template is well-formed:
     valid semver, uniquely-named parameters, and — crucially — **every
     placeholder in the body is a declared parameter and every declared
     parameter is actually used** (no undeclared substitution, no dead knob).

  2. :data:`BUILTIN_E2E_TEMPLATES` + :func:`load_e2e_templates` /
     :func:`get_e2e_template` — the curated, versioned content and its loader.
     The templates are *content/seed* (a registry), not schema: no migration,
     no new table — they live as definition data the loader returns. The QA
     agent reads them through :func:`get_e2e_template`; an unknown name raises a
     typed :class:`E2ETemplateError`.

  3. :meth:`E2ETestTemplate.instantiate` — substitutes a caller's
     ``{param: value}`` mapping into the skeleton, yielding a concrete spec
     string ready to drop under ``apps/admin-panel/e2e/`` (or a tenant repo).
     A missing required parameter (one without a default) is a typed error; a
     parameter with a default falls back to it.

Multi-tenancy note: these templates are **platform-curated content** with no
tenant ownership — the same status as the GLOBAL Playwright listing (Phase A
hybrid model: catalog content is global, only *installations* are
tenant-scoped). A tenant never owns or mutates the registry; it merely
instantiates a copy into its own project repo. There is therefore no RLS
surface here — the registry is pure, importable, no-I/O Python.

Pure Python, no DB, no migration, no new dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from api_server.marketplace._format_common import is_valid_semver

# A ``{{placeholder}}`` reference in a template body. The name inside is a
# simple identifier (letters / digits / underscore) — a placeholder that does
# not match is left untouched and flagged by validation as malformed.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class E2ETemplateError(ValueError):
    """An E2E test template is malformed, unknown, or wrongly instantiated.

    Raised for: a template that fails validation (bad semver, duplicate or
    undeclared parameter, dead parameter, malformed placeholder), an unknown
    template name in :func:`get_e2e_template`, or an instantiation missing a
    required parameter.

    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers
    (and the routers' 422 mapping) keep working, while callers that care can
    catch the precise type.
    """


@dataclass(frozen=True, slots=True)
class E2ETemplateParameter:
    """One declared parameter of an E2E test template.

    ``name`` is the identifier substituted for ``{{name}}`` in the body.
    ``description`` documents what the operator should supply; ``example`` is a
    realistic sample value the UI can pre-fill. ``default`` makes the parameter
    optional at instantiation time — when absent the operator MUST supply it.
    """

    name: str
    description: str
    example: str
    default: str | None = None

    @property
    def required(self) -> bool:
        """True when the parameter has no default and must be supplied."""
        return self.default is None

    def to_dict(self) -> dict[str, Any]:
        """Render the parameter as a JSON-able mapping (for the UI / catalog)."""
        return {
            "name": self.name,
            "description": self.description,
            "example": self.example,
            "default": self.default,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class E2ETestTemplate:
    """A parametrized, well-formed Playwright spec skeleton.

    ``frozen`` + ``slots`` so a registered template is immutable and cheap. The
    ``body`` is a Playwright spec with ``{{param}}`` placeholders; the declared
    :attr:`parameters` say what those placeholders are. :meth:`validate` proves
    the two agree; :meth:`instantiate` substitutes a concrete mapping.
    """

    name: str
    description: str
    version: str
    parameters: tuple[E2ETemplateParameter, ...]
    body: str
    # The user flow this template exercises (login / signup / checkout / ...),
    # kept as plain metadata the UI groups by.
    flow: str

    @property
    def parameter_names(self) -> tuple[str, ...]:
        """The declared parameter names, in declaration order."""
        return tuple(p.name for p in self.parameters)

    def placeholders(self) -> set[str]:
        """The distinct ``{{placeholder}}`` names referenced in the body."""
        return set(_PLACEHOLDER_RE.findall(self.body))

    def validate(self) -> None:
        """Raise :class:`E2ETemplateError` unless the template is well-formed.

        Checks, in order:

          * ``name`` / ``description`` / ``body`` are non-empty,
          * ``version`` is valid semver (the shared helper — no re-encoding),
          * parameter names are non-empty and unique,
          * every declared parameter is referenced by the body (no dead knob),
          * every ``{{placeholder}}`` in the body is a declared parameter (no
            undeclared substitution would silently leak through).
        """
        if not self.name or not self.name.strip():
            raise E2ETemplateError("e2e template 'name' must be a non-empty string")
        if not self.description or not self.description.strip():
            raise E2ETemplateError(
                f"e2e template {self.name!r} 'description' must be a non-empty string"
            )
        if not self.body or not self.body.strip():
            raise E2ETemplateError(f"e2e template {self.name!r} 'body' must be a non-empty string")
        if not is_valid_semver(self.version):
            raise E2ETemplateError(
                f"e2e template {self.name!r} 'version' is not a valid semver string: "
                f"{self.version!r}"
            )

        seen: set[str] = set()
        for param in self.parameters:
            if not param.name or not param.name.strip():
                raise E2ETemplateError(
                    f"e2e template {self.name!r} has a parameter with an empty name"
                )
            if param.name in seen:
                raise E2ETemplateError(
                    f"e2e template {self.name!r} declares duplicate parameter {param.name!r}"
                )
            seen.add(param.name)

        placeholders = self.placeholders()
        declared = set(self.parameter_names)

        undeclared = placeholders - declared
        if undeclared:
            raise E2ETemplateError(
                f"e2e template {self.name!r} references undeclared parameter(s): "
                f"{', '.join(sorted(undeclared))}"
            )

        unused = declared - placeholders
        if unused:
            raise E2ETemplateError(
                f"e2e template {self.name!r} declares unused parameter(s): "
                f"{', '.join(sorted(unused))}"
            )

    def instantiate(self, values: dict[str, str] | None = None) -> str:
        """Substitute ``values`` into the body, returning a concrete spec.

        A declared parameter not present in ``values`` falls back to its
        ``default``; a required parameter (no default) that is still absent
        raises :class:`E2ETemplateError`. Unknown keys in ``values`` (not a
        declared parameter) are rejected — a typo must not silently no-op.
        Every placeholder resolves, so the returned string carries no
        ``{{...}}`` markers.
        """
        supplied = dict(values or {})
        declared = {p.name: p for p in self.parameters}

        unknown = set(supplied) - set(declared)
        if unknown:
            raise E2ETemplateError(
                f"e2e template {self.name!r} got unknown parameter(s): "
                f"{', '.join(sorted(unknown))}"
            )

        resolved: dict[str, str] = {}
        for name, param in declared.items():
            if name in supplied:
                resolved[name] = supplied[name]
            elif param.default is not None:
                resolved[name] = param.default
            else:
                raise E2ETemplateError(
                    f"e2e template {self.name!r} is missing required parameter {name!r}"
                )

        def _replace(match: re.Match[str]) -> str:
            return resolved[match.group(1)]

        return _PLACEHOLDER_RE.sub(_replace, self.body)

    def to_dict(self) -> dict[str, Any]:
        """Render the template's metadata as a JSON-able mapping (catalog/UI)."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "flow": self.flow,
            "parameters": [p.to_dict() for p in self.parameters],
        }


# ---------------------------------------------------------------------------
# Common parameters reused across flows (URL + credential selectors).
# ---------------------------------------------------------------------------
_BASE_URL = E2ETemplateParameter(
    name="base_url",
    description="The base URL of the application under test.",
    example="https://app.example.test",
    default="http://localhost:3000",
)


def _login_template() -> E2ETestTemplate:
    body = """\
import { expect, test } from "@playwright/test";

// E2E: login flow (instantiated from the 'login' template).
test("a registered user can log in", async ({ page }) => {
  await page.goto("{{base_url}}{{login_path}}");
  await page.fill("{{email_selector}}", "{{email_value}}");
  await page.fill("{{password_selector}}", "{{password_value}}");
  await page.click("{{submit_selector}}");
  await expect(page).toHaveURL(new RegExp("{{success_url_pattern}}"));
  await expect(page.locator("{{success_selector}}")).toBeVisible();
});
"""
    return E2ETestTemplate(
        name="login",
        description="Log a registered user in and assert the post-login landing.",
        version="1.0.0",
        flow="login",
        body=body,
        parameters=(
            _BASE_URL,
            E2ETemplateParameter(
                "login_path", "Path to the login page.", "/login", default="/login"
            ),
            E2ETemplateParameter(
                "email_selector",
                "Selector for the email/username field.",
                "input[name='email']",
            ),
            E2ETemplateParameter("email_value", "The email to log in with.", "user@example.test"),
            E2ETemplateParameter(
                "password_selector",
                "Selector for the password field.",
                "input[name='password']",
            ),
            E2ETemplateParameter("password_value", "The password to log in with.", "s3cr3t!"),
            E2ETemplateParameter(
                "submit_selector",
                "Selector for the submit/login button.",
                "button[type='submit']",
            ),
            E2ETemplateParameter(
                "success_url_pattern",
                "Regex the URL should match after a successful login.",
                "/dashboard",
                default="/dashboard",
            ),
            E2ETemplateParameter(
                "success_selector",
                "Selector for an element visible only when logged in.",
                "[data-testid='user-menu']",
            ),
        ),
    )


def _signup_template() -> E2ETestTemplate:
    body = """\
import { expect, test } from "@playwright/test";

// E2E: signup flow (instantiated from the 'signup' template).
test("a new user can sign up", async ({ page }) => {
  await page.goto("{{base_url}}{{signup_path}}");
  await page.fill("{{email_selector}}", "{{email_value}}");
  await page.fill("{{password_selector}}", "{{password_value}}");
  await page.fill("{{confirm_selector}}", "{{password_value}}");
  await page.click("{{submit_selector}}");
  await expect(page.locator("{{success_selector}}")).toBeVisible();
});
"""
    return E2ETestTemplate(
        name="signup",
        description="Register a brand-new account and assert the welcome state.",
        version="1.0.0",
        flow="signup",
        body=body,
        parameters=(
            _BASE_URL,
            E2ETemplateParameter(
                "signup_path", "Path to the signup page.", "/signup", default="/signup"
            ),
            E2ETemplateParameter(
                "email_selector",
                "Selector for the email field.",
                "input[name='email']",
            ),
            E2ETemplateParameter(
                "email_value", "A unique email to register.", "new-user@example.test"
            ),
            E2ETemplateParameter(
                "password_selector",
                "Selector for the password field.",
                "input[name='password']",
            ),
            E2ETemplateParameter("password_value", "The password to register with.", "s3cr3t!"),
            E2ETemplateParameter(
                "confirm_selector",
                "Selector for the confirm-password field.",
                "input[name='confirm']",
            ),
            E2ETemplateParameter(
                "submit_selector",
                "Selector for the create-account button.",
                "button[type='submit']",
            ),
            E2ETemplateParameter(
                "success_selector",
                "Selector for an element shown after a successful signup.",
                "[data-testid='welcome']",
            ),
        ),
    )


def _checkout_template() -> E2ETestTemplate:
    body = """\
import { expect, test } from "@playwright/test";

// E2E: checkout flow (instantiated from the 'checkout' template).
test("a shopper can complete checkout", async ({ page }) => {
  await page.goto("{{base_url}}{{product_path}}");
  await page.click("{{add_to_cart_selector}}");
  await page.goto("{{base_url}}{{cart_path}}");
  await page.click("{{checkout_selector}}");
  await page.fill("{{card_selector}}", "{{card_value}}");
  await page.click("{{pay_selector}}");
  await expect(page.locator("{{confirmation_selector}}")).toBeVisible();
});
"""
    return E2ETestTemplate(
        name="checkout",
        description="Add a product to the cart and complete a checkout/payment.",
        version="1.0.0",
        flow="checkout",
        body=body,
        parameters=(
            _BASE_URL,
            E2ETemplateParameter(
                "product_path", "Path to a product page.", "/products/1", default="/products/1"
            ),
            E2ETemplateParameter(
                "add_to_cart_selector",
                "Selector for the add-to-cart button.",
                "[data-testid='add-to-cart']",
            ),
            E2ETemplateParameter("cart_path", "Path to the cart page.", "/cart", default="/cart"),
            E2ETemplateParameter(
                "checkout_selector",
                "Selector for the proceed-to-checkout button.",
                "[data-testid='checkout']",
            ),
            E2ETemplateParameter(
                "card_selector",
                "Selector for the card-number field.",
                "input[name='card']",
            ),
            E2ETemplateParameter("card_value", "A (test) card number.", "4242424242424242"),
            E2ETemplateParameter(
                "pay_selector",
                "Selector for the pay/confirm-order button.",
                "[data-testid='pay']",
            ),
            E2ETemplateParameter(
                "confirmation_selector",
                "Selector for the order-confirmation element.",
                "[data-testid='order-confirmed']",
            ),
        ),
    )


def _search_template() -> E2ETestTemplate:
    body = """\
import { expect, test } from "@playwright/test";

// E2E: search flow (instantiated from the 'search' template).
test("a user can search and see results", async ({ page }) => {
  await page.goto("{{base_url}}{{search_path}}");
  await page.fill("{{search_selector}}", "{{query_value}}");
  await page.press("{{search_selector}}", "Enter");
  await expect(page.locator("{{results_selector}}")).toBeVisible();
  await expect(page.locator("{{results_selector}}")).toContainText("{{expected_text}}");
});
"""
    return E2ETestTemplate(
        name="search",
        description="Run a search query and assert matching results render.",
        version="1.0.0",
        flow="search",
        body=body,
        parameters=(
            _BASE_URL,
            E2ETemplateParameter(
                "search_path", "Path to the search page.", "/search", default="/search"
            ),
            E2ETemplateParameter(
                "search_selector",
                "Selector for the search input.",
                "input[type='search']",
            ),
            E2ETemplateParameter("query_value", "The search query to type.", "laptop"),
            E2ETemplateParameter(
                "results_selector",
                "Selector for the results container.",
                "[data-testid='results']",
            ),
            E2ETemplateParameter(
                "expected_text",
                "Text the results are expected to contain.",
                "laptop",
            ),
        ),
    )


def _form_submit_template() -> E2ETestTemplate:
    body = """\
import { expect, test } from "@playwright/test";

// E2E: generic form-submit flow (instantiated from the 'form-submit' template).
test("a user can submit a form and see confirmation", async ({ page }) => {
  await page.goto("{{base_url}}{{form_path}}");
  await page.fill("{{name_selector}}", "{{name_value}}");
  await page.fill("{{message_selector}}", "{{message_value}}");
  await page.click("{{submit_selector}}");
  await expect(page.locator("{{success_selector}}")).toBeVisible();
});
"""
    return E2ETestTemplate(
        name="form-submit",
        description="Fill and submit a generic form, asserting the success message.",
        version="1.0.0",
        flow="form-submit",
        body=body,
        parameters=(
            _BASE_URL,
            E2ETemplateParameter(
                "form_path", "Path to the form page.", "/contact", default="/contact"
            ),
            E2ETemplateParameter(
                "name_selector",
                "Selector for the name field.",
                "input[name='name']",
            ),
            E2ETemplateParameter("name_value", "The name to enter.", "Ada Lovelace"),
            E2ETemplateParameter(
                "message_selector",
                "Selector for the message/textarea field.",
                "textarea[name='message']",
            ),
            E2ETemplateParameter("message_value", "The message to enter.", "Hello there!"),
            E2ETemplateParameter(
                "submit_selector",
                "Selector for the submit button.",
                "button[type='submit']",
            ),
            E2ETemplateParameter(
                "success_selector",
                "Selector for the success/confirmation element.",
                "[data-testid='form-success']",
            ),
        ),
    )


# The curated, versioned registry — keyed by template name. Built once at
# import time from the factory functions so the bodies stay readable above.
BUILTIN_E2E_TEMPLATES: dict[str, E2ETestTemplate] = {
    template.name: template
    for template in (
        _login_template(),
        _signup_template(),
        _checkout_template(),
        _search_template(),
        _form_submit_template(),
    )
}


def load_e2e_templates() -> dict[str, E2ETestTemplate]:
    """Return the curated registry, validating every template first.

    The loader is the single entry point the QA agent / catalog uses to read
    the registry. It validates each template (so a malformed builtin fails
    loudly at load time, not at instantiation) and returns a fresh dict keyed
    by name. Pure — no I/O, no DB.
    """
    for template in BUILTIN_E2E_TEMPLATES.values():
        template.validate()
    return dict(BUILTIN_E2E_TEMPLATES)


def get_e2e_template(name: str) -> E2ETestTemplate:
    """Return one validated template by name, or raise on an unknown name.

    Raises :class:`E2ETemplateError` if ``name`` is not in the registry —
    the QA agent must never silently get a wrong/empty template.
    """
    template = BUILTIN_E2E_TEMPLATES.get(name)
    if template is None:
        known = ", ".join(sorted(BUILTIN_E2E_TEMPLATES))
        raise E2ETemplateError(f"unknown e2e template {name!r}; known templates: {known}")
    template.validate()
    return template


__all__ = [
    "BUILTIN_E2E_TEMPLATES",
    "E2ETemplateError",
    "E2ETemplateParameter",
    "E2ETestTemplate",
    "get_e2e_template",
    "load_e2e_templates",
]
