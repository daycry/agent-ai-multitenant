"""El spec y el token interno no viven en el env del proceso del agente
(`task_cv_20`, D-01).

Auditoría 2026-09-01. `shell_exec` heredaba el env COMPLETO del runtime
(`subprocess.run` sin `env=`), así que `AGENT_TASK_SPEC` (cabeceras de MCP,
`approved_actions`, código de python_function) y `AGENTIC_INTERNAL_TOKEN` (que
autoriza `mcp-oauth-token`) eran legibles por el modelo con un `env` o por
cualquier inyección. Dos capas:

  1. el hijo de `shell_exec` recibe un env mínimo explícito (PATH, HOME, LANG…);
  2. el worker deja el spec y el token en `/run/secrets` y en el env sólo viajan
     los PUNTEROS (`AGENT_TASK_SPEC_FILE`, `AGENTIC_INTERNAL_TOKEN_FILE`); el
     boot los lee UNA vez y los retira de `os.environ`, así que ni siquiera un
     worker antiguo que los mande en línea los deja al alcance de un hijo.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from agent_runtime import __main__ as boot
from agent_runtime.shell_exec import ShellExecTool

_PROBE = (
    "import os,sys;"
    "sys.exit(int('AGENTIC_INTERNAL_TOKEN' in os.environ or 'AGENT_TASK_SPEC' in os.environ))"
)


def test_shell_exec_children_do_not_inherit_the_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTIC_INTERNAL_TOKEN", "tok-secreto")
    monkeypatch.setenv("AGENT_TASK_SPEC", json.dumps({"task": {"title": "t"}}))
    # el intérprete del venv por delante en PATH: `python` a secas lo resuelve
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    tool = ShellExecTool(allowed_commands=frozenset({"python"}), workspace=str(tmp_path))

    result = tool({"command": f'python -c "{_PROBE}"'})

    assert result.ok is True, result.error
    assert "exit code 1" not in str(result.output) and "exit_code" not in str(result.error)


def test_the_spec_is_read_from_its_file_and_scrubbed_from_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec_file = tmp_path / "task-spec.json"
    spec_file.write_text(json.dumps({"task": {"title": "desde fichero"}}), encoding="utf-8")
    monkeypatch.setenv("AGENT_TASK_SPEC_FILE", str(spec_file))
    monkeypatch.setenv("AGENT_TASK_SPEC", json.dumps({"task": {"title": "en línea (viejo)"}}))

    spec = boot._load_spec()

    assert spec is not None and spec["task"]["title"] == "desde fichero"
    assert "AGENT_TASK_SPEC" not in os.environ
    assert "AGENT_TASK_SPEC_FILE" not in os.environ


def test_an_inline_spec_from_an_old_worker_still_works_and_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_TASK_SPEC_FILE", raising=False)
    monkeypatch.setenv("AGENT_TASK_SPEC", json.dumps({"task": {"title": "en línea"}}))

    spec = boot._load_spec()

    assert spec is not None and spec["task"]["title"] == "en línea"
    assert "AGENT_TASK_SPEC" not in os.environ


def _sin_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """El cliente comprueba el api-server al construirse; aquí no hay red."""
    from agent_runtime.internal_api import InternalAgentAPI

    monkeypatch.setattr(InternalAgentAPI, "ensure_reachable", lambda _self: None)


def test_the_internal_token_is_read_once_from_its_file_and_scrubbed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "internal-token"
    token_file.write_text("tok-from-file\n", encoding="utf-8")
    monkeypatch.setenv("AGENTIC_INTERNAL_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("AGENTIC_INTERNAL_TOKEN", raising=False)
    _sin_red(monkeypatch)
    boot._forget_boot_secrets()

    api = boot._build_internal_api()

    assert api is not None and api.bearer_token == "tok-from-file"
    assert "AGENTIC_INTERNAL_TOKEN" not in os.environ
    assert "AGENTIC_INTERNAL_TOKEN_FILE" not in os.environ
    # segunda construcción (el boot la pide varias veces): sigue funcionando sin env
    again = boot._build_internal_api()
    assert again is not None and again.bearer_token == "tok-from-file"
    boot._forget_boot_secrets()


def test_an_inline_token_from_an_old_worker_is_scrubbed_after_the_first_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENTIC_INTERNAL_TOKEN_FILE", raising=False)
    monkeypatch.setenv("AGENTIC_INTERNAL_TOKEN", "tok-inline")
    _sin_red(monkeypatch)
    boot._forget_boot_secrets()

    api = boot._build_internal_api()

    assert api is not None and api.bearer_token == "tok-inline"
    assert "AGENTIC_INTERNAL_TOKEN" not in os.environ
    boot._forget_boot_secrets()
