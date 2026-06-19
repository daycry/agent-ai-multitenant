"""Construcción del entorno de autenticación para operaciones git (ADR 0072).

Las operaciones contra un remoto (clone/fetch/push a GitHub/GitLab/Azure DevOps/
self-hosted) necesitan credenciales. Este módulo traduce el modo de auth del
proyecto + el secreto (resuelto de Vault por el caller) a las variables de
entorno que `git` consume, SIN persistir nada en `.git/config` ni en la URL:

  * ``pat`` (HTTPS) → un script ``GIT_ASKPASS`` efímero que devuelve
    usuario/token desde ``GIT_USERNAME``/``GIT_PASSWORD``. El ``origin`` queda
    limpio (sin token); el token vive solo en el env del proceso git.
  * ``ssh`` → la clave privada en un fichero temporal ``0600`` +
    ``GIT_SSH_COMMAND`` apuntando a ella.
  * cualquier otro / sin secreto → sin auth (no-op).

Devuelve un :class:`GitAuthEnv` con el ``env`` a fusionar en `_run_git` y un
``cleanup()`` que borra los temporales — el caller DEBE llamarlo tras la
operación (idealmente en un ``finally``).
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["GitAuthEnv", "build_git_auth_env"]

# Username por defecto del PAT según proveedor (cuando no se da uno explícito).
# GitHub acepta cualquier user con un PAT; el convencional es x-access-token.
# GitLab usa oauth2; Azure DevOps acepta cualquier string no vacío.
_DEFAULT_PAT_USER = {
    "github": "x-access-token",
    "gitlab": "oauth2",
    "azure_devops": "pat",
}


@dataclass
class GitAuthEnv:
    """Variables de entorno para autenticar git + limpieza de temporales."""

    env: dict[str, str]
    cleanup: Callable[[], None]


def _noop() -> None:
    return None


def build_git_auth_env(
    auth_mode: str | None,
    *,
    provider: str | None = None,
    username: str | None = None,
    token: str | None = None,
    ssh_key: str | None = None,
) -> GitAuthEnv:
    """Construye el env de auth git para el modo dado. Ver módulo para detalle."""
    if auth_mode == "pat" and token:
        fd, script = tempfile.mkstemp(prefix="git-askpass-", suffix=".sh")
        with os.fdopen(fd, "w", newline="\n") as f:
            # git llama al askpass con el texto del prompt en $1: "Username for ..."
            # o "Password for ...". Devolvemos el dato correcto según el prompt.
            f.write(
                '#!/bin/sh\ncase "$1" in\n'
                '  Username*) printf "%s" "$GIT_USERNAME" ;;\n'
                '  *) printf "%s" "$GIT_PASSWORD" ;;\n'
                "esac\n"
            )
        os.chmod(script, 0o700)
        user = username or _DEFAULT_PAT_USER.get(provider or "", "git")
        env = {
            "GIT_ASKPASS": script,
            "GIT_USERNAME": user,
            "GIT_PASSWORD": token,
            "GIT_TERMINAL_PROMPT": "0",
        }

        def cleanup() -> None:
            with contextlib.suppress(OSError):
                os.unlink(script)

        return GitAuthEnv(env=env, cleanup=cleanup)

    if auth_mode == "ssh" and ssh_key:
        fd, keyfile = tempfile.mkstemp(prefix="git-ssh-", suffix=".key")
        with os.fdopen(fd, "w", newline="\n") as f:
            f.write(ssh_key if ssh_key.endswith("\n") else ssh_key + "\n")
        os.chmod(keyfile, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        env = {
            "GIT_SSH_COMMAND": (
                f"ssh -i {keyfile} -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=accept-new -o PasswordAuthentication=no"
            ),
            "GIT_TERMINAL_PROMPT": "0",
        }

        def cleanup() -> None:
            with contextlib.suppress(OSError):
                os.unlink(keyfile)

        return GitAuthEnv(env=env, cleanup=cleanup)

    return GitAuthEnv(env={}, cleanup=_noop)
