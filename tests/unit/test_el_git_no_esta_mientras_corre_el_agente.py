"""ADR 0163: el `.git` del worktree no existe mientras corre el agente.

El operador pidió explícitamente comprobar que esto **no interfiere ni a los
agentes, ni a los worktrees, ni a los commits, ni a los pushes finales**. Cada
una de esas cuatro tiene su bloque abajo.

## Qué se decidió y por qué

El puntero del worktree —`gitdir: <bare>/worktrees/<id>`, un fichero de una
línea— es dentro del sandbox:

* **inútil**: apunta a metadatos que no se montan, así que todo `git` sale 128;
* **imprescindible** para el worker, que commitea a través de él;
* **un obstáculo**: `composer create-project` se niega a andamiar en un
  directorio no vacío.

El 2026-08-31 un agente lo borró para poder instalar CodeIgniter. Lo instaló
bien, y el cierre murió con `fatal: not a git repository`: el deliverable quedó
hecho, en disco y fuera de toda rama.

Retirarlo mientras corre el agente elimina la clase entera — no hay nada que
borrar, y el andamiador canónico de cada stack funciona.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from workers.plan_git import git_link_hidden, repair_worktree_link

pytestmark = pytest.mark.unit


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.email=harness@example.com",
            "-c",
            "user.name=harness",
            "-c",
            "safe.bareRepository=all",
            *args,
        ],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} -> rc={proc.returncode}: {proc.stderr}")
    return proc.stdout


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """Disposición real: bare + worktree, con un remoto al que poder empujar."""
    remoto = tmp_path / "remoto.git"
    _git("init", "--bare", "-q", str(remoto))

    project = tmp_path / "proyecto"
    (project / "repos").mkdir(parents=True)
    (project / "worktrees").mkdir()
    bare = project / "repos" / "app.git"
    _git("clone", "--bare", "-q", str(remoto), str(bare))

    semilla = tmp_path / "semilla"
    _git("clone", "-q", str(remoto), str(semilla))
    (semilla / "README.md").write_text("# hola\n", encoding="utf-8")
    _git("add", "-A", cwd=semilla)
    _git("commit", "-qm", "init", cwd=semilla)
    _git("push", "-q", "origin", "HEAD:master", cwd=semilla)
    _git("--git-dir", str(bare), "fetch", "-q", "origin", "master:master")

    wt = project / "worktrees" / "t1"
    _git("--git-dir", str(bare), "worktree", "add", "-q", str(wt), "master")
    return wt


# ---------------------------------------------------------------------------
# 1. No interfiere al AGENTE — que es el punto: le devuelve el andamiaje
# ---------------------------------------------------------------------------
def test_mientras_corre_el_agente_no_hay_git(worktree: Path) -> None:
    with git_link_hidden(worktree) as oculto:
        assert oculto is True
        assert not (worktree / ".git").exists(), (
            "el agente sigue viendo `.git`: los andamiadores estrictos seguirán fallando"
        )


def test_el_directorio_queda_vacio_para_un_andamiador(worktree: Path) -> None:
    """La razón de ser del ADR, en una aserción.

    `composer create-project` se niega si el directorio no está vacío. Con el
    repo recién clonado el worktree tiene `README.md` y `.git`; lo que el ADR
    garantiza es que **el puntero deja de contar** — el resto es del proyecto y
    el agente puede decidir sobre ello.
    """
    (worktree / "README.md").unlink()
    with git_link_hidden(worktree):
        restante = [p.name for p in worktree.iterdir()]
        assert restante == [], f"queda algo que bloquearía el andamiaje: {restante}"


# ---------------------------------------------------------------------------
# 2. No interfiere al WORKTREE — se repone siempre, y con lo mismo
# ---------------------------------------------------------------------------
def test_se_repone_identico_al_salir(worktree: Path) -> None:
    antes = (worktree / ".git").read_bytes()
    with git_link_hidden(worktree):
        pass
    assert (worktree / ".git").read_bytes() == antes, (
        "el puntero repuesto no es el que había: se reconstruyó por convención"
    )


def test_se_repone_aunque_el_run_reviente(worktree: Path) -> None:
    """El `finally` es la razón de que esto sea un gestor de contexto."""
    antes = (worktree / ".git").read_bytes()
    with pytest.raises(RuntimeError), git_link_hidden(worktree):
        raise RuntimeError("el contenedor murió")
    assert (worktree / ".git").read_bytes() == antes


def test_git_vuelve_a_funcionar_tras_el_bloque(worktree: Path) -> None:
    with git_link_hidden(worktree):
        pass
    assert "master" in _git("branch", "--show-current", cwd=worktree)


def test_un_git_del_andamiador_se_descarta(worktree: Path) -> None:
    """`cargo new` crea su PROPIO `.git`. El versionado lo lleva el worktree.

    Sin esto, reponer el puntero encontraría el sitio ocupado y el worktree
    quedaría apuntando al repo que se inventó el andamiador — roto igual que en
    el incidente, pero sin que nadie hubiera borrado nada.
    """
    antes = (worktree / ".git").read_bytes()
    with git_link_hidden(worktree):
        _git("init", "-q", str(worktree))  # lo que hace `cargo new`
        assert (worktree / ".git").is_dir()

    assert (worktree / ".git").is_file(), "quedó el repo del andamiador, no el puntero"
    assert (worktree / ".git").read_bytes() == antes


# ---------------------------------------------------------------------------
# 3. No interfiere al COMMIT
# ---------------------------------------------------------------------------
def test_el_trabajo_del_agente_se_commitea_igual(worktree: Path) -> None:
    with git_link_hidden(worktree):
        (worktree / "app").mkdir()
        (worktree / "app" / "Hello.php").write_text("<?php\n", encoding="utf-8")

    _git("add", "-A", cwd=worktree)
    _git("commit", "-qm", "deliverable", cwd=worktree)
    assert "app/Hello.php" in _git("show", "--name-only", "--format=", "HEAD", cwd=worktree)


def test_los_trailers_sobreviven(worktree: Path) -> None:
    """Principio 5: los commits llevan `Plan-Id`/`Task-Id`/`Execution-Id`."""
    with git_link_hidden(worktree):
        (worktree / "x.txt").write_text("y\n", encoding="utf-8")

    _git("add", "-A", cwd=worktree)
    _git(
        "commit",
        "-qm",
        "con trailers",
        "--trailer=Plan-Id=p1",
        "--trailer=Task-Id=t1",
        cwd=worktree,
    )
    cuerpo = _git("log", "-1", "--format=%B", cwd=worktree)
    assert "Plan-Id: p1" in cuerpo
    assert "Task-Id: t1" in cuerpo


# ---------------------------------------------------------------------------
# 4. No interfiere al PUSH final
# ---------------------------------------------------------------------------
def test_el_push_al_remoto_sigue_funcionando(worktree: Path) -> None:
    """El ciclo completo: agente trabaja sin `.git`, worker commitea y empuja."""
    with git_link_hidden(worktree):
        (worktree / "entregable.txt").write_text("hecho\n", encoding="utf-8")

    _git("checkout", "-q", "-b", "plan/abc", cwd=worktree)
    _git("add", "-A", cwd=worktree)
    _git("commit", "-qm", "trabajo del plan", cwd=worktree)
    _git("push", "-q", "origin", "plan/abc", cwd=worktree)

    bare = worktree.parent.parent / "repos" / "app.git"
    remoto = worktree.parent.parent.parent / "remoto.git"
    assert "plan/abc" in _git("--git-dir", str(bare), "branch", "--list", "plan/abc")
    assert "plan/abc" in _git("--git-dir", str(remoto), "branch", "--list", "plan/abc"), (
        "la rama del plan no llegó al remoto"
    )


# ---------------------------------------------------------------------------
# 5. Los bordes: nada de esto puede tumbar un run
# ---------------------------------------------------------------------------
def test_un_worktree_sin_puntero_no_revienta(tmp_path: Path) -> None:
    d = tmp_path / "suelto"
    d.mkdir()
    with git_link_hidden(d) as oculto:
        assert oculto is False


def test_un_clon_normal_no_se_toca(tmp_path: Path) -> None:
    """En un clon corriente `.git` es un DIRECTORIO y no es nuestro puntero.

    Retirarlo sería destruir el repositorio entero, no esconder un enlace.
    """
    d = tmp_path / "clon"
    d.mkdir()
    _git("init", "-q", str(d))
    assert (d / ".git").is_dir()

    with git_link_hidden(d) as oculto:
        assert oculto is False
        assert (d / ".git").is_dir(), "se retiró el .git de un clon normal"


def test_la_red_de_reparacion_sigue_cubriendo(worktree: Path) -> None:
    """Si el worker muere entre retirar y reponer, `repair_worktree_link` salva.

    Es lo que convierte a esa función en lo que debe ser —una red— en vez de en
    el arreglo principal.
    """
    contenido = (worktree / ".git").read_bytes()
    (worktree / ".git").unlink()  # el worker murió aquí

    assert repair_worktree_link(worktree) is True
    assert (worktree / ".git").exists()
    assert contenido  # el puntero original existía y no se perdió el repo
    _git("status", "--porcelain", cwd=worktree)


# ---------------------------------------------------------------------------
# 6. El PRUNE CONCURRENTE — el bloqueante que encontró la auditoría
# ---------------------------------------------------------------------------
# Sin el puntero, git considera el worktree `prunable`. El `git worktree prune`
# que dispara una tarea hermana al arrancar —o el reaper programado— borraría
# sus metadatos, y entonces reponer el puntero NO SIRVE DE NADA porque ya no hay
# a dónde apuntar. Sería exactamente el incidente que esto viene a evitar,
# provocado por la propia cura: la ventana la abre el arreglo.
#
# Medido antes de implementar el lock:
#
#     sin lock: oculto + prune -> metadatos PODADOS -> commit imposible
#     con lock: oculto + prune -> metadatos intactos -> commit posible
#
# `git worktree lock` es el mecanismo que git prevé para un worktree
# temporalmente no disponible, y es lo que hace segura toda la maniobra.


def test_un_prune_concurrente_no_puede_podar_el_worktree_oculto(worktree: Path) -> None:
    """El caso que la auditoría marcó como bloqueante."""
    bare = worktree.parent.parent / "repos" / "app.git"

    with git_link_hidden(worktree):
        # Una tarea hermana del mismo proyecto arranca y poda.
        _git("--git-dir", str(bare), "worktree", "prune")
        assert (bare / "worktrees" / worktree.name).is_dir(), (
            "el prune se llevó los metadatos: reponer el puntero ya no sirve"
        )

    # Y el ciclo sigue funcionando después.
    (worktree / "tras_el_prune.txt").write_text("ok\n", encoding="utf-8")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-qm", "sobrevive al prune", cwd=worktree)


def test_el_lock_se_suelta_al_terminar(worktree: Path) -> None:
    """Un worktree que quedara bloqueado nunca lo podaría el reaper.

    El disco crecería sin que nadie lo notara, que es la forma cara de que una
    protección se convierta en una fuga.
    """
    bare = worktree.parent.parent / "repos" / "app.git"
    with git_link_hidden(worktree):
        assert (bare / "worktrees" / worktree.name / "locked").exists()

    assert not (bare / "worktrees" / worktree.name / "locked").exists(), (
        "el worktree quedó bloqueado: el reaper no podrá podarlo nunca"
    )


def test_el_lock_se_suelta_aunque_el_run_reviente(worktree: Path) -> None:
    bare = worktree.parent.parent / "repos" / "app.git"
    with pytest.raises(RuntimeError), git_link_hidden(worktree):
        raise RuntimeError("el contenedor murió")
    assert not (bare / "worktrees" / worktree.name / "locked").exists()


def test_reparar_suelta_el_lock_que_dejo_un_worker_muerto(worktree: Path) -> None:
    """Si el proceso muere con el puntero oculto, el lock sobrevive a propósito.

    Es lo que permite que el reintento REPARE en vez de encontrarse los
    metadatos podados. Pero una vez reparado hay que soltarlo, o ese worktree
    queda a salvo del reaper para siempre.
    """
    bare = worktree.parent.parent / "repos" / "app.git"
    _git("--git-dir", str(bare), "worktree", "lock", str(worktree), "--reason", "run muerto")
    (worktree / ".git").unlink()

    assert repair_worktree_link(worktree) is True
    assert not (bare / "worktrees" / worktree.name / "locked").exists(), (
        "la reparación no soltó el lock: el worktree es inmortal para el reaper"
    )


def test_sin_poder_bloquear_no_se_oculta(tmp_path: Path) -> None:
    """Preferimos que el andamiador falle a arriesgar el deliverable.

    Si el worktree no está registrado en ningún bare no se puede bloquear, y sin
    lock ocultar el puntero es abrir la ventana del prune sin la red.
    """
    suelto = tmp_path / "proyecto" / "worktrees" / "huerfano"
    suelto.mkdir(parents=True)
    (suelto.parent.parent / "repos").mkdir()
    (suelto / ".git").write_text("gitdir: /no/registrado\n", encoding="utf-8")

    with git_link_hidden(suelto) as oculto:
        assert oculto is False
        assert (suelto / ".git").exists(), "se ocultó sin poder bloquear"


# ---------------------------------------------------------------------------
# 7. Con el CÓDIGO REAL, no con git a pelo
# ---------------------------------------------------------------------------
# La auditoría del 2026-08-31 señaló que los bloques de arriba dicen cubrir
# COMMIT y PUSH y en realidad sólo llaman a `git` directamente: si `commit_task`
# se rompiera, seguirían en verde. Un test que no toca el camino que dice
# proteger mide otra cosa y no lo dice.


def test_commit_task_real_cierra_la_tarea_tras_el_bloque(worktree: Path) -> None:
    """El camino de producción entero: ocultar, trabajar, y `commit_task`."""
    from workers.plan_git import CommitTrailers, commit_task

    with git_link_hidden(worktree):
        (worktree / "app").mkdir()
        (worktree / "app" / "Hello.php").write_text("<?php\n", encoding="utf-8")

    sha = commit_task(
        worktree,
        message="entregable de la tarea",
        trailers=CommitTrailers(
            plan_id="01a0550c", task_id="01a0550e", execution_id="ex1", generated_by="agente"
        ),
    )
    assert len(sha) == 40, f"sha inesperado: {sha!r}"
    cuerpo = _git("log", "-1", "--format=%B", cwd=worktree)
    assert "Plan-Id: 01a0550c" in cuerpo
    assert "Task-Id: 01a0550e" in cuerpo
    assert "app/Hello.php" in _git("show", "--name-only", "--format=", "HEAD", cwd=worktree)


def test_commit_task_real_sobrevive_a_un_prune_de_una_hermana(worktree: Path) -> None:
    """El bloqueante, medido contra `commit_task` y no contra `git add` a pelo.

    Es el test que la auditoría echó en falta: dos worktrees sobre el mismo bare,
    la hermana podando mientras el primero está oculto.
    """
    from workers.plan_git import CommitTrailers, commit_task

    bare = worktree.parent.parent / "repos" / "app.git"

    with git_link_hidden(worktree):
        # La tarea hermana se provisiona: `WorktreeManager.add` poda siempre.
        _git("--git-dir", str(bare), "worktree", "prune")
        hermana = worktree.parent / "t2"
        _git("--git-dir", str(bare), "worktree", "add", "-q", str(hermana), "-b", "otra")
        (worktree / "trabajo.txt").write_text("del agente\n", encoding="utf-8")

    sha = commit_task(
        worktree,
        message="pese a la hermana",
        trailers=CommitTrailers(plan_id="p", task_id="t", execution_id="e", generated_by="agente"),
    )
    assert len(sha) == 40
    assert "trabajo.txt" in _git("show", "--name-only", "--format=", "HEAD", cwd=worktree)


# ---------------------------------------------------------------------------
# 8. El CABLEADO: que esto se aplique donde debe, y sólo donde debe
# ---------------------------------------------------------------------------
def test_el_lanzamiento_oculta_el_git_solo_si_el_workspace_es_escribible() -> None:
    """Sobre el AST de `execution.py`, porque la condición es la mitad del diseño.

    Un run de REVIEW monta el worktree del implementador de sólo lectura (ADR
    0095): ahí el agente no puede romper nada y tocarlo arriesgaría pisar a un
    run concurrente sobre el mismo worktree. Ocultarlo también en ese caso sería
    un defecto que ningún test de `git_link_hidden` puede ver, porque vive en el
    llamante.
    """
    import ast
    import inspect

    from workers import execution

    fuente = inspect.getsource(execution.conduct_execution)
    arbol = ast.parse(fuente.strip())

    # Se busca la LLAMADA, no el nombre suelto: el `import` lo mantiene en el
    # texto aunque nadie lo invoque. Es la tercera vez hoy que ese atajo deja un
    # test pasando con el defecto dentro — comprobado mutando, no razonado.
    llamadas = {
        n.func.id
        for n in ast.walk(arbol)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "git_link_hidden" in llamadas, (
        "el lanzamiento IMPORTA el ocultado pero no lo LLAMA: el ADR 0163 dejó "
        "de aplicarse y los andamiadores estrictos volverán a fallar"
    )
    condiciones = [
        ast.unparse(n)
        for n in ast.walk(arbol)
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "ocultar" for t in n.targets)
    ]
    assert condiciones, "no se encuentra la condición que decide si ocultar"
    assert "read_only" in condiciones[0], (
        f"la condición no mira `read_only`: {condiciones[0]!r}. Un run de review "
        "monta el worktree ajeno de sólo lectura y no debe tocarse"
    )


def test_la_provision_repara_antes_de_sincronizar() -> None:
    """La red para la muerte dura del worker, y su ORDEN.

    Si el proceso muere sin ejecutar el `finally`, el worktree queda sin puntero
    y `sync_to_head` sale 128: la tarea quedaba en `workspace_unavailable` en
    CADA reintento. Reparar después de sincronizar no serviría — nunca se llega.
    """
    import ast
    import inspect

    from workers import execution

    fuente = inspect.getsource(execution._provision_worktree)
    arbol = ast.parse(fuente.strip())
    llamadas: list[str] = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                llamadas.append(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                llamadas.append(n.func.attr)

    assert "repair_worktree_link" in llamadas, (
        "la provisión no repara el enlace: una muerte dura deja la tarea "
        "irrecuperable en todos los reintentos"
    )
    assert "sync_to_head" in llamadas, "¿cambió la provisión de forma?"
    assert llamadas.index("repair_worktree_link") < llamadas.index("sync_to_head"), (
        "se repara DESPUÉS de sincronizar, y `sync_to_head` es justo lo que "
        "revienta con el puntero ausente: nunca se llegaría a reparar"
    )
