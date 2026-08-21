#!/usr/bin/env python
"""Gate de higiene: ningún secreto en claro en el árbol de trabajo.

Plan prod-10 `task_prod10_03` (hallazgos secrets-1, deploy-11, quality-3).

Por qué existe
--------------
El 2026-06-10 la auditoría encontró las 5 unseal keys y el root token REALES de
Vault en `vault-init-output/`, en claro, dentro del working tree desde el
2026-05-20 — legibles por cualquier proceso de la máquina, incluidos los agentes
IA que trabajan sobre este repo. `.gitignore` los ocultaba de git, que es
justamente lo que hizo que nadie los viera durante tres semanas: *no* estaban
comiteados, así que ningún escáner de secretos de git los delataba.

Este gate mira el DISCO, no el índice de git. Corre en dos sitios:

* **pre-commit** (hook local, `pass_filenames: false`): avisa al operador en el
  momento en que el fichero aparece.
* **CI** (`.github/workflows/ci.yml`, job de lint): impide que un checkout con
  artefactos de secretos llegue a un `docker build` cuyo contexto es la raíz del
  repo (los agent-runtimes lo son — ver `.dockerignore`).

Dos criterios
-------------
1. **Directorios de artefactos con contenido**: `vault-init-output/` y
   compañía. Un directorio VACÍO pasa: es el estado normal después del borrado
   seguro, y exigir que el operador borre también la carpeta sería un gate que
   molesta sin proteger.
2. **Tokens de servicio de Vault pegados en cualquier fichero**: el prefijo
   `hvs.` seguido de material real. El umbral de longitud está puesto para que
   la documentación pueda escribir la FORMA de un token (`hvs.<algo>`,
   `hvs.zAntQ…`) sin volver el gate inusable — un falso positivo en un gate de
   cada commit se desactiva en una semana, y entonces no protege de nada.

Lo que este gate NO hace
------------------------
No custodia nada ni revoca nada: eso es `task_prod10_01`, una operación humana
(mover las 5 keys a custodias separadas, `vault token revoke` del root expuesto).
Este script solo hace IMPOSIBLE que el estado vuelva a pasar desapercibido.

Uso
---
    python scripts/check_no_secret_artifacts.py            # raíz = cwd
    python scripts/check_no_secret_artifacts.py --root .   # explícito

Salida: 0 si el árbol está limpio, 1 con un informe accionable si no. El informe
nombra FICHEROS, nunca su contenido: un gate que imprime el secreto para
justificar el fallo lo copia al log de CI.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from pathlib import Path

# Directorios que solo pueden contener material de alto valor. Si existen con
# ficheros dentro, el árbol está sucio.
SECRET_ARTIFACT_DIRS: tuple[str, ...] = (
    "vault-init-output",
    ".vault-init",
)

# Ficheros de artefacto suelto en la raíz (el nombre que `init-vault.sh`
# escribía antes de prod-10 y el que sugieren los runbooks antiguos).
SECRET_ARTIFACT_FILES: tuple[str, ...] = (
    "vault-unseal-keys.txt",
    "vault-root-token.txt",
)

# Extensiones que SÍ pueden vivir dentro de un directorio de artefactos: la
# salida cifrada de `scripts/init-vault.sh`. Un blob age/gpg no es un secreto en
# claro, y prohibirlo dejaría al script de init sin sitio donde escribir — el
# operador acabaría volviendo al `.txt`, que es el problema original.
ENCRYPTED_SUFFIXES: frozenset[str] = frozenset({".age", ".gpg", ".asc", ".pgp"})

# Prefijo de los service tokens de Vault (>= 1.10). El umbral de 16 caracteres
# de material deja pasar los placeholders de documentación (`hvs.<algo>`,
# `hvs.zAntQ…`) y atrapa un token real, que ronda los 90+ caracteres.
#
# El patrón se construye por concatenación a propósito: escrito de un tirón, el
# literal de este fichero fuente casaría con su propio criterio y el gate se
# denunciaría a sí mismo.
_TOKEN_PREFIX = "hvs"
VAULT_TOKEN_RE = re.compile(_TOKEN_PREFIX + r"\.[A-Za-z0-9_-]{16,}")

# Árboles que no se recorren: vendorizados, cachés y artefactos de build. Sin
# esto el gate tarda minutos en cada commit (y `node_modules/` está lleno de
# strings que parecen cualquier cosa).
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "dist",
        "build",
        ".next",
        "playwright-report",
        "test-results",
        ".dev",
    }
)

# Extensiones que se escanean por contenido. Lista blanca en vez de negra: un
# binario nuevo no debe hacer que el gate empiece a leer megabytes.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        "",
        ".py",
        ".pyi",
        ".sh",
        ".bash",
        ".ps1",
        ".psm1",
        ".cmd",
        ".bat",
        ".md",
        ".txt",
        ".rst",
        ".yml",
        ".yaml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".env",
        ".example",
        ".hcl",
        ".tf",
        ".conf",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".sql",
        ".dockerfile",
        ".tpl",
    }
)

# Techo de lectura por fichero. Un token vive en las primeras líneas de un
# `.env` o de una nota; leer un CSV de 400 MB para buscarlo no aporta.
MAX_BYTES = 2_000_000

# Este propio fichero: describe el patrón, así que se excluye del escaneo por
# contenido para que la guarda no se denuncie a sí misma si un día el literal
# cambia de forma.
SELF_NAME = Path(__file__).name


class Finding:
    """Un hallazgo. Lleva la RUTA y el motivo, nunca el valor encontrado."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason

    def render(self, root: Path) -> str:
        try:
            shown = self.path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - defensivo
            shown = self.path.as_posix()
        return f"  {shown}\n      {self.reason}"


def _iter_files(root: Path) -> Iterator[Path]:
    """Los ficheros de texto del árbol, sin entrar en los directorios de SKIP_DIRS."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:  # pragma: no cover - permisos/enlaces roto
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    continue
                stack.append(entry)
                continue
            if entry.name == SELF_NAME:
                continue
            if _is_runtime_env_file(entry):
                continue
            if entry.suffix.lower() in TEXT_SUFFIXES:
                yield entry


def _is_runtime_env_file(path: Path) -> bool:
    """¿Es un `.env` de ejecución (y no un `.env.example`)?

    Los `.env` son el sitio DESIGNADO de las credenciales de ejecución: es donde
    `scripts/vault-mint-service-tokens.sh` deja los tokens por servicio, y donde
    el compose lee las contraseñas obligatorias. Están en `.gitignore` y en
    `.dockerignore`, así que no llegan ni al repositorio ni a una capa de imagen.
    Marcarlos sería un falso positivo garantizado en cualquier despliegue bien
    configurado — y un gate que grita en el caso correcto se desactiva.

    Un `.env.example` SÍ se escanea: un token real ahí es un error, porque ese
    fichero sí se commitea.
    """
    name = path.name
    if name.endswith(".example"):
        return False
    return name == ".env" or name.startswith(".env.")


def _artifact_dir_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name in SECRET_ARTIFACT_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        for entry in sorted(directory.rglob("*")):
            if not entry.is_file():
                continue
            if entry.suffix.lower() in ENCRYPTED_SUFFIXES:
                continue
            findings.append(
                Finding(
                    entry,
                    f"artefacto de secretos en claro dentro de {name}/ "
                    "(unseal keys / root token de Vault)",
                )
            )
    for name in SECRET_ARTIFACT_FILES:
        candidate = root / name
        if candidate.is_file():
            findings.append(Finding(candidate, "artefacto de secretos en claro en la raíz"))
    return findings


def _token_findings(root: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    for path in _iter_files(root):
        try:
            raw = path.read_bytes()[:MAX_BYTES]
        except OSError:  # pragma: no cover - defensivo
            continue
        scanned += 1
        text = raw.decode("utf-8", errors="ignore")
        if VAULT_TOKEN_RE.search(text):
            findings.append(
                Finding(
                    path,
                    "contiene lo que parece un service token de Vault "
                    f"({_TOKEN_PREFIX}. + material). Revócalo y sácalo del árbol.",
                )
            )
    return findings, scanned


_REMEDIATION = """
Cómo se arregla (docs/06-runbooks/dr-vault-unseal-rotation.md):

  1. Mueve las 5 unseal keys a custodias SEPARADAS (gestores de contraseñas /
     sobres sellados). Perder >= 3 de las 5 significa perder los datos de Vault.
  2. Guarda el root token en tu gestor personal y NO lo uses en configs: mintea
     tokens por servicio con scripts/vault-mint-service-tokens.sh.
  3. Si el token estuvo expuesto, revócalo:
       docker compose -f docker/docker-compose.yml exec vault \\
         vault token revoke <token>
  4. Borrado seguro de las copias locales:
       shred -u vault-init-output/*            # Linux/macOS
       (Windows) sobrescribe antes de borrar, p.ej. con `sdelete -p 3`.

A partir de ahora scripts/init-vault.sh CIFRA la respuesta de init (age/gpg) o
la imprime una sola vez: no vuelve a dejar *.txt en claro.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Raíz del árbol a inspeccionar (por defecto el directorio actual).",
    )
    parser.add_argument(
        "filenames",
        nargs="*",
        help=(
            "Ignorado. Aceptado para que pre-commit pueda pasar ficheros sin que "
            "el gate cambie de criterio: los artefactos que busca están en "
            ".gitignore, así que NUNCA llegan al índice y un gate que solo mirase "
            "lo staged no vería jamás el problema que motivó este script."
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    findings = _artifact_dir_findings(root)
    token_findings, scanned = _token_findings(root)
    findings.extend(token_findings)

    if findings:
        print(
            f"check_no_secret_artifacts: {len(findings)} artefacto(s) de secretos en {root}",
            file=sys.stderr,
        )
        for finding in findings:
            print(finding.render(root), file=sys.stderr)
        print(_REMEDIATION, file=sys.stderr)
        return 1

    print(f"check_no_secret_artifacts: limpio ({scanned} ficheros inspeccionados en {root}).")
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
