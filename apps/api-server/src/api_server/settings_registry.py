"""Registry of known tenant settings (Plan 06.7 task_06_7_02).

The DB table ``tenant_settings`` stores values, but the *list of
valid (category, key) pairs* lives here in code. Why:

  * Type safety — `get_setting` returns the right type per key.
  * UI generability — `/admin/settings` reads the registry through
    ``GET /tenant-settings/_registry`` and renders cards + forms
    without hardcoding anything in the frontend.
  * No drift — a `PUT /tenant-settings/{category}/{key}` against an
    unknown key returns 404 instead of silently writing garbage.

Adding a new setting:

  1. Add the entry to :data:`KNOWN_SETTINGS`.
  2. Use :func:`get_setting` from wherever the value matters.

That's it — no migration, no router changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import TenantSetting

SettingType = Literal["float", "int", "string", "bool"]


@dataclass(frozen=True)
class SettingDef:
    """One entry in the registry."""

    type: SettingType
    default: Any
    description_es: str
    """Short Spanish description shown in the UI form."""
    label_es: str
    """Human-readable name shown above the input."""
    min_value: float | int | None = None
    """Inclusive lower bound (validated by the PUT endpoint)."""
    max_value: float | int | None = None
    """Inclusive upper bound."""


@dataclass(frozen=True)
class CategoryDef:
    """A category groups settings the UI renders together."""

    label_es: str
    icon: str
    """Name of the lucide-react component (resolved by the frontend
    to a real `<Brain/>` / `<Coins/>` / …)."""
    description_es: str = ""
    external_page: str | None = None
    """When set, the index card links to this URL instead of the
    auto-generated `/admin/settings/<category>`. Used for legacy
    pages we don't want to rebuild (e.g. hourly-rate)."""
    settings: dict[str, SettingDef] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry — the source of truth
# ---------------------------------------------------------------------------

KNOWN_SETTINGS: dict[str, CategoryDef] = {
    "memories": CategoryDef(
        label_es="Memorias",
        icon="Brain",
        description_es=(
            "Cómo el sistema detecta memorias semánticamente similares "
            "para que el operador las revise y fusione."
        ),
        settings={
            "similarity.threshold": SettingDef(
                type="float",
                default=0.85,
                description_es=(
                    "Similitud coseno mínima para considerar dos memorias "
                    "como candidatas a duplicado. 1.0 = idénticas; 0.5 = "
                    "muy permisivo."
                ),
                label_es="Umbral de similitud",
                min_value=0.5,
                max_value=0.99,
            ),
            "similarity.limit": SettingDef(
                type="int",
                default=5,
                description_es="Número máximo de candidatos devueltos por memoria.",
                label_es="Número de candidatos",
                min_value=1,
                max_value=20,
            ),
        },
    ),
    "costs": CategoryDef(
        label_es="Costes",
        icon="Coins",
        description_es=("Tarifa horaria del tenant para el cálculo de coste humano de los planes."),
        # The hourly-rate page predates the generic settings system —
        # we link to it directly instead of duplicating the form.
        external_page="/admin/settings/hourly-rate",
        settings={},
    ),
    "sso": CategoryDef(
        label_es="SSO empresarial",
        icon="Shield",
        description_es=(
            "Inicio de sesión único (OIDC) por tenant: Azure AD, Google "
            "Workspace, Okta, Auth0 y más. Configúralo sin tocar el login "
            "local."
        ),
        # The SSO config is a bespoke CRUD form (provider templates +
        # secret handling), not a flat key/value list — it has its own
        # dedicated page (Plan 08 task_08_03).
        external_page="/admin/settings/sso",
        settings={},
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class UnknownSettingError(KeyError):
    """The (category, key) pair is not registered."""


def get_setting_def(category: str, key: str) -> SettingDef:
    """Resolve `(category, key)` to its :class:`SettingDef`."""
    cat = KNOWN_SETTINGS.get(category)
    if cat is None:
        raise UnknownSettingError(f"unknown setting category {category!r}")
    if key not in cat.settings:
        raise UnknownSettingError(f"unknown setting {category}.{key!r}")
    return cat.settings[key]


async def get_setting(
    session: AsyncSession,
    tenant_id: UUID,
    category: str,
    key: str,
) -> Any:
    """Read a tenant's setting value. Returns the registry default when
    the tenant hasn't configured it."""
    sdef = get_setting_def(category, key)  # also validates the key exists
    stmt = select(TenantSetting.value).where(
        TenantSetting.tenant_id == tenant_id,
        TenantSetting.category == category,
        TenantSetting.key == key,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row if row is not None else sdef.default


def validate_setting_value(category: str, key: str, value: Any) -> Any:
    """Validate `value` against the registry's type + range. Returns
    the coerced value (e.g. int(value) when type=='int') ready for
    JSONB storage. Raises :class:`ValueError` on mismatch."""
    sdef = get_setting_def(category, key)
    coerced = _coerce_type(sdef.type, value)
    if sdef.min_value is not None and coerced < sdef.min_value:
        raise ValueError(f"{category}.{key}: value {coerced} below minimum {sdef.min_value}")
    if sdef.max_value is not None and coerced > sdef.max_value:
        raise ValueError(f"{category}.{key}: value {coerced} above maximum {sdef.max_value}")
    return coerced


def _coerce_type(target: SettingType, value: Any) -> Any:
    """Best-effort type coercion: the UI sends JSON, so numbers may
    arrive as int when float is expected. We accept either if the
    convert is lossless."""
    if target == "float":
        return float(value)
    if target == "int":
        f = float(value)
        if not f.is_integer():
            raise ValueError(f"expected int, got fractional value {value}")
        return int(f)
    if target == "string":
        if not isinstance(value, str):
            raise ValueError(f"expected string, got {type(value).__name__}")
        return value
    if target == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"expected bool, got {type(value).__name__}")
    raise ValueError(f"unknown setting type {target!r}")


def registry_to_dict() -> dict[str, Any]:
    """Serialise the registry for the `/_registry` endpoint. The UI
    consumes this once on settings page mount to build the form."""
    return {
        category: {
            "label_es": cat.label_es,
            "icon": cat.icon,
            "description_es": cat.description_es,
            "external_page": cat.external_page,
            "settings": {
                key: {
                    "type": sdef.type,
                    "default": sdef.default,
                    "label_es": sdef.label_es,
                    "description_es": sdef.description_es,
                    "min_value": sdef.min_value,
                    "max_value": sdef.max_value,
                }
                for key, sdef in cat.settings.items()
            },
        }
        for category, cat in KNOWN_SETTINGS.items()
    }


__all__ = [
    "KNOWN_SETTINGS",
    "CategoryDef",
    "SettingDef",
    "SettingType",
    "UnknownSettingError",
    "get_setting",
    "get_setting_def",
    "registry_to_dict",
    "validate_setting_value",
]
