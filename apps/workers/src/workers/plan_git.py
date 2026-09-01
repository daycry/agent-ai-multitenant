"""Git integration tied to the Plan (Plan 06 Fase F).

Plan 06 ties every plan to one git branch per repo, every commit to
the plan + task + execution it came from, and every plan closure to
an automatic PR. Five tasks of Fase F live here:

  * :func:`make_plan_branch_name` (06_21) — stable branch naming.
  * :func:`commit_task` (06_22) — commits with the four mandatory
    trailers: ``Plan-Id``, ``Task-Id``, ``Execution-Id``,
    ``Generated-By``.
  * :class:`PlanGitWorkflow.push_review_to_bare` (06_23) — the
    worktree → bare-repo step that fires after a successful auto-
    review.
  * :class:`PlanGitWorkflow.push_branch_to_remote` (06_23) — the
    bare → remote step gated by ``branch_push_mode`` (``incremental``
    pushes per task, ``final_only`` pushes once at plan close).
  * :class:`PlanGitWorkflow.open_plan_pr` (06_24) — opens a PR per
    affected repo at plan completion.
  * :class:`PlanGitWorkflow.apply_push_policy` (06_25) — the merge-
    time policy: ``forbidden`` rejects, ``branch_only_pr_required``
    keeps the PR open, ``direct_to_default_allowed`` fast-forwards
    the default branch on the bare.

The three policies (``branch_push_mode`` x ``plan_validation_mode`` x
``push_policy``) are orthogonal — every combination yields a
well-defined behaviour. Section 12.6 of the .docx is the source of
truth; the matrix tests pin the combinations.
"""

from __future__ import annotations

import contextlib
import re
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog

from workers.git_identity import PLATFORM_GIT_EMAIL, PLATFORM_GIT_NAME, git_identity_env
from workers.git_repos import BareRepoLayout, GitCommandError, _run_git

# Optimistic-concurrency retries for the worktree→bare push: sibling tasks of the
# same plan push to the SAME plan branch, so a losing task must rebase onto the
# branch tip and retry. A handful of retries covers any realistic contention.
_PUSH_RECONCILE_RETRIES = 5


def _is_non_fast_forward(exc: GitCommandError) -> bool:
    """Whether a push failed because the branch advanced (a sibling pushed first)."""
    msg = str(exc).lower()
    return (
        "non-fast-forward" in msg
        or "fast-forwards" in msg
        or "failed to push some refs" in msg
        or "[rejected]" in msg
    )


_log = structlog.get_logger("workers.plan_git")

# Policy axes (Plan 06 section 12.6 of the .docx).
BranchPushMode = Literal["incremental", "final_only"]
PlanValidationMode = Literal["human_required", "auto_approve"]
PushPolicy = Literal["forbidden", "branch_only_pr_required", "direct_to_default_allowed"]


# ---------------------------------------------------------------------------
# task_06_21 — Plan branch naming
# ---------------------------------------------------------------------------

# Short id width — 8 hex chars matches the convention we use across
# the codebase (project.id.hex[:8] is the most common pattern).
_PLAN_ID_SHORT_LEN = 8

# Slug normaliser: lowercase, kebab-case, alnum + dashes only.
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def make_plan_branch_name(plan_id: str, slug: str) -> str:
    """Return ``plan/{id_short}-{slug}`` for a plan id + human slug.

    ``plan_id`` may be a UUID-string (with or without dashes) or any
    other string; we take the first 8 hex characters of its
    non-dashed lowercase form. ``slug`` is normalised to kebab-case.

    Examples::

        >>> make_plan_branch_name("11111111-2222-3333-4444-555555555555", "Fix Auth")
        'plan/11111111-fix-auth'
        >>> make_plan_branch_name("abc123", "")
        'plan/abc123'
    """
    short = plan_id.replace("-", "").lower()[:_PLAN_ID_SHORT_LEN] or plan_id
    # PROY2-14: transliterar acentos/diéresis/ñ (NFKD) en vez de perder letras
    # ("Búsqueda" → "busqueda", no "b-squeda") — espejo de api_server.slug.
    import unicodedata

    folded = (
        unicodedata.normalize("NFKD", slug or "").encode("ascii", "ignore").decode("ascii").lower()
    )
    norm = _SLUG_RE.sub("-", folded).strip("-")
    if not norm:
        return f"plan/{short}"
    return f"plan/{short}-{norm}"


@dataclass(frozen=True)
class PlanGitIdentity:
    """A plan's git coordinates: which bare repo holds its commits + its branch.

    ``project_slug`` is BOTH the ``BareRepoLayout`` directory and the bare repo
    name (one bare per project, ADR 0085 decision 2), so the on-disk bare is
    ``.../{tenant_slug}/{project_slug}/repos/{project_slug}.git``.
    """

    project_slug: str
    plan_branch: str


def plan_git_identity(plan_id: str, plan_slug: str, project_slug: str) -> PlanGitIdentity:
    """Single source of truth for a plan's git identity (bare repo + branch).

    Execution, clone and the auto-PR MUST resolve IDENTICAL coordinates, so this
    is the only place that derives them. Callers pass the PERSISTED slugs
    (``projects.slug`` / ``plans.slug``, generated once at creation, ADR 0085) —
    never re-slugify a name/title. Re-slugifying the (prefixed) title in the
    auto-PR while execution used ``plan.slug`` is exactly what made the PR branch
    diverge from the branch that held the commits (audit 2026-07-03, P1/P2).
    """
    return PlanGitIdentity(
        project_slug=project_slug,
        plan_branch=make_plan_branch_name(plan_id, plan_slug),
    )


def worktree_layout(
    *,
    data_root: str | Path,
    tenant_slug: str,
    project_slug: str,
) -> BareRepoLayout:
    """La primitiva de LAYOUT de :func:`worktree_coordinates` (remate I-2, auditoría
    2026-07-10): el sitio que solo necesita el layout — la resolución read-only del
    worktree del review, que no tiene plan a mano — la llama directamente en vez de
    reconstruir ``BareRepoLayout`` a mano, y no puede divergir de los demás.

    IDENTIDAD DooD (CRÍTICO): ``settings.data_root`` es un path DAEMON-SIDE que el worker
    monta en la MISMA ruta y entrega VERBATIM al daemon como bind source. NO normaliza
    (``Path(data_root)`` sin ``resolve()``/realpath) para preservar la identidad
    container-side == daemon-side de los binds ``/workspace``."""
    return BareRepoLayout(
        data_root=Path(data_root), tenant_slug=tenant_slug, project_slug=project_slug
    )


def worktree_coordinates(
    *,
    data_root: str | Path,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
) -> tuple[BareRepoLayout, str]:
    """``(BareRepoLayout, plan_branch)`` — las coordenadas de worktree que TODOS los
    sitios (provisión, resolución read-only, commit/push, review, back-fill) deben
    derivar IDÉNTICAS (hallazgo #10a). Antes cada uno reconstruía el ``BareRepoLayout``
    + ``make_plan_branch_name`` a mano — 5+ puntos propensos a divergir del contrato de
    :func:`plan_git_identity` («Execution, clone and the auto-PR MUST resolve IDENTICAL
    coordinates»). El layout sale de :func:`worktree_layout` (misma primitiva que usa
    la resolución read-only del review); ver allí el invariante DooD de no-normalización.

    El ``repo_name`` (nombre del bare, ADR 0085 = ``project_slug`` salvo override legacy
    por-request) lo resuelve cada caller y lo pasa a ``layout.bare_repo_path`` /
    ``WorktreeManager``."""
    return (
        worktree_layout(data_root=data_root, tenant_slug=tenant_slug, project_slug=project_slug),
        make_plan_branch_name(plan_id, plan_slug),
    )


# ---------------------------------------------------------------------------
# task_06_22 — Commit with trailers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitTrailers:
    """The four mandatory trailers every plan-generated commit carries.

    Mirrors the format ``git commit --trailer`` emits — one trailer
    per output line, ``Key: Value``. Section 12.6 of the .docx
    pins these names + their order.
    """

    plan_id: str
    task_id: str
    execution_id: str
    generated_by: str = "agentic-platform"

    def as_args(self) -> list[str]:
        """Return ``--trailer Key=Value`` args for ``git commit``."""
        return [
            f"--trailer=Plan-Id={self.plan_id}",
            f"--trailer=Task-Id={self.task_id}",
            f"--trailer=Execution-Id={self.execution_id}",
            f"--trailer=Generated-By={self.generated_by}",
        ]


def _bares_candidatos(worktree_path: Path) -> list[Path]:
    """Los bare repos del proyecto al que pertenece este worktree.

    Disposición fijada por `git_repos`: ``<project>/worktrees/<task_id>`` junto a
    ``<project>/repos/<repo>.git``. Se derivan en vez de recibirlos por parámetro
    para no cambiar la firma de las tres llamadas vivas de `commit_task`.
    """
    return sorted((worktree_path.parent.parent / "repos").glob("*.git"))


def _bare_de(worktree_path: Path) -> Path | None:
    """El bare que REGISTRA este worktree, o ``None`` si ninguno lo hace."""
    for bare in _bares_candidatos(worktree_path):
        if (bare / "worktrees" / worktree_path.name).is_dir():
            return bare
    return None


def _worktree_lock(worktree_path: Path, *, motivo: str) -> Path | None:
    """Marca el worktree como bloqueado para que ``git worktree prune`` lo respete.

    Devuelve el bare sobre el que se tomó el lock, o ``None`` si no se pudo.
    """
    bare = _bare_de(worktree_path)
    if bare is None:
        return None
    try:
        _run_git("--git-dir", str(bare), "worktree", "lock", str(worktree_path), "--reason", motivo)
    except GitCommandError as exc:
        # Ya bloqueado (un reintento tras un worker muerto) no es un fallo.
        if "already locked" in str(exc).lower():
            return bare
        _log.warning("worktree.lock_failed", worktree=str(worktree_path), error=str(exc))
        return None
    return bare


def _worktree_unlock(worktree_path: Path, bare: Path) -> None:
    with contextlib.suppress(GitCommandError):
        _run_git("--git-dir", str(bare), "worktree", "unlock", str(worktree_path))


def repair_worktree_link(worktree_path: Path) -> bool:
    """Restaura ``<worktree>/.git`` si falta. Devuelve ``True`` si reparó.

    **Por qué el worker no puede confiar en ese fichero.** Es un puntero de una
    línea (``gitdir: <bare>/worktrees/<id>``) que vive DENTRO del workspace
    montado en escritura para el agente, y del que depende todo el
    ``git add -A`` del cierre de tarea. El agente puede borrarlo sin querer y
    sin saberlo: medido el 2026-08-31, uno lo hizo para que
    ``composer create-project`` aceptara el directorio —esa herramienta EXIGE
    uno vacío— instaló CodeIgniter 4.7.4 correctamente, y el cierre murió con
    ``fatal: not a git repository``. El deliverable quedó en disco y fuera de
    toda rama.

    Cerrar la tool de borrado no basta y conviene decir por qué: la allowlist
    base del SDK incluye ``rm``, así que ``shell_exec("rm .git")`` hace lo mismo.
    Una guarda por puerta deja las otras abiertas; ésta cubre el resultado.

    **El orden importa, y es la parte que se descubrió midiendo.**
    ``git worktree repair`` reconstruye el puntero SÓLO mientras sobrevivan los
    metadatos del bare (``<bare>/worktrees/<id>``). En cuanto algún git dispara
    ``worktree prune`` —lo hace solo al ver un puntero roto— esos metadatos
    desaparecen y ya no hay nada que reparar. Comprobado en los dos casos:

        .git borrado, metadatos intactos  -> repair lo restaura, el commit entra
        .git borrado, metadatos podados   -> repair no puede hacer nada

    Por eso esto se llama al PRINCIPIO de ``commit_task``, antes de cualquier
    otra invocación de git sobre el worktree.
    """
    if (worktree_path / ".git").exists():
        return False

    bares = _bares_candidatos(worktree_path)
    if not bares:
        _log.warning(
            "worktree.link_missing_no_bare",
            worktree=str(worktree_path),
            note="`.git` ausente y ningún bare bajo repos/: no se puede reparar",
        )
        return False

    for bare in bares:
        if not (bare / "worktrees" / worktree_path.name).is_dir():
            continue
        # `worktree repair` SALE CON CÓDIGO 1 aunque haya reparado: imprime
        # «error: unable to locate repository; .git file broken» —que describe el
        # estado que viene a arreglar— y reconstruye el puntero igualmente.
        # Medido: la primera versión trataba ese rc como fallo y abortaba antes
        # de mirar el resultado. Es juzgar por la intención en vez de por el
        # efecto, que es justo lo que este arreglo persigue. Se ejecuta, se
        # ignora el código de salida y se COMPRUEBA el fichero.
        with contextlib.suppress(GitCommandError):
            _run_git("--git-dir", str(bare), "worktree", "repair", str(worktree_path))
        if (worktree_path / ".git").exists():
            # Un lock superviviente es la huella de un worker que murió con el
            # puntero oculto. Se suelta al reparar: si no, ese worktree quedaría
            # a salvo del reaper para siempre.
            _worktree_unlock(worktree_path, bare)
            _log.warning(
                "worktree.link_repaired",
                worktree=str(worktree_path),
                bare=str(bare),
                note=(
                    "el `.git` del worktree faltaba y se ha reconstruido; "
                    "algo dentro del sandbox lo borró"
                ),
            )
            return True

    _log.warning(
        "worktree.link_missing_metadata_gone",
        worktree=str(worktree_path),
        note=(
            "`.git` ausente y los metadatos del bare ya estaban podados: "
            "irrecuperable. El deliverable sigue en disco, fuera de toda rama"
        ),
    )
    return False


@contextlib.contextmanager
def git_link_hidden(worktree_path: Path) -> Iterator[bool]:
    """Retira ``<worktree>/.git`` mientras dura el bloque y lo repone al salir.

    ADR 0163. El puntero del worktree es, dentro del sandbox del agente, un
    fichero **inútil** (apunta a metadatos que no se montan: todo git sale 128),
    **imprescindible** para el worker (que commitea a través de él) y **un
    obstáculo** (``composer create-project`` se niega a andamiar en un directorio
    no vacío). Con esas tres a la vez y ``rm`` en la allowlist, que el agente lo
    corte no es un accidente raro: es lo que va a pasar — y pasó el 2026-08-31,
    dejando CodeIgniter instalado y fuera de toda rama.

    Quitarlo de en medio elimina la clase entera en vez de taparla: no hay nada
    que borrar, y el andamiador canónico de cada stack funciona.

    Garantías, que son las que hacen esto seguro:

    * **Se repone SIEMPRE**, también si el run revienta o lo cancelan: el
      ``finally`` es la razón de que esto sea un gestor de contexto y no dos
      llamadas sueltas que alguien pueda desemparejar.
    * **Se repone el contenido EXACTO** que había, leído antes de retirarlo. No
      se reconstruye por convención: si mañana git cambia el formato del puntero,
      esto sigue siendo correcto.
    * **Si el andamiador dejó su PROPIO ``.git``** —``cargo new`` lo hace— se
      retira antes de reponer el nuestro. El versionado lo lleva el worktree, no
      el scaffolder; es lo que hace ``--remove-vcs`` de composer. Se registra,
      porque descartar el repo que otra herramienta creyó dejar hecho no debe
      ser silencioso.

    ``yield`` devuelve ``True`` si de verdad se retiró algo. Con un worktree sin
    puntero (o un directorio que no lo es) no hace nada y devuelve ``False``.
    """
    enlace = worktree_path / ".git"
    contenido: bytes | None = None
    bare_bloqueado: Path | None = None

    if enlace.is_file():
        # EL LOCK VA PRIMERO, y es lo que hace segura toda la maniobra. Sin el
        # puntero, git considera el worktree `prunable`: el `git worktree prune`
        # que dispara una tarea hermana al arrancar —o el reaper programado—
        # borraría sus metadatos, y entonces reponer el puntero no sirve de nada
        # porque ya no hay a dónde apuntar. Sería EXACTAMENTE el incidente que
        # esto viene a evitar, provocado por la propia cura.
        #
        # `git worktree lock` es el mecanismo que git prevé para un worktree
        # temporalmente no disponible. Medido: con lock, un `prune` concurrente
        # deja los metadatos intactos y el commit posterior entra.
        bare_bloqueado = _worktree_lock(worktree_path, motivo="agent run in progress")
        if bare_bloqueado is None:
            # Sin lock no se oculta: preferimos que `composer create-project`
            # falle a arriesgar el deliverable de la tarea.
            _log.warning(
                "worktree.link_hide_skipped",
                worktree=str(worktree_path),
                note="no se pudo bloquear el worktree; se deja el `.git` en su sitio",
            )
        else:
            try:
                contenido = enlace.read_bytes()
                enlace.unlink()
            except OSError as exc:
                _log.warning(
                    "worktree.link_hide_failed", worktree=str(worktree_path), error=str(exc)
                )
                contenido = None
                _worktree_unlock(worktree_path, bare_bloqueado)
                bare_bloqueado = None

    try:
        yield contenido is not None
    finally:
        if contenido is not None:
            try:
                # Un `.git` que NO es el nuestro sólo puede haberlo puesto algo de
                # dentro del sandbox. Se retira: el worktree es quien versiona.
                if enlace.exists():
                    intruso = "directorio" if enlace.is_dir() else "fichero"
                    if enlace.is_dir():
                        shutil.rmtree(enlace, ignore_errors=True)
                    else:
                        with contextlib.suppress(OSError):
                            enlace.unlink()
                    _log.warning(
                        "worktree.scaffolder_git_discarded",
                        worktree=str(worktree_path),
                        kind=intruso,
                        note=(
                            "el andamiador dejó su propio `.git` y se descarta: "
                            "el versionado lo lleva el worktree del plan"
                        ),
                    )
                enlace.write_bytes(contenido)
            except OSError as exc:
                # No se puede tirar el run por esto: `commit_task` repara.
                _log.error(
                    "worktree.link_restore_failed",
                    worktree=str(worktree_path),
                    error=str(exc),
                    note="`repair_worktree_link` intentará reconstruirlo al commitear",
                )
        if bare_bloqueado is not None:
            # El lock se suelta SIEMPRE. Un worktree que queda bloqueado tras un
            # run muerto nunca lo podaría el reaper, y el disco crecería sin que
            # nadie lo notara. Si el proceso muere antes de llegar aquí, el lock
            # sobrevive a propósito: es lo que permite que el reintento repare.
            _worktree_unlock(worktree_path, bare_bloqueado)


#: El prefijo que `agent_runtime.file_tools` pone a lo que aparta antes de
#: destruirlo. Se repite aquí a propósito y no se importa: el runtime corre
#: DENTRO del contenedor efímero y el worker fuera, sin ningún paquete común
#: entre los dos. Lo que ata los dos lados es
#: `tests/unit/test_el_residuo_del_runtime_no_llega_al_commit.py`, que falla si
#: alguien cambia uno solo.
_PREFIJO_TRANSITORIO_RUNTIME = ".agent-runtime-tmp."


# ---------------------------------------------------------------------------
# Directorios de dependencias: ni entran en la rama, ni se quedan si ya entraron
# ---------------------------------------------------------------------------


def _nombres_de_dependencias() -> tuple[str, ...]:
    """Los directorios de dependencias que declara el catálogo de runtimes.

    **La decisión ya estaba tomada en otro sitio.** Cada plantilla declara los
    suyos (``vendor`` en los php, ``node_modules`` en los node, ``.venv``/``venv``
    en python) y `execution._provision_worktree` se los pasa a
    ``sync_to_head(preserve=...)`` para que el ``clean -fdx`` NO se los lleve. O
    sea que la plataforma **ya** trata esos árboles como «no forman parte del
    entregable»; hasta hoy decía a la vez lo contrario, porque el ``git add -A``
    del cierre de tarea se los llevaba a la rama del plan.

    Import perezoso a propósito: la api-server importa este módulo para el visor
    de diffs (`api_server.code_diff`) y no tiene por qué arrastrar el catálogo de
    runtimes —cuya importación lee además el manifiesto de release—.

    Los nombres se VALIDAN en vez de pasarse tal cual, igual que en
    `git_repos.clean_args`: hoy vienen del catálogo, pero entregar cadenas sin
    mirar a una línea de comandos de git es un agujero que no se deja abierto.
    """
    from shared_test_runtimes import catalog as runtime_catalog

    nombres: list[str] = []
    for crudo in runtime_catalog.dependency_dirs():
        nombre = str(crudo).strip()
        if (
            not nombre
            or nombre.startswith("-")
            or "/" in nombre
            or "\\" in nombre
            or ".." in nombre
            or "*" in nombre
            or nombre in {".", ".git"}
        ):
            raise ValueError(f"nombre de directorio de dependencias inválido: {crudo!r}")
        nombres.append(nombre)
    return tuple(nombres)


def _patrones_de_dependencias(nombres: Sequence[str]) -> list[str]:
    """``**/<nombre>/**`` — el CONTENIDO de cada directorio, a cualquier profundidad.

    Un solo sitio para el patrón, que se usa con dos magias de pathspec distintas:
    ``:(glob)`` para BUSCAR lo que ya está versionado y ``:(exclude,glob)`` para
    que el ``git add -A`` no lo meta. Escribirlo dos veces sería la forma de que
    una mitad dejara de casar con la otra sin que nada avisara.

    Dos decisiones, las dos medidas:

    * El ``**/`` inicial porque un monorepo tiene ``frontend/node_modules/`` y
      ``backend/vendor/``. Es el mismo motivo por el que `dependency_dirs()`
      devuelve la UNIÓN de los runtimes y no los del template declarado.
    * Sólo el CONTENIDO (``/**``) y no el nombre a secas, al revés que la
      exclusión del residuo del runtime. Un directorio de dependencias sólo puede
      ser un directorio, y git no versiona directorios vacíos: excluir lo de
      dentro basta —comprobado, `git add -A` respeta el pathspec aunque el
      directorio esté entero sin trackear—. La diferencia importa porque un
      FICHERO llamado ``vendor`` (un script en ``bin/``) sí es deliverable, y un
      patrón sobre el nombre se lo llevaría por delante.
    """
    return [f"**/{nombre}/**" for nombre in nombres]


def _directorio_contenedor(ruta: str, nombres: frozenset[str]) -> str:
    """``frontend/node_modules/react/index.js`` → ``frontend/node_modules``.

    Para que el registro diga DE QUÉ directorio salió cada fichero y no sólo
    cuántos: en un monorepo hay varios y no es lo mismo uno que otro.
    """
    partes = ruta.split("/")
    for i, parte in enumerate(partes):
        if parte in nombres:
            return "/".join(partes[: i + 1])
    return ruta  # inalcanzable: el pathspec ya garantizó el componente


def _desversionar_dependencias(worktree_path: Path, nombres: Sequence[str]) -> int:
    """Saca del ÍNDICE lo que ya está versionado y es dependencia. Sin tocar disco.

    **Por qué no basta con excluirlas de los futuros commits.** Excluir arregla
    las ramas nuevas; en la rama donde el artefacto YA entró, `sync_to_head` lo
    sigue trayendo en cada tarea y la guarda de rutas versionadas (ADR 0164) lo
    sigue blindando —«refusing to recursively delete 'vendor': it is tracked in
    this branch»—, que es exactamente el punto muerto medido el 2026-09-01: 1.151
    ficheros de `vendor/` en la rama, `composer create-project` rechazado por
    directorio no vacío, `delete_file` y `move_file` rechazados por la guarda, y
    el run muerto en `max_tokens_exceeded` porque `list_files` costaba ~9.100
    tokens por iteración.

    **`git rm --cached` y nunca un borrado.** El agente y el toolchain NECESITAN
    ese `vendor/` para trabajar —`sync_to_head` lo preserva justamente por eso—,
    así que esto des-versiona dejando el árbol de trabajo intacto. Es la garantía
    que da la propia opción: «Working tree files, whether modified or not, will be
    left alone».

    El ``-f`` acompaña al ``--cached`` y no lo debilita: sólo salta el chequeo de
    «tienes contenido estageado distinto del fichero y de HEAD», que puede darse
    si el agente hizo su propio `git add` sobre una dependencia por `shell_exec`.
    Ese contenido estageado es justo lo que se quiere quitar del índice, y sigue
    en disco. Sin ``-f`` esa situación aborta el cierre entero de la tarea.

    EL RIESGO, escrito donde se ve: un proyecto que versione sus dependencias A
    PROPÓSITO verá cómo la plataforma se las des-versiona.

    **Ojo con el argumento que NO vale, porque se propuso y se midió que era
    falso** (2026-09-01): «da igual, ese proyecto ya está roto hoy porque
    `sync_to_head` preserva esos directorios en vez de sincronizarlos». No es
    cierto. `preserve` sólo alimenta el `git clean -fdx -e …`, que por definición
    no toca ficheros TRACKEADOS; el `reset --hard FETCH_HEAD` los sincroniza
    perfectamente. Medido: `t1` versiona `vendor/lib.js`=v1, `t2` lo sube a v2,
    `t1` sincroniza y ve v2. La incoherencia que ese argumento decía resolver no
    existía.

    El argumento que SÍ sostiene la decisión es más estrecho, y conviene tenerlo
    presente porque marca dónde deja de valer:

      * el directorio está declarado como de dependencias por el runtime template
        del propio proyecto (`shared_test_runtimes.catalog`), o sea que es la
        plataforma reconociendo lo que el proyecto le dijo que era;
      * versionarlo casi siempre es un ACCIDENTE —falta un `.gitignore` y el
        `git add -A` se lo lleva— y ese accidente tuvo un coste medido: 1.151
        ficheros en la rama, la guarda del ADR 0164 blindando un artefacto
        reconstruible, y una tarea muerta por `max_tokens` sin poder andamiar;
      * el contenido no se pierde: sigue en disco y sigue en la historia de la
        rama, un commit más atrás.

    **La limitación conocida, que es real:** hay proyectos que versionan un
    directorio con ese nombre a propósito. El caso concreto es el
    `assets/vendor/` de Symfony AssetMapper, que la documentación de Symfony
    manda commitear, y que casa con `**/vendor/**`. Hoy NO hay forma de que un
    proyecto lo declare. Si aparece ese caso, la salida no es quitar la
    exclusión —volvería el punto muerto— sino darle al proyecto la misma clase de
    declaración que ya tiene para `allowed_commands`. Queda escrito aquí para que
    quien se lo encuentre sepa que se pensó y no se le pasó a nadie.

    Devuelve cuántos ficheros salieron del índice (0 si no había nada).
    """
    pathspecs = [f":(glob){patron}" for patron in _patrones_de_dependencias(nombres)]
    if not pathspecs:
        return 0
    try:
        salida = _run_git("ls-files", "-z", "--", *pathspecs, cwd=worktree_path)
    except GitCommandError as exc:
        # Best-effort de principio a fin: sin esto el comportamiento es el de
        # antes (la rama sigue atascada), y tumbar el cierre de la tarea sería
        # peor que no des-versionar.
        _log.warning(
            "commit_task.dependency_scan_failed",
            worktree=str(worktree_path),
            error=str(exc)[:300],
        )
        return 0
    # `-z` y no líneas: sin él git devuelve los nombres no-ASCII C-quoted y el
    # conteo por directorio saldría mal justo en los repos en castellano.
    versionados = [ruta for ruta in salida.split("\0") if ruta]
    if not versionados:
        return 0

    por_directorio: dict[str, int] = {}
    conjunto = frozenset(nombres)
    for ruta in versionados:
        clave = _directorio_contenedor(ruta, conjunto)
        por_directorio[clave] = por_directorio.get(clave, 0) + 1

    # SÓLO los pathspecs que de verdad casaron. `git rm` —al revés que
    # `ls-files`— aborta con rc=128 si CUALQUIER pathspec no encuentra nada
    # («fatal: pathspec ':(glob)**/.venv/**' did not match any files»), y en un
    # proyecto PHP nunca hay `.venv/`. Medido: con los cuatro nombres del
    # catálogo, el des-versionado de `vendor/` no llegaba a ocurrir NUNCA.
    #
    # Se filtra en vez de añadir `--ignore-unmatch` a propósito: así un rc≠0 de
    # `git rm` sigue significando «algo ha ido mal de verdad» y se registra.
    presentes = {parte for ruta in versionados for parte in ruta.split("/") if parte in conjunto}
    a_retirar = [f":(glob){patron}" for patron in _patrones_de_dependencias(sorted(presentes))]
    try:
        # Pathspecs y NO la lista de rutas: eran 1.151 ficheros en el caso
        # medido, y una línea de comandos con 1.151 rutas no cabe en Windows.
        _run_git("rm", "-r", "--cached", "--quiet", "-f", "--", *a_retirar, cwd=worktree_path)
    except GitCommandError as exc:
        _log.error(
            "commit_task.dependency_unversion_failed",
            worktree=str(worktree_path),
            files=len(versionados),
            by_directory=por_directorio,
            error=str(exc)[:300],
            note=(
                "las dependencias siguen versionadas en la rama: la tarea siguiente "
                "seguirá sin poder retirarlas (guarda del ADR 0164)"
            ),
        )
        return 0

    _log.warning(
        "commit_task.dependencies_unversioned",
        worktree=str(worktree_path),
        files=len(versionados),
        by_directory=por_directorio,
        note=(
            "artefactos reconstruibles que habían entrado en la rama por un "
            "`git add -A` sin `.gitignore`; salen del índice y SIGUEN en disco"
        ),
    )
    return len(versionados)


#: Cabecera del `.gitignore` base. Los nombres NO se escriben aquí: se derivan de
#: `dependency_dirs()`, que es la fuente única.
_CABECERA_GITIGNORE_BASE = """\
# .gitignore base — lo escribió la plataforma agéntica al cerrar una tarea,
# porque el proyecto no traía ninguno.
#
# ES TUYO: edítalo, amplíalo o déjalo vacío. Mientras este fichero exista, la
# plataforma no lo vuelve a tocar: sólo lo escribe cuando NO hay ninguno.
#
# Por qué existe: casi ningún andamiador deja un `.gitignore`. `composer
# create-project` instala desde el *dist* y los repos suelen marcar `.gitignore`
# como `export-ignore` — comprobado sobre una instalación intacta de CodeIgniter
# 4: 14 entradas en la raíz y ninguna es `.gitignore`. Le pasa a media Packagist,
# y el equivalente pasa con npm, pip, go mod y maven. Sin él, un `git add -A` se
# lleva el directorio de dependencias entero al repositorio: medido el
# 2026-09-01, 1.151 ficheros de `vendor/` en la rama de un plan.
#
# Los nombres salen del catálogo de runtimes de la plataforma, la MISMA lista que
# preserva al sincronizar el worktree entre tareas.

"""


def ensure_base_gitignore(worktree_path: Path) -> bool:
    """Deja un `.gitignore` base en el worktree si el proyecto no trae uno.

    Devuelve ``True`` sólo si lo ha escrito.

    **Por qué hace falta además de la exclusión de `commit_task`.** Aquélla cierra
    el agujero para la plataforma; éste lo cierra para las personas. Quien clone
    el repo y trabaje fuera del sistema se come exactamente el mismo problema en
    su primer `git add -A`, y el PR del plan se lo comería con él.

    **CUÁNDO se llama es parte del diseño, no un detalle.** Sólo desde
    `commit_task`, sólo cuando ya va a haber commit y sólo si el proyecto no se
    queda vacío — ver :func:`_el_proyecto_tiene_contenido`. Escrito en la provisión, el
    fichero está en el workspace mientras corre el agente y devuelve a FALLA la
    casilla del ADR 0163: medido el 2026-09-01 con Composer 2.9.4,
    ``composer create-project codeigniter4/framework .`` sale con rc=1 «Project
    directory is not empty» con sólo ese dotfile delante, porque
    ``Filesystem::isDirEmpty()`` usa ``ignoreDotFiles(false)``.

    **Nunca sobrescribe.** Si hay `.gitignore` —aunque esté vacío, aunque sea peor
    que éste— es del proyecto y se respeta. Vaciarlo es, de hecho, la vía de
    escape documentada dentro del propio fichero.

    Best-effort: cualquier fallo se registra y devuelve ``False``. Un `.gitignore`
    que no se pudo escribir no puede tumbar el cierre de la tarea.
    """
    destino = worktree_path / ".gitignore"
    try:
        if destino.exists():
            return False
        nombres = _nombres_de_dependencias()
    except (OSError, ValueError) as exc:
        _log.warning(
            "commit_task.base_gitignore_skipped", worktree=str(worktree_path), error=str(exc)[:300]
        )
        return False
    if not nombres:
        return False
    cuerpo = _CABECERA_GITIGNORE_BASE + "".join(f"{nombre}/\n" for nombre in nombres)
    try:
        # `newline="\n"`: en Windows el default traduciría a CRLF y el fichero
        # viajaría al PR con finales de línea que nadie pidió.
        destino.write_text(cuerpo, encoding="utf-8", newline="\n")
    except OSError as exc:
        _log.warning(
            "commit_task.base_gitignore_failed", worktree=str(worktree_path), error=str(exc)[:300]
        )
        return False
    _log.info(
        "commit_task.base_gitignore_written",
        worktree=str(worktree_path),
        dirs=list(nombres),
        note="el proyecto no traía `.gitignore`; se deja uno base, editable",
    )
    return True


def _el_proyecto_tiene_contenido(worktree_path: Path) -> bool:
    """¿Queda algo en el índice? (llamar DESPUÉS del ``git add``).

    Tiene función propia porque la respuesta ``False`` es la que decide si se
    escribe el `.gitignore` base, y la razón no se ve venir: una tarea cuyo único
    cambio es el des-versionado de `vendor/` sobre una rama que no tenía nada más
    deja el árbol VACÍO. Añadirle el `.gitignore` lo convertiría en «un fichero»,
    y la tarea siguiente —la del esqueleto, en el plan del incidente— se
    estrellaría contra `isDirEmpty()` exactamente igual que si lo hubiéramos
    escrito en la provisión. El fallo diferido una tarea sigue siendo el fallo:
    si el proyecto está vacío tiene que SEGUIR vacío al empezar la tarea
    siguiente (ADR 0163).

    Y ahí está la diferencia con el des-versionado, que SÍ produce commit él solo
    (:func:`_desversionar_dependencias`): aquél RETIRA algo que atasca la rama y
    tiene que llegar al bare para desatascarla; éste AÑADE, y un repositorio cuyo
    único contenido fuera este fichero no gana nada con él.
    """
    try:
        salida = _run_git("ls-files", "-z", cwd=worktree_path)
    except GitCommandError:
        return False
    return any(ruta for ruta in salida.split("\0") if ruta)


def _hay_cambios_estagiados(worktree_path: Path) -> bool:
    """¿Va a haber commit de todas formas? (llamar DESPUÉS del ``git add``)."""
    try:
        return bool(_run_git("diff", "--cached", "--name-only", "HEAD", cwd=worktree_path).strip())
    except GitCommandError:
        # HEAD sin nacer (repo sin ningún commit): todo lo que hay en el índice
        # es nuevo, así que «hay cambios» equivale a «hay contenido».
        return _el_proyecto_tiene_contenido(worktree_path)


def commit_task(
    worktree_path: Path,
    *,
    message: str,
    trailers: CommitTrailers,
    author_name: str = PLATFORM_GIT_NAME,
    author_email: str = PLATFORM_GIT_EMAIL,
) -> str:
    """Stage everything, commit with trailers, return the new sha.

    Pre-conditions: the agent has already written its changes into
    the worktree. We always ``git add -A`` first because the agent's
    file-tools don't guarantee a clean staging area.

    Returns the new commit sha. Raises :class:`GitCommandError` if
    the working tree was clean (nothing to commit) — the caller
    treats that as "the task produced no code change" and skips the
    push.

    **Un des-versionado de dependencias cuenta como cambio.** Si esta llamada
    saca `vendor/` del índice y la tarea no tocó nada más, el árbol NO está
    limpio y aquí se produce un commit. Es deliberado: tratarlo como «limpio»
    dejaría el des-versionado sin llegar al bare, `sync_to_head` volvería a traer
    el artefacto en la tarea siguiente y el punto muerto seguiría idéntico. Ver
    :func:`_desversionar_dependencias`.

    **Un `.gitignore` base, en cambio, NUNCA es la única razón de un commit.**
    Éste es además el único punto del ciclo donde se puede escribir sin que se lo
    encuentre el agente (ADR 0163). Ver :func:`ensure_base_gitignore` y
    :func:`_el_proyecto_tiene_contenido`.
    """
    # ANTES de cualquier git sobre el worktree: un puntero roto hace que el
    # siguiente comando dispare `worktree prune` y con él la posibilidad de
    # reparar. Ver `repair_worktree_link`.
    repair_worktree_link(worktree_path)

    try:
        nombres_dependencias = _nombres_de_dependencias()
    except ValueError as exc:  # catálogo con un nombre que no se puede usar
        _log.error("commit_task.dependency_names_invalid", error=str(exc))
        nombres_dependencias = ()
    # El des-versionado va ANTES del `git add`: en ese punto el índice todavía
    # coincide con HEAD para esas rutas, y la exclusión de abajo impide que el
    # `add` las vuelva a meter.
    _desversionar_dependencias(worktree_path, nombres_dependencias)

    env_extra = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    # `-A` con exclusión: el residuo de las tools de fichero NO entra en la rama.
    #
    # Las tres tools destructivas de la familia `file` dejaron de destruir en su
    # sitio (ADR 0164): apartan con un renombrado, y descartan después. Cuando el
    # descarte no se puede —el mismo EACCES que motivaba el cambio— queda un
    # hermano `.agent-runtime-tmp.<nombre>.<n>` en el workspace. Sin esta
    # exclusión, `git add -A` lo commitea en la rama del plan y viaja al PR: el
    # deliverable acaba con una copia oculta del árbol que se quiso retirar, con
    # un nombre que no significa nada para quien lo revise.
    #
    # Dos patrones, y los dos hacen falta:
    #
    #   * el `**/` inicial porque el residuo aparece AL LADO de su objetivo, que
    #     puede estar a cualquier profundidad
    #     (`app/Config/.agent-runtime-tmp.cache.0`). Uno anclado a la raíz sólo
    #     cazaría el caso fácil.
    #   * el `/**` final porque lo apartado suele ser un DIRECTORIO: el primer
    #     patrón excluye su nombre, pero `git add` desciende igual y mete lo de
    #     dentro con rutas que ya no casan. Medido: sin esta segunda línea
    #     `.agent-runtime-tmp.vendor.0/paquete/autoload.php` entraba en el commit.
    #
    # A la misma exclusión se suman los DIRECTORIOS DE DEPENDENCIAS del catálogo
    # (`vendor/`, `node_modules/`, `.venv/`…). Sin ellos, una tarea que corre
    # `composer install` para enseñarle una prueba al reviewer se lleva el árbol
    # entero a la rama del plan — 1.151 ficheros medidos el 2026-09-01— y la deja
    # atascada. La forma de los patrones y por qué aquí sólo se excluye el
    # CONTENIDO (a diferencia del residuo) está en `_patrones_de_dependencias`.
    exclusiones = [
        f":(exclude,glob)**/{_PREFIJO_TRANSITORIO_RUNTIME}*",
        f":(exclude,glob)**/{_PREFIJO_TRANSITORIO_RUNTIME}*/**",
        *(f":(exclude,glob){p}" for p in _patrones_de_dependencias(nombres_dependencias)),
    ]
    _run_git("add", "-A", "--", ".", *exclusiones, cwd=worktree_path, env_extra=env_extra)

    # «Sin cambio» se decide MIRANDO EL ÍNDICE, no leyendo la prosa de git.
    #
    # Medido el 2026-09-01, y es consecuencia directa de la exclusión de arriba:
    # una tarea que corre `composer install` y no entrega nada más deja `vendor/`
    # SIN VERSIONAR en el árbol, y entonces git no dice «nothing to commit» sino
    # «nothing added to commit but untracked files present». Esa cadena no casa
    # con el `if "nothing to commit"` de abajo NI con el `if "clean"` del llamador
    # (`execution._commit_and_push_worktree`), así que el run moría con un error
    # de git donde antes —cuando el `add -A` se llevaba `vendor/`— había un
    # commit. Índice contra HEAD responde lo mismo sin depender de la redacción.
    if not _hay_cambios_estagiados(worktree_path):
        raise GitCommandError("commit_task: worktree is clean")

    # Y AQUÍ el `.gitignore` base: es el único punto del ciclo donde escribirlo no
    # se lo encuentra el agente (ADR 0163) y llega igual al repositorio, que es
    # para lo que se quería. Va después del `add` porque necesita saber si el
    # índice tiene contenido, y detrás de la guarda de arriba porque un
    # `.gitignore` no puede ser la única razón de un commit: eso fabricaría uno
    # donde el llamador espera «la tarea no produjo cambio de código». Ver
    # `ensure_base_gitignore` y `_el_proyecto_tiene_contenido`.
    if _el_proyecto_tiene_contenido(worktree_path) and ensure_base_gitignore(worktree_path):
        _run_git("add", "--", ".gitignore", cwd=worktree_path, env_extra=env_extra)
    try:
        _run_git(
            "commit",
            "-m",
            message,
            *trailers.as_args(),
            cwd=worktree_path,
            env_extra=env_extra,
        )
    except GitCommandError as exc:
        if "nothing to commit" in str(exc).lower():
            raise GitCommandError("commit_task: worktree is clean") from exc
        raise
    return _run_git("rev-parse", "HEAD", cwd=worktree_path).strip()


# ---------------------------------------------------------------------------
# task_06_23..06_25 — Workflow class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanGitPolicies:
    """The three orthogonal policies that drive the Git side of a plan.

    Defaults are the Plan 06 section 12.6.8 "razonables" combination:
    incremental + human_required + branch_only_pr_required — the rama
    is visible on the remote from the first task, the human validates
    at end-of-plan, and the system opens a PR for the human to merge.
    """

    branch_push_mode: BranchPushMode = "incremental"
    plan_validation_mode: PlanValidationMode = "human_required"
    push_policy: PushPolicy = "branch_only_pr_required"


# Type alias for the PR-opener seam. The worker injects a real
# ``gh pr create`` runner or a GitHub/AzureDevOps API client; tests
# inject a fake that records calls.
PrOpener = Any  # Callable[[str, str], str] returning the PR URL


@dataclass(frozen=True)
class PrInfo:
    """Result of :meth:`PlanGitWorkflow.open_plan_pr` per repo."""

    repo_name: str
    branch: str
    url: str | None
    skipped_reason: str | None = None


class PlanGitWorkflow:
    """The git side of a plan's life cycle.

    The orchestrator instantiates one per plan and calls these
    methods at the right moments. The class doesn't own state across
    method calls — it's a thin coordinator around
    :func:`workers.git_repos._run_git` + the injected PR opener.
    """

    def __init__(
        self,
        *,
        bare_repo_path: Path,
        plan_branch: str,
        policies: PlanGitPolicies,
        pr_opener: PrOpener | None = None,
        auth_env: dict[str, str] | None = None,
        base_branch: str | None = None,
    ) -> None:
        self._bare_path = bare_repo_path
        self._plan_branch = plan_branch
        self._policies = policies
        self._pr_opener = pr_opener
        # ADR 0072: env de auth git (GIT_ASKPASS/GIT_SSH_COMMAND) para el push al
        # remoto. None = remoto local o ya autenticable por el host.
        self._auth_env = auth_env
        # Rama base del PR (default_branch del git config). Cuando está
        # presente, open_plan_pr verifica ANTES de llamar a la API que la base
        # remota comparte historia con la rama del plan — el 422 «no history
        # in common» del proveedor deja de ser el primer aviso.
        self._base_branch = base_branch

    @property
    def plan_branch(self) -> str:
        return self._plan_branch

    def _base_ancestry_guard(self) -> str | None:
        """Motivo accionable para NO llamar a la API del proveedor, o ``None``.

        Re-fetch de la rama base + ``merge-base`` contra la rama del plan.
        Best-effort: un fallo transitorio del fetch (red/credenciales) NO
        bloquea el intento de PR (contrato anterior); solo bloquean los dos
        casos deterministas — base ausente en el remoto e historias sin
        ancestro común (el caso api-ci: base local sembrada sintética).
        """
        base = self._base_branch
        if not base:
            return None
        try:
            _run_git("fetch", "origin", base, cwd=self._bare_path, env_extra=self._auth_env)
        except GitCommandError as exc:
            if "couldn't find remote ref" in str(exc).lower():
                return (
                    f"el remoto no tiene la rama base '{base}': haz un push inicial de esa "
                    "rama o corrige default_branch en el git del proyecto"
                )
            _log.warning("plan_pr.base_fetch_failed", base=base, error=str(exc))
            return None  # transitorio → no bloquear el intento
        try:
            _run_git("merge-base", "FETCH_HEAD", self._plan_branch, cwd=self._bare_path)
        except GitCommandError:
            return (
                f"la rama base '{base}' del remoto no comparte historia con la rama del plan "
                "(la base local se sembró sin el contenido del remoto): re-sincroniza el git "
                "del proyecto y rebasa la rama del plan, o ajusta default_branch"
            )
        return None

    # ----- task_06_23 — transitions ------------------------------------

    def push_review_to_bare(self, worktree_path: Path) -> str:
        """worktree → bare, reconciling concurrent sibling commits.

        Pushes the worktree's HEAD to the plan branch on the bare repo. Several
        sibling tasks of the same plan share ONE plan branch, so a plain push
        fails *non-fast-forward* whenever another task pushed first — which used
        to surface as ``commit_failed`` and blocked the task. We rebase this
        task's commit onto the branch's current tip and retry (optimistic
        concurrency). A genuine rebase CONFLICT (two tasks changed the same
        lines) is NOT a transient race — it is re-raised as a
        :class:`GitCommandError` so the caller escalates it for resolution.

        Returns the sha now on the bare's branch tip.
        """
        # The rebase replays our commit; give git an identity for the new
        # committer (the worktree carries no user.name/email config). Single
        # source (causa raíz A) — la misma con la que se firmó el commit.
        committer_env = git_identity_env()
        last_exc: GitCommandError | None = None
        for _ in range(_PUSH_RECONCILE_RETRIES):
            try:
                _run_git(
                    "push",
                    str(self._bare_path),
                    f"HEAD:refs/heads/{self._plan_branch}",
                    cwd=worktree_path,
                )
                return _run_git(
                    "rev-parse", f"refs/heads/{self._plan_branch}", cwd=self._bare_path
                ).strip()
            except GitCommandError as exc:
                if not _is_non_fast_forward(exc):
                    raise
                last_exc = exc
                # Reconcile: replay our commit on top of the branch's current tip.
                _run_git("fetch", str(self._bare_path), self._plan_branch, cwd=worktree_path)
                try:
                    _run_git("rebase", "FETCH_HEAD", cwd=worktree_path, env_extra=committer_env)
                except GitCommandError as rebase_exc:
                    # Anticipo ADR 0099: capturar el contexto ESTRUCTURADO del
                    # conflicto (ficheros en disputa + shas de ambos lados)
                    # ANTES del abort — despues ya no existe. Best-effort: si
                    # git falla aqui, el contexto queda parcial pero el error
                    # original se propaga igual.
                    context: dict[str, Any] = {"plan_branch": self._plan_branch}
                    with contextlib.suppress(GitCommandError):
                        context["files"] = [
                            line.strip()
                            for line in _run_git(
                                "diff", "--name-only", "--diff-filter=U", cwd=worktree_path
                            ).splitlines()
                            if line.strip()
                        ][:50]
                    with contextlib.suppress(GitCommandError):
                        context["worktree_sha"] = _run_git(
                            "rev-parse", "HEAD", cwd=worktree_path
                        ).strip()
                    with contextlib.suppress(GitCommandError):
                        context["branch_sha"] = _run_git(
                            "rev-parse", "FETCH_HEAD", cwd=worktree_path
                        ).strip()
                    with contextlib.suppress(GitCommandError):
                        _run_git("rebase", "--abort", cwd=worktree_path)
                    conflict_error = GitCommandError(
                        f"push_review_to_bare: rebase onto {self._plan_branch} conflicted "
                        f"(another task changed the same lines): {rebase_exc}"
                    )
                    conflict_error.conflict_context = context  # type: ignore[attr-defined]
                    raise conflict_error from rebase_exc
        # Exhausted retries — persistent contention; surface the last push error.
        assert last_exc is not None  # the loop only exits here via a non-ff push
        raise last_exc

    def push_branch_to_remote(self, *, force: bool = False) -> bool:
        """bare → remote. Gated by ``branch_push_mode``.

        ``incremental`` pushes every time the bare's branch advances
        (one push per accepted task). ``final_only`` skips here and
        is invoked once at plan close. Returns True iff a push
        actually happened.

        ``force`` bypasses the ``branch_push_mode`` gate (it is NOT
        ``git push --force``): the plan close pushes the tip whatever
        the mode, so the PR cannot target an incomplete branch.
        """
        if self._policies.branch_push_mode == "final_only" and not force:
            return False
        # If the bare has no ``origin``, treat that as "local-only
        # project" — the user gets the rama in the bare and no
        # remote step.
        if not self._has_origin():
            return False
        _run_git(
            "push",
            "origin",
            f"refs/heads/{self._plan_branch}:refs/heads/{self._plan_branch}",
            cwd=self._bare_path,
            env_extra=self._auth_env,  # ADR 0072: auth (PAT/SSH) si la hay
        )
        return True

    def _has_origin(self) -> bool:
        try:
            _run_git("remote", "get-url", "origin", cwd=self._bare_path)
        except GitCommandError:
            return False
        return True

    # ----- task_06_24 — PR creation ------------------------------------

    def open_plan_pr(self, *, title: str, body: str) -> PrInfo:
        """Open one PR for this repo at plan close.

        Skipped (with a reason) when:
          * ``push_policy='forbidden'`` — the project never pushes.
          * No ``origin`` remote — local-only project.
          * The branch could not be pushed to the remote (see below).
          * No ``pr_opener`` injected — dev/test without GitHub creds.

        Otherwise calls the injected opener and returns the URL it
        produced. The actual ``gh pr create`` machinery lives in the
        platform's wiring; here we just dispatch.
        """
        # `forbidden` means «this project never pushes», so it is checked FIRST:
        # the final_only push used to run before this gate and mirrored the branch
        # to the remote of a project configured never to push (audit 2026-07-03, P4).
        if self._policies.push_policy == "forbidden":
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason="push_policy=forbidden",
            )
        if not self._has_origin():
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason="no remote origin configured",
            )
        # The remote must hold the branch TIP before the PR is opened, whatever the
        # `branch_push_mode` (residual P3, audit 2026-07-03): `final_only` never
        # pushed until now, and under `incremental` (the DEFAULT) the closure-docs
        # commit —written to the bare moments earlier so the PR carries its own
        # changelog— stayed local, as did any task commit whose best-effort
        # incremental push was skipped or failed. This is the one place that can
        # guarantee «the PR points at the branch that holds the commits». Idempotent:
        # a remote already at the tip makes it a no-op.
        try:
            self.push_branch_to_remote(force=True)
        except GitCommandError as exc:
            # A rejected push (diverged remote branch) must NOT degrade into a PR
            # against an INCOMPLETE remote branch — that silent-loss shape is what
            # this whole chain exists to prevent. Report it actionably instead.
            reason = (
                f"no se pudo empujar la rama '{self._plan_branch}' al remoto "
                f"(el PR habría apuntado a una rama incompleta): {exc}"
            )
            _log.warning(
                "plan_pr.branch_push_failed",
                repo=self._bare_path.stem,
                branch=self._plan_branch,
                error=str(exc),
            )
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason=reason,
            )
        if self._pr_opener is None:
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason="no pr_opener wired",
            )

        guard = self._base_ancestry_guard()
        if guard is not None:
            _log.warning(
                "plan_pr.base_guard_skip",
                repo=self._bare_path.stem,
                branch=self._plan_branch,
                reason=guard,
            )
            return PrInfo(
                repo_name=self._bare_path.stem,
                branch=self._plan_branch,
                url=None,
                skipped_reason=guard,
            )

        url = self._pr_opener(title, body)
        _log.info(
            "plan_pr.opened",
            repo=self._bare_path.stem,
            branch=self._plan_branch,
            url=url,
        )
        return PrInfo(
            repo_name=self._bare_path.stem,
            branch=self._plan_branch,
            url=url,
        )

    # ----- task_06_25 — push policy at merge time ----------------------

    def apply_push_policy(self, *, default_branch: str = "main") -> str:
        """Apply ``push_policy`` to the merge step. Returns the action
        actually taken: ``"forbidden"``, ``"pr_required"``, or
        ``"merged_to_default"``.

        * ``forbidden`` — does nothing, returns ``"forbidden"``. The
          plan branch lives on the bare (and possibly the remote)
          forever.
        * ``branch_only_pr_required`` — leaves the PR open for the
          human; returns ``"pr_required"``.
        * ``direct_to_default_allowed`` — fast-forwards the bare's
          default branch to the plan branch's tip. Returns
          ``"merged_to_default"``. The remote push of the new default
          tip is a separate concern (whoever owns CI handles it).
        """
        if self._policies.push_policy == "forbidden":
            return "forbidden"
        if self._policies.push_policy == "branch_only_pr_required":
            return "pr_required"
        # direct_to_default_allowed → fast-forward the bare's default.
        _run_git(
            "update-ref",
            f"refs/heads/{default_branch}",
            f"refs/heads/{self._plan_branch}",
            cwd=self._bare_path,
        )
        _log.info(
            "plan_git.merged_to_default",
            default=default_branch,
            plan_branch=self._plan_branch,
        )
        return "merged_to_default"


__all__ = [
    "BranchPushMode",
    "CommitTrailers",
    "PlanGitPolicies",
    "PlanGitWorkflow",
    "PlanValidationMode",
    "PrInfo",
    "PrOpener",
    "PushPolicy",
    "commit_task",
    "ensure_base_gitignore",
    "make_plan_branch_name",
]
