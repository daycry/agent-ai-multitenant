"""Temporary bootstrap installer backend.

This package is the FastAPI backend served by the *installer* container —
a self-destructing, temporary container that runs ONLY during first-time
provisioning (see ``docs/roadmap/15-instalador-produccion.md`` Fase A,
Decisiones Clave: "Installer en contenedor separado que se autodestruye").

It is NOT part of the runtime stack and never ships in the production
docker-compose. Everything that touches the host (docker compose up, prereq
probes, file writes under ``/data/agent-platform``, Vault bootstrap) lives
behind injectable *seams* (Protocols) so the wizard state machine and install
orchestration can be exercised in tests without a real Docker host.

Phase A (this plan) ships the shell: the 9-step wizard state machine + a
minimal API. The real config generators (compose/.env/Vault) arrive in
Phase B (tasks 15_07-15_09); the wizard steps 1-9 are filled by 15_02-15_06.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.0"
