"""CLI gate: ``python -m api_server.docs_structure <repo_path>``.

A thin pre-merge / CI entrypoint around
:func:`api_server.docs_structure.validator.check_docs_structure`. Prints
a human-readable summary and exits ``0`` when the docs tree is canonical,
``1`` when it has structural violations — the exit-code contract a CI job
or a server-side pre-receive hook needs to block a malformed tree.

Usage::

    python -m api_server.docs_structure                 # validate CWD
    python -m api_server.docs_structure /path/to/repo   # validate a repo
"""

from __future__ import annotations

import sys
from pathlib import Path

from api_server.docs_structure.validator import check_docs_structure

#: Process exit code when the docs tree is canonical.
EXIT_OK = 0
#: Process exit code when at least one structural violation is found.
EXIT_VIOLATIONS = 1


def main(argv: list[str] | None = None) -> int:
    """Validate a repo's docs tree; return the process exit code.

    Args:
        argv: Argument list *excluding* the program name (defaults to
            ``sys.argv[1:]``). At most one positional arg — the repo
            path; defaults to the current working directory.

    Returns:
        :data:`EXIT_OK` when the structure is canonical, otherwise
        :data:`EXIT_VIOLATIONS`.
    """
    args = sys.argv[1:] if argv is None else argv
    repo_path = Path(args[0]) if args else Path.cwd()

    result = check_docs_structure(repo_path)
    # Summary goes to stdout on success, stderr on failure, so a CI log
    # surfaces the failure prominently.
    print(result.summary(), file=sys.stdout if result.ok else sys.stderr)
    return EXIT_OK if result.ok else EXIT_VIOLATIONS


if __name__ == "__main__":
    raise SystemExit(main())
