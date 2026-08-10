"""Fail-closed configuration guards (prod-09 task_prod09_02, authz-2).

``api_server.config.Settings`` already refused a dev-only secret in
``staging``/``prod`` (Plan 06.14). The audit found the guard itself was
**fail-open**: ``environment`` was a free-form string and every predicate asked
"is it staging or prod?", so ANY value the guard did not recognise — a typo like
``production``, an empty variable, a ``prod\\n`` from a heredoc — meant "dev". A
misspelling in a compose file silently switched off the dev-secret guard AND the
whole ``/admin`` hardening (MFA, IP allowlist, 15-minute admin sessions), with no
error and no log line.

This suite pins the fail-closed behaviour, and deliberately weights the REJECTION
cases: a guard is only worth what it refuses.

  1. ``environment`` is a CLOSED set — an unknown value fails construction
     (this is the human check "start with API_SERVER_ENVIRONMENT=production and
     the process must NOT come up").
  2. The dev-secret guard is written as "skip only for dev", so a hypothetical
     fourth environment is guarded by default.
  3. HMAC signing secrets have a length floor outside dev — "not a dev default"
     is not the same as "strong".
  4. ``internal_token_secret`` must differ from ``jwt_secret``
     (task_prod09_03 / secrets-9) — same value, same blast radius.

Init kwargs win over any ambient env/.env in pydantic-settings, so these tests
are independent of the developer's environment.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from api_server.config import Settings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _fake_secret(seed: str) -> str:
    """48 caracteres deterministas y de ALTA ENTROPÍA.

    Antes esto era `"j" * 48`, que no lleva marcador de dev y medía de sobra —
    y por eso arrancaba producción. Desde prod-10 `task_prod10_04` el config
    tiene además un suelo de variedad (≥8 caracteres distintos, ≥2 bits/carácter),
    así que un relleno de un solo carácter ya no es un secreto «realista»: es
    justo el caso que el guard rechaza. El hex de SHA-256 da 16 símbolos y ~4
    bits/carácter, y sigue siendo determinista, que es lo que un test necesita.
    """
    return hashlib.sha256(seed.encode()).hexdigest()[:48]


# Real-looking secrets: none carries a dev marker, all long enough.
def _real(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "jwt_secret": _fake_secret("jwt"),
        "internal_token_secret": _fake_secret("internal"),
        "review_url_signing_secret": _fake_secret("review"),
        "sso_encryption_key": _fake_secret("sso"),
        "notification_encryption_key": _fake_secret("notify"),
        "incoming_webhook_encryption_key": _fake_secret("webhook"),
        "minio_secret_key": _fake_secret("minio"),
        "minio_access_key": "prod-access-key",
        "database_url": "postgresql+asyncpg://app_user:S3cr3tA@db/agentic",
        "admin_database_url": "postgresql+asyncpg://migrations_user:S3cr3tM@db/agentic",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# (1) environment is a closed set — the fail-open hole
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bogus",
    [
        "production",  # the typo from the human checklist
        "staging2",
        "test",
        "PRODUCTION",
        "",
        "dev ,staging",
    ],
)
def test_unknown_environment_refuses_to_start(bogus: str) -> None:
    """An unrecognised environment tag is a HARD failure, not a silent 'dev'.

    This is the whole point of authz-2: before the enum, every one of these
    values disabled the dev-secret guard and the admin hardening while the
    service came up looking healthy.
    """
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment=bogus, **_real())
    # The message must name the accepted values — an operator has to be able to
    # fix this from the error alone.
    rendered = str(excinfo.value)
    assert "dev" in rendered and "staging" in rendered and "prod" in rendered


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_known_environments_are_accepted(env: str) -> None:
    assert Settings(environment=env, **_real()).environment == env


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" prod ", "prod"), ("PROD", "prod"), ("Staging\n", "staging"), ("Dev", "dev")],
)
def test_environment_is_normalised_not_rejected(raw: str, expected: str) -> None:
    """Whitespace/case noise from a compose or .env file is a typing accident,
    not an intent to run unguarded — normalise it. Crucially it normalises
    TOWARDS enforcement: ``" PROD "`` becomes ``prod`` (guards ON), it does not
    fall through to dev."""
    assert Settings(environment=raw, **_real()).environment == expected


def test_normalised_prod_still_enforces_the_secret_guard() -> None:
    """The regression that mattered: ``API_SERVER_ENVIRONMENT="prod "`` (a
    trailing space) used to mean dev, so a dev JWT secret sailed through."""
    with pytest.raises(ValidationError):
        Settings(environment="prod ", **_real(jwt_secret="dev-only-jwt-secret-change-me"))


# ---------------------------------------------------------------------------
# (2) The dev-secret guard is 'skip only for dev', not 'enforce for {staging, prod}'
# ---------------------------------------------------------------------------
def test_guard_is_written_as_skip_only_for_dev() -> None:
    """Structural: the guard must branch on ``== dev``, so adding a fourth
    environment enforces by default instead of by remembering to widen a set.

    Written against the source because that is where the fail-open lived: the
    behaviour is identical today (the enum has exactly one non-enforcing value),
    so no behavioural test can distinguish the two shapes — only the shape can.
    """
    import inspect

    from api_server import config as config_mod

    source = inspect.getsource(config_mod.Settings._forbid_dev_secrets_outside_dev)
    assert "_DEV_ENVIRONMENT" in source, "the guard no longer branches on the dev constant"
    assert 'in {"staging", "prod"}' not in source, (
        "the guard went back to an allow-list of enforcing environments, which "
        "fails OPEN for any value not in it"
    )


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_dev_jwt_secret_is_rejected_outside_dev(env: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment=env, **_real(jwt_secret="dev-only-jwt-secret-change-me"))


def test_dev_defaults_still_work_in_dev() -> None:
    """The counterweight: the hardening must not break local boot. A bare
    ``Settings(environment="dev")`` — every dev default, short secrets and all —
    still constructs."""
    assert Settings(environment="dev").environment == "dev"


# ---------------------------------------------------------------------------
# (3) Length floor for the HMAC signing secrets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_short_jwt_secret_is_rejected_outside_dev(env: str) -> None:
    """A secret can pass the dev-marker check and still be worthless: ``x`` is
    not a dev default, and it signs SESSIONS."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment=env, **_real(jwt_secret="short-but-not-a-dev-marker"))
    assert "32" in str(excinfo.value)


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_short_internal_token_secret_is_rejected_outside_dev(env: str) -> None:
    with pytest.raises(ValidationError):
        Settings(environment=env, **_real(internal_token_secret="tiny-worker-key"))


def test_exactly_32_chars_is_accepted() -> None:
    """The floor is inclusive — pin the boundary so a later ``>`` / ``>=`` slip
    is caught rather than quietly rejecting valid installer output."""
    assert (
        Settings(environment="prod", **_real(jwt_secret=_fake_secret("boundary")[:32])).environment
        == "prod"
    )


def test_short_secrets_are_fine_in_dev() -> None:
    """Dev must stay usable: the integration conftest itself sets
    ``API_SERVER_JWT_SECRET=test-secret`` (11 chars)."""
    assert Settings(environment="dev", jwt_secret="test-secret").environment == "dev"


# ---------------------------------------------------------------------------
# (4) The two signing domains must not collapse back into one
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_internal_secret_equal_to_jwt_secret_is_rejected(env: str) -> None:
    """Setting both to the same value satisfies every other check while
    restoring exactly the blast radius task_prod09_03 removes."""
    same = _fake_secret("same")
    with pytest.raises(ValidationError) as excinfo:
        Settings(environment=env, **_real(jwt_secret=same, internal_token_secret=same))
    assert "INTERNAL_TOKEN_SECRET" in str(excinfo.value)


def test_distinct_secrets_are_accepted() -> None:
    settings = Settings(environment="prod", **_real())
    assert (
        settings.internal_token_secret.get_secret_value() != settings.jwt_secret.get_secret_value()
    )
