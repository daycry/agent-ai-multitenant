"""`scripts/init-vault.sh` no vuelve a dejar secretos en claro en el disco.

Plan prod-10 `task_prod10_02` (hallazgos secrets-1, deploy-11).

El script escribía `unseal-keys.txt` + `root-token.txt` + `init-response.json`
en claro con modo 600 y dejaba al operador la responsabilidad de custodiarlos y
hacer `shred`. Medido: llevaban 3 semanas ahí. Un procedimiento cuyo paso de
seguridad depende de que un humano se acuerde no es un procedimiento, es una
esperanza — así que ahora el DEFAULT cifra y no hay camino que escriba `.txt`.

Cómo se testea sin Vault ni docker
----------------------------------
Con **shims**: un directorio temporal al frente del `PATH` con un `docker`, un
`age` y un `python3` de mentira. El script cree que habla con un Vault real; los
shims graban lo que se les pidió en ficheros que el test lee después. Eso permite
afirmar lo importante de verdad:

* el árbol resultante no tiene un solo `.txt` ni el JSON en claro;
* el fichero cifrado NO contiene el root token en texto;
* el **unseal sigue funcionando** — se pasan exactamente `threshold` keys, leídas
  de memoria y no de un fichero;
* sin recipiente y sin `--print-once` el script falla **ANTES de inicializar**.
  Este último es el que de verdad importa: fallar DESPUÉS de `operator init`
  destruiría las keys sin recuperación posible.

Bash: el repo se desarrolla en Windows con Git Bash, y CI corre en ubuntu. Si no
hay `bash`, el módulo se salta (no se finge verde).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "init-vault.sh"

_BASH = shutil.which("bash")
pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(_BASH is None, reason="requiere bash (Git Bash en Windows, nativo en CI)"),
]

# Las 5 keys y el token que el `docker` de mentira devuelve en `operator init`.
_FAKE_KEYS = [f"unsealkey{i}aaaaaaaaaaaaaaaaaaaaaaaaaaaa=" for i in range(1, 6)]
_FAKE_ROOT_TOKEN = "hvs" + ".FAKEROOTTOKENfor0testing0only0000"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_shims(bindir: Path, *, initialized: bool = False) -> None:
    """Crea `docker` y `age` de mentira en *bindir*.

    El `docker` shim entiende sólo las cuatro invocaciones que hace el script:
    `vault status`, `vault operator init`, `vault operator unseal <key>` y
    `vault secrets list|enable`. Cualquier otra cosa sale con código 99, así que
    si el script cambia de forma el test se enterará en vez de pasar en vacío.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    # `vault operator init` en su formato de TEXTO — el que el script parsea con
    # grep/sed (igual que docker/vault/auto-unseal.sh), sin depender de python3.
    init_text = "\n".join(
        [f"Unseal Key {n}: {key}" for n, key in enumerate(_FAKE_KEYS, start=1)]
        + ["", f"Initial Root Token: {_FAKE_ROOT_TOKEN}", ""]
    )
    status_text = f"Initialized     {'true' if initialized else 'false'}\nSealed     true"

    _write_exec(
        bindir / "docker",
        f"""#!/usr/bin/env bash
# Shim de `docker` para el test de scripts/init-vault.sh.
LOG="$SHIM_LOG"
echo "docker $*" >>"$LOG"
# La invocación real es `docker compose -f F exec -T [-e K=V] vault vault <sub> ...`
# — «vault» aparece DOS veces (servicio y binario). Cada vez que aparece se
# reinicia la acumulación, así que lo que queda es el subcomando del CLI.
seen_vault=0
sub=""
rest=()
for a in "$@"; do
  if [ "$a" = "vault" ]; then
    seen_vault=1
    sub=""
    rest=()
    continue
  fi
  if [ "$seen_vault" = "1" ]; then
    if [ -z "$sub" ]; then sub="$a"; else rest+=("$a"); fi
  fi
done
case "$sub" in
  status)
    cat <<'STATUS_EOF'
{status_text}
STATUS_EOF
    ;;
  operator)
    case "${{rest[0]}}" in
      init)
        echo "INIT_CALLED" >>"$LOG"
        cat <<'INIT_EOF'
{init_text}
INIT_EOF
        ;;
      unseal)
        echo "UNSEAL ${{rest[1]}}" >>"$LOG"
        ;;
      *) exit 99 ;;
    esac
    ;;
  secrets)
    case "${{rest[0]}}" in
      list) echo '{{}}' ;;
      enable) echo "KV_ENABLED" >>"$LOG" ;;
      *) exit 99 ;;
    esac
    ;;
  "") exit 0 ;;
  *) exit 99 ;;
esac
""",
    )

    # `age -r <recipient> -o <out>`: copia stdin a <out> con una marca delante,
    # para que el test distinga "pasó por el cifrador" de "se escribió en claro".
    _write_exec(
        bindir / "age",
        """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -r) shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$out" ] || exit 3
{ printf 'AGE-STUB-CIPHERTEXT\\n'; cat | tr 'a-zA-Z' 'n-za-mN-ZA-M'; } >"$out"
""",
    )


def _run(
    tmp_path: Path,
    *args: str,
    env_extra: dict[str, str] | None = None,
    initialized: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bindir = tmp_path / "bin"
    outdir = tmp_path / "out"
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    _make_shims(bindir, initialized=initialized)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["SHIM_LOG"] = str(log)
    env["VAULT_INIT_OUTPUT_DIR"] = str(outdir)
    # El script hace `docker compose -f "$COMPOSE_FILE"`; el shim lo ignora.
    env["COMPOSE_FILE"] = "docker/docker-compose.yml"
    for key in ("VAULT_INIT_RECIPIENT", "VAULT_INIT_ENCRYPT"):
        env.pop(key, None)
    if env_extra:
        env.update(env_extra)

    assert _BASH is not None
    result = subprocess.run(
        [_BASH, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        check=False,
    )
    return result, outdir, log


def _files(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file())


# ---------------------------------------------------------------------------
# (1) El default: cifrado, y NADA en claro
# ---------------------------------------------------------------------------
def test_default_mode_encrypts_and_writes_no_plaintext(tmp_path: Path) -> None:
    result, outdir, _log = _run(
        tmp_path, env_extra={"VAULT_INIT_RECIPIENT": "age1fakerecipientfortests"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    names = _files(outdir)
    assert names, f"no escribió nada; stdout={result.stdout} stderr={result.stderr}"

    # Ni un .txt, ni el JSON en claro. Este es el hallazgo, literalmente.
    assert not [n for n in names if n.endswith(".txt")], names
    assert "init-response.json" not in names, names
    assert any(n.endswith((".age", ".gpg")) for n in names), names

    # Y el fichero cifrado no lleva el token legible (el shim rota las letras).
    encrypted = next(n for n in names if n.endswith(".age"))
    blob = (outdir / encrypted).read_text(encoding="utf-8")
    assert "AGE-STUB-CIPHERTEXT" in blob, "el JSON no pasó por el cifrador"
    assert _FAKE_ROOT_TOKEN not in blob


def test_unseal_still_happens_from_memory(tmp_path: Path) -> None:
    """La regresión que un refactor mal hecho provocaría: dejar de escribir el
    fichero de keys y con ello dejar de desellar."""
    _, _, log = _run(tmp_path, env_extra={"VAULT_INIT_RECIPIENT": "age1fake"})

    entries = log.read_text(encoding="utf-8").splitlines()
    unsealed = [line.split(" ", 1)[1] for line in entries if line.startswith("UNSEAL ")]
    assert unsealed == _FAKE_KEYS[:3], f"unseal recibió {unsealed!r}"
    assert any(line == "KV_ENABLED" for line in entries), "dejó de montar KV v2 en secret/"


def test_no_secret_reaches_stdout_in_encrypted_mode(tmp_path: Path) -> None:
    """En modo cifrado el operador no ve las keys: para eso está `--print-once`.
    Un script que las imprime «por comodidad» las deja en el scrollback y en el
    log del terminal."""
    result, _, _ = _run(tmp_path, env_extra={"VAULT_INIT_RECIPIENT": "age1fake"})

    combined = result.stdout + result.stderr
    assert _FAKE_ROOT_TOKEN not in combined
    for key in _FAKE_KEYS:
        assert key not in combined


# ---------------------------------------------------------------------------
# (2) Fail-fast ANTES de inicializar — el caso destructivo
# ---------------------------------------------------------------------------
def test_missing_recipient_fails_before_touching_vault(tmp_path: Path) -> None:
    """Sin recipiente y sin `--print-once`, el script tiene que negarse ANTES de
    `operator init`.

    Si fallara después, las 5 unseal keys y el root token se habrían generado y
    perdido en el mismo comando: Vault quedaría inicializado, sellado y
    **irrecuperable**. No hay segundo intento.
    """
    result, outdir, log = _run(tmp_path)

    assert result.returncode != 0
    entries = log.read_text(encoding="utf-8")
    assert "INIT_CALLED" not in entries, "inicializó Vault y LUEGO falló: keys perdidas"
    assert _files(outdir) == []

    combined = result.stdout + result.stderr
    assert "VAULT_INIT_RECIPIENT" in combined
    assert "--print-once" in combined


def test_missing_encryptor_binary_fails_before_touching_vault(tmp_path: Path) -> None:
    """Mismo razonamiento con el recipiente puesto pero `age`/`gpg` ausentes."""
    bindir = tmp_path / "bin"
    outdir = tmp_path / "out"
    log = tmp_path / "shim.log"
    log.write_text("", encoding="utf-8")
    _make_shims(bindir)
    (bindir / "age").unlink()

    env = dict(os.environ)
    # PATH mínimo: sólo el shim, para que no aparezca un `age`/`gpg` del sistema.
    env["PATH"] = str(bindir)
    env["SHIM_LOG"] = str(log)
    env["VAULT_INIT_OUTPUT_DIR"] = str(outdir)
    env["VAULT_INIT_RECIPIENT"] = "age1fake"
    env["VAULT_INIT_ENCRYPT"] = "age"

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
    assert "INIT_CALLED" not in log.read_text(encoding="utf-8")
    assert _files(outdir) == []


# ---------------------------------------------------------------------------
# (3) `--print-once`: por stdout y punto
# ---------------------------------------------------------------------------
def test_print_once_writes_nothing_to_disk(tmp_path: Path) -> None:
    result, outdir, log = _run(tmp_path, "--print-once")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _files(outdir) == [], f"--print-once dejó ficheros: {_files(outdir)}"

    combined = result.stdout + result.stderr
    assert _FAKE_ROOT_TOKEN in combined, "el modo print-once debe revelar el root token"
    for key in _FAKE_KEYS:
        assert key in combined, "el modo print-once debe revelar las 5 unseal keys"

    # Y desella igual.
    entries = log.read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in entries if line.startswith("UNSEAL ")] == (
        _FAKE_KEYS[:3]
    )


# ---------------------------------------------------------------------------
# (4) Idempotencia: un Vault ya inicializado sigue siendo un no-op
# ---------------------------------------------------------------------------
def test_already_initialized_is_a_noop(tmp_path: Path) -> None:
    result, outdir, log = _run(tmp_path, initialized=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "INIT_CALLED" not in log.read_text(encoding="utf-8")
    assert _files(outdir) == []


# ---------------------------------------------------------------------------
# (5) Guarda estática: ninguna ruta del script escribe un .txt de secretos
# ---------------------------------------------------------------------------
def test_script_source_has_no_plaintext_secret_files() -> None:
    """Complemento de los tests de comportamiento: aunque una rama no se
    ejercite, el nombre de fichero no puede estar en el fuente."""
    # Sólo las líneas EJECUTABLES: la cabecera del script cuenta la historia del
    # hallazgo (qué ficheros escribía antes) y ese comentario es lo que evita que
    # alguien lo «arregle» de vuelta.
    code = [
        line
        for line in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert len(code) >= 50, f"el parser dejó de ver el cuerpo del script ({len(code)} líneas)"
    body = "\n".join(code)
    for forbidden in ("unseal-keys.txt", "root-token.txt", "init-response.json"):
        assert forbidden not in body, (
            f"el script vuelve a mencionar {forbidden}: alguna ruta escribe secretos en claro"
        )


# ---------------------------------------------------------------------------
# (6) El gemelo de Windows — el que DE VERDAD produjo el incidente
# ---------------------------------------------------------------------------
# Este repo se desarrolla en Windows: los ficheros de `vault-init-output/` los
# escribió `init-vault.ps1`, no el `.sh`. Arreglar sólo el bash habría dejado
# intacta la ruta real. Aquí no hay shims (levantar `docker.cmd` + pwsh dentro de
# pytest es frágil en los dos sentidos), así que se afirma sobre el FUENTE — pero
# lo que se afirma es justo lo que no se puede comprobar leyendo por encima: el
# ORDEN de las comprobaciones.
_PS1 = _REPO_ROOT / "scripts" / "init-vault.ps1"


def _ps1_code_lines() -> list[str]:
    return [
        line
        for line in _PS1.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def test_ps1_twin_writes_no_plaintext_secret_files() -> None:
    code = _ps1_code_lines()
    assert len(code) >= 100, f"el parser dejó de ver el cuerpo del .ps1 ({len(code)} líneas)"
    body = "\n".join(code)
    for forbidden in ("unseal-keys.txt", "root-token.txt", "init-response.json"):
        assert forbidden not in body, f"init-vault.ps1 vuelve a escribir {forbidden}"


def test_ps1_twin_offers_the_same_two_paths() -> None:
    body = "\n".join(_ps1_code_lines())
    assert "PrintOnce" in body, "falta el modo de revelado único en el gemelo de Windows"
    assert "VAULT_INIT_RECIPIENT" in body, "el gemelo de Windows no lee el recipiente de cifrado"
    assert "vault-init." in body, "el gemelo de Windows no escribe el blob cifrado"


def test_ps1_twin_checks_the_recipient_before_initialising() -> None:
    """El orden es lo que evita el desastre: si el `throw` por recipiente ausente
    cayera DESPUÉS de `vault operator init`, las 5 shares y el root token se
    generarían y se perderían en el mismo comando."""
    lines = _PS1.read_text(encoding="utf-8").splitlines()
    guard = next(
        (i for i, line in enumerate(lines) if "no place to put the unseal keys" in line),
        None,
    )
    init_call = next(
        (i for i, line in enumerate(lines) if "vault operator init" in line and "#" not in line),
        None,
    )
    assert guard is not None, "desapareció la guarda de recipiente del .ps1"
    assert init_call is not None, "no se encuentra la llamada a `vault operator init` en el .ps1"
    assert guard < init_call, (
        f"la guarda de recipiente (línea {guard + 1}) está DESPUÉS de "
        f"`vault operator init` (línea {init_call + 1}): fallar ahí destruye las "
        "unseal keys sin recuperación"
    )
