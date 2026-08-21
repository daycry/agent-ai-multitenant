"""Identidad git de la plataforma — FUENTE ÚNICA (auditoría 2026-07-03, causa raíz A).

Todo commit que crea el sistema (el raíz sintético del bare, el commit de una tarea,
el committer del rebase que reconcilia dos tareas hermanas, el commit de la
documentación de cierre) se firma con ESTA identidad y con ninguna otra.

Antes vivía escrita a mano en tres sitios y con dos valores distintos
(``platform@agentic.local`` en ``git_repos``, ``noreply@agentic.local`` en
``plan_git``), así que el historial de un mismo proyecto quedaba atribuido a dos
autores; los proveedores git atribuyen por email, de modo que cualquier mapeo o
filtro por autor solo cubría la mitad de los commits.

Se unifica en ``noreply@agentic.local``: es la dirección de los commits de tarea (la
mayoría abrumadora del historial) y ``noreply@`` es la convención para direcciones no
entregables (la misma que usa GitHub). El nombre visible sigue siendo
«Agentic Platform».

NO se lee de configuración: es la firma del sistema, no una preferencia del tenant.
Un proyecto que quiera atribuir a un humano lo hace con el trailer
``Generated-By``/``Plan-Id`` (ver :class:`workers.plan_git.CommitTrailers`), no
cambiando el autor — el autor identifica QUIÉN escribió el commit, y lo escribió la
plataforma.
"""

from __future__ import annotations

#: Nombre visible del autor/committer de todo commit que crea la plataforma.
PLATFORM_GIT_NAME = "Agentic Platform"

#: Email del autor/committer. Dominio no entregable a propósito.
PLATFORM_GIT_EMAIL = "noreply@agentic.local"


def git_identity_env() -> dict[str, str]:
    """Las cuatro variables de entorno que git necesita para firmar un commit.

    Se pasa como ``env_extra`` a ``_run_git`` en vez de confiar en ``user.name``/
    ``user.email``: dentro del contenedor no hay config git de usuario (y si la
    hubiera, la identidad dejaría de ser predecible).
    """
    return {
        "GIT_AUTHOR_NAME": PLATFORM_GIT_NAME,
        "GIT_AUTHOR_EMAIL": PLATFORM_GIT_EMAIL,
        "GIT_COMMITTER_NAME": PLATFORM_GIT_NAME,
        "GIT_COMMITTER_EMAIL": PLATFORM_GIT_EMAIL,
    }


__all__ = ["PLATFORM_GIT_EMAIL", "PLATFORM_GIT_NAME", "git_identity_env"]
