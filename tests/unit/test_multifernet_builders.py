"""The four at-rest ciphers are key RINGS, not single keys (prod-05 task_prod05_01).

What this file has to prove is narrower and sharper than "MultiFernet works":

1. **Nothing already in the database changes meaning.** The derivation
   (SHA-256 → urlsafe-base64) must be byte-identical to the pre-prod-05 one, and
   a one-key ring must behave exactly like the old single ``Fernet``. If that
   breaks, the migration is not a rotation feature — it is a data loss event on
   deploy day.
2. **A retired key still decrypts.** That is the entire point: add the new key at
   the head, and yesterday's ciphertext keeps reading.
3. **New writes land on the HEAD key.** A ring that decrypted with everything but
   also kept encrypting with the OLD key would look green in every roundtrip test
   and never converge — step 3 of the rotation (drop the old key) would then
   destroy data. So the tests assert WHICH key produced the token, not just that
   the roundtrip works.
4. **The api-server and the dispatcher agree.** They are two apps with two
   parsers over the same ciphertext (``notification_channels.secret_encrypted``);
   a divergence shows up as a notification silently failing to send, not as a
   test failure. The symmetry is asserted over a table of inputs.
5. **No single-key builder survived the migration.** A static guard that asserts
   it found the builders first, so it cannot pass vacuously if the layout moves
   (docs/03-guides/verificar-antes-de-implementar.md §4).
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest
from api_server.auth import mfa as _mfa_pkg  # noqa: F401  (ensures the subpackage imports)
from api_server.auth.crypto_keys import (
    KeyRingError,
    build_multifernet,
    derive_fernet_key,
    key_fingerprint,
    parse_key_ring,
    primary_fernet,
)
from api_server.config import Settings
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from notification_dispatcher.config import Settings as DispatcherSettings
from notification_dispatcher.crypto_keys import parse_key_ring as dispatcher_parse_key_ring

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Two keys long enough to satisfy the HMAC floor of the staging/prod guard, so
# the same values can be reused by the guard tests below.
_OLD = "retired-at-rest-key-0123456789abcdefghij"
_NEW = "freshly-minted-at-rest-key-9876543210zyxw"


# ---------------------------------------------------------------------------
# 1. The parser
# ---------------------------------------------------------------------------
def test_the_singular_value_is_a_one_element_ring() -> None:
    """The compatibility hinge: an unset list must reproduce today's behaviour."""
    assert parse_key_ring(plural="", singular="only-key", name="X") == ("only-key",)
    assert parse_key_ring(plural=None, singular="only-key", name="X") == ("only-key",)


def test_a_non_blank_list_wins_and_the_singular_value_is_ignored() -> None:
    """Retiring a key must be "delete it from the list" and nothing else.

    If the singular value were appended, phase 3 of the rotation (drop the old
    key) would silently keep the old key live — the operator would believe they
    had retired a compromised key when they had not.
    """
    ring = parse_key_ring(plural="new,old", singular="still-configured", name="X")
    assert ring == ("new", "old")
    assert "still-configured" not in ring


def test_entries_are_stripped_blanks_dropped_and_duplicates_collapsed() -> None:
    assert parse_key_ring(plural=" a , b ,, a ,c ", singular=None, name="X") == ("a", "b", "c")


def test_an_empty_resolution_fails_loudly_instead_of_encrypting_with_nothing() -> None:
    with pytest.raises(KeyRingError):
        parse_key_ring(plural="", singular="", name="API_SERVER_SSO_ENCRYPTION_KEY(S)")
    with pytest.raises(KeyRingError):
        parse_key_ring(plural=" , , ", singular=None, name="X")


def test_build_and_primary_reject_an_empty_ring() -> None:
    """MultiFernet([]) is accepted by cryptography and then fails inside
    ``encrypt`` with an IndexError — a 500 far from the cause."""
    with pytest.raises(KeyRingError):
        build_multifernet([])
    with pytest.raises(KeyRingError):
        primary_fernet([])


# ---------------------------------------------------------------------------
# 2. The derivation must not move a single byte
# ---------------------------------------------------------------------------
def test_the_derivation_is_byte_identical_to_the_pre_prod05_one() -> None:
    """Recomputed here from first principles, deliberately NOT by calling the
    module under test: this is the assertion that guarantees every ciphertext
    already stored in the database still decrypts after the deploy."""
    for raw in ("dev-only-sso-encryption-key-change-me", _OLD, "ñ-unicode-key"):
        expected = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        assert derive_fernet_key(raw) == expected


def test_a_one_key_ring_reads_a_token_written_by_a_bare_single_key_fernet() -> None:
    """End-to-end version of the same guarantee, at the ciphertext level."""
    legacy_token = Fernet(derive_fernet_key(_OLD)).encrypt(b"stored-before-the-deploy")
    assert build_multifernet((_OLD,)).decrypt(legacy_token) == b"stored-before-the-deploy"


def test_key_fingerprint_is_stable_short_and_key_specific() -> None:
    assert len(key_fingerprint(_OLD)) == 8
    assert key_fingerprint(_OLD) == key_fingerprint(_OLD)
    assert key_fingerprint(_OLD) != key_fingerprint(_NEW)


# ---------------------------------------------------------------------------
# 3. The four builders (+ MFA) — ring semantics through the real modules
# ---------------------------------------------------------------------------
def _api_settings(**overrides: str) -> Settings:
    return Settings(environment="dev", **overrides)  # type: ignore[arg-type]


#: (module path, name of the setting holding the LIST, encrypt fn, decrypt fn)
_API_FAMILIES = [
    (
        "api_server.auth.sso.secrets",
        "sso_encryption_keys",
        "encrypt_client_secret",
        "decrypt_client_secret",
    ),
    (
        "api_server.auth.mfa.secrets",
        "mfa_encryption_keys",
        "encrypt_totp_secret",
        "decrypt_totp_secret",
    ),
    (
        "api_server.webhooks.secrets",
        "incoming_webhook_encryption_keys",
        "encrypt_signing_secret",
        "decrypt_signing_secret",
    ),
]


@pytest.mark.parametrize(
    ("module_path", "list_setting", "encrypt_name", "decrypt_name"), _API_FAMILIES
)
def test_a_token_written_under_the_old_key_survives_adding_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    list_setting: str,
    encrypt_name: str,
    decrypt_name: str,
) -> None:
    """Phase 1 of the rotation: new key at the head, nothing re-encrypted yet."""
    import importlib

    module = importlib.import_module(module_path)
    encrypt = getattr(module, encrypt_name)
    decrypt = getattr(module, decrypt_name)

    monkeypatch.setattr(module, "get_settings", lambda: _api_settings(**{list_setting: _OLD}))
    written_before = encrypt("the-stored-secret")

    monkeypatch.setattr(
        module, "get_settings", lambda: _api_settings(**{list_setting: f"{_NEW},{_OLD}"})
    )
    assert decrypt(written_before) == "the-stored-secret"

    # ...and the NEW write must be on the HEAD key, or step 3 of the rotation
    # (dropping _OLD) would destroy it.
    written_after = encrypt("written-during-the-rotation")
    assert Fernet(derive_fernet_key(_NEW)).decrypt(written_after.encode("ascii")) == (
        b"written-during-the-rotation"
    )
    with pytest.raises(InvalidToken):
        Fernet(derive_fernet_key(_OLD)).decrypt(written_after.encode("ascii"))


def test_notification_secrets_pair_survives_the_same_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write side is the api-server, the read side is the dispatcher: the
    rotation has to hold ACROSS the two apps, which is the failure mode the pair
    contract exists for."""
    from api_server.notifications import secrets as write_side
    from notification_dispatcher import secrets as read_side

    monkeypatch.setattr(
        write_side, "get_settings", lambda: _api_settings(notification_encryption_keys=_OLD)
    )
    ciphertext = write_side.encrypt_channel_secret("xoxb-bot-token")

    rotated_dispatcher = DispatcherSettings(
        environment="dev", notification_encryption_keys=f"{_NEW},{_OLD}"
    )
    assert read_side.decrypt_secret(ciphertext, rotated_dispatcher) == "xoxb-bot-token"


def test_every_builder_returns_a_multifernet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard against a partial migration: one builder left as a bare Fernet
    would keep working in every roundtrip test and fail only on rotation day."""
    import importlib

    from api_server.notifications import secrets as notif
    from notification_dispatcher import secrets as dispatcher_secrets

    seen = 0
    for module_path in (
        "api_server.auth.sso.secrets",
        "api_server.auth.mfa.secrets",
        "api_server.webhooks.secrets",
    ):
        module = importlib.import_module(module_path)
        monkeypatch.setattr(module, "get_settings", _api_settings)
        assert isinstance(module._fernet(), MultiFernet), module_path
        seen += 1

    monkeypatch.setattr(notif, "get_settings", _api_settings)
    assert isinstance(notif._fernet(), MultiFernet)
    seen += 1

    assert isinstance(
        dispatcher_secrets._fernet(DispatcherSettings(environment="dev")), MultiFernet
    )
    seen += 1

    assert seen == 5, f"the four builders + MFA are five ciphers; checked {seen}"


# ---------------------------------------------------------------------------
# 4. MFA gets its own ring (ADR 0143) without breaking anybody
# ---------------------------------------------------------------------------
def test_mfa_inherits_the_sso_ring_when_no_dedicated_key_is_configured() -> None:
    """The compatibility half of ADR 0143: unset means "keep decrypting the seeds
    you already have", so the split ships without a re-encryption run."""
    settings = _api_settings(sso_encryption_keys=f"{_NEW},{_OLD}")
    assert settings.mfa_key_is_dedicated is False
    assert settings.mfa_encryption_key_ring == (_NEW, _OLD)


def test_a_dedicated_mfa_key_decouples_the_two_rotations() -> None:
    """The security half: with its own ring, rotating the SSO key cannot
    invalidate a TOTP seed — which is what locked the System Admins out (gap2-4).
    """
    settings = _api_settings(sso_encryption_keys="sso-key-only", mfa_encryption_keys=_NEW)
    assert settings.mfa_key_is_dedicated is True
    assert settings.mfa_encryption_key_ring == (_NEW,)
    assert "sso-key-only" not in settings.mfa_encryption_key_ring


def test_the_mfa_singular_var_also_counts_as_dedicated() -> None:
    settings = _api_settings(mfa_encryption_key=_NEW)
    assert settings.mfa_key_is_dedicated is True
    assert settings.mfa_encryption_key_ring == (_NEW,)


# ---------------------------------------------------------------------------
# 5. The two parsers must not drift
# ---------------------------------------------------------------------------
_PARSER_TABLE = [
    ("", "single"),
    (None, "single"),
    ("a,b", "ignored"),
    (" a , b ,, a ,c ", None),
    ("only", None),
    ("  spaced  ", "x"),
]


@pytest.mark.parametrize(("plural", "singular"), _PARSER_TABLE)
def test_the_api_server_and_dispatcher_parsers_agree(
    plural: str | None, singular: str | None
) -> None:
    """Duplicated code with one contract: the api-server WRITES the channel
    ciphertext and the dispatcher READS it, so a divergence here is a
    notification that silently fails to send at 03:00."""
    mine = parse_key_ring(plural=plural, singular=singular, name="X")
    theirs = dispatcher_parse_key_ring(plural=plural, singular=singular, name="X")
    assert mine == theirs


def test_both_parsers_reject_the_empty_resolution() -> None:
    with pytest.raises(ValueError):
        parse_key_ring(plural="", singular="", name="X")
    with pytest.raises(ValueError):
        dispatcher_parse_key_ring(plural="", singular="", name="X")


# ---------------------------------------------------------------------------
# 6. No single-key builder survived
# ---------------------------------------------------------------------------
def test_no_at_rest_cipher_is_built_from_a_single_key() -> None:
    """Static guard. ``Fernet(...)`` may only appear inside the two key-ring
    modules (which build the ring's members); anywhere else it is a cipher that
    cannot be rotated.

    Asserts it found the builders BEFORE asserting there are no offenders, so the
    day someone moves the files this fails instead of passing on an empty scan.
    """
    single_key_re = re.compile(r"(?<!Multi)\bFernet\(")
    allowed = {
        Path("apps/api-server/src/api_server/auth/crypto_keys.py"),
        Path("apps/notification-dispatcher/src/notification_dispatcher/crypto_keys.py"),
    }
    roots = [_REPO_ROOT / "apps", _REPO_ROOT / "packages"]

    builders = 0
    offenders: list[str] = []
    scanned = 0
    for root in roots:
        assert root.is_dir(), f"expected {root} to exist"
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "site-packages" in path.parts:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8")
            builders += text.count("build_multifernet(")
            relative = path.relative_to(_REPO_ROOT)
            if relative in allowed:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if single_key_re.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")

    assert scanned >= 200, f"the scan stopped finding source files (saw {scanned})"
    assert builders >= 5, (
        "the guard lost track of the ring builders: expected at least the five "
        f"at-rest ciphers to call build_multifernet(), saw {builders}"
    )
    assert not offenders, "single-key Fernet ciphers survive:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# 7. The staging/prod guards now see the whole ring
# ---------------------------------------------------------------------------
def _prod_kwargs() -> dict[str, str]:
    """A minimal staging config that passes every guard, so each test below can
    break exactly one thing."""
    return {
        "environment": "staging",
        "jwt_secret": "real-human-session-secret-0123456789abcd",
        "internal_token_secret": "real-worker-internal-secret-0123456789ab",
        "review_url_signing_secret": "real-review-url-secret-0123456789",
        "sso_encryption_key": _OLD,
        "notification_encryption_key": _OLD,
        "incoming_webhook_encryption_key": _OLD,
        "minio_secret_key": "real-minio-secret-key-0123456789",
        "minio_access_key": "real-minio-access",
        "database_url": "postgresql+asyncpg://app:realpw@db/agentic_platform",
        "admin_database_url": "postgresql+asyncpg://svc:realpw@db/agentic_platform",
    }


def test_the_baseline_staging_config_is_accepted() -> None:
    """Without this, every negative test below could be passing for the wrong
    reason (some unrelated guard firing first)."""
    assert Settings(**_prod_kwargs()).environment == "staging"  # type: ignore[arg-type]


def test_a_dev_key_hidden_in_the_TAIL_of_a_ring_is_rejected_in_staging() -> None:
    """The subtle one. A retired key signs/encrypts nothing new, but it still
    DECRYPTS, so a publicly known key in position 1 is exactly as bad as in
    position 0 — and the pre-prod-05 guard, which only read the singular var,
    would not have seen it."""
    kwargs = _prod_kwargs() | {
        "sso_encryption_keys": f"{_NEW},dev-only-sso-encryption-key-change-me"
    }
    with pytest.raises(ValueError, match="SSO_ENCRYPTION_KEY"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_a_rotated_deployment_is_accepted_even_if_the_singular_var_is_a_dev_default() -> None:
    """The mirror-image failure: the guard must not reject a GOOD config. With
    the list set, the singular value is dead configuration — rejecting it would
    force operators to edit two vars to rotate one key."""
    kwargs = _prod_kwargs() | {
        "sso_encryption_key": "dev-only-sso-encryption-key-change-me",
        "sso_encryption_keys": f"{_NEW},{_OLD}",
    }
    assert Settings(**kwargs).sso_encryption_key_ring == (_NEW, _OLD)  # type: ignore[arg-type]


def test_a_short_key_in_the_tail_of_the_jwt_ring_is_rejected() -> None:
    kwargs = _prod_kwargs() | {"jwt_secrets": f"{_prod_kwargs()['jwt_secret']},x"}
    with pytest.raises(ValueError, match="at least"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_the_jwt_and_internal_rings_must_stay_disjoint() -> None:
    """The prod-09 invariant, widened by prod-05: it used to be "the two head
    values differ", which a rotation could satisfy while leaving the retired
    session key in the worker's verify list — the same privilege escalation by a
    slower route."""
    shared = _prod_kwargs()["jwt_secret"]
    kwargs = _prod_kwargs() | {
        "internal_token_secrets": f"{_prod_kwargs()['internal_token_secret']},{shared}"
    }
    with pytest.raises(ValueError, match="share NO key"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_the_dispatcher_guard_also_reads_the_whole_ring() -> None:
    with pytest.raises(ValueError, match="NOTIFICATION_ENCRYPTION_KEY"):
        DispatcherSettings(
            environment="staging",
            database_url="postgresql+asyncpg://svc:realpw@db/agentic_platform",
            notification_encryption_keys=f"{_NEW},dev-only-notification-encryption-key-change-me",
        )
