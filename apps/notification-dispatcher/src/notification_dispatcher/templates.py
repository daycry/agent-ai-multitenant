"""Sandboxed Jinja2 notification template system (Plan 10 task_10_03).

Renders a notification's subject + body from a Jinja2 template keyed by
``(event_type, channel_type, locale)`` against a context dict. Two layers,
most-specific-wins:

  1. **Tenant override** — a live row in ``notification_templates``
     (``api_server.db.notification.NotificationTemplate``) for the
     request's tenant. The dispatcher passes the resolved override in;
     this module renders it. A tenant override always beats the builtin.
  2. **Builtin fallback** — a template shipped in code in
     :data:`BUILTIN_TEMPLATES` for the core system events, in BOTH ``es``
     and ``en`` (CLAUDE.md §12: ES + EN only). This is the *platform*
     layer of the three-layer model — it lives in code, not as a
     NULL-tenant row, which is why ``notification_templates`` is a plain
     tenant-owned table (see its migration / model docstring).

Safety (CLAUDE.md / spec §17): rendering uses
:class:`jinja2.sandbox.SandboxedEnvironment` so a tenant-authored template
can NEVER execute arbitrary code, reach attributes/builtins, or call unsafe
methods — a blocked expression raises :class:`jinja2.exceptions.SecurityError`
which we surface as :class:`TemplateRenderError`. ``autoescape`` is ON for
markup channels (email / telegram render HTML), so a context value that
contains ``<`` / ``&`` can't inject markup; it is OFF for plaintext / JSON
channels (sms / webhook / slack / teams / discord — whose structured payloads
are JSON-encoded downstream, where HTML-escaping would corrupt the text).

Missing context variables are handled SAFELY, never crashing the dispatcher:
an undefined variable renders as the empty string
(:class:`jinja2.ChainableUndefined`) rather than raising, so a half-populated
event context still produces a deliverable message.

Operational tunables (the markup-channel set, the default locale, the per-
render size cap) live as module constants here / are env-overridable via the
dispatcher :class:`~notification_dispatcher.config.Settings`, never as inline
magic numbers in the render path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jinja2 import ChainableUndefined
from jinja2.exceptions import SecurityError, TemplateError
from jinja2.sandbox import SandboxedEnvironment

# ---------------------------------------------------------------------------
# Tunables (config, not magic numbers).
# ---------------------------------------------------------------------------
# Channels whose body is HTML/markup, so autoescape MUST be on. The others
# (sms / webhook / slack / teams / discord) are plaintext or JSON-structured
# downstream, where HTML-escaping would corrupt the text.
MARKUP_CHANNEL_TYPES: frozenset[str] = frozenset({"email", "telegram"})

# Locales the system ships builtins for (CLAUDE.md §12: ES + EN only).
SUPPORTED_LOCALES: frozenset[str] = frozenset({"es", "en"})
# Locale used when the requested one has no template (and isn't supported).
DEFAULT_LOCALE = "en"


class TemplateRenderError(RuntimeError):
    """A template could not be resolved or rendered.

    Raised for: an unknown ``(event_type, channel_type, locale)`` with no
    tenant override AND no builtin fallback; a malformed template source; a
    sandbox :class:`~jinja2.exceptions.SecurityError` (a blocked dangerous
    expression). The dispatcher turns this into a ``failed`` log + dead-letter
    rather than silently sending an empty message.
    """


@dataclass(frozen=True)
class TemplateSource:
    """The raw Jinja2 source for one notification template.

    A builtin (from :data:`BUILTIN_TEMPLATES`) and a tenant override (an
    ``api_server.db.notification.NotificationTemplate`` row) both reduce to
    this shape, so the renderer treats them identically.
    """

    body: str
    subject: str | None = None


@dataclass(frozen=True)
class RenderedNotification:
    """The rendered, ready-to-deliver message text."""

    subject: str | None
    body: str


# ---------------------------------------------------------------------------
# Builtin templates — the platform-layer fallback shipped in code.
#
# Keyed by (event_type, locale); the same source serves every channel (the
# per-channel structuring — Slack blocks, Teams cards — is layered by the
# channel adapters in Fase B/C, which receive this rendered text). Both ES
# and EN are provided for each core event (plan approved, task failed,
# execution finished, review requested). Adding an event = adding both
# locales here.
# ---------------------------------------------------------------------------
_BUILTINS_RAW: dict[tuple[str, str], TemplateSource] = {
    # --- plan_approved -----------------------------------------------------
    ("plan_approved", "es"): TemplateSource(
        subject="Plan aprobado: {{ plan_name | default('(sin nombre)') }}",
        body=(
            "El plan «{{ plan_name | default('(sin nombre)') }}» "
            "del proyecto «{{ project_name | default('(sin proyecto)') }}» "
            "ha sido aprobado por {{ approver | default('un administrador') }}."
        ),
    ),
    ("plan_approved", "en"): TemplateSource(
        subject="Plan approved: {{ plan_name | default('(unnamed)') }}",
        body=(
            "Plan \"{{ plan_name | default('(unnamed)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\" "
            "was approved by {{ approver | default('an administrator') }}."
        ),
    ),
    # --- antivirus_unreachable (prod-12 av_01 / ADR 0105) -------------------
    ("antivirus_unreachable", "es"): TemplateSource(
        subject="Antivirus inalcanzable ({{ minutes_down | default('?') }} min)",
        body=(
            "El backend antivirus (ClamAV) lleva {{ minutes_down | default('?') }} "
            "minutos sin responder. La ingesta de documentos esta en fail-closed: "
            "los documentos nuevos quedan en `pending_scan` (no se indexan) y se "
            "reescanearan solos cuando el antivirus vuelva. Revisa el servicio clamav."
        ),
    ),
    ("antivirus_unreachable", "en"): TemplateSource(
        subject="Antivirus unreachable ({{ minutes_down | default('?') }} min)",
        body=(
            "The antivirus backend (ClamAV) has been unreachable for "
            "{{ minutes_down | default('?') }} minutes. Document ingestion is "
            "fail-closed: new documents stay in `pending_scan` (not indexed) and "
            "will be rescanned automatically once the antivirus is back. Check the "
            "clamav service."
        ),
    ),
    # --- plan_blocked (c3/T7) ---------------------------------------------
    ("plan_blocked", "es"): TemplateSource(
        subject="Plan bloqueado: {{ plan_name | default('(sin nombre)') }}",
        body=(
            "El plan «{{ plan_name | default('(sin nombre)') }}» "
            "del proyecto «{{ project_name | default('(sin proyecto)') }}» "
            "quedó bloqueado: todas las tareas restantes están bloqueadas y "
            "ninguna puede avanzar sola. Revísalo y desbloquea o reintenta una "
            "tarea para continuar."
        ),
    ),
    ("plan_blocked", "en"): TemplateSource(
        subject="Plan blocked: {{ plan_name | default('(unnamed)') }}",
        body=(
            "Plan \"{{ plan_name | default('(unnamed)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\" "
            "is blocked: every remaining task is blocked and none can advance "
            "on its own. Review it and unblock or retry a task to continue."
        ),
    ),
    # --- task_failed -------------------------------------------------------
    ("task_failed", "es"): TemplateSource(
        subject="Tarea fallida: {{ task_title | default('(sin título)') }}",
        body=(
            "La tarea «{{ task_title | default('(sin título)') }}» "
            "del proyecto «{{ project_name | default('(sin proyecto)') }}» "
            "ha fallado. Motivo: {{ reason | default('desconocido') }}."
        ),
    ),
    ("task_failed", "en"): TemplateSource(
        subject="Task failed: {{ task_title | default('(untitled)') }}",
        body=(
            "Task \"{{ task_title | default('(untitled)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\" "
            "failed. Reason: {{ reason | default('unknown') }}."
        ),
    ),
    # --- execution_finished ------------------------------------------------
    ("execution_finished", "es"): TemplateSource(
        subject="Ejecución finalizada: {{ plan_name | default('(sin nombre)') }}",
        body=(
            "La ejecución del plan «{{ plan_name | default('(sin nombre)') }}» "
            "ha finalizado con estado {{ status | default('desconocido') }} "
            "({{ tasks_done | default(0) }}/{{ tasks_total | default(0) }} tareas)."
        ),
    ),
    ("execution_finished", "en"): TemplateSource(
        subject="Execution finished: {{ plan_name | default('(unnamed)') }}",
        body=(
            "Execution of plan \"{{ plan_name | default('(unnamed)') }}\" "
            "finished with status {{ status | default('unknown') }} "
            "({{ tasks_done | default(0) }}/{{ tasks_total | default(0) }} tasks)."
        ),
    ),
    # --- review_requested --------------------------------------------------
    ("review_requested", "es"): TemplateSource(
        subject="Revisión solicitada: {{ task_title | default('(sin título)') }}",
        body=(
            "Se solicita tu revisión de «{{ task_title | default('(sin título)') }}» "
            "en el proyecto «{{ project_name | default('(sin proyecto)') }}». "
            "Solicitada por {{ requester | default('un agente') }}."
        ),
    ),
    ("review_requested", "en"): TemplateSource(
        subject="Review requested: {{ task_title | default('(untitled)') }}",
        body=(
            "Your review is requested for \"{{ task_title | default('(untitled)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\". "
            "Requested by {{ requester | default('an agent') }}."
        ),
    ),
    # --- task_blocked ------------------------------------------------------
    ("task_blocked", "es"): TemplateSource(
        subject="Tarea bloqueada: {{ task_title | default('(sin título)') }}",
        body=(
            "La tarea «{{ task_title | default('(sin título)') }}» "
            "del proyecto «{{ project_name | default('(sin proyecto)') }}» "
            "está bloqueada. Motivo: {{ reason | default('desconocido') }}."
        ),
    ),
    ("task_blocked", "en"): TemplateSource(
        subject="Task blocked: {{ task_title | default('(untitled)') }}",
        body=(
            "Task \"{{ task_title | default('(untitled)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\" "
            "is blocked. Reason: {{ reason | default('unknown') }}."
        ),
    ),
    # --- plan_rejected -----------------------------------------------------
    ("plan_rejected", "es"): TemplateSource(
        subject="Plan rechazado: {{ plan_name | default('(sin nombre)') }}",
        body=(
            "El plan «{{ plan_name | default('(sin nombre)') }}» "
            "del proyecto «{{ project_name | default('(sin proyecto)') }}» "
            "ha sido rechazado por {{ approver | default('un administrador') }}. "
            "Motivo: {{ reason | default('no indicado') }}."
        ),
    ),
    ("plan_rejected", "en"): TemplateSource(
        subject="Plan rejected: {{ plan_name | default('(unnamed)') }}",
        body=(
            "Plan \"{{ plan_name | default('(unnamed)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\" "
            "was rejected by {{ approver | default('an administrator') }}. "
            "Reason: {{ reason | default('not given') }}."
        ),
    ),
    # --- execution_failed --------------------------------------------------
    ("execution_failed", "es"): TemplateSource(
        subject="Ejecución fallida: {{ plan_name | default('(sin nombre)') }}",
        body=(
            "La ejecución del plan «{{ plan_name | default('(sin nombre)') }}» "
            "ha fallado. Motivo: {{ reason | default('desconocido') }}."
        ),
    ),
    ("execution_failed", "en"): TemplateSource(
        subject="Execution failed: {{ plan_name | default('(unnamed)') }}",
        body=(
            "Execution of plan \"{{ plan_name | default('(unnamed)') }}\" "
            "failed. Reason: {{ reason | default('unknown') }}."
        ),
    ),
    # --- human_validation_needed -------------------------------------------
    ("human_validation_needed", "es"): TemplateSource(
        subject="Validación humana requerida: {{ task_title | default('(sin título)') }}",
        body=(
            "Se requiere tu validación de «{{ task_title | default('(sin título)') }}» "
            "en el proyecto «{{ project_name | default('(sin proyecto)') }}» "
            "antes de continuar."
        ),
    ),
    ("human_validation_needed", "en"): TemplateSource(
        subject="Human validation needed: {{ task_title | default('(untitled)') }}",
        body=(
            "Your validation is needed for \"{{ task_title | default('(untitled)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\" "
            "before work can continue."
        ),
    ),
    # --- human_task_assigned (Plan 16 task_16_05) --------------------------
    # The context carries task_title / project_name / assigned_to (the
    # human-readable assignee name). All keys tolerate a missing value.
    ("human_task_assigned", "es"): TemplateSource(
        subject="Tarea asignada: {{ task_title | default('(sin título)') }}",
        body=(
            "Se te ha asignado la tarea «{{ task_title | default('(sin título)') }}» "
            "en el proyecto «{{ project_name | default('(sin proyecto)') }}». "
            "Acéptala desde tu bandeja antes de que expire el plazo de aceptación."
        ),
    ),
    ("human_task_assigned", "en"): TemplateSource(
        subject="Task assigned: {{ task_title | default('(untitled)') }}",
        body=(
            "You have been assigned the task \"{{ task_title | default('(untitled)') }}\" "
            "in project \"{{ project_name | default('(no project)') }}\". "
            "Accept it from your inbox before the acceptance window expires."
        ),
    ),
    # --- review_escalated --------------------------------------------------
    ("review_escalated", "es"): TemplateSource(
        subject="Revisión escalada: {{ task_title | default('(sin título)') }}",
        body=(
            "La revisión de «{{ task_title | default('(sin título)') }}» "
            "se ha escalado a un humano tras "
            "{{ retries | default('varios') }} reintentos."
        ),
    ),
    ("review_escalated", "en"): TemplateSource(
        subject="Review escalated: {{ task_title | default('(untitled)') }}",
        body=(
            "Review of \"{{ task_title | default('(untitled)') }}\" "
            "was escalated to a human after "
            "{{ retries | default('several') }} retries."
        ),
    ),
    # --- budget_alert (Plan 11.1 task_11_1_05) -----------------------------
    # Scope-aware: ``scope`` is 'tenant' or 'project'; ``plan_name`` carries the
    # human label (the tenant or the project name). ``percent_used`` is the
    # real percent of budget; ``spent`` is the canonical-USD spend. All keys
    # tolerate a missing value (ChainableUndefined → '') so a partial context
    # still renders.
    ("budget_alert", "es"): TemplateSource(
        subject=(
            "Alerta de presupuesto ({{ threshold | default('?') }}%): "
            "{{ plan_name | default('(sin nombre)') }}"
        ),
        body=(
            "El presupuesto "
            "{% if scope == 'project' %}del proyecto «{{ project_name | "
            "default(plan_name) | default('(sin nombre)') }}»"
            "{% else %}del tenant{% endif %} "
            "ha cruzado el umbral del {{ threshold | default('?') }}% "
            "({{ percent_used | default('?') }}% usado). "
            "Gasto actual: {{ spent | default('?') }}."
        ),
    ),
    ("budget_alert", "en"): TemplateSource(
        subject=(
            "Budget alert ({{ threshold | default('?') }}%): "
            "{{ plan_name | default('(unnamed)') }}"
        ),
        body=(
            "The "
            "{% if scope == 'project' %}project \"{{ project_name | "
            "default(plan_name) | default('(unnamed)') }}\"{% else %}tenant{% endif %} "
            "budget crossed the {{ threshold | default('?') }}% threshold "
            "({{ percent_used | default('?') }}% used). "
            "Current spend: {{ spent | default('?') }}."
        ),
    ),
    # --- guardrail_alert (Plan 11 task_11_21) ------------------------------
    ("guardrail_alert", "es"): TemplateSource(
        subject="Alerta de guardrails: {{ rule_name | default('(sin nombre)') }}",
        body=(
            "La regla de alerta «{{ rule_name | default('(sin nombre)') }}» se ha "
            "disparado: {{ count | default('?') }} violación(es) de guardrail "
            "{{ guardrail_type | default('de cualquier tipo') }} "
            "en los últimos {{ window_seconds | default('?') }} s "
            "(umbral {{ threshold | default('?') }})."
        ),
    ),
    ("guardrail_alert", "en"): TemplateSource(
        subject="Guardrail alert: {{ rule_name | default('(unnamed)') }}",
        body=(
            "Alert rule \"{{ rule_name | default('(unnamed)') }}\" tripped: "
            "{{ count | default('?') }} {{ guardrail_type | default('') }} "
            "guardrail violation(s) in the last "
            "{{ window_seconds | default('?') }}s "
            "(threshold {{ threshold | default('?') }})."
        ),
    ),
    # --- quality_drift_alert (Plan 14 task_14_10) --------------------------
    ("quality_drift_alert", "es"): TemplateSource(
        subject="Deriva de calidad detectada en un dataset de evaluación",
        body=(
            "La calidad ha caído de forma sostenida: "
            "{{ consecutive_declines | default('?') }} ejecución(es) consecutiva(s) "
            "con descenso del pass-rate (caída total {{ total_decline | default('?') }}) "
            "sobre una ventana de {{ window | default('?') }} "
            "(umbral por paso {{ drop_threshold | default('?') }}). "
            "Dataset {{ dataset_id | default('(desconocido)') }}."
        ),
    ),
    ("quality_drift_alert", "en"): TemplateSource(
        subject="Quality drift detected on an eval dataset",
        body=(
            "Quality declined in a sustained way: "
            "{{ consecutive_declines | default('?') }} consecutive run(s) with a "
            "pass-rate drop (total slide {{ total_decline | default('?') }}) over a "
            "window of {{ window | default('?') }} "
            "(per-step threshold {{ drop_threshold | default('?') }}). "
            "Dataset {{ dataset_id | default('(unknown)') }}."
        ),
    ),
    # --- agent_outlier_alert (Plan 14 task_14_13) --------------------------
    ("agent_outlier_alert", "es"): TemplateSource(
        subject="Alerta de outlier de agente: {{ rule_name | default('(sin nombre)') }}",
        body=(
            "La regla «{{ rule_name | default('(sin nombre)') }}» ha detectado "
            "{{ flagged_count | default('?') }} agente(s) outlier por "
            "{{ metric | default('métrica') }} en los últimos "
            "{{ window_days | default('?') }} día(s). El más destacado: "
            "«{{ agent_name | default('(sin nombre)') }}» "
            "({{ agent_role | default('?') }}) con valor "
            "{{ value | default('?') }} frente al umbral {{ bound | default('?') }}."
        ),
    ),
    ("agent_outlier_alert", "en"): TemplateSource(
        subject="Agent outlier alert: {{ rule_name | default('(unnamed)') }}",
        body=(
            "Rule \"{{ rule_name | default('(unnamed)') }}\" flagged "
            "{{ flagged_count | default('?') }} outlier agent(s) on "
            "{{ metric | default('a metric') }} over the last "
            "{{ window_days | default('?') }} day(s). Worst: "
            "\"{{ agent_name | default('(unnamed)') }}\" "
            "({{ agent_role | default('?') }}) at {{ value | default('?') }} "
            "vs bound {{ bound | default('?') }}."
        ),
    ),
    # --- credential_rotation_failed (Plan 15 task_15_17) -------------------
    # Platform-scoped ops alert. The context is the rotation audit's
    # secret-free log fields (status / static_secrets / new_lease_id / error) —
    # NEVER a credential value, so the template can only ever render names +
    # lease-ids + the non-leaky error string.
    ("credential_rotation_failed", "es"): TemplateSource(
        subject="Fallo en la rotación automática de credenciales",
        body=(
            "La rotación automática de credenciales (Vault) ha fallado a las "
            "{{ rotated_at | default('(desconocido)') }} con estado "
            "{{ status | default('failed') }}. Motivo: "
            "{{ error | default('(sin detalle)') }}. El sistema sigue operativo "
            "con las credenciales actuales; revisa Vault y el runbook de "
            "rotación de credenciales."
        ),
    ),
    ("credential_rotation_failed", "en"): TemplateSource(
        subject="Automatic credential rotation failed",
        body=(
            "Automatic credential rotation (Vault) failed at "
            "{{ rotated_at | default('(unknown)') }} with status "
            "{{ status | default('failed') }}. Reason: "
            "{{ error | default('(no detail)') }}. The system stays up on its "
            "current credentials; check Vault and the credential-rotation "
            "runbook."
        ),
    ),
    # --- fx_fetch_failed (Plan 11.1 task_11_1_02) --------------------------
    # Platform-scoped ops alert. The context carries only the source name + the
    # non-leaky error string — the catalog keeps its last good rates, so the
    # message points the System Admin at the staleness, not a credential.
    ("fx_fetch_failed", "es"): TemplateSource(
        subject="Fallo al actualizar los tipos de cambio",
        body=(
            "La descarga diaria de tipos de cambio (fuente "
            "{{ source | default('ecb') }}) ha fallado. Motivo: "
            "{{ error | default('(sin detalle)') }}. El catálogo conserva los "
            "últimos tipos válidos (la conversión usa el más reciente anterior); "
            "revisa la fuente de tipos de cambio."
        ),
    ),
    ("fx_fetch_failed", "en"): TemplateSource(
        subject="Exchange-rates update failed",
        body=(
            "The daily exchange-rates fetch (source "
            "{{ source | default('ecb') }}) failed. Reason: "
            "{{ error | default('(no detail)') }}. The catalog keeps its last "
            "good rates (conversion falls back to the most-recent prior rate); "
            "check the exchange-rates source."
        ),
    ),
}

# Public, read-only view of the builtin catalogue keyed by
# (event_type, locale). Importable so the event-mapping task (task_10_04) and
# tests can introspect which events have builtins.
BUILTIN_TEMPLATES: dict[tuple[str, str], TemplateSource] = dict(_BUILTINS_RAW)


def builtin_event_types() -> frozenset[str]:
    """Return the set of event_types that have a builtin template."""
    return frozenset(event for (event, _locale) in BUILTIN_TEMPLATES)


def get_builtin(event_type: str, locale: str) -> TemplateSource | None:
    """Return the builtin :class:`TemplateSource` for the key, or None.

    Falls back to :data:`DEFAULT_LOCALE` when the requested locale is not
    one of the supported ES/EN (or simply has no entry) so an unexpected
    locale still produces a deliverable message rather than nothing.
    """
    source = BUILTIN_TEMPLATES.get((event_type, locale))
    if source is not None:
        return source
    if locale != DEFAULT_LOCALE:
        return BUILTIN_TEMPLATES.get((event_type, DEFAULT_LOCALE))
    return None


# ---------------------------------------------------------------------------
# Sandboxed rendering.
# ---------------------------------------------------------------------------
def _environment(*, autoescape: bool) -> SandboxedEnvironment:
    """Build a fresh sandboxed Jinja2 environment.

    A new environment per render keeps the autoescape decision (which
    depends on the channel) isolated and avoids any shared mutable cache.
    ``ChainableUndefined`` makes a missing variable — or a missing attribute
    of one — render as the empty string instead of raising, so a
    half-populated context never crashes the dispatcher.
    """
    return SandboxedEnvironment(
        autoescape=autoescape,
        undefined=ChainableUndefined,
    )


def _render_one(source: str, context: dict[str, Any], *, autoescape: bool) -> str:
    """Render a single template source string in the sandbox.

    Raises:
        TemplateRenderError: the source is malformed, or the sandbox blocked
            a dangerous expression (a :class:`~jinja2.exceptions.SecurityError`).
    """
    env = _environment(autoescape=autoescape)
    try:
        template = env.from_string(source)
        return template.render(**context)
    except SecurityError as exc:
        # A blocked dangerous expression (attr access, unsafe call). Never
        # echo the offending source back; surface a clear, safe error.
        raise TemplateRenderError(f"template blocked by sandbox: {exc}") from exc
    except TemplateError as exc:
        raise TemplateRenderError(f"template render failed: {exc}") from exc


def render_template(
    source: TemplateSource,
    context: dict[str, Any],
    *,
    channel_type: str,
) -> RenderedNotification:
    """Render an explicit :class:`TemplateSource` against ``context``.

    ``autoescape`` is decided by ``channel_type`` (on for markup channels —
    email / telegram). Used directly when the caller already holds the
    source (e.g. a test, or a resolved tenant override); most callers use
    :func:`render_notification` which resolves override-vs-builtin first.
    """
    autoescape = channel_type in MARKUP_CHANNEL_TYPES
    body = _render_one(source.body, context, autoescape=autoescape)
    subject = (
        _render_one(source.subject, context, autoescape=autoescape)
        if source.subject is not None
        else None
    )
    return RenderedNotification(subject=subject, body=body)


def render_notification(
    *,
    event_type: str,
    channel_type: str,
    locale: str,
    context: dict[str, Any] | None = None,
    override: TemplateSource | None = None,
) -> RenderedNotification:
    """Resolve the template most-specific-wins and render it.

    Resolution order:

      1. ``override`` (a live ``notification_templates`` row for the
         request's tenant, already resolved by the dispatcher under the
         tenant's RLS scope) — a tenant override ALWAYS beats the builtin.
      2. the builtin for ``(event_type, locale)`` (with a DEFAULT_LOCALE
         fallback) — the platform-layer fallback shipped in code.

    Raises:
        TemplateRenderError: no override AND no builtin for the key (an
            unknown event/locale — a clear error, never a silent empty send),
            or a render/sandbox failure.
    """
    ctx = dict(context or {})
    source = override if override is not None else get_builtin(event_type, locale)
    if source is None:
        raise TemplateRenderError(
            "no notification template for "
            f"event_type={event_type!r} channel_type={channel_type!r} locale={locale!r} "
            "(no tenant override and no builtin fallback)"
        )
    return render_template(source, ctx, channel_type=channel_type)


def template_source_from_row(row: Any) -> TemplateSource:
    """Adapt a ``NotificationTemplate`` ORM row to a :class:`TemplateSource`.

    Duck-typed: the row just needs ``body_template`` / ``subject_template``
    attributes. Lets the dispatcher hand a resolved override straight to
    :func:`render_notification`.
    """
    return TemplateSource(
        body=row.body_template,
        subject=getattr(row, "subject_template", None),
    )


__all__ = [
    "BUILTIN_TEMPLATES",
    "DEFAULT_LOCALE",
    "MARKUP_CHANNEL_TYPES",
    "SUPPORTED_LOCALES",
    "RenderedNotification",
    "TemplateRenderError",
    "TemplateSource",
    "builtin_event_types",
    "get_builtin",
    "render_notification",
    "render_template",
    "template_source_from_row",
]
