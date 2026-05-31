"""Granular per-permission consent logic (Plan 09 task_09_07).

The pure, I/O-free heart of the consent flow. The router (the FastAPI +
RLS + audit shell) calls into here; everything here is plain Python so it
unit-tests without a database.

Binding plan decisions this module enforces:

  * The granular permission surface a listing requests is
    ``allowed_domains`` / ``allowed_paths`` / ``network_policy`` (decision
    (c)). Each requested permission is a descriptor dict shaped
    ``{"type": <one of PERMISSION_KEYS>, "value": <...>}`` — the canonical
    shape the Phase A install endpoint and its tests already use.
  * ``community`` and ``experimental`` listings ALWAYS require explicit
    per-permission consent from the project owner (decisions (a)+(b));
    ``verified`` does not (minimal friction, decision (d)). Which trust
    levels require consent is resolved through
    :func:`api_server.marketplace.trust.trust_policy` —
    ``per_permission_consent_required`` — so this module never re-encodes
    the policy table.
  * An install that requires consent cannot be ENABLED until EVERY
    requested permission is granted. An explicit deny of any required
    permission keeps the install disabled.

Each permission is identified by its ``type``. A requested permission is
in exactly one bucket: GRANTED, DENIED, or PENDING (requested minus granted
minus denied). The project owner approves/denies each one; the install
becomes enable-eligible only when no permission is pending or denied.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from api_server.db.marketplace import MarketplaceTrustLevel
from api_server.marketplace.trust import PERMISSION_KEYS, trust_policy


class PermissionDecision(enum.StrEnum):
    """A project owner's verdict on a single requested permission."""

    GRANT = "grant"
    DENY = "deny"


class ConsentState(enum.StrEnum):
    """Per-permission state derived from the install's granted/denied sets."""

    GRANTED = "granted"
    DENIED = "denied"
    PENDING = "pending"


class ConsentError(ValueError):
    """A consent decision references a permission the listing never
    requested, or is otherwise malformed. Surfaces as a 422 in the router."""


def consent_required_for(trust_level: MarketplaceTrustLevel | str) -> bool:
    """True when ``trust_level`` mandates explicit per-permission consent.

    Thin pass-through to the trust policy table so there is one source of
    truth (``community`` / ``experimental`` → True; ``verified`` → False).
    """
    return trust_policy(trust_level).per_permission_consent_required


def permission_type(descriptor: Any) -> str:
    """Extract the canonical ``type`` of a permission descriptor.

    A descriptor is ``{"type": <PERMISSION_KEYS member>, "value": ...}``.
    Raises :class:`ConsentError` for a malformed or unknown-type
    descriptor — an unrecognised permission must fail loudly rather than
    silently slip through the consent gate.
    """
    if not isinstance(descriptor, Mapping):
        raise ConsentError(f"permission descriptor must be an object, got {type(descriptor)!r}")
    ptype = descriptor.get("type")
    if not isinstance(ptype, str) or ptype not in PERMISSION_KEYS:
        raise ConsentError(f"unknown permission type: {ptype!r}")
    return ptype


def requested_types(requested_permissions: Iterable[Any]) -> list[str]:
    """The ordered, de-duplicated permission types a listing requests."""
    seen: dict[str, None] = {}
    for descriptor in requested_permissions:
        seen.setdefault(permission_type(descriptor), None)
    return list(seen)


@dataclass(frozen=True, slots=True)
class PermissionView:
    """One requested permission + its current consent state, for the UI."""

    type: str
    descriptor: dict[str, Any]
    state: ConsentState


@dataclass(frozen=True, slots=True)
class ConsentSummary:
    """The whole permission surface of an install, resolved for display.

    ``all_granted`` is the enable-gate: an install that requires consent is
    enable-eligible only when every requested permission is granted (no
    pending, no denied).
    """

    consent_required: bool
    permissions: tuple[PermissionView, ...]

    @property
    def pending(self) -> tuple[PermissionView, ...]:
        return tuple(p for p in self.permissions if p.state is ConsentState.PENDING)

    @property
    def denied(self) -> tuple[PermissionView, ...]:
        return tuple(p for p in self.permissions if p.state is ConsentState.DENIED)

    @property
    def all_granted(self) -> bool:
        """True when no requested permission is pending or denied.

        Vacuously true when the listing requests nothing — then an install
        needs no consent gate at all.
        """
        return all(p.state is ConsentState.GRANTED for p in self.permissions)


def _index_by_type(descriptors: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Map permission type → descriptor (last write wins for a dup type)."""
    out: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        out[permission_type(descriptor)] = dict(descriptor)
    return out


def summarize(
    *,
    trust_level: MarketplaceTrustLevel | str,
    requested_permissions: Iterable[Any],
    granted_permissions: Iterable[Any],
    denied_permissions: Iterable[Any],
) -> ConsentSummary:
    """Resolve every requested permission into GRANTED / DENIED / PENDING.

    The listing's ``requested_permissions`` is authoritative for what
    exists; the install's granted/denied sets classify each. A permission
    in neither set is PENDING. Decisions referencing a type the listing did
    not request are ignored here (the *write* path rejects them).
    """
    requested = _index_by_type(requested_permissions)
    granted = {permission_type(d) for d in granted_permissions}
    denied = {permission_type(d) for d in denied_permissions}

    views: list[PermissionView] = []
    for ptype, descriptor in requested.items():
        if ptype in granted:
            state = ConsentState.GRANTED
        elif ptype in denied:
            state = ConsentState.DENIED
        else:
            state = ConsentState.PENDING
        views.append(PermissionView(type=ptype, descriptor=descriptor, state=state))

    return ConsentSummary(
        consent_required=consent_required_for(trust_level),
        permissions=tuple(views),
    )


@dataclass(frozen=True, slots=True)
class ConsentOutcome:
    """The result of applying a batch of per-permission decisions.

    ``granted`` / ``denied`` are the NEW descriptor lists to persist on the
    installation. ``enable`` is True when the install may transition to
    ``enabled`` (consent not required, OR every requested permission is now
    granted). ``any_denied`` flags whether an explicit deny was recorded in
    this batch (the router writes a ``consent_denied`` audit row when so).
    """

    granted: list[dict[str, Any]]
    denied: list[dict[str, Any]]
    enable: bool
    any_denied: bool


def apply_decisions(
    *,
    trust_level: MarketplaceTrustLevel | str,
    requested_permissions: Iterable[Any],
    existing_granted: Iterable[Any],
    existing_denied: Iterable[Any],
    decisions: Mapping[str, PermissionDecision | str],
) -> ConsentOutcome:
    """Fold a batch of ``{permission_type: grant|deny}`` decisions in.

    Merges the new decisions over the existing granted/denied state. A
    later decision on the same type overrides an earlier one (grant after a
    deny moves it back to granted, and vice versa). Raises
    :class:`ConsentError` if a decision references a permission type the
    listing did not request.

    The returned ``enable`` flag is the single source of truth for the
    install's status transition: consent-not-required installs are always
    enable-eligible; consent-required ones only once every requested
    permission is granted.
    """
    requested = _index_by_type(requested_permissions)

    granted_map = _index_by_type(existing_granted)
    denied_map = _index_by_type(existing_denied)

    any_denied = False
    for ptype, raw in decisions.items():
        if ptype not in requested:
            raise ConsentError(f"permission {ptype!r} is not requested by this listing")
        decision = PermissionDecision(raw)
        descriptor = requested[ptype]
        if decision is PermissionDecision.GRANT:
            granted_map[ptype] = descriptor
            denied_map.pop(ptype, None)
        else:  # DENY
            denied_map[ptype] = descriptor
            granted_map.pop(ptype, None)
            any_denied = True

    summary = summarize(
        trust_level=trust_level,
        requested_permissions=list(requested.values()),
        granted_permissions=list(granted_map.values()),
        denied_permissions=list(denied_map.values()),
    )
    enable = (not summary.consent_required) or summary.all_granted

    return ConsentOutcome(
        granted=list(granted_map.values()),
        denied=list(denied_map.values()),
        enable=enable,
        any_denied=any_denied,
    )


__all__ = [
    "ConsentError",
    "ConsentOutcome",
    "ConsentState",
    "ConsentSummary",
    "PermissionDecision",
    "PermissionView",
    "apply_decisions",
    "consent_required_for",
    "permission_type",
    "requested_types",
    "summarize",
]
