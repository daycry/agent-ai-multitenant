"""Cliente Docker best-effort compartido (dedup 2026-07-08).

El patrón «``docker.from_env()`` + ``ping()`` o degrada con gracia» estaba
copiado en varios sitios (test-runtime, spawn del review-runtime, reap de
contenedores). Una sola implementación: devuelve el cliente listo o ``None``
cuando el SDK no está instalado o el daemon no responde — cada caller decide
su propio valor de degradación (stub, tupla vacía, 0 reaped…).

``stack_exec`` NO usa este helper a propósito: distingue import-fail de
daemon-fail en su mensaje al agente y necesita ``docker.errors.APIError``
en un ``except`` (el módulo, no solo el cliente).
"""

from __future__ import annotations

from typing import Any


def get_docker_client() -> Any | None:
    """El cliente Docker del worker, o ``None`` si SDK/daemon no están."""
    try:
        import docker
    except ImportError:
        return None
    try:
        client = docker.from_env()
        client.ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
        return None
    return client
