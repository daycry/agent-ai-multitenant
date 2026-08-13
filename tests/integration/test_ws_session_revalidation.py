"""Open WebSockets re-validate their session (prod-09 task_prod09_13, authz-3).

``routers/ws.py`` documented that "logout/revocation closes existing sockets".
It did not: the session lookup ran ONCE, at accept. An already-connected socket
kept streaming kanban / execution / conversation / plan events after logout, after
SCIM deprovisioning, and past its own token's expiry — for as long as the browser
tab stayed open. The only bound was the tab's lifetime.

``_pump`` now re-checks the credential every
``ws_session_revalidate_seconds`` and closes with **1008** (policy violation) the
moment the session is gone or the token has expired.

These tests drive the REAL ``_pump`` with a fake WebSocket and a fake Redis
stream, so the loop under test is the production one; only its two I/O edges are
doubled. The revalidation interval is squeezed to a few milliseconds via the
settings seam so the tests are fast AND deterministic — no sleeping for 30 s and
hoping.

Both directions matter and both are asserted: a revoked credential MUST close the
socket, and a live one MUST NOT (a pump that closed on every tick would pass the
first test and break the product).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from api_server.auth.deps import AuthPrincipal
from api_server.routers import ws as ws_mod

pytestmark = pytest.mark.integration

_CLOSE_POLICY = 1008


# ---------------------------------------------------------------------------
# Doubles for the two I/O edges of _pump
# ---------------------------------------------------------------------------
class _FakeWebSocket:
    """Records what the pump sent/closed; ``receive()`` never completes.

    A client that stays connected is exactly a ``receive()`` that never returns,
    which is what lets the loop keep iterating on the Redis read.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: tuple[int, str | None] | None = None
        self._never = asyncio.Event()

    async def receive(self) -> dict[str, Any]:
        await self._never.wait()
        return {}  # pragma: no cover - unreachable

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_with = (code, reason)
        self._never.set()  # unblock the reader so the pump can unwind


class _FakeRedis:
    """A stream that yields ``entries`` once and then behaves like an idle
    stream: each subsequent ``xread`` sleeps briefly and returns nothing."""

    def __init__(self, entries: list[tuple[str, dict[str, str]]] | None = None) -> None:
        self._entries = entries or []
        self.xread_calls = 0

    async def xread(
        self, streams: dict[str, str], count: int = 0, block: int = 0
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self.xread_calls += 1
        if self._entries:
            pending, self._entries = self._entries, []
            return [(next(iter(streams)), pending)]
        await asyncio.sleep(0.005)  # stand-in for the real XREAD block window
        return []

    async def time(self) -> tuple[int, int]:
        return (1_700_000_000, 0)


class _Sessions:
    """Session store double whose liveness can be flipped mid-flight."""

    def __init__(self, *, live: bool = True) -> None:
        self.live = live
        self.lookups = 0

    async def get(self, _sid: UUID) -> dict[str, Any] | None:
        self.lookups += 1
        return {"user_id": str(uuid4()), "created_at": 0} if self.live else None


def _principal() -> AuthPrincipal:
    return AuthPrincipal(
        user_id=uuid4(), session_id=uuid4(), tenant_id=uuid4(), is_system_admin=False
    )


def _fast_revalidation(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Point ``_pump`` at a tiny revalidation interval.

    Patches the settings accessor the pump reads rather than the constant, so the
    test exercises the real configurable seam (a hardcoded 30 s would be
    untestable, which is why it is a setting).
    """

    class _S:
        ws_session_revalidate_seconds = seconds

    monkeypatch.setattr(ws_mod, "get_settings", _S)


async def _run_pump(ws: _FakeWebSocket, **kwargs: Any) -> None:
    """Run the pump with a hard timeout so a hang fails loudly instead of
    stalling the suite."""
    await asyncio.wait_for(ws_mod._pump(ws, **kwargs), timeout=5.0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The finding: a revoked session closes the open socket
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoked_session_closes_the_socket_with_1008(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logout while the socket is open -> 1008, without the client doing anything.

    The pump starts on a LIVE session (so the close cannot be blamed on a
    never-authenticated socket), the session is then dropped, and the next
    revalidation tick must close it.
    """
    _fast_revalidation(monkeypatch, 0.01)
    ws, redis, sessions = _FakeWebSocket(), _FakeRedis(), _Sessions(live=True)
    principal = _principal()

    async def _revoke_soon() -> None:
        await asyncio.sleep(0.05)
        sessions.live = False

    revoker = asyncio.ensure_future(_revoke_soon())
    await _run_pump(
        ws,
        redis=redis,
        stream="s",
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=None,
    )
    await revoker

    assert ws.closed_with is not None, "the socket outlived the revocation"
    code, reason = ws.closed_with
    assert code == _CLOSE_POLICY
    assert reason is not None and "revoked" in reason
    assert sessions.lookups >= 2, (
        f"the pump only checked the session {sessions.lookups} time(s) — it is not "
        "re-validating periodically"
    )


@pytest.mark.asyncio
async def test_expired_token_closes_the_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expiry is enforced on the OPEN socket too, not only at accept.

    A 24 h token accepted at minute 0 used to keep streaming at hour 25. The pump
    re-runs ``decode_jwt``, so ``exp`` is honoured with at most one interval of
    delay. Uses a genuinely expired, correctly-signed token — not a mocked
    validator — so the test would catch a pump that skipped the token leg.
    """
    _fast_revalidation(monkeypatch, 0.01)
    from api_server.auth.jwt import encode_jwt

    principal = _principal()
    expired = encode_jwt(
        user_id=principal.user_id,
        session_id=principal.session_id,
        tenant_id=principal.tenant_id,
        expires_in=timedelta(seconds=-30),
    )
    ws, redis = _FakeWebSocket(), _FakeRedis()
    sessions = _Sessions(live=True)  # session still live: only the TOKEN is stale

    await _run_pump(
        ws,
        redis=redis,
        stream="s",
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=expired,
    )

    assert ws.closed_with is not None
    assert ws.closed_with[0] == _CLOSE_POLICY


@pytest.mark.asyncio
async def test_a_live_credential_keeps_the_socket_open_and_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterweight — without it, "close on every tick" would pass above.

    A valid token + a live session survive many revalidation ticks, the stream
    entry is still delivered, and nothing is closed.
    """
    _fast_revalidation(monkeypatch, 0.01)
    from api_server.auth.jwt import encode_jwt

    principal = _principal()
    valid = encode_jwt(
        user_id=principal.user_id,
        session_id=principal.session_id,
        tenant_id=principal.tenant_id,
        expires_in=timedelta(minutes=30),
    )
    ws = _FakeWebSocket()
    redis = _FakeRedis(entries=[("1-0", {"event": "task.moved", "payload": "{}"})])
    sessions = _Sessions(live=True)

    pump = asyncio.ensure_future(
        ws_mod._pump(
            ws,
            redis,  # type: ignore[arg-type]
            "s",
            project_filter=None,
            sessions=sessions,
            principal=principal,
            token=valid,
        )
    )
    await asyncio.sleep(0.2)  # ~20 revalidation intervals
    assert ws.closed_with is None, f"a live credential was closed: {ws.closed_with}"
    assert sessions.lookups >= 5, "the periodic check is not running"
    assert ws.sent and ws.sent[0]["event"] == "task.moved"

    pump.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pump


@pytest.mark.asyncio
async def test_revalidation_can_be_disabled_with_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ws_session_revalidate_seconds = 0`` opts out (an escape hatch for an
    operator debugging a Redis problem). Pinned so the check is a knob and not an
    accident: with 0 the store is consulted exactly ZERO times."""
    _fast_revalidation(monkeypatch, 0)
    ws, redis, sessions = _FakeWebSocket(), _FakeRedis(), _Sessions(live=False)

    pump = asyncio.ensure_future(
        ws_mod._pump(
            ws,
            redis,  # type: ignore[arg-type]
            "s",
            project_filter=None,
            sessions=sessions,
            principal=_principal(),
            token=None,
        )
    )
    await asyncio.sleep(0.1)
    assert ws.closed_with is None
    assert sessions.lookups == 0

    pump.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pump


# ---------------------------------------------------------------------------
# Wiring: no endpoint may mount a pump that skips the re-check
# ---------------------------------------------------------------------------
def test_every_ws_endpoint_passes_the_credential_to_the_pump() -> None:
    """The re-check is only real if every socket gets it.

    ``_pump``'s ``sessions``/``principal``/``token`` are keyword-REQUIRED, so a
    call that omits them is a TypeError at request time — but that is a runtime
    failure on a rarely-exercised path, so pin it statically as well: every
    ``_pump(`` call site in the module must pass ``sessions=``.
    """
    import inspect
    import re

    source = inspect.getsource(ws_mod)
    calls = re.findall(r"await _pump\((.*?)\n    \)", source, flags=re.DOTALL)
    assert len(calls) >= 5, f"expected the 5 /ws endpoints to pump; found {len(calls)}"
    missing = [call for call in calls if "sessions=sessions" not in call]
    assert not missing, f"{len(missing)} _pump call(s) do not pass the session store"


def test_the_stale_guarantee_is_no_longer_claimed_without_a_mechanism() -> None:
    """The module docstring promised revocation closed open sockets while nothing
    implemented it. Assert the prose and the code now agree: the docstring names
    the periodic re-validation, and the function it names exists."""
    assert "task_prod09_13" in (ws_mod.__doc__ or "")
    assert hasattr(ws_mod, "_credential_still_valid")


def test_expiry_is_not_reimplemented_from_claims() -> None:
    """The re-check must delegate to ``decode_jwt`` rather than compare ``exp``
    by hand — a second expiry implementation is a second thing to get wrong."""
    import inspect

    source = inspect.getsource(ws_mod._credential_still_valid)
    assert "decode_jwt" in source
    assert '"exp"' not in source and "'exp'" not in source


@pytest.mark.asyncio
async def test_datetime_import_is_used_for_a_real_token() -> None:
    """Sanity guard for this test module itself: the expired token above must be
    genuinely in the past, otherwise that test would pass vacuously."""
    from api_server.auth.jwt import decode_jwt

    stale = datetime.now(tz=UTC) - timedelta(seconds=30)
    assert stale < datetime.now(tz=UTC)
    from api_server.auth.jwt import InvalidTokenError, encode_jwt

    token = encode_jwt(user_id=uuid4(), session_id=uuid4(), expires_in=timedelta(seconds=-30))
    with pytest.raises(InvalidTokenError):
        decode_jwt(token)
