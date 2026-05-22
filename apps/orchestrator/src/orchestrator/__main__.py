"""Orchestrator process entry point.

    python -m orchestrator        # serve on 0.0.0.0:8002

Port 8002 keeps clear of api-server (8001) and admin-panel (3000).
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # Lazy import so `configure_logging` runs before the app builds.
    from api_server.logging import configure_logging

    configure_logging(service="orchestrator")

    uvicorn.run(
        "orchestrator.app:app",
        host=os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("ORCHESTRATOR_PORT", "8002")),
        log_config=None,  # structlog owns logging
    )


if __name__ == "__main__":
    main()
