"""Unit tests for the sandboxed Jinja2 notification template system
(Plan 10 task_10_03).

In-process, no DB: the template engine is pure rendering. We pin the
contract the rest of Plan 10 (the event mapping task_10_04, the channel
adapters Fase B/C) builds on:

  - a builtin template renders with context in BOTH ``es`` and ``en``
    (CLAUDE.md §12: ES + EN only),
  - a MISSING context variable is handled safely (renders empty, never
    crashes the dispatcher),
  - the SANDBOX blocks dangerous expressions (attribute access to dunders /
    unsafe calls raise a clear ``TemplateRenderError``, never execute),
  - an UNKNOWN ``(event_type, locale)`` with no override and no builtin
    raises a clear error rather than silently sending an empty message,
  - a TENANT OVERRIDE beats the builtin fallback,
  - ``autoescape`` is ON for markup channels (email / telegram) and OFF for
    plaintext / JSON channels (sms / webhook / slack / …).
"""

from __future__ import annotations

import pytest
from notification_dispatcher.templates import (
    BUILTIN_TEMPLATES,
    DEFAULT_LOCALE,
    MARKUP_CHANNEL_TYPES,
    SUPPORTED_LOCALES,
    TemplateRenderError,
    TemplateSource,
    builtin_event_types,
    get_builtin,
    render_notification,
    render_template,
    template_source_from_row,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Builtin catalogue — core events in BOTH locales (CLAUDE.md §12: ES + EN).
# ---------------------------------------------------------------------------
def test_supported_locales_are_es_en_only() -> None:
    assert {"es", "en"} == SUPPORTED_LOCALES
    assert DEFAULT_LOCALE in SUPPORTED_LOCALES


def test_core_events_have_builtins_in_both_locales() -> None:
    """Core preloaded events ship in es+en. `task_failed` se RETIRÓ (NOTIF-3):
    era imposible por diseño (los fallos de run convergen en `blocked`, que ya
    notifica task_blocked) y daba cobertura ilusoria."""
    core_events = {
        "plan_approved",
        "plan_rejected",
        "execution_finished",
        "execution_failed",
        "review_requested",
    }
    assert core_events <= builtin_event_types()
    assert "task_failed" not in builtin_event_types()
    for event in core_events:
        for locale in ("es", "en"):
            assert (event, locale) in BUILTIN_TEMPLATES, f"missing {event}/{locale}"


def test_builtin_renders_with_context_in_es() -> None:
    rendered = render_notification(
        event_type="plan_approved",
        channel_type="sms",  # plaintext channel — no escaping noise
        locale="es",
        context={"plan_name": "Lanzamiento", "project_name": "Web", "approver": "Ana"},
    )
    assert "Lanzamiento" in rendered.body
    assert "Web" in rendered.body
    assert "Ana" in rendered.body
    assert rendered.subject is not None
    assert "Lanzamiento" in rendered.subject


def test_builtin_renders_with_context_in_en() -> None:
    rendered = render_notification(
        event_type="plan_approved",
        channel_type="sms",
        locale="en",
        context={"plan_name": "Launch", "project_name": "Web", "approver": "Bob"},
    )
    assert "Launch" in rendered.body
    assert "Web" in rendered.body
    assert "Bob" in rendered.body
    assert rendered.subject is not None and "Launch" in rendered.subject


def test_es_and_en_differ() -> None:
    """The two locales are genuinely distinct text, not the same string."""
    ctx = {"plan_name": "P", "project_name": "Q", "approver": "R"}
    es = render_notification(
        event_type="plan_approved", channel_type="sms", locale="es", context=ctx
    )
    en = render_notification(
        event_type="plan_approved", channel_type="sms", locale="en", context=ctx
    )
    assert es.body != en.body


# ---------------------------------------------------------------------------
# Missing variables — handled safely (never crashes the dispatcher).
# ---------------------------------------------------------------------------
def test_missing_variables_render_safely() -> None:
    """An empty context must not raise; undefined vars fall back to the
    template's `default(...)` (or render empty), producing a deliverable
    message rather than a crash."""
    rendered = render_notification(
        event_type="task_blocked",
        channel_type="sms",
        locale="en",
        context={},  # nothing supplied
    )
    # The default(...) filter kicks in for the named vars; the message is
    # still non-empty and contains no raw Jinja markers.
    assert rendered.body
    assert "{{" not in rendered.body
    assert "untitled" in rendered.body


def test_missing_nested_attribute_renders_empty_not_error() -> None:
    """ChainableUndefined: a missing attribute of a missing var renders
    empty instead of raising — a half-populated context is tolerated."""
    rendered = render_template(
        TemplateSource(body="Hello {{ user.profile.name }}!"),
        context={},
        channel_type="sms",
    )
    assert rendered.body == "Hello !"


# ---------------------------------------------------------------------------
# Sandbox — dangerous expressions are blocked, never executed.
# ---------------------------------------------------------------------------
def test_sandbox_blocks_dunder_attribute_access() -> None:
    """The classic sandbox-escape probe (reaching __class__ / __mro__ to get
    at builtins) must be blocked with a clear TemplateRenderError."""
    payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    with pytest.raises(TemplateRenderError):
        render_template(TemplateSource(body=payload), context={}, channel_type="sms")


def test_sandbox_neutralizes_unsafe_attribute_on_context_object() -> None:
    """Even with a real object in context, the sandbox must NOT leak its
    unsafe internals (e.g. the real ``__globals__`` of a passed function).

    Jinja2's sandbox blocks unsafe attribute access; depending on the
    attribute it either raises a SecurityError (e.g. ``__class__`` on a str,
    covered above) or — combined with ChainableUndefined — resolves it to
    undefined (renders empty). The security property under test is the same
    either way: the real internals are never exposed in the output."""

    def some_func() -> None:  # pragma: no cover - never actually called
        return None

    rendered = render_template(
        TemplateSource(body="[{{ fn.__globals__ }}]"),
        context={"fn": some_func},
        channel_type="sms",
    )
    # The real globals dict (which would contain module names) is NOT leaked.
    assert rendered.body == "[]"
    assert "__builtins__" not in rendered.body
    assert "module" not in rendered.body


def test_sandbox_allows_ordinary_filters() -> None:
    """The sandbox is not so strict it breaks legitimate templating: plain
    filters (upper, default) still work."""
    rendered = render_template(
        TemplateSource(body="{{ name | upper }}"),
        context={"name": "ada"},
        channel_type="sms",
    )
    assert rendered.body == "ADA"


# ---------------------------------------------------------------------------
# Unknown key — a clear error, never a silent empty send.
# ---------------------------------------------------------------------------
def test_unknown_event_with_no_builtin_raises_clear_error() -> None:
    with pytest.raises(TemplateRenderError) as exc:
        render_notification(
            event_type="totally_unknown_event",
            channel_type="email",
            locale="en",
            context={},
        )
    assert "no notification template" in str(exc.value)


def test_unknown_locale_falls_back_to_default_builtin() -> None:
    """A locale outside ES/EN still produces a message via the DEFAULT_LOCALE
    builtin (graceful), rather than erroring."""
    src = get_builtin("plan_approved", "fr")  # unsupported locale
    assert src is not None  # fell back to DEFAULT_LOCALE
    rendered = render_notification(
        event_type="plan_approved",
        channel_type="sms",
        locale="fr",
        context={"plan_name": "X"},
    )
    assert "X" in rendered.body


# ---------------------------------------------------------------------------
# Tenant override beats the builtin fallback.
# ---------------------------------------------------------------------------
def test_tenant_override_beats_builtin() -> None:
    override = TemplateSource(
        subject="[ACME] {{ plan_name }}",
        body="ACME custom: plan {{ plan_name }} approved.",
    )
    rendered = render_notification(
        event_type="plan_approved",
        channel_type="sms",
        locale="en",
        context={"plan_name": "Phoenix"},
        override=override,
    )
    assert rendered.body == "ACME custom: plan Phoenix approved."
    assert rendered.subject == "[ACME] Phoenix"
    # And it is genuinely different from the builtin.
    builtin = render_notification(
        event_type="plan_approved",
        channel_type="sms",
        locale="en",
        context={"plan_name": "Phoenix"},
    )
    assert rendered.body != builtin.body


def test_template_source_from_orm_like_row() -> None:
    """A NotificationTemplate-shaped row adapts to a TemplateSource the
    renderer can consume — the dispatcher hands a resolved override straight
    through this adapter."""

    class _Row:
        body_template = "Body {{ x }}"
        subject_template = "Subj {{ x }}"

    src = template_source_from_row(_Row())
    rendered = render_template(src, context={"x": "1"}, channel_type="sms")
    assert rendered.body == "Body 1"
    assert rendered.subject == "Subj 1"


# ---------------------------------------------------------------------------
# Autoescape — on for markup channels, off for plaintext / JSON.
# ---------------------------------------------------------------------------
def test_markup_channel_set_is_email_and_telegram() -> None:
    assert {"email", "telegram"} == MARKUP_CHANNEL_TYPES


def test_autoescape_on_for_markup_channel() -> None:
    """An email body escapes HTML-significant chars in context values, so a
    value containing markup cannot inject HTML."""
    rendered = render_template(
        TemplateSource(body="Hi {{ name }}"),
        context={"name": "<b>x</b> & y"},
        channel_type="email",
    )
    assert "&lt;b&gt;" in rendered.body
    assert "&amp;" in rendered.body
    assert "<b>" not in rendered.body


def test_autoescape_off_for_plaintext_channel() -> None:
    """An SMS / webhook body does NOT HTML-escape — the raw text is preserved
    (escaping would corrupt a plaintext SMS or a JSON webhook payload)."""
    rendered = render_template(
        TemplateSource(body="Hi {{ name }}"),
        context={"name": "<b>x</b> & y"},
        channel_type="sms",
    )
    assert rendered.body == "Hi <b>x</b> & y"
