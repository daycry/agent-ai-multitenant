"""The file_read / file_write / file_list builtin tools (task_02_16).

Every path is resolved relative to the workspace root and must stay
inside it — an absolute path or a `../` traversal that escapes the
workspace is rejected before any filesystem access. The agent only ever
sees /workspace.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.tools import ToolResult

# Cap on a single file_read so a huge file cannot blow up the steps_log.
_MAX_READ_BYTES = 1_000_000

# The Claude Code CLI drops its own state (.claude.json ~25KB, .claude/) into the
# working dir. Hide it from listings so the agent never wastes a turn reading CLI
# config into its context — it is not part of the task's workspace.
_CLI_ARTIFACTS = frozenset({".claude", ".claude.json"})


#: El enlace del worktree con su rama. Ver :meth:`_mutable_path`.
_GIT_DIR = ".git"


@dataclass
class WorkspaceFiles:
    """File tools confined to one workspace directory."""

    root: str = "/workspace"

    def _safe_path(self, raw: object) -> Path | ToolResult:
        """Resolve `raw` under the workspace root, or a failed ToolResult.

        An absolute path or a traversal escaping the root is rejected —
        `Path(root) / raw` followed by `resolve()` collapses any `..`,
        and the result must still sit under (or be) the root.
        """
        if not isinstance(raw, str) or not raw.strip():
            return ToolResult(ok=False, error="a non-empty 'path' is required")
        root = Path(self.root).resolve()
        candidate = (root / raw).resolve()
        if candidate != root and root not in candidate.parents:
            return ToolResult(ok=False, error=f"path escapes the workspace: {raw}")
        return candidate

    def _mutable_path(self, raw: object) -> Path | ToolResult:
        """Como :meth:`_safe_path`, pero además prohíbe tocar ``.git``.

        Medido en vivo el 2026-08-31: un agente que intentaba
        ``composer create-project codeigniter4/framework .`` —que exige
        directorio vacío— borró ``.git`` para quitarlo de en medio. Instaló el
        framework correctamente y `php spark routes` respondió, pero al cerrar la
        tarea ``git add -A`` salió con «fatal: not a git repository» y el
        deliverable se perdió: hecho y no entregable.

        Desde el lado del agente eso NO es un error. Es un fichero que estorba, y
        nada le dice que sostiene los principios 4 y 5 del sistema (worktree por
        tarea; plan = rama con trailers). La guarda tiene que estar aquí, no en
        el prompt: un prompt se puede ignorar bajo presión de una herramienta que
        insiste en un directorio vacío.

        Por qué la protección que había no lo cubría, que es lo que hay que
        recordar: ``file_delete`` ya rechaza directorios «so a stray path cannot
        wipe a subtree», y en un clon normal eso basta porque ``.git`` ES un
        directorio. En un WORKTREE es un FICHERO con un puntero ``gitdir:``, así
        que la guarda dejaba de aplicar justo donde vive el modelo del sistema.
        """
        resolved = self._safe_path(raw)
        if isinstance(resolved, ToolResult):
            return resolved
        root = Path(self.root).resolve()
        try:
            partes = resolved.relative_to(root).parts
        except ValueError:  # pragma: no cover - _safe_path ya lo garantiza
            return ToolResult(ok=False, error=f"path escapes the workspace: {raw}")
        if _GIT_DIR in partes:
            return ToolResult(
                ok=False,
                error=(
                    "refusing to touch '.git': it links this worktree to the "
                    "plan branch, and without it your work cannot be committed "
                    "or pushed. If a command demands an empty directory, run it "
                    "in a subdirectory and move the result, or use its "
                    "--no-install / existing-directory mode."
                ),
            )
        return resolved

    def file_read(self, args: dict[str, object]) -> ToolResult:
        resolved = self._safe_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.is_file():
            return ToolResult(ok=False, error=f"not a file: {args.get('path')}")
        if resolved.stat().st_size > _MAX_READ_BYTES:
            return ToolResult(ok=False, error=f"file exceeds {_MAX_READ_BYTES} bytes")
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, output={"path": args.get("path"), "content": content})

    def file_write(self, args: dict[str, object]) -> ToolResult:
        resolved = self._mutable_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        content = args.get("content", "")
        if not isinstance(content, str):
            return ToolResult(ok=False, error="'content' must be a string")
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, output={"path": args.get("path"), "bytes_written": len(content)})

    def _delete_tree(self, resolved: Path, *, raw: object, recursive: bool) -> ToolResult:
        """La rama de DIRECTORIO de :meth:`file_delete`, aparte por legibilidad.

        Separada no por longitud sino porque son dos operaciones distintas con
        guardas distintas: borrar un fichero no puede llevarse nada por delante,
        y borrar un árbol sí.
        """
        if not recursive:
            return ToolResult(
                ok=False,
                error=(
                    f"is a directory, not a file: {raw}. "
                    "Pass recursive=true to remove it with everything inside."
                ),
            )
        if resolved == Path(self.root).resolve():
            return ToolResult(
                ok=False,
                error=(
                    "refusing to empty the workspace root: that removes the whole "
                    "deliverable, not a subtree. Delete the specific paths you mean "
                    "instead."
                ),
            )
        # Se cuenta ANTES de borrar: después no hay nada que contar, y el número
        # es lo que hace legible la entrada del `steps_log`.
        entradas = sum(1 for _ in resolved.rglob("*"))
        try:
            shutil.rmtree(resolved)
        except OSError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, output={"path": raw, "deleted": True, "entries": entradas})

    def file_delete(self, args: dict[str, object]) -> ToolResult:
        """Remove a file — or, with ``recursive``, a directory tree.

        El caso original (R6 / ADR 0089): reconciliar el deliverable cuando un
        intento anterior dejó un fichero rancio o duplicado en el worktree, que
        persiste entre runs. Sin esta tool no había forma de limpiarlo (`rm` y
        `git rm` los gatea el allowlist del proyecto) y las implementaciones en
        competencia nunca convergían.

        **``recursive`` (2026-08-31).** Faltaba el caso del DIRECTORIO, y no es
        raro: reinstalar dependencias pide borrar ``vendor/`` o
        ``node_modules/``, y un módulo mal andamiado se retira entero. Fichero a
        fichero eso son miles de llamadas — inviable, así que el agente acababa
        intentando ``shell_exec("rm -rf ...")``, que rebota contra el allowlist.
        Medido en vivo el 2026-08-31, en el run que instaló CodeIgniter.

        Por qué aquí y no abriendo ``rm`` en el allowlist, que era la otra
        salida:

        * ``shell_exec`` es la puerta equivocada del ADR 0162 — comparte lista
          con ``stack_exec``, así que un ``rm`` autorizado ahí confunde sobre
          qué corre dónde;
        * ``rm -rf ./*`` es ilimitado por naturaleza. Esto mantiene la jaula de
          ruta y sigue rechazando ``.git``;
        * queda AUDITADO: el ``steps_log`` guarda qué ruta se borró y cuántas
          entradas se llevó. Un ``rm`` por shell sólo dice que hubo un ``rm``;
        * el runtime ya lo gatea como ``code_changes``, así que la política de
          aprobación humana del proyecto se aplica sola.

        Lo que sigue sin poder hacerse, a propósito: **vaciar la raíz del
        workspace**. Es la única operación cuyo resultado no es «un árbol menos»
        sino «el deliverable entero», y ninguna necesidad legítima la pide —
        para andamiar sobre un directorio limpio está el ADR 0163, que quita de
        en medio lo único que estorbaba.
        """
        recursive = bool(args.get("recursive", False))
        resolved = self._mutable_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.exists():
            return ToolResult(ok=False, error=f"not found: {args.get('path')}")

        if resolved.is_dir():
            return self._delete_tree(resolved, raw=args.get("path"), recursive=recursive)

        try:
            resolved.unlink()
        except OSError as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, output={"path": args.get("path"), "deleted": True})

    def file_list(self, args: dict[str, object]) -> ToolResult:
        resolved = self._safe_path(args.get("path", "."))
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.is_dir():
            return ToolResult(ok=False, error=f"not a directory: {args.get('path', '.')}")
        entries = [
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
            for child in sorted(resolved.iterdir())
            if child.name not in _CLI_ARTIFACTS
        ]
        return ToolResult(ok=True, output={"path": args.get("path", "."), "entries": entries})
