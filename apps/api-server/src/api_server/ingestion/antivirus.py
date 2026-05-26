"""Antivirus integration (Plan 04 task_04_13).

Every uploaded document is scanned **before** it reaches Docling. The
default backend is ClamAV's INSTREAM TCP protocol (already in the
docker-compose); a positive hit (`AntivirusVerdict.INFECTED`) makes
the pipeline flip the document to ``failed`` with the signature name
on `error_message`.

For tests we ship two fakes:

  - :class:`NullAntivirus` — passes everything (used by integration
    tests that don't care about the AV path).
  - :class:`StubAntivirus` — returns ``INFECTED`` whenever the
    payload contains the EICAR test string; deterministic and
    keyboard-typeable.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import structlog

from api_server.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# Standard EICAR test pattern (https://www.eicar.org/). Used by
# `StubAntivirus` so tests can hit the infected branch without a
# real malware sample.
EICAR_TEST_STRING = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class AntivirusVerdict(StrEnum):
    """Outcome of one scan."""

    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"  # backend unreachable or timed out


@dataclass(frozen=True)
class AntivirusReport:
    """One scan result. ``signature`` is the matched signature name
    when ``verdict == INFECTED``; ``message`` is a human-readable
    blurb (e.g. the AV's raw response or the timeout reason)."""

    verdict: AntivirusVerdict
    signature: str | None = None
    message: str | None = None


class AntivirusScanner(Protocol):
    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Real implementation: ClamAV INSTREAM
# ---------------------------------------------------------------------------
class ClamAVScanner:
    """TCP INSTREAM client for clamd.

    Protocol (from `clamd(8)`):

        nINSTREAM\\n
        <chunk-size:uint32 BE><chunk-bytes>...
        <0:uint32 BE>           # end of stream

        ← stream: <signature> FOUND
        ← stream: OK
        ← stream: <reason> ERROR
    """

    _CHUNK = 64 * 1024  # 64 KiB — clamd's default StreamMaxLength is well above this

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout_seconds: float = 30.0,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self._host = host or cfg.clamav_host
        self._port = port or cfg.clamav_port
        self._timeout = timeout_seconds

    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as exc:
            logger.warning("antivirus.connect_failed", error=str(exc))
            return AntivirusReport(
                verdict=AntivirusVerdict.ERROR,
                message=f"clamd unreachable: {exc}",
            )
        try:
            writer.write(b"nINSTREAM\n")
            for offset in range(0, len(data), self._CHUNK):
                chunk = data[offset : offset + self._CHUNK]
                writer.write(len(chunk).to_bytes(4, "big") + chunk)
            writer.write((0).to_bytes(4, "big"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=self._timeout)
        except (OSError, asyncio.IncompleteReadError, TimeoutError) as exc:
            logger.warning("antivirus.stream_failed", error=str(exc), filename=filename)
            return AntivirusReport(verdict=AntivirusVerdict.ERROR, message=f"stream failed: {exc}")
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

        response = raw.decode("ascii", errors="replace").strip()
        # `stream: <signature> FOUND` or `stream: OK` or `... ERROR`.
        if response.endswith("OK"):
            return AntivirusReport(verdict=AntivirusVerdict.CLEAN, message=response)
        if response.endswith("FOUND"):
            # `stream: Eicar-Signature FOUND` → signature is the
            # middle token.
            parts = response.split()
            signature = parts[1] if len(parts) >= 3 else "unknown"
            return AntivirusReport(
                verdict=AntivirusVerdict.INFECTED,
                signature=signature,
                message=response,
            )
        return AntivirusReport(verdict=AntivirusVerdict.ERROR, message=response)

    async def aclose(self) -> None:  # pragma: no cover — nothing to close
        pass


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------
class NullAntivirus:
    """Passes everything. Useful when a test wants to exercise the
    pipeline minus the AV path."""

    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport:  # noqa: ARG002
        return AntivirusReport(verdict=AntivirusVerdict.CLEAN)

    async def aclose(self) -> None:  # pragma: no cover
        pass


class StubAntivirus:
    """Returns ``INFECTED`` when the payload contains the EICAR test
    string. Deterministic and safe to type — no real malware required.
    """

    name = "EICAR-TEST"

    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport:  # noqa: ARG002
        if EICAR_TEST_STRING.encode("ascii") in data:
            return AntivirusReport(
                verdict=AntivirusVerdict.INFECTED,
                signature=self.name,
                message="stub: EICAR string detected",
            )
        return AntivirusReport(verdict=AntivirusVerdict.CLEAN)

    async def aclose(self) -> None:  # pragma: no cover
        pass


__all__ = [
    "EICAR_TEST_STRING",
    "AntivirusReport",
    "AntivirusScanner",
    "AntivirusVerdict",
    "ClamAVScanner",
    "NullAntivirus",
    "StubAntivirus",
]
