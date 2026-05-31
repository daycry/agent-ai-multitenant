"""Process entry point for the installer backend.

Run with ``python -m installer_backend`` or the ``installer-backend`` script.
The installer container's bootstrap compose runs this; it is torn down after
the install completes (self-destructing installer, Plan 15 Fase A).
"""

from __future__ import annotations

import os


def main() -> None:
    # Lazy import so `--help`-style tooling and the test suite don't pay the
    # uvicorn import cost, and so logging can be configured before the app
    # builds (same pattern as the orchestrator/watchdog entry points).
    import uvicorn

    host = os.environ.get("INSTALLER_HOST", "0.0.0.0")
    port = int(os.environ.get("INSTALLER_PORT", "8080"))
    uvicorn.run("installer_backend.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
