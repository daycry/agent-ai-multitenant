"""Higiene del árbol: ningún secreto en claro ni en el contexto de build.

Plan prod-10 `task_prod10_03` (hallazgos secrets-1, deploy-11, quality-3).

El punto de partida real: `vault-init-output/` llevaba desde 2026-05-20 en el
working tree con las 5 unseal keys y el root token de Vault **en claro**, y
`.github/workflows/ci.yml` construye los agent-runtimes con la raíz del repo
como contexto de build — así que sin `.dockerignore` esos ficheros habrían
quedado legibles en una capa de imagen.

Dos guardas, y las dos tienen que poder FALLAR:

1. `.dockerignore` excluye los artefactos de secretos del contexto de build.
   Guarda estática ⇒ lleva su propia aserción de que **encontró algo**
   (`docs/03-guides/verificar-antes-de-implementar.md` §4).
2. `scripts/check_no_secret_artifacts.py` es el gate que CI y el hook
   pre-commit ejecutan. Aquí se prueba su LÓGICA contra árboles de mentira
   (`tmp_path`), no contra el repo: el repo de un operador puede tener
   legítimamente `vault-init-output/` a medio custodiar mientras hace la
   Fase A, y un test que dependa de eso sería un rojo crónico — y «una suite
   que siempre falla tampoco es una suite» (§4).

Lo que NO se testea aquí: la ausencia de `vault-init-output/` en ESTE árbol.
Eso es `task_prod10_01`, una operación humana (custodia de las 5 keys +
revocación del root token). El gate del punto 2 es el que lo hace cumplir en
CI, donde el checkout viene de git y el directorio está en `.gitignore`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"
_CHECKER = _REPO_ROOT / "scripts" / "check_no_secret_artifacts.py"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Los patrones que el contexto de build NO puede llevar nunca. `vault-init-output/`
# es el del hallazgo; `.env*` son las credenciales del compose; `*.log` puede
# arrastrar un token en una traza.
_REQUIRED_DOCKERIGNORE_PATTERNS = ("vault-init-output/", ".env", ".env.*", "*.log")

# Un token de Vault de MENTIRA, montado por concatenación. Escrito de un tirón,
# este propio fichero casaría con el criterio del gate y CI se caería por un
# secreto que no existe — exactamente el falso positivo que hace que la gente
# desactive el gate a la semana.
_FAKE_TOKEN = "hvs" + "." + "CAESIJ" + "abcdefghijklmnopqrstuvwxyz0123"


# ---------------------------------------------------------------------------
# (1) .dockerignore
# ---------------------------------------------------------------------------
def _dockerignore_patterns() -> list[str]:
    lines = _DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def test_dockerignore_excludes_secret_artifacts() -> None:
    patterns = _dockerignore_patterns()

    # Guarda contra el paso en vacío: si el fichero se vacía o el parser deja de
    # leerlo, esto falla en vez de aprobar por silencio.
    assert len(patterns) >= 10, (
        f".dockerignore dejó de parsearse (vio {len(patterns)} patrones); "
        "sin él, el contexto de build de los agent-runtimes es la raíz del repo"
    )

    missing = [p for p in _REQUIRED_DOCKERIGNORE_PATTERNS if p not in patterns]
    assert not missing, (
        "estos patrones faltan en .dockerignore, así que entrarían en el contexto "
        f"de build de los agent-runtimes (ci.yml build con contexto `.`): {missing}"
    )


# ---------------------------------------------------------------------------
# (2) El gate: scripts/check_no_secret_artifacts.py
# ---------------------------------------------------------------------------
def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_checker_exists_and_is_executable_as_a_script() -> None:
    assert _CHECKER.is_file(), f"falta el gate {_CHECKER}"


def test_checker_passes_on_a_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_fails_when_vault_init_output_has_content(tmp_path: Path) -> None:
    """El caso del hallazgo, y el del test humano `human_prod10_01`:
    crear `vault-init-output/dummy.txt` tiene que tumbar el gate."""
    outdir = tmp_path / "vault-init-output"
    outdir.mkdir()
    (outdir / "dummy.txt").write_text("whatever\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "vault-init-output" in combined
    # El mensaje tiene que decirle al operador qué hacer, no solo que falló.
    assert "custod" in combined.lower() or "shred" in combined.lower(), combined


def test_checker_does_not_print_the_secret_it_found(tmp_path: Path) -> None:
    """Un gate que imprime el secreto para justificar el fallo lo COPIA al log
    de CI, que es público en la mayoría de configuraciones."""
    outdir = tmp_path / "vault-init-output"
    outdir.mkdir()
    secret = "hvs" + ".SUPERSECRETVALUE0123456789"
    (outdir / "root-token.txt").write_text(secret + "\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "SUPERSECRETVALUE" not in combined, "el gate volcó el contenido del secreto"
    assert "root-token.txt" in combined, "pero sí debe nombrar el fichero"


def test_checker_tolerates_an_empty_artifact_directory(tmp_path: Path) -> None:
    """Un directorio vacío es el estado NORMAL tras el borrado seguro: el gate
    no puede exigir que el operador borre también la carpeta."""
    (tmp_path / "vault-init-output").mkdir()

    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_allows_the_encrypted_blob_the_init_script_writes(tmp_path: Path) -> None:
    """Contrapeso necesario: `scripts/init-vault.sh` escribe su salida CIFRADA en
    ese mismo directorio. Si el gate la prohibiera, el operador volvería al
    `.txt` en claro — que es el problema que este plan cierra."""
    outdir = tmp_path / "vault-init-output"
    outdir.mkdir()
    (outdir / "vault-init.age").write_bytes(b"age-encryption.org/v1\n-> X25519 ...\n")

    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_flags_a_vault_token_in_any_file(tmp_path: Path) -> None:
    """El segundo criterio del plan: un token de servicio de Vault pegado en
    cualquier fichero del árbol (un `.env` de ejemplo, un README, un script)."""
    (tmp_path / "notes.md").write_text(f"el token es {_FAKE_TOKEN}\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "notes.md" in result.stdout + result.stderr


def test_checker_ignores_placeholder_token_shapes(tmp_path: Path) -> None:
    """Contrapeso: la documentación necesita poder escribir la FORMA de un token
    sin que el gate se vuelva inusable (`hvs.…`, `hvs.xxxx`)."""
    (tmp_path / "runbook.md").write_text(
        "el root token tiene la forma hvs.<algo>; en el plan sale como hvs.zAntQ…\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_allows_a_service_token_in_a_runtime_env_file(tmp_path: Path) -> None:
    """`.env` es el sitio DESIGNADO de los tokens por servicio que mintea
    `scripts/vault-mint-service-tokens.sh`, y está en `.gitignore` y en
    `.dockerignore`. Marcarlo sería un falso positivo garantizado en cualquier
    despliegue bien configurado — y un gate que grita en el caso correcto se
    desactiva en una semana."""
    (tmp_path / ".env").write_text(f"API_SERVER_VAULT_TOKEN={_FAKE_TOKEN}\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_still_flags_a_token_in_a_committed_env_example(tmp_path: Path) -> None:
    """El contrapeso: `.env.example` SÍ se commitea, así que un token real ahí
    es exactamente lo que este gate existe para cazar."""
    (tmp_path / ".env.example").write_text(
        f"API_SERVER_VAULT_TOKEN={_FAKE_TOKEN}\n", encoding="utf-8"
    )

    result = _run_checker(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ".env.example" in result.stdout + result.stderr


def test_checker_skips_binary_and_vendored_trees(tmp_path: Path) -> None:
    """Rendimiento y falsos positivos: el gate corre en cada commit y no puede
    tardar minutos recorriendo `.venv/` ni `node_modules/`."""
    vendored = tmp_path / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text(f"const t = '{_FAKE_TOKEN}';\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_reports_the_repo_it_actually_scanned(tmp_path: Path) -> None:
    """Guarda contra el paso en vacío del propio gate: si un día deja de
    recorrer ficheros, tiene que decirlo en vez de salir 0 en silencio."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = _run_checker(tmp_path)
    assert result.returncode == 0
    assert "1" in result.stdout, f"el gate no reporta cuántos ficheros vio: {result.stdout!r}"


# ---------------------------------------------------------------------------
# (3) El gate está CABLEADO en CI — «mecanismo entregado, cero llamantes» (§5)
# ---------------------------------------------------------------------------
def test_ci_runs_the_secret_artifact_gate() -> None:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}
    assert jobs, "ci.yml dejó de tener jobs"

    runs = [
        str(step.get("run", ""))
        for job in jobs.values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]
    assert any("check_no_secret_artifacts.py" in run for run in runs), (
        "ningún paso de CI ejecuta scripts/check_no_secret_artifacts.py: el gate "
        "existe pero no lo llama nadie"
    )
