"""Hay tokens por servicio, y son los de las políticas que el instalador escribe.

Plan prod-10 `task_prod10_08` (hallazgo secrets-4).

`installer_backend.vault_bootstrap` escribe cuatro políticas ACL de mínimo
privilegio —api-server, workers, orchestrator, notification-dispatcher—, cada una
con permiso de lectura sobre exactamente las rutas KV que ese servicio consume.
Y **nadie mintea un token contra ellas**: buscado en el repo, no existe una sola
llamada a `create_token`, y `scripts/init-vault.sh` entregaba el root token y
punto. El resultado práctico es el que encontró la auditoría: todos los servicios
configurados con el root token — lo contrario del mínimo privilegio, y además
irrenovable.

Lo que se prueba aquí:

* el script mintea un token por política, **periódico** (renovable para siempre
  mientras el `VaultTokenManager` lo renueve) y **huérfano** (revocar el root
  token expuesto NO puede llevarse por delante la plataforma);
* no escribe nada al disco salvo que se le pida — misma disciplina que
  `init-vault.sh`;
* y, el que de verdad envejece: **los nombres de política del script son los
  mismos que los de `initial_policies()`**. Un script bash no puede importar el
  Python, así que la deriva se caza aquí en vez de a las 3 de la mañana con un
  `permission denied` de Vault.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "vault-mint-service-tokens.sh"

_BASH = shutil.which("bash")
pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(_BASH is None, reason="requiere bash (Git Bash en Windows, nativo en CI)"),
]


def _make_docker_shim(bindir: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "docker"
    shim.write_text(
        """#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_LOG"
# `vault token create ... -field=token` devuelve el token pelado por stdout.
for a in "$@"; do
  if [ "$a" = "create" ]; then
    echo "hvs.FAKE$RANDOM$RANDOM$RANDOM0000000000"
    exit 0
  fi
done
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    bindir = tmp_path / "bin"
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    _make_docker_shim(bindir)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["SHIM_LOG"] = str(log)
    env["VAULT_TOKEN"] = "hvs" + ".FAKEROOTTOKEN000000000000"

    assert _BASH is not None
    result = subprocess.run(
        [_BASH, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        check=False,
    )
    return result, log


#: Las cuatro variables que cada servicio lee de su propio Settings.
_EXPECTED_VARS = (
    "API_SERVER_VAULT_TOKEN",
    "WORKERS_VAULT_TOKEN",
    "ORCHESTRATOR_VAULT_TOKEN",
    "NOTIFY_VAULT_TOKEN",
)


def test_mints_one_periodic_orphan_token_per_policy(tmp_path: Path) -> None:
    result, log = _run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [
        line for line in log.read_text(encoding="utf-8").splitlines() if "token create" in line
    ]
    assert len(calls) == 4, f"minteó {len(calls)} tokens, esperaba 4: {calls}"

    for call in calls:
        assert "-period=" in call, f"token no periódico (caduca en una fecha): {call}"
        assert "-orphan" in call, (
            "token no huérfano: revocar el root token expuesto se llevaría por "
            f"delante la plataforma entera — {call}"
        )


def test_emits_one_env_line_per_service(tmp_path: Path) -> None:
    result, _ = _run(tmp_path)

    for var in _EXPECTED_VARS:
        assert f"{var}=hvs" in result.stdout.replace("\r", ""), (
            f"falta la línea de {var} en la salida:\n{result.stdout}"
        )


def test_writes_nothing_to_disk_by_default(tmp_path: Path) -> None:
    """Misma disciplina que init-vault.sh: el secreto sale por stdout y es el
    operador quien decide dónde va."""
    _run(tmp_path)

    # `bin/` y `shim.log` los crea el propio andamiaje del test; cualquier otra
    # cosa la habría escrito el script.
    fixture = {"bin", "shim.log"}
    written = {p.name for p in tmp_path.iterdir()} - fixture
    assert written == set(), f"escribió ficheros no pedidos: {written}"


def test_refuses_without_a_creating_token(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    _make_docker_shim(bindir)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["SHIM_LOG"] = str(log)
    env.pop("VAULT_TOKEN", None)

    assert _BASH is not None
    result = subprocess.run(
        [_BASH, str(_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert "VAULT_TOKEN" in result.stdout + result.stderr
    assert "token create" not in log.read_text(encoding="utf-8")


def test_dry_run_reveals_nothing(tmp_path: Path) -> None:
    result, log = _run(tmp_path, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "token create" not in log.read_text(encoding="utf-8")
    assert "hvs." not in result.stdout


def test_the_policy_names_match_the_installer(tmp_path: Path) -> None:
    """La guarda contra la deriva. Si alguien añade una quinta política en
    `vault_bootstrap.py` (o renombra una), este script se quedaría minteando
    contra un nombre que Vault no conoce — y el fallo aparecería en el arranque
    del servicio, no aquí."""
    from installer_backend.vault_bootstrap import initial_policies

    expected = {policy.name for policy in initial_policies()}
    assert len(expected) >= 4, f"la guarda dejó de leer las políticas (vio {expected})"

    source = _SCRIPT.read_text(encoding="utf-8")
    missing = sorted(name for name in expected if f"{name}:" not in source)
    assert not missing, (
        "scripts/vault-mint-service-tokens.sh no mintea token para estas "
        f"políticas del instalador: {missing}"
    )
