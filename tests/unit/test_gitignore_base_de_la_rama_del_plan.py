"""El `.gitignore` base que la plataforma deja al CERRAR una tarea del plan.

Excluir los directorios de dependencias del `git add -A` de `commit_task` cierra
el agujero **para la plataforma**, pero deja el repo mal para las personas:
cualquiera que lo clone y trabaje fuera del sistema se come exactamente el mismo
problema en su primer `git add -A`.

Y no basta con confiar en el andamiador. Comprobado el 2026-09-01 sobre una
instalación intacta de CodeIgniter 4: 14 entradas en la raíz y **ninguna** es
`.gitignore` (ni `.gitattributes`). No es un descuido de CodeIgniter —
`composer create-project` instala desde el *dist* y los repos suelen marcar
`.gitignore` como `export-ignore`, así que le pasa a media Packagist; el
equivalente pasa con npm, pip, go mod y maven.

Los nombres NO se escriben a mano aquí: salen de `dependency_dirs()`, que es la
misma fuente que usa la exclusión del commit y el `preserve` del sync.

Aquí se prueba la función SOLA, sobre un repo normal como el que tendría una
persona. Cuándo se la llama —`commit_task`, nunca la provisión, y sólo
acompañando a contenido— es una decisión del ADR 0163 y la fija
`tests/integration/test_gitignore_base_llega_a_la_rama.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from shared_test_runtimes import catalog
from workers.plan_git import ensure_base_gitignore

pytestmark = pytest.mark.unit


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=h@e.com", "-c", "user.name=h", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Un repo de trabajo normal — el que tendría una persona que clona."""
    assert _git("init", "-q", ".", cwd=tmp_path).returncode == 0
    return tmp_path


def _git_ignora(repo_path: Path, ruta: str) -> bool:
    """¿Git ignora de verdad esa ruta? (rc=0 de `check-ignore`)."""
    return _git("check-ignore", "-q", "--", ruta, cwd=repo_path).returncode == 0


def test_git_ignora_de_verdad_cada_directorio_del_catalogo(repo: Path) -> None:
    """Comportamiento, no texto: se le pregunta a git, no al fichero."""
    assert ensure_base_gitignore(repo) is True

    for nombre in catalog.dependency_dirs():
        assert _git_ignora(repo, f"{nombre}/instalado.txt"), (
            f"git NO ignora {nombre}/ pese al .gitignore base"
        )
        assert _git_ignora(repo, f"frontend/{nombre}/instalado.txt"), (
            f"git NO ignora {nombre}/ anidado: en un monorepo el problema vuelve"
        )


def test_un_git_add_de_una_persona_ya_no_se_lleva_las_dependencias(repo: Path) -> None:
    """El escenario completo, con el `git add -A` desnudo de un humano.

    Es la razón de ser de este fichero: la exclusión de `commit_task` sólo
    protege a la plataforma.
    """
    ensure_base_gitignore(repo)
    (repo / "vendor" / "codeigniter4").mkdir(parents=True)
    (repo / "vendor" / "autoload.php").write_text("<?php\n", encoding="utf-8")
    (repo / "app").mkdir()
    (repo / "app" / "Home.php").write_text("<?php\n", encoding="utf-8")

    assert _git("add", "-A", cwd=repo).returncode == 0
    estagiados = _git("ls-files", cwd=repo).stdout.split()

    assert "app/Home.php" in estagiados
    assert not [f for f in estagiados if f.startswith("vendor/")], (
        f"un `git add -A` humano sigue llevándose vendor/: {estagiados}"
    )
    assert ".gitignore" in estagiados, "el propio .gitignore sí se versiona"


def test_jamas_sobrescribe_el_gitignore_del_proyecto(repo: Path) -> None:
    """Es SUYO. Aunque sea peor que el nuestro, aunque esté vacío."""
    propio = repo / ".gitignore"
    propio.write_text("# el mío\n/build\n", encoding="utf-8")

    assert ensure_base_gitignore(repo) is False
    assert propio.read_text(encoding="utf-8") == "# el mío\n/build\n"


def test_un_gitignore_vacio_tambien_cuenta_como_suyo(repo: Path) -> None:
    """La vía de escape documentada: vaciarlo (no borrarlo) mantiene a la
    plataforma fuera, y no depende de leer el código para descubrirla."""
    propio = repo / ".gitignore"
    propio.write_text("", encoding="utf-8")

    assert ensure_base_gitignore(repo) is False
    assert propio.read_text(encoding="utf-8") == ""


def test_es_idempotente(repo: Path) -> None:
    """El cierre de tarea corre en CADA tarea del plan: la segunda no puede pisar
    la primera ni cambiar el fichero bajo los pies de nadie."""
    assert ensure_base_gitignore(repo) is True
    contenido = (repo / ".gitignore").read_text(encoding="utf-8")

    assert ensure_base_gitignore(repo) is False
    assert (repo / ".gitignore").read_text(encoding="utf-8") == contenido


def test_dice_quien_lo_puso_y_que_se_puede_tocar(repo: Path) -> None:
    """Un fichero aparecido de la nada en el repo de otro es un misterio.

    No se afirma sobre una frase literal —eso sería fijar la redacción— sino
    sobre lo que git hace con ella: todo lo que no es un directorio del catálogo
    tiene que ser comentario, y tiene que haberlo.
    """
    ensure_base_gitignore(repo)
    lineas = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()

    comentarios = [linea for linea in lineas if linea.startswith("#")]
    reglas = [linea for linea in lineas if linea.strip() and not linea.startswith("#")]

    assert comentarios, "el .gitignore no explica de dónde sale"
    assert sorted(reglas) == sorted(f"{n}/" for n in catalog.dependency_dirs()), (
        f"el fichero trae reglas que no salen del catálogo: {reglas}"
    )


def test_la_lista_se_deriva_del_catalogo_y_no_esta_escrita_a_mano(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Añadir un runtime al catálogo tiene que bastar.

    Se sustituye la fuente por un nombre que no existe en ninguna parte del
    código: si el `.gitignore` lo recoge, es que se derivó de verdad.
    """
    monkeypatch.setattr(catalog, "dependency_dirs", lambda: ("cachorros",))

    assert ensure_base_gitignore(repo) is True
    assert _git_ignora(repo, "cachorros/x.txt")
    assert not _git_ignora(repo, "vendor/x.txt"), (
        "sigue ignorando vendor/: la lista está escrita a mano en algún sitio"
    )
