"""El PAT del proyecto sólo viaja al host de su propio remoto.

**El defecto (2026-08-27).** `build_git_auth_env` montaba un `GIT_ASKPASS` que
devolvía `GIT_PASSWORD` a lo que git preguntase, sin mirar a qué host. Y la API
permite repuntar `remote_url` **conservando el PAT ya guardado** — está
documentado como feature en `routers/projects.py`: «un update que solo cambia
metadatos puede omitir la credencial si ya hay una guardada».

Encadenando: un `tenant_admin` apunta el remoto de su proyecto a un servidor
suyo, el worker hace `git fetch`, git pide credenciales y el askpass se las da.
Con una cuenta de servicio compartida a nivel de tenant o de plataforma —el
patrón normal en un despliegue departamental— eso es exfiltración de credencial
rellenando un formulario, sin explotar nada.

**Por qué se prueba EJECUTANDO el script.** La guarda vive en un `case` de
shell, no en Python. Un test que leyera el fichero y buscase la cadena
`GIT_ALLOWED_HOST` comprobaría que el texto está, no que funcione — y el modo de
fallo real de una comprobación por patrón es casar de más. Aquí se invoca el
askpass con los prompts que git usa de verdad y se mira qué imprime.

Los tests se saltan en Windows: el askpass es un `#!/bin/sh` y el runner de CI
es Linux, que es donde corre el worker.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from workers.git_auth import build_git_auth_env, host_de_remote

pytestmark = pytest.mark.unit

_SOLO_POSIX = pytest.mark.skipif(
    sys.platform == "win32", reason="el askpass es un script sh; el worker corre en Linux"
)


def _preguntar(env_auth: object, prompt: str) -> tuple[int, str]:
    """Invoca el askpass como lo hace git: el prompt en `$1`."""
    env = dict(env_auth.env)  # type: ignore[attr-defined]
    salida = subprocess.run(
        [env["GIT_ASKPASS"], prompt],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return salida.returncode, salida.stdout


@pytest.mark.parametrize(
    ("remote", "esperado"),
    [
        ("https://github.com/daycry/repo.git", "github.com"),
        ("https://gitlab.example.com:8443/g/r.git", "gitlab.example.com:8443"),
        ("ssh://git@github.com/daycry/repo.git", "github.com"),
        ("git@github.com:daycry/repo.git", "github.com"),
        ("", None),
        (None, None),
    ],
)
def test_el_host_permitido_sale_del_propio_remoto(remote: str | None, esperado: str | None) -> None:
    """Sin campo nuevo ni migración: el host lo dice el `remote_url` que ya hay.

    El puerto se conserva a propósito — git lo mete en el prompt, y dos
    servicios en puertos distintos del mismo nombre no son el mismo destino.
    """
    assert host_de_remote(remote) == esperado


@_SOLO_POSIX
@pytest.mark.parametrize(
    "prompt",
    [
        "Password for 'https://git@github.com': ",
        "Username for 'https://github.com': ",
    ],
)
def test_la_credencial_se_entrega_a_su_propio_host(prompt: str) -> None:
    """Si la guarda rompe el caso legítimo, es peor que el agujero."""
    auth = build_git_auth_env(
        "pat", provider="github", username="git", token="s3cr3t", allowed_host="github.com"
    )
    try:
        rc, salida = _preguntar(auth, prompt)
        assert rc == 0, f"el askpass falló en el host legítimo: {prompt!r}"
        assert salida in {"s3cr3t", "git"}, f"no devolvió la credencial: {salida!r}"
    finally:
        auth.cleanup()


@_SOLO_POSIX
@pytest.mark.parametrize(
    "prompt",
    [
        "Password for 'https://atacante.example': ",
        "Password for 'https://git@atacante.example': ",
        # La forma clásica de saltarse una comprobación por subcadena: el host
        # permitido aparece dentro del malicioso. Es lo que ancla el `'` final.
        "Password for 'https://github.com.atacante.net': ",
        "Password for 'https://git@github.com.atacante.net': ",
    ],
)
def test_la_credencial_NO_viaja_a_otro_host(prompt: str) -> None:
    """El caso que convierte un formulario en exfiltración de credencial."""
    auth = build_git_auth_env(
        "pat", provider="github", username="git", token="s3cr3t", allowed_host="github.com"
    )
    try:
        rc, salida = _preguntar(auth, prompt)
        assert rc != 0, f"el askpass ACEPTÓ un host ajeno: {prompt!r}"
        assert "s3cr3t" not in salida, f"la credencial se ha filtrado: {salida!r}"
    finally:
        auth.cleanup()


@_SOLO_POSIX
def test_un_puerto_distinto_es_otro_destino() -> None:
    """`gitlab:8443` y `gitlab:9000` no son el mismo servicio."""
    auth = build_git_auth_env(
        "pat", username="git", token="s3cr3t", allowed_host="gitlab.example.com:8443"
    )
    try:
        rc_ok, _ = _preguntar(auth, "Password for 'https://gitlab.example.com:8443': ")
        rc_ko, salida = _preguntar(auth, "Password for 'https://gitlab.example.com:9000': ")
        assert rc_ok == 0, "el puerto correcto debería pasar"
        assert rc_ko != 0 and "s3cr3t" not in salida, "otro puerto no debería recibir el secreto"
    finally:
        auth.cleanup()


@_SOLO_POSIX
def test_sin_host_declarado_NO_se_rompe_el_askpass() -> None:
    """`allowed_host=None` significa «no comprobar», no «fallar siempre».

    Es la trampa que casi cuelo: con la variable a cadena vacía, los patrones no
    casarían con NINGÚN prompt real y el askpass fallaría en todos los casos. Eso
    no es no-comprobar, es romper. Por eso la guarda ni siquiera se escribe
    cuando no hay host.
    """
    auth = build_git_auth_env("pat", username="git", token="s3cr3t")
    try:
        rc, salida = _preguntar(auth, "Password for 'https://cualquiera.example': ")
        assert rc == 0 and salida == "s3cr3t"
    finally:
        auth.cleanup()
