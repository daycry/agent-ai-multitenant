"""Integración git por proyecto (ADR 0072) — helpers compartidos.

Importable tanto por el api-server (endpoint que guarda la credencial) como por
el worker (task de clone que la lee), así el path de Vault es fuente única.
"""

from __future__ import annotations

from uuid import UUID

__all__ = ["project_git_secret_path"]


def project_git_secret_path(project_id: UUID | str) -> str:
    """Path lógico (bajo el mount KV de plataforma) del secreto git del proyecto.

    Es un *pointer*, nunca el secreto. Espejo de ``provider_secret_path`` (ADR
    0028): los secretos solo viven en Vault. Para PAT guarda ``{username, token}``;
    para SSH, ``{ssh_key}``.
    """
    return f"projects/{project_id}/git"
