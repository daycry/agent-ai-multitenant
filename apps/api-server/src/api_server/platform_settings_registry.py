"""Registry of operator-tunable PLATFORM settings that lack a dedicated page.

Mirror of :mod:`api_server.settings_registry` (which serves the per-tenant
``tenant_settings``) but for the **platform-global** ``platform_settings`` table
(ADR 0028 — System-Admin only). Several platform defaults were operator-tunable
*by design* (e.g. ``model.default_config`` per ADR 0055) but never got a write
endpoint / UI; this registry is the single source of truth that backs a generic
``/admin/platform-settings`` surface so they can be edited from the panel without
hardcoding anything in the frontend.

Settings that ALREADY own a dedicated page (backups, SSO, notifications, the
per-tenant memories page, model prices) are deliberately NOT listed here — this
registry covers only the gap.

Adding a setting: add the entry to :data:`PLATFORM_KNOWN_SETTINGS`. The value is
read with :func:`api_server.db.platform_settings.get_platform_setting` (its own
``get_*`` helper keeps applying its clamping/validation on the read path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from api_server.db.approval_repo import (
    APPROVAL_EXPIRY_ENABLED_KEY,
    APPROVAL_TIMEOUT_HOURS_KEY,
    DEFAULT_APPROVAL_EXPIRY_ENABLED,
    DEFAULT_APPROVAL_TIMEOUT_HOURS,
    MAX_APPROVAL_TIMEOUT_HOURS,
    MIN_APPROVAL_TIMEOUT_HOURS,
)
from api_server.db.llm_providers import LLM_PROVIDER_KINDS
from api_server.db.platform_settings import (
    DEFAULT_MODEL_CONFIG,
    InvalidModelConfigError,
    validate_chat_model_config,
)

# ``model_config`` is the structured agent-default spec (provider/model/temperature);
# the rest are scalars. The UI renders a control per type.
PlatformSettingType = Literal["bool", "int", "decimal", "model_config", "guardrails_config"]


@dataclass(frozen=True)
class PlatformSettingDef:
    """One tunable platform setting (keyed by its real ``platform_settings`` key)."""

    type: PlatformSettingType
    default: Any
    label_es: str
    description_es: str
    min_value: float | int | None = None
    max_value: float | int | None = None


@dataclass(frozen=True)
class PlatformCategoryDef:
    """A UI grouping. ``platform_settings`` is flat (key→value); the category is
    presentation-only — the key is the globally-unique platform setting key."""

    label_es: str
    icon: str  # lucide-react component name, resolved by the frontend
    description_es: str = ""
    settings: dict[str, PlatformSettingDef] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry — the source of truth (only settings WITHOUT a dedicated page)
# ---------------------------------------------------------------------------
PLATFORM_KNOWN_SETTINGS: dict[str, PlatformCategoryDef] = {
    "modelos": PlatformCategoryDef(
        label_es="Modelos",
        icon="Cpu",
        description_es="Modelo que heredan los agentes que no fijan uno propio.",
        settings={
            "model.default_config": PlatformSettingDef(
                type="model_config",
                default=DEFAULT_MODEL_CONFIG,
                label_es="Modelo por defecto de agentes",
                description_es=(
                    "Proveedor (kind) + modelo + temperatura que heredan los agentes sin "
                    "modelo propio (ADR 0055). El dispatch resuelve el proveedor más nuevo "
                    "ACTIVO de ese kind."
                ),
            ),
        },
    ),
    "ejecucion": PlatformCategoryDef(
        label_es="Ejecución",
        icon="Gauge",
        description_es="Límites y reintentos de las ejecuciones de agentes.",
        settings={
            "max_review_retries": PlatformSettingDef(
                type="int",
                default=3,
                label_es="Reintentos máximos de revisión",
                description_es="Cuántas veces un agente puede reworkear su salida tras un reject.",
                min_value=0,
                max_value=10,
            ),
            "execution_soft_time_limit_s": PlatformSettingDef(
                type="int",
                default=7500,
                label_es="Límite de tiempo soft (s)",
                description_es="SoftTimeLimit por ejecución; el agente puede capturarlo.",
                min_value=60,
                # prod-06 zombi_03 (decision 2): acotado a 6h (< hard) — el cap
                # real lo fija el hard, que a su vez debe quedar < visibility_timeout.
                max_value=21600,
            ),
            "execution_hard_time_limit_s": PlatformSettingDef(
                type="int",
                default=7800,
                label_es="Límite de tiempo hard (s)",
                description_es="HardTimeLimit por ejecución (SIGKILL). Debe ser > soft.",
                min_value=60,
                # prod-06 zombi_03 (decision 2): 6h. DEBE quedar < el
                # broker visibility_timeout (7h) o Redis redeliver-ía un run vivo
                # (ejecución duplicada). Antes era 24h, mayor que la ventana del
                # broker — la causa raíz de workers-3.
                max_value=21600,
            ),
        },
    ),
    "planes": PlatformCategoryDef(
        label_es="Planes",
        icon="ClipboardCheck",
        description_es="Política de aprobación de planes.",
        settings={
            "plan_approval_double_signature_threshold": PlatformSettingDef(
                type="decimal",
                default="0",
                label_es="Umbral de doble firma",
                description_es=(
                    "Coste estimado (IA) por encima del cual un plan exige una SEGUNDA "
                    "firma. 0 = siempre basta una firma."
                ),
                min_value=0,
            ),
        },
    ),
    # Las dos palancas de la caducidad de solicitudes de aprobación (ADR 0016 /
    # prod-03 task_prod03_05). Funcionaban y se leían de `platform_settings`
    # desde entonces, pero NO estaban registradas aquí: la única forma de
    # tocarlas era un INSERT a mano en la tabla. Van juntas a propósito — la
    # ventana no significa nada si el barrido está apagado, y el operador que
    # busca una encuentra la otra al lado.
    "aprobaciones": PlatformCategoryDef(
        label_es="Aprobaciones",
        icon="ShieldCheck",
        description_es=(
            "Caducidad de las solicitudes de aprobación humana pendientes "
            "(barrido `workers.expire_stale_approvals`)."
        ),
        settings={
            APPROVAL_TIMEOUT_HOURS_KEY: PlatformSettingDef(
                type="decimal",
                # Decimal y no int: el suelo del rango son 15 minutos (0.25 h),
                # así que un entero no podría expresar la mitad del rango útil.
                default=str(DEFAULT_APPROVAL_TIMEOUT_HOURS),
                label_es="Ventana de caducidad (horas)",
                description_es=(
                    "Horas que una solicitud `pending` espera antes de caducar; al "
                    "caducar, ABORTA la ejecución que la esperaba. El barrido lo "
                    "relee en cada pasada, así que el cambio surte efecto sin "
                    f"reiniciar nada. Rango {MIN_APPROVAL_TIMEOUT_HOURS} h "
                    f"(15 min) - {MAX_APPROVAL_TIMEOUT_HOURS} h (30 días)."
                ),
                min_value=MIN_APPROVAL_TIMEOUT_HOURS,
                max_value=MAX_APPROVAL_TIMEOUT_HOURS,
            ),
            APPROVAL_EXPIRY_ENABLED_KEY: PlatformSettingDef(
                type="bool",
                default=DEFAULT_APPROVAL_EXPIRY_ENABLED,
                label_es="Barrido de caducidad",
                description_es=(
                    "Interruptor vivo del barrido. ON por defecto: sin él, una "
                    "decisión que nadie toma cuelga la ejecución para siempre. "
                    "Apagarlo es la palanca de emergencia si el barrido está "
                    "caducando solicitudes que un humano no ha tenido tiempo de ver."
                ),
            ),
        },
    ),
    "rag": PlatformCategoryDef(
        label_es="RAG",
        icon="Search",
        description_es="Recuperación aumentada (búsqueda en KBs).",
        settings={
            "rag.reranker_enabled": PlatformSettingDef(
                type="bool",
                default=False,
                label_es="Reranker del RAG",
                description_es=(
                    "Aplica un cross-encoder sobre el recall híbrido (BM25+vector). OFF por "
                    "defecto: el reranker real es una dependencia pesada (torch)."
                ),
            ),
        },
    ),
    "seguridad": PlatformCategoryDef(
        label_es="Seguridad",
        icon="Shield",
        description_es="Guardrails declarativos de la plataforma (ADR 0102).",
        settings={
            # Capa PLATAFORMA de los guardrails (principio 10): config
            # declarativa {guardrails: {hook: [checks...]}} que el orquestador
            # fusiona con la capa proyecto y transporta al runtime. Vacia =
            # baseline del runtime (post_tool prompt_injection en LOG).
            "guardrails_config": PlatformSettingDef(
                type="guardrails_config",
                default={},
                label_es="Guardrails de plataforma",
                description_es="Config declarativa por hook (pre_llm/post_llm/"
                "pre_tool/post_tool). Los checks con locked:true no pueden "
                "relajarse por proyecto. action:block aplica enforce real.",
            ),
        },
    ),
    "mantenimiento": PlatformCategoryDef(
        label_es="Mantenimiento",
        icon="Wrench",
        description_es="Palancas de los jobs programados.",
        settings={
            "cred_rotation_enabled": PlatformSettingDef(
                type="bool",
                default=True,
                label_es="Rotación de credenciales",
                description_es="Job periódico que rota secretos/leases en Vault.",
            ),
            "human_escalation_enabled": PlatformSettingDef(
                type="bool",
                default=True,
                label_es="Escalado de tareas humanas",
                description_es="Sweep que reasigna/bloquea tareas humanas vencidas por timeout.",
            ),
            # ADR 0098: barrido periódico de fetch de los remotos git de los
            # proyectos. OFF por defecto — sondear remotos de terceros es una
            # decisión consciente; el botón manual «Sincronizar» siempre está.
            "git_fetch_sweep_enabled": PlatformSettingDef(
                type="bool",
                default=False,
                label_es="Fetch periódico de remotos git",
                description_es="Sweep que hace fetch autenticado del remoto de cada "
                "proyecto con git configurado (cadencia WORKERS_GIT_FETCH_CRON).",
            ),
        },
    ),
}


class UnknownPlatformSettingError(KeyError):
    """The platform setting key is not registered as tunable here."""


def _find_def(key: str) -> PlatformSettingDef:
    for cat in PLATFORM_KNOWN_SETTINGS.values():
        if key in cat.settings:
            return cat.settings[key]
    raise UnknownPlatformSettingError(f"unknown platform setting {key!r}")


def all_setting_keys() -> list[str]:
    """Every tunable key in the registry (flat)."""
    return [key for cat in PLATFORM_KNOWN_SETTINGS.values() for key in cat.settings]


def validate_platform_setting_value(key: str, value: Any) -> Any:
    """Validate + coerce a value against the registry entry for ``key``.

    Returns the value ready for JSONB storage. Raises :class:`ValueError`
    (the router maps it to 422) on any mismatch."""
    sdef = _find_def(key)
    if sdef.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key}: expected a boolean")
        return value
    if sdef.type == "int":
        as_int = _coerce_int(value)
        _check_bounds(key, as_int, sdef)
        return as_int
    if sdef.type == "decimal":
        return _validate_decimal(key, value, sdef)
    if sdef.type == "model_config":
        if not isinstance(value, dict):
            raise ValueError(f"{key}: expected a model config object")
        try:
            # Accept a CONCRETE provider pinned by provider_id (UI picks providers
            # by name, like the project/team/chat pickers) OR the legacy kind-based
            # shape — validate_chat_model_config covers both (ADR 0021/0055).
            return dict(validate_chat_model_config(value))
        except InvalidModelConfigError as exc:
            raise ValueError(str(exc)) from exc
    if sdef.type == "guardrails_config":
        if not isinstance(value, dict):
            raise ValueError(f"{key}: expected a guardrails config object")
        import json as _json

        if len(_json.dumps(value)) > 64_000:
            raise ValueError(f"{key}: config exceeds the 64KB cap (ADR 0102 D3)")
        try:
            from shared_guardrails.config import parse_config

            parse_config(dict(value))
        except Exception as exc:
            raise ValueError(f"{key}: invalid guardrails config: {exc}") from exc
        return dict(value)
    raise ValueError(f"unknown setting type {sdef.type!r}")  # pragma: no cover


def _validate_decimal(key: str, value: Any, sdef: PlatformSettingDef) -> str:
    """Coerce to the normalised decimal STRING and check BOTH bounds.

    El techo faltaba: el único `decimal` que existía
    (`plan_approval_double_signature_threshold`) no declara `max_value`, así que la
    omisión no se notaba — y `approval.timeout_hours` sí tiene techo (720 h). Sin
    esta comprobación la UI aceptaría 1000 h y el barrido clamparía a 720 en
    silencio: el operador leería un número que el sistema no usa.
    """
    as_str = _coerce_decimal(value)
    as_decimal = Decimal(as_str)
    if sdef.min_value is not None and as_decimal < Decimal(str(sdef.min_value)):
        raise ValueError(f"{key}: value {as_str} below minimum {sdef.min_value}")
    if sdef.max_value is not None and as_decimal > Decimal(str(sdef.max_value)):
        raise ValueError(f"{key}: value {as_str} above maximum {sdef.max_value}")
    return as_str


def _coerce_int(value: Any) -> int:
    f = float(value)
    if not f.is_integer():
        raise ValueError(f"expected an integer, got {value!r}")
    return int(f)


def _coerce_decimal(value: Any) -> str:
    """Return the value as a normalised decimal STRING (how it is stored)."""
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"expected a decimal number, got {value!r}") from exc


def _check_bounds(key: str, value: int | float, sdef: PlatformSettingDef) -> None:
    if sdef.min_value is not None and value < sdef.min_value:
        raise ValueError(f"{key}: value {value} below minimum {sdef.min_value}")
    if sdef.max_value is not None and value > sdef.max_value:
        raise ValueError(f"{key}: value {value} above maximum {sdef.max_value}")


def platform_registry_to_dict() -> dict[str, Any]:
    """Serialise the registry for ``GET /admin/platform-settings/_registry``.

    For ``model_config`` entries the valid provider kinds (ADR 0021 closed
    catalogue) are inlined so the UI can render the provider select without a
    second round-trip."""
    return {
        category: {
            "label_es": cat.label_es,
            "icon": cat.icon,
            "description_es": cat.description_es,
            "settings": {
                key: {
                    "type": sdef.type,
                    "default": sdef.default,
                    "label_es": sdef.label_es,
                    "description_es": sdef.description_es,
                    "min_value": sdef.min_value,
                    "max_value": sdef.max_value,
                    **(
                        {"provider_kinds": list(LLM_PROVIDER_KINDS)}
                        if sdef.type == "model_config"
                        else {}
                    ),
                }
                for key, sdef in cat.settings.items()
            },
        }
        for category, cat in PLATFORM_KNOWN_SETTINGS.items()
    }


__all__ = [
    "PLATFORM_KNOWN_SETTINGS",
    "PlatformCategoryDef",
    "PlatformSettingDef",
    "PlatformSettingType",
    "UnknownPlatformSettingError",
    "all_setting_keys",
    "platform_registry_to_dict",
    "validate_platform_setting_value",
]
