"""Directorios de dependencias: cuáles son, y cuáles son ACCIDENTES de la plataforma.

Un solo sitio para las tres preguntas que antes se contestaban en dos módulos
(`plan_git` y `tracked_paths`) y con criterios que divergían:

1. **Qué nombres cuentan como dependencia.** Los declara cada runtime template
   (`shared_test_runtimes.catalog.dependency_dirs`: ``vendor`` en php y ruby,
   ``node_modules`` en node, ``.venv``/``venv`` en python). Se usa la UNIÓN de los
   14 templates porque un worktree lleva varios stacks a la vez (monorepo con
   backend PHP y frontend node), igual que hace `sync_to_head(preserve=...)`.

2. **Cuáles de los que están VERSIONADOS son un accidente.** Aquí está la
   corrección de la auditoría del 2026-09-01. La primera versión des-versionaba
   TODO directorio con nombre de dependencia y lo justificaba con «lo declara el
   runtime template del propio proyecto»; era falso —es la unión— y se reprodujo
   con un proyecto Go que versiona ``vendor/`` a propósito (``go mod vendor``, el
   flujo canónico de Go; y ``go-test`` no declara ``vendor``): el primer commit
   de la plataforma lo sacaba del índice y dejaba de protegerlo. Le pasa igual a
   ``public/vendor/`` de Laravel, ``assets/vendor/`` de Symfony AssetMapper o
   ``vendor/cache`` de Bundler, que sus propios frameworks mandan commitear.

   El criterio que sí separa las dos poblaciones no es el NOMBRE sino la
   **AUTORÍA**: el accidente que motivó todo esto —1.151 ficheros de ``vendor/``
   en la rama de un plan— lo firmó ``commit_task`` con la identidad de la
   plataforma (`workers.git_identity`). Un ``vendor/`` que commiteó una persona
   es una decisión del proyecto. La plataforma deshace lo primero y respeta lo
   segundo; y basta que UNA persona haya tocado el directorio para que deje de
   ser un accidente, porque a partir de ahí hay trabajo humano dentro.

3. **Qué hacer en cada caso.** Lo decide quien llama: `plan_git.commit_task`
   des-versiona los accidentes y commitea los cambios de los respetados;
   `tracked_paths` resta los accidentes de la lista que protege al deliverable y
   deja los respetados dentro, protegidos como cualquier otro árbol del proyecto.

Best-effort en el sentido conservador: cuando git no puede contestar quién
versionó algo, se trata como RESPETADO. Pasarse cuesta que una tarea se queje de
no poder borrar ``vendor/``; quedarse corto costó 85 ficheros de ``app/`` el
2026-08-31 y, en el sentido contrario, habría borrado del PR las dependencias
vendorizadas de un proyecto Go.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from workers.git_identity import PLATFORM_GIT_EMAIL
from workers.git_repos import GitCommandError, _run_git

_log = structlog.get_logger("workers.dependency_dirs")


def nombres() -> tuple[str, ...]:
    """Los directorios de dependencias que declara el catálogo de runtimes.

    Import perezoso a propósito: la api-server importa `plan_git` para el visor
    de diffs (`api_server.code_diff`) y no tiene por qué arrastrar el catálogo de
    runtimes, cuya importación lee además el manifiesto de release.

    Los nombres se VALIDAN en vez de pasarse tal cual, igual que en
    `git_repos.clean_args`: hoy vienen del catálogo, pero entregar cadenas sin
    mirar a una línea de comandos de git es un agujero que no se deja abierto.
    """
    from shared_test_runtimes import catalog as runtime_catalog

    validos: list[str] = []
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
        validos.append(nombre)
    return tuple(validos)


def patrones(nombres_: Sequence[str]) -> list[str]:
    """``**/<nombre>/**`` — el CONTENIDO de cada directorio, a cualquier profundidad.

    Un solo sitio para el patrón, que se usa con dos magias de pathspec distintas:
    ``:(glob)`` para BUSCAR lo que ya está versionado y ``:(exclude,glob)`` para
    que el ``git add -A`` no lo meta. Escribirlo dos veces sería la forma de que
    una mitad dejara de casar con la otra sin que nada avisara.

    * El ``**/`` inicial porque un monorepo tiene ``frontend/node_modules/`` y
      ``backend/vendor/``.
    * Sólo el CONTENIDO (``/**``) y no el nombre a secas. Un directorio de
      dependencias sólo puede ser un directorio, y git no versiona directorios
      vacíos: excluir lo de dentro basta —comprobado, ``git add -A`` respeta el
      pathspec aunque el directorio esté entero sin trackear—. La diferencia
      importa porque un FICHERO llamado ``vendor`` (un script en ``bin/``) sí es
      deliverable, y un patrón sobre el nombre se lo llevaría por delante.
    """
    return [f"**/{nombre}/**" for nombre in nombres_]


def directorio_contenedor(ruta: str, conjunto: frozenset[str]) -> str:
    """``frontend/node_modules/react/index.js`` → ``frontend/node_modules``.

    El directorio de dependencias MÁS EXTERNO de la ruta: es la unidad sobre la
    que se decide (accidente o respetado) y la que se registra.
    """
    partes = ruta.split("/")
    for i, parte in enumerate(partes):
        if parte in conjunto:
            return "/".join(partes[: i + 1])
    return ruta  # inalcanzable: el pathspec ya garantizó el componente


@dataclass(frozen=True)
class Clasificacion:
    """Los directorios de dependencias VERSIONADOS en HEAD, ya juzgados.

    Las claves son rutas relativas POSIX del directorio de dependencias más
    externo (``vendor``, ``frontend/node_modules``); los valores, cuántos
    ficheros versionados cuelgan de él.
    """

    #: Los versionó SÓLO la plataforma: un `commit_task` sin `.gitignore`.
    accidentes: dict[str, int] = field(default_factory=dict)
    #: Los tocó al menos una persona: son del proyecto y no se retiran.
    respetados: dict[str, int] = field(default_factory=dict)

    @property
    def nombres_respetados(self) -> frozenset[str]:
        """Los NOMBRES (último componente) de los respetados, para el `.gitignore`."""
        return frozenset(Path(ruta).name for ruta in self.respetados)


def clasificar_versionados(worktree_path: Path, nombres_: Sequence[str]) -> Clasificacion:
    """Qué directorios de dependencias están en el índice, y de quién son.

    Una lectura del índice (``ls-files`` con los patrones) y una consulta de
    historia por directorio encontrado. En el caso normal —ningún directorio de
    dependencias versionado— es una sola llamada a git.
    """
    pathspecs = [f":(glob){patron}" for patron in patrones(nombres_)]
    if not pathspecs:
        return Clasificacion()
    try:
        salida = _run_git("ls-files", "-z", "--", *pathspecs, cwd=worktree_path)
    except GitCommandError as exc:
        _log.warning(
            "dependency_dirs.scan_failed", worktree=str(worktree_path), error=str(exc)[:300]
        )
        return Clasificacion()
    # `-z` y no líneas: sin él git devuelve los nombres no-ASCII C-quoted y el
    # conteo por directorio saldría mal justo en los repos en castellano.
    versionados = [ruta for ruta in salida.split("\0") if ruta]
    if not versionados:
        return Clasificacion()

    conjunto = frozenset(nombres_)
    por_directorio: dict[str, int] = {}
    for ruta in versionados:
        clave = directorio_contenedor(ruta, conjunto)
        por_directorio[clave] = por_directorio.get(clave, 0) + 1

    accidentes: dict[str, int] = {}
    respetados: dict[str, int] = {}
    for directorio, ficheros in sorted(por_directorio.items()):
        if _lo_versiono_solo_la_plataforma(worktree_path, directorio):
            accidentes[directorio] = ficheros
        else:
            respetados[directorio] = ficheros
    return Clasificacion(accidentes=accidentes, respetados=respetados)


def _lo_versiono_solo_la_plataforma(worktree_path: Path, directorio: str) -> bool:
    """¿Todos los commits que tocaron ``directorio`` los firmó la plataforma?

    Se mira el AUTOR (``%ae``) y no el committer: el rebase con el que
    `push_review_to_bare` reconcilia a las tareas hermanas reescribe el committer
    con la identidad de la plataforma y conserva el autor, así que el committer
    diría «plataforma» también sobre un commit que escribió una persona.

    Conservador ante la duda: si git no contesta, o no hay ningún commit (no
    debería ocurrir con ficheros versionados), se responde ``False`` y el
    directorio se RESPETA.
    """
    try:
        salida = _run_git("log", "--format=%ae", "--", f":(literal){directorio}", cwd=worktree_path)
    except GitCommandError as exc:
        _log.warning(
            "dependency_dirs.authorship_unknown",
            worktree=str(worktree_path),
            directory=directorio,
            error=str(exc)[:200],
            detail="se respeta: sin saber quién lo versionó no se retira",
        )
        return False
    autores = {linea.strip() for linea in salida.splitlines() if linea.strip()}
    return bool(autores) and autores <= {PLATFORM_GIT_EMAIL}


def pathspecs_literales(directorios: Iterable[str]) -> list[str]:
    """``:(literal)<dir>`` por directorio: sin comodines que interpretar."""
    return [f":(literal){directorio}" for directorio in directorios]


__all__ = [
    "Clasificacion",
    "clasificar_versionados",
    "directorio_contenedor",
    "nombres",
    "pathspecs_literales",
    "patrones",
]
