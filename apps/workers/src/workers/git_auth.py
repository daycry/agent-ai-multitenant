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

__all__ = ["GitAuthEnv", "build_git_auth_env", "host_de_remote"]

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


def host_de_remote(remote_url: str | None) -> str | None:
    """El `host[:puerto]` de un remoto git, o `None` si no se puede leer.

    Es la fuente del `allowed_host` de :func:`build_git_auth_env`: el host al
    que la credencial de ESTE proyecto puede viajar sale de su propio
    `remote_url`, así que no hay campo nuevo que migrar ni default que adivinar.

    Cubre las dos formas que acepta la API (ver la allowlist de
    `schemas/projects.py`): la de URL —`https://host[:puerto]/ruta`,
    `ssh://[user@]host[:puerto]/ruta`— y la scp-like `git@host:ruta`, que no es
    una URL y a la que `urlsplit` le saca un host vacío.

    El puerto se conserva a propósito: git lo incluye en el prompt del askpass,
    y dos servicios distintos en puertos distintos del mismo nombre no son el
    mismo destino.
    """
    if not remote_url:
        return None
    candidato = remote_url.strip()
    if "://" in candidato:
        resto = candidato.split("://", 1)[1]
        autoridad = resto.split("/", 1)[0]
        return autoridad.rsplit("@", 1)[-1] or None
    # scp-like: `usuario@host:ruta`. El `:` de después del host separa la ruta,
    # no un puerto, así que aquí no hay puerto que conservar.
    if "@" in candidato and ":" in candidato:
        return candidato.split("@", 1)[1].split(":", 1)[0] or None
    return None


def _noop() -> None:
    return None


def build_git_auth_env(
    auth_mode: str | None,
    *,
    provider: str | None = None,
    username: str | None = None,
    token: str | None = None,
    ssh_key: str | None = None,
    allowed_host: str | None = None,
) -> GitAuthEnv:
    """Construye el env de auth git para el modo dado. Ver módulo para detalle.

    ``allowed_host`` es el host —con puerto si lo lleva— al que este secreto
    puede viajar; sale del ``remote_url`` del propio proyecto vía
    :func:`host_de_remote`, así que no hace falta ni campo nuevo ni migración.

    Con ``None`` NO se comprueba el host, que es el comportamiento de antes. Los
    tres llamantes reales lo pasan siempre; el default existe para los tests que
    construyen el env sin remoto.
    """
    if auth_mode == "pat" and token:
        fd, script = tempfile.mkstemp(prefix="git-askpass-", suffix=".sh")
        with os.fdopen(fd, "w", newline="\n") as f:
            # git llama al askpass con el texto del prompt en $1: "Username for
            # 'https://github.com'" o "Password for 'https://user@github.com'".
            # Devolvemos el dato correcto según el prompt.
            #
            # La PRIMERA rama (2026-08-27) es la que ata el secreto a su host.
            # Antes este script entregaba `GIT_PASSWORD` a lo que git preguntase,
            # y quien pudiera repuntar `remote_url` —la API permite cambiarlo
            # CONSERVANDO el PAT ya guardado— se llevaba la credencial a su
            # servidor. Con una cuenta de servicio compartida a nivel de tenant,
            # eso es exfiltración con un formulario.
            #
            # El `'` final de cada patrón NO es adorno: ancla el fin del host.
            # Sin él, `github.com` casaría con `github.com.atacante.net`, que es
            # la forma clásica de saltarse una comprobación por subcadena.
            guarda_de_host = (
                'case "$1" in\n'
                '  *"://$GIT_ALLOWED_HOST\'"*|*"@$GIT_ALLOWED_HOST\'"*) ;;\n'
                "  *) exit 1 ;;\n"
                "esac\n"
            )
            f.write(
                "#!/bin/sh\n"
                # Sin `allowed_host` no se escribe la guarda: con la variable
                # vacía los patrones no casarían con NINGÚN prompt real y el
                # askpass fallaría siempre, que no es «no comprobar» sino
                # «romper». Los tres llamantes reales sí lo pasan.
                + (guarda_de_host if allowed_host else "")
                + 'case "$1" in\n'
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
            "GIT_ALLOWED_HOST": allowed_host or "",
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
