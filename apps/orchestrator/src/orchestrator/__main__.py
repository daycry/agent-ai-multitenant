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

    from orchestrator.app import create_app
    from orchestrator.config import get_settings
    from orchestrator.dispatch import build_dispatch_handler

    configure_logging(service="orchestrator")

    # Build the app with the real dispatch handler wired in (task_02_31)
    # and hand uvicorn the instance — string-import would skip the wiring.
    settings = get_settings()
    app = create_app(settings, handler=build_dispatch_handler(settings))

    uvicorn.run(
        app,
        host=os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("ORCHESTRATOR_PORT", "8002")),
        log_config=None,  # structlog owns logging
    )


if __name__ == "__main__":
    main()
