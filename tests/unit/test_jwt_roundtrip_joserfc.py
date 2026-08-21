"""One JOSE stack: `auth/jwt.py` on `joserfc` (prod-09 task_prod09_17, quality-10).

The migration itself is a one-liner's worth of API translation. What makes it
dangerous — and what this file exists to pin down — is that **the two libraries
do not validate the same things by default**:

  * ``jose.jwt.decode`` (python-jose) validates ``exp`` on its own and raises
    ``ExpiredSignatureError`` (a ``JWTError``).
  * ``joserfc.jwt.decode`` validates ONLY the signature. It returns the claims of
    an expired token happily; expiry is a separate, opt-in
    ``JWTClaimsRegistry().validate(claims)`` call.

A naive port therefore keeps every existing test green while silently accepting
expired sessions forever — and `routers/ws.py::_credential_still_valid`
(authz-3) documents in prose that it relies on ``decode_jwt`` to enforce ``exp``
on an already-open socket, so the blast radius includes long-lived WebSockets,
not just the next HTTP request.

The second trap is smaller but also silent: joserfc will not take a ``str``
secret. Handed one it raises a bare ``ValueError("Invalid key")``, which is NOT a
``JoseError`` — so an ``except JoseError`` port turns "bad token" into an
unhandled 500. The key must be wrapped in an ``OctKey``.

Every token below that is not produced by ``encode_jwt`` is signed HERE, at the
wire level, with ``hmac`` + ``base64url`` and no JOSE library at all. That keeps
the assertions independent of the implementation under test (they would catch a
bug that both libraries shared) and lets the legacy-compatibility test survive
the removal of python-jose from the dependency set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from api_server.auth import jwt as jwt_mod
from api_server.auth.jwt import InvalidTokenError, decode_jwt, encode_jwt
from api_server.config import Settings

pytestmark = pytest.mark.unit

_SECRET = "human-session-signing-key-0123456789abcd"


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    cfg = Settings(
        environment="dev",
        jwt_secret=_SECRET,
        internal_token_secret="worker-internal-signing-key-0123456789ab",
    )
    monkeypatch.setattr(jwt_mod, "get_settings", lambda: cfg)
    return cfg


# ---------------------------------------------------------------------------
# Wire-level HS256, deliberately library-free
# ---------------------------------------------------------------------------
def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _sign_raw(claims: dict[str, Any], secret: str, *, alg: str = "HS256") -> str:
    """Mint a JWT with the stdlib only. ``alg='none'`` leaves the signature empty."""
    digest = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}
    signing_input = (
        _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
        + b"."
        + _b64(json.dumps(claims).encode())
    )
    if alg == "none":
        signature = b""
    else:
        signature = _b64(hmac.new(secret.encode(), signing_input, digest[alg]).digest())
    return (signing_input + b"." + signature).decode()


def _fresh_claims(**extra: Any) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(extra)
    return claims


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_roundtrip_carries_every_claim(settings: Settings) -> None:
    user_id, session_id, tenant_id = uuid4(), uuid4(), uuid4()
    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=True,
        is_system_owner=True,
        extra_claims={"amr": ["mfa"]},
    )
    claims = decode_jwt(token)

    assert claims["sub"] == str(user_id)
    assert claims["sid"] == str(session_id)
    assert claims["tid"] == str(tenant_id)
    assert claims["sys"] is True
    assert claims["own"] is True
    assert claims["amr"] == ["mfa"]
    assert claims["exp"] > claims["iat"]


def test_tenantless_token_omits_tid(settings: Settings) -> None:
    """The pre-tenant-selection session: `tid` must be ABSENT, not null — the
    middleware distinguishes the two."""
    claims = decode_jwt(encode_jwt(user_id=uuid4(), session_id=uuid4()))
    assert "tid" not in claims
    assert "sys" not in claims
    assert "own" not in claims


def test_the_header_declares_the_configured_algorithm(settings: Settings) -> None:
    token = encode_jwt(user_id=uuid4(), session_id=uuid4())
    header_b64 = token.split(".")[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded))
    assert header["alg"] == settings.jwt_algorithm == "HS256"


# ---------------------------------------------------------------------------
# TRAP 1 — expiry. joserfc's bare decode() does not check it.
# ---------------------------------------------------------------------------
def test_an_expired_token_is_rejected(settings: Settings) -> None:
    """THE regression this migration could have introduced.

    `routers/ws.py::_credential_still_valid` re-calls ``decode_jwt`` on an open
    socket precisely to notice expiry; `auth/deps.py` does the same per request.
    If this assertion ever flips, both stop expiring anything.
    """
    stale = encode_jwt(user_id=uuid4(), session_id=uuid4(), expires_in=timedelta(seconds=-10))
    with pytest.raises(InvalidTokenError):
        decode_jwt(stale)


def test_a_token_that_expired_one_second_ago_is_rejected(settings: Settings) -> None:
    """No accidental leeway: joserfc's registry accepts a ``leeway`` argument and
    we must not be passing one."""
    now = datetime.now(tz=UTC)
    claims = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int((now - timedelta(hours=1)).timestamp()),
        "exp": int((now - timedelta(seconds=1)).timestamp()),
    }
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(claims, _SECRET))


def test_a_correctly_signed_token_without_exp_is_rejected(settings: Settings) -> None:
    """``exp`` is not optional. joserfc's registry treats every claim as
    optional unless declared essential, so a token minted without ``exp`` — by a
    future caller, or by anyone who ever gets hold of the signing key — would
    otherwise be a session that never dies."""
    claims = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(claims, _SECRET))


def test_a_token_issued_in_the_future_is_rejected(settings: Settings) -> None:
    now = datetime.now(tz=UTC) + timedelta(days=1)
    claims = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(claims, _SECRET))


# ---------------------------------------------------------------------------
# Signature / algorithm
# ---------------------------------------------------------------------------
def test_a_token_signed_with_another_key_is_rejected(settings: Settings) -> None:
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(_fresh_claims(), "a-completely-different-signing-key-xxx"))


def test_a_tampered_payload_is_rejected(settings: Settings) -> None:
    token = encode_jwt(user_id=uuid4(), session_id=uuid4())
    header, _payload, signature = token.split(".")
    forged_payload = _b64(json.dumps(_fresh_claims(sys=True)).encode()).decode()
    with pytest.raises(InvalidTokenError):
        decode_jwt(f"{header}.{forged_payload}.{signature}")


def test_alg_none_is_rejected(settings: Settings) -> None:
    """The classic downgrade: an unsigned token whose header says `alg: none`."""
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(_fresh_claims(), _SECRET, alg="none"))


def test_a_different_hmac_algorithm_is_rejected(settings: Settings) -> None:
    """Signed with the RIGHT key but the wrong algorithm: the allowlist, not the
    key, has to be what rejects it."""
    with pytest.raises(InvalidTokenError):
        decode_jwt(_sign_raw(_fresh_claims(), _SECRET, alg="HS512"))


# ---------------------------------------------------------------------------
# TRAP 2 — every failure must surface as InvalidTokenError, never a bare
# ValueError/TypeError that would escape `except InvalidTokenError` and 500.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "abc",
        "a.b",
        "a.b.c",
        "....",
        "e30.e30.",
        "Bearer eyJ.eyJ.sig",
        "eyJhbGciOiJIUzI1NiJ9.bm90LWpzb24.c2ln",
        "ñññ.ñññ.ñññ",
    ],
)
def test_malformed_tokens_raise_invalid_token_error(settings: Settings, garbage: str) -> None:
    with pytest.raises(InvalidTokenError):
        decode_jwt(garbage)


def test_a_json_array_payload_is_rejected(settings: Settings) -> None:
    """A signed but non-object payload: `decode_jwt` promises a claims dict, and
    the callers index into it."""
    signing_input = (
        _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + b"."
        + _b64(json.dumps(["not", "an", "object"]).encode())
    )
    signature = _b64(hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest())
    with pytest.raises(InvalidTokenError):
        decode_jwt((signing_input + b"." + signature).decode())


# ---------------------------------------------------------------------------
# No flag day: tokens minted before the deploy keep working
# ---------------------------------------------------------------------------
def test_a_token_minted_by_the_previous_library_still_validates(settings: Settings) -> None:
    """Same HS256 wire format, same secret, same claims — only the library
    changed, so the 24 h of sessions in flight at deploy time must survive."""
    claims = _fresh_claims(tid=str(uuid4()), sys=True)
    decoded = decode_jwt(_sign_raw(claims, _SECRET))
    assert decoded == claims


# ---------------------------------------------------------------------------
# The retirement itself
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_jose_is_gone_from_the_tree() -> None:
    """The point of the task: ONE JOSE stack. Scans real source, and asserts it
    scanned something, so the guard cannot pass vacuously if the layout moves.
    """
    # `joserfc` must NOT match: the boundary after `jose` is what separates the
    # library we keep from the one we retire.
    import_re = re.compile(r"^\s*(?:from\s+jose(?:\.\w+)*\s+import|import\s+jose(?:\.\w+)*)\b")
    roots = [
        _REPO_ROOT / "apps",
        _REPO_ROOT / "packages",
        _REPO_ROOT / "tests",
    ]
    scanned = 0
    offenders: list[str] = []
    for root in roots:
        assert root.is_dir(), f"expected {root} to exist"
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "site-packages" in path.parts:
                continue
            scanned += 1
            for line in path.read_text(encoding="utf-8").splitlines():
                if import_re.match(line):
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {line.strip()}")
    assert scanned >= 500, f"the scan stopped finding source files (saw {scanned})"
    assert not offenders, "python-jose is still imported:\n" + "\n".join(offenders)


def test_python_jose_is_gone_from_the_dependency_set() -> None:
    """Reads the DEPENDENCY DATA, not the file text — the prose around it
    mentions the retired package on purpose (so the next reader knows why the
    swap was not a drop-in)."""
    with (_REPO_ROOT / "apps/api-server/pyproject.toml").open("rb") as fh:
        dependencies = tomllib.load(fh)["project"]["dependencies"]
    names = {re.split(r"[<>=!~\[; ]", dep, maxsplit=1)[0] for dep in dependencies}
    assert names, "no dependencies parsed — the guard would pass vacuously"
    assert "python-jose" not in names
    assert "joserfc" in names, "the replacement must be declared, not merely transitive"


def test_the_mypy_override_for_the_retired_package_is_gone() -> None:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        overrides = tomllib.load(fh)["tool"]["mypy"]["overrides"]
    modules = {module for override in overrides for module in override["module"]}
    assert "joserfc" in modules, "the scan lost track of the mypy overrides"
    assert "jose" not in modules
    assert "jose.*" not in modules
