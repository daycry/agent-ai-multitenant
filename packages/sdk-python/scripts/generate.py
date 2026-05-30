"""Regenerate the Python SDK from the v1 OpenAPI spec (Plan 13 task_13_13).

Reproducibility entry point: run this whenever the public ``/api/v1``
contract changes. It does two things, in order, with NO running server
needed:

1. **Build the v1 OpenAPI 3.1 document in-process** by calling
   :func:`api_server.routers.api_v1.openapi.build_v1_openapi` and writing it
   to ``packages/sdk-python/openapi-v1.json``. That function is the SAME one
   the live ``/api/v1/openapi.json`` endpoint serves, so the committed spec
   the SDK is generated from is byte-for-byte the published contract.

2. **Generate the typed models** (``src/agentic_platform_sdk/models.py``)
   from that spec with ``datamodel-code-generator`` (Pydantic v2). We use
   datamodel-code-generator rather than ``openapi-python-client`` because its
   output is plain Pydantic v2 — the same modelling library the whole
   platform already uses — instead of an ``attrs``-based client with its own
   runtime deps and a code style that fights the repo's ruff-format/mypy.

The hand-written, thin ``httpx`` client (``client.py``) that wires those
models to the v1 endpoints is NOT regenerated — it is small, stable and
typed by hand against the generated models.

Usage (from the repo root, with the dev venv active)::

    python packages/sdk-python/scripts/generate.py

Requires ``datamodel-code-generator`` (dev dependency). The generated
``models.py`` + ``openapi-v1.json`` are committed; the
``packages/sdk-python/src/agentic_platform_sdk`` dir is EXCLUDED from the
repo's black/ruff/mypy hooks (see ``packages/sdk-python/README.md`` and the
root ``pyproject.toml`` excludes) because generated code follows the
generator's own style, not the repo's.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# packages/sdk-python/
_PKG_ROOT = Path(__file__).resolve().parents[1]
_SPEC_PATH = _PKG_ROOT / "openapi-v1.json"
_MODELS_PATH = _PKG_ROOT / "src" / "agentic_platform_sdk" / "models.py"
# apps/api-server/src must be importable to build the spec in-process.
_API_SERVER_SRC = _PKG_ROOT.parents[1] / "apps" / "api-server" / "src"


def write_spec() -> Path:
    """Build the v1 OpenAPI document in-process and write it to disk."""
    sys.path.insert(0, str(_API_SERVER_SRC))
    from api_server.routers.api_v1.openapi import build_v1_openapi

    spec = build_v1_openapi()
    # newline="\n" pins LF endings so regeneration is byte-stable on Windows
    # (avoids the repo's mixed-line-ending hook re-touching the file).
    _SPEC_PATH.write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote spec -> {_SPEC_PATH} (openapi {spec['openapi']}, {len(spec['paths'])} paths)")
    return _SPEC_PATH


def generate_models(spec_path: Path) -> None:
    """Run datamodel-code-generator over the spec to (re)write models.py."""
    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(spec_path),
        "--input-file-type",
        "openapi",
        "--output",
        str(_MODELS_PATH),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.12",
        "--use-standard-collections",
        "--use-union-operator",
        "--use-schema-description",
        "--field-constraints",
        "--disable-timestamp",
    ]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)  # - fixed argv, no shell
    # Normalize to LF: datamodel-code-generator writes the host's native line
    # endings (CRLF on Windows); pin LF so the committed file is byte-stable
    # across platforms and the mixed-line-ending hook never re-touches it.
    _MODELS_PATH.write_text(
        _MODELS_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    print(f"wrote models -> {_MODELS_PATH}")


def main() -> None:
    spec_path = write_spec()
    generate_models(spec_path)
    print("done. Generated SDK is committed; the package dir is linter-excluded (see README).")


if __name__ == "__main__":
    main()
