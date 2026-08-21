"""Propagar un secreto rotado deja de ser un procedimiento a mano.

Plan prod-05 `task_prod05_06` (hallazgo gap2-2) · [ADR 0144].

## Qué faltaba

El [ADR 0144] eligió la opción B —regenerar el `.env` y reiniciar los servicios
en la misma ventana, en vez de leer de Vault en runtime— y su propia sección «Lo
que este ADR NO entrega» dejaba escrito que `scripts/rotate-platform-secret.sh`
**no existía**: la propagación era el procedimiento manual del runbook, paso por
paso y copiable.

Eso no es un detalle de comodidad. El ciclo de rotación escribe el valor nuevo en
KV con `pending_apply=true` y **la plataforma sigue usando el viejo** hasta que
alguien propaga. Un procedimiento manual de ocho pasos, ejecutado bajo presión
tras una fuga de credencial, es donde se invierten los pasos 2 y 3 del patrón
add-then-remove — y ese error concreto **deja a toda la plataforma sin object
storage** (riesgo 4 del plan).

## Lo que se prueba, y por qué así

Con un *shim* de `docker` en el PATH, igual que
`test_vault_service_tokens.py`: lo que hay que verificar es el **orden de las
operaciones** y lo que acaba escrito en el `.env`, no que Vault funcione.

Los dos invariantes que costarían caro si se rompieran:

1. la clave nueva se **antepone** conservando la anterior — retirarla en el mismo
   paso corta todas las sesiones en vuelo, que es justo lo que el anillo dual de
   `task_prod05_04` existe para evitar;
2. la revocación de la credencial MinIO anterior ocurre **después** del reinicio.
   Nunca antes.

[ADR 0144]: ../../docs/05-architecture-decisions/0144-propagacion-de-secretos-rotados.md
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "rotate-platform-secret.sh"

_BASH = shutil.which("bash")
pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(_BASH is None, reason="requiere bash (Git Bash en Windows, nativo en CI)"),
]

#: Los valores que el Vault de mentira devuelve. Deliberadamente reconocibles:
#: varios tests afirman que NO aparecen por la salida estándar.
_NEW_JWT = "NEWJWTVALUE-0123456789abcdefghijklmnop"
_NEW_MINIO_ACCESS = "NEWMINIOACCESSKEY0123"
_NEW_MINIO_SECRET = "NEWMINIOSECRETVALUE-0123456789abcdefgh"


def _make_docker_shim(bindir: Path) -> None:
    """Un `docker` de mentira que registra sus argumentos y sabe leer del KV."""
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "docker"
    shim.write_text(
        f"""#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_LOG"
field=""
is_kv_get=0
prev_get=0
for a in "$@"; do
  case "$a" in
    -field=*) field="${{a#-field=}}" ;;
    kv) prev_get=1 ;;
    get) [ "$prev_get" = "1" ] && is_kv_get=1 ;;
  esac
done
if [ "$is_kv_get" = "1" ]; then
  case "$field" in
    value) echo "{_NEW_JWT}" ;;
    access_key) echo "{_NEW_MINIO_ACCESS}" ;;
    secret_key) echo "{_NEW_MINIO_SECRET}" ;;
    pending_apply) echo "true" ;;
    *) echo "" ;;
  esac
fi
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


def _run(
    tmp_path: Path, *args: str, env_body: str = ""
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bindir = tmp_path / "bin"
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    _make_docker_shim(bindir)

    env_file = tmp_path / ".env"
    env_file.write_text(env_body, encoding="utf-8", newline="\n")

    environ = dict(os.environ)
    environ["PATH"] = f"{bindir}{os.pathsep}{environ.get('PATH', '')}"
    environ["SHIM_LOG"] = str(log)

    assert _BASH is not None
    result = subprocess.run(
        [_BASH, str(_SCRIPT), *args, "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=environ,
        check=False,
    )
    return result, env_file, log


def _env_value(env_file: Path, key: str) -> str | None:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


# ---------------------------------------------------------------------------
# 1. JWT: anteponer, NUNCA sustituir
# ---------------------------------------------------------------------------
def test_the_new_jwt_key_is_prepended_keeping_the_previous_one(tmp_path: Path) -> None:
    """Sustituir en vez de anteponer invalida de golpe todas las sesiones y todos
    los agent tokens en vuelo. El anillo dual (`task_prod05_04`) existe para que
    la retirada sea un paso APARTE, más tarde."""
    result, env_file, _ = _run(
        tmp_path, "jwt", "--yes", env_body="API_SERVER_JWT_SECRETS=old-session-key-0123456789\n"
    )

    assert result.returncode == 0, result.stderr
    assert _env_value(env_file, "API_SERVER_JWT_SECRETS") == (
        f"{_NEW_JWT},old-session-key-0123456789"
    )


def test_the_singular_variable_seeds_the_ring_when_there_is_no_plural(tmp_path: Path) -> None:
    """Un despliegue que nunca rotó sólo tiene `API_SERVER_JWT_SECRET`. Ignorarlo
    dejaría la clave vieja fuera del anillo — o sea, cortaría las sesiones justo
    igual que sustituir."""
    result, env_file, _ = _run(
        tmp_path, "jwt", "--yes", env_body="API_SERVER_JWT_SECRET=only-key-0123456789abcd\n"
    )

    assert result.returncode == 0, result.stderr
    assert _env_value(env_file, "API_SERVER_JWT_SECRETS") == (f"{_NEW_JWT},only-key-0123456789abcd")


def test_running_it_twice_does_not_duplicate_the_key(tmp_path: Path) -> None:
    """Idempotencia: reintentar tras un fallo a mitad es lo normal en una
    ventana de rotación, y un anillo con la misma clave dos veces es basura que
    el operador tendrá que limpiar a mano justo cuando menos tiempo tiene."""
    _run(tmp_path, "jwt", "--yes", env_body="API_SERVER_JWT_SECRETS=old-key-0123456789abc\n")
    env_file = tmp_path / ".env"
    first = env_file.read_text(encoding="utf-8")

    # Segunda pasada sobre el `.env` que dejó la primera.
    _run(tmp_path, "jwt", "--yes", env_body=first)

    assert _env_value(env_file, "API_SERVER_JWT_SECRETS") == (f"{_NEW_JWT},old-key-0123456789abc")


# ---------------------------------------------------------------------------
# 2. El ORDEN: escribir, reiniciar, y sólo entonces revocar
# ---------------------------------------------------------------------------
def test_the_services_are_restarted_after_the_env_is_rewritten(tmp_path: Path) -> None:
    result, _, log = _run(
        tmp_path, "jwt", "--yes", env_body="API_SERVER_JWT_SECRETS=old-key-0123456789abc\n"
    )

    assert result.returncode == 0, result.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    assert any("up -d" in line or " up " in line for line in lines), (
        f"no reinició ningún servicio: {lines}"
    )


def test_minio_revocation_happens_only_after_the_restart(tmp_path: Path) -> None:
    """El invariante caro. Invertir estos dos pasos borra la credencial que los
    servicios siguen usando y deja a la plataforma entera sin object storage."""
    result, _, log = _run(tmp_path, "minio", "--yes", env_body="API_SERVER_MINIO_ACCESS_KEY=old\n")

    assert result.returncode == 0, result.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    restart_at = next((i for i, line in enumerate(lines) if " up " in line), None)
    revoke_at = next((i for i, line in enumerate(lines) if "revoke" in line), None)
    assert restart_at is not None, f"no hubo reinicio: {lines}"
    assert revoke_at is not None, f"no se revocó la credencial anterior: {lines}"
    assert restart_at < revoke_at, (
        "revocó la credencial ANTES de propagar la nueva: eso deja a toda la "
        f"plataforma sin object storage\n{lines}"
    )


def test_minio_writes_both_halves_of_the_credential(tmp_path: Path) -> None:
    """Escribir sólo el secret key y no el access key deja el `.env` describiendo
    una credencial que no existe."""
    _, env_file, _ = _run(tmp_path, "minio", "--yes", env_body="API_SERVER_MINIO_ACCESS_KEY=old\n")

    assert _env_value(env_file, "API_SERVER_MINIO_ACCESS_KEY") == _NEW_MINIO_ACCESS
    assert _env_value(env_file, "API_SERVER_MINIO_SECRET_KEY") == _NEW_MINIO_SECRET


# ---------------------------------------------------------------------------
# 3. Contrapesos: no romper ni filtrar
# ---------------------------------------------------------------------------
def test_dry_run_changes_nothing_and_restarts_nothing(tmp_path: Path) -> None:
    body = "API_SERVER_JWT_SECRETS=old-key-0123456789abc\n"
    result, env_file, log = _run(tmp_path, "jwt", "--dry-run", env_body=body)

    assert result.returncode == 0, result.stderr
    assert env_file.read_text(encoding="utf-8") == body, "el dry-run tocó el .env"
    assert " up " not in log.read_text(encoding="utf-8"), "el dry-run reinició servicios"


def test_no_secret_value_reaches_the_terminal(tmp_path: Path) -> None:
    """El valor va del KV al `.env` sin pasar por la pantalla: la salida de una
    rotación acaba pegada en un ticket o en un chat con más frecuencia de la que
    a nadie le gustaría."""
    result, _, _ = _run(
        tmp_path, "jwt", "--yes", env_body="API_SERVER_JWT_SECRETS=old-key-0123456789abc\n"
    )

    combined = result.stdout + result.stderr
    assert _NEW_JWT not in combined, "el script imprimió el secreto rotado"


def test_an_unknown_secret_name_is_refused(tmp_path: Path) -> None:
    result, _, log = _run(tmp_path, "postgres", "--yes")

    assert result.returncode != 0
    assert " up " not in log.read_text(encoding="utf-8"), (
        "reinició servicios pese a no saber qué rotar"
    )


def test_a_missing_env_file_is_refused_before_anything_happens() -> None:
    assert _BASH is not None
    result = subprocess.run(
        [_BASH, str(_SCRIPT), "jwt", "--yes", "--env-file", "/nonexistent/path/.env"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        check=False,
    )

    assert result.returncode != 0
    assert "env" in (result.stderr + result.stdout).lower()


def test_the_runbook_points_at_the_script() -> None:
    """«Mecanismo entregado, cero llamantes» (§5): un script que el runbook no
    nombra es un script que nadie ejecutará en la ventana en la que hace falta.
    """
    runbook = (_REPO_ROOT / "docs" / "06-runbooks" / "05-key-rotation.md").read_text(
        encoding="utf-8"
    )
    assert "rotate-platform-secret.sh" in runbook, (
        "el runbook de rotación no menciona el script de propagación"
    )
