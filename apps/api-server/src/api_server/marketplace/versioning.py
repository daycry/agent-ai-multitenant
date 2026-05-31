"""Semver versioning + update detection (Plan 09 task_09_12).

The pure, I/O-free logic that decides whether an installation is *outdated*
and, if so, which listing version it should move to. The router (the
FastAPI + RLS + audit shell) and the install orchestrator (task_09_11) call
in here; everything here is plain Python so it unit-tests without a
database.

Two binding plan requirements live here:

  * **Parse + compare versions** with the pip-clean :mod:`packaging` library
    (already a project dependency — no heavy new dep). The format parsers
    (task_09_09 / task_09_10) already validate the *syntax* of a version
    string via :func:`api_server.marketplace._format_common.is_valid_semver`;
    this module adds *ordering* and *compatibility* on top.

  * **Respect compatibility** — an update never auto-jumps a MAJOR version.
    :func:`select_update_target` only proposes a higher version within the
    same major (a minor/patch bump) unless the caller passes
    ``allow_major=True`` (the explicit opt-in the plan demands). A
    major-version candidate exists but is gated; the consumer must opt in.

The "latest compatible version" of an installed listing is the highest
*other* listing row that shares the install's ``(source_id, tenant_id,
kind, name)`` coordinates (the marketplace models already key listings by
``(source_id, tenant_id, name, version)``, so multiple version rows of the
same logical listing coexist). Resolving those rows is the router's job
(it owns RLS); this module only orders + compares the version strings it is
handed.

Pure Python, no I/O — safe to import anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from api_server.marketplace._format_common import is_valid_semver


class VersioningError(ValueError):
    """A version string is not parseable as semver.

    Subclasses :class:`ValueError` so existing ``except ValueError``
    handlers keep working; the router maps it to a 422 / 500 depending on
    whether the bad value came from the wire or from a stored row.
    """


def parse_version(value: str) -> Version:
    """Parse a semver string into a comparable :class:`packaging.Version`.

    We first gate on :func:`is_valid_semver` (the same strict semver regex
    the format parsers use) so a value :mod:`packaging` would *leniently*
    accept but semver would not (e.g. ``"1.2"`` or ``"1.2.3.4"``) is
    rejected here too — the marketplace speaks strict semver everywhere.
    Raises :class:`VersioningError` for anything unparseable.
    """
    if not is_valid_semver(value):
        raise VersioningError(f"not a valid semver string: {value!r}")
    try:
        return Version(value)
    except InvalidVersion as exc:  # pragma: no cover - regex already gates this
        raise VersioningError(f"not a valid semver string: {value!r}") from exc


def compare_versions(left: str, right: str) -> int:
    """Return -1 / 0 / 1 for ``left`` < / == / > ``right`` (semver order).

    Both operands are parsed as strict semver; ordering follows the semver
    precedence rules :mod:`packaging` implements (prerelease < release,
    numeric MAJOR.MINOR.PATCH, build metadata ignored for precedence).
    """
    a = parse_version(left)
    b = parse_version(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def is_outdated(installed: str, candidate: str) -> bool:
    """True when ``candidate`` is a strictly newer version than ``installed``."""
    return compare_versions(installed, candidate) < 0


def is_major_bump(installed: str, candidate: str) -> bool:
    """True when ``candidate`` crosses a MAJOR version boundary upward.

    A move from ``1.x.y`` to ``2.0.0`` is a major bump (breaking-change
    territory in semver); a ``1.2.0`` → ``1.9.0`` minor bump is not. Only an
    *upward* major change counts — a downgrade is never auto-proposed.
    """
    a = parse_version(installed)
    b = parse_version(candidate)
    return b.major > a.major


def latest_version(candidates: Iterable[str]) -> str | None:
    """The highest semver among ``candidates`` (``None`` if empty).

    Every candidate must be strict semver; an unparseable value raises
    :class:`VersioningError` (a malformed listing version must fail loudly,
    not silently sort last).
    """
    parsed = [(parse_version(v), v) for v in candidates]
    if not parsed:
        return None
    parsed.sort(key=lambda pair: pair[0])
    return parsed[-1][1]


@dataclass(frozen=True, slots=True)
class UpdateAssessment:
    """The verdict on whether an installation should update, and to what.

    ``installed_version`` is the version currently on the install row.
    ``latest_version`` is the highest available listing version (or the
    installed one when nothing newer exists). ``target_version`` is the
    version an update would move to under the compatibility rule (``None``
    when no eligible target). ``latest_is_major_bump`` flags that the single
    highest version crosses a major boundary — i.e. updating to it needs the
    explicit ``allow_major`` opt-in.
    """

    installed_version: str
    latest_version: str
    target_version: str | None
    latest_is_major_bump: bool

    @property
    def outdated(self) -> bool:
        """True when a newer version exists at all (major or not)."""
        return is_outdated(self.installed_version, self.latest_version)

    @property
    def update_available(self) -> bool:
        """True when an eligible (compatibility-respecting) target exists."""
        return self.target_version is not None


def select_update_target(
    installed: str,
    candidates: Iterable[str],
    *,
    allow_major: bool = False,
) -> UpdateAssessment:
    """Decide the version an install should move to, respecting compatibility.

    ``candidates`` is every available listing version for the installed
    logical listing (typically including the installed version itself; it is
    ignored as a target since it is not strictly newer).

    The target is the highest version strictly newer than ``installed``
    that the compatibility rule permits:

      * with ``allow_major=False`` (default) the target is the highest newer
        version **within the same MAJOR** — a minor/patch bump only. If the
        only newer versions are major bumps, ``target_version`` is ``None``
        (an update IS available but is gated behind the opt-in).
      * with ``allow_major=True`` (the explicit opt-in the plan requires) the
        target is simply the highest newer version, major bumps included.

    Always reports ``latest_version`` (the single highest available) and
    ``latest_is_major_bump`` so a caller can surface "a major upgrade is
    available, opt in to take it" without re-deriving it.
    """
    installed_v = parse_version(installed)
    # Highest available overall (falls back to installed when nothing else).
    highest = latest_version(candidates) or installed
    highest_v = parse_version(highest)

    # Strictly newer than the installed version.
    newer = [v for v in candidates if compare_versions(installed, v) < 0]
    if allow_major:
        eligible = newer
    else:
        eligible = [v for v in newer if parse_version(v).major == installed_v.major]

    target = latest_version(eligible)
    return UpdateAssessment(
        installed_version=installed,
        latest_version=highest,
        target_version=target,
        latest_is_major_bump=highest_v.major > installed_v.major,
    )


__all__ = [
    "UpdateAssessment",
    "VersioningError",
    "compare_versions",
    "is_major_bump",
    "is_outdated",
    "latest_version",
    "parse_version",
    "select_update_target",
]
