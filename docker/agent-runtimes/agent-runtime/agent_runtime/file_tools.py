"""The file_read / file_write / file_list builtin tools (task_02_16).

Every path is resolved relative to the workspace root and must stay
inside it — an absolute path or a `../` traversal that escapes the
workspace is rejected before any filesystem access. The agent only ever
sees /workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_runtime.tools import ToolResult

# Cap on a single file_read so a huge file cannot blow up the steps_log.
_MAX_READ_BYTES = 1_000_000

# The Claude Code CLI drops its own state (.claude.json ~25KB, .claude/) into the
# working dir. Hide it from listings so the agent never wastes a turn reading CLI
# config into its context — it is not part of the task's workspace.
_CLI_ARTIFACTS = frozenset({".claude", ".claude.json"})


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
        resolved = self._safe_path(args.get("path"))
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

    def file_delete(self, args: dict[str, object]) -> ToolResult:
        """Remove a single file under the workspace (R6 / ADR 0089).

        The agent needs this to reconcile a deliverable when an earlier run
        left a stale or duplicate file in the (persistent) worktree — `rm` /
        `git rm` are gated by the shell allowlist and there was no delete tool,
        so competing implementations could never be cleaned up. Path-jailed like
        every file tool; refuses a directory so a stray `path` cannot wipe a
        subtree.
        """
        resolved = self._safe_path(args.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        if resolved.is_dir():
            return ToolResult(ok=False, error=f"is a directory, not a file: {args.get('path')}")
        if not resolved.exists():
            return ToolResult(ok=False, error=f"not a file: {args.get('path')}")
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
