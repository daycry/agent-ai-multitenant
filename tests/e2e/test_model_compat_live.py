"""Prueba viva: el bucle del agente con un modelo real de Ollama (ADR 0169, `task_cm_09`).

Gated por ``MODEL_COMPAT_LIVE=1``. Sin la variable se salta, y el salto NO acredita
nada: esta prueba existe precisamente porque los tests unitarios de `compat-modelos`
usan dobles, y lo que el operador pidió es que el sistema funcione con modelos reales.

Entra por el MISMO camino que el contenedor del runtime: ``agent_runtime.__main__
.run_task`` con un spec como el que arma el worker, incluidos los esquemas de tools
que construye ``workers.agent_tool_schemas.build_model_tool_schemas``. No lanza runs
de la plataforma ni toca su BD: el workspace es un directorio temporal.

Variables:

* ``MODEL_COMPAT_OLLAMA_MODELS`` — modelos separados por comas (default ``gemma4:e4b``).
* ``MODEL_COMPAT_OLLAMA_BASE_URL`` — default ``http://127.0.0.1:11434/v1``.
* ``MODEL_COMPAT_OLLAMA_API_KEY`` — para Ollama cloud (``https://ollama.com/v1``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MODEL_COMPAT_LIVE") != "1",
    reason="MODEL_COMPAT_LIVE!=1: prueba viva de compatibilidad de modelos NO ejecutada",
)

_MODELS = [
    m.strip()
    for m in os.environ.get("MODEL_COMPAT_OLLAMA_MODELS", "gemma4:e4b").split(",")
    if m.strip()
]
_BASE_URL = os.environ.get("MODEL_COMPAT_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
_TOOLS = ["list_files", "read_file", "write_file"]
_EXPECTED = "hola desde el agente"


def _spec(model: str) -> dict[str, Any]:
    from workers.agent_tool_schemas import build_model_tool_schemas

    tool_specs = [{"name": n, "implementation_type": "builtin", "config": {}} for n in _TOOLS]
    model_spec: dict[str, Any] = {
        "kind": "ollama",
        "model": model,
        "base_url": _BASE_URL,
        "tools": build_model_tool_schemas(_TOOLS, tool_specs, include_system_tools=False),
        "max_output_tokens": 4096,
    }
    if os.environ.get("MODEL_COMPAT_OLLAMA_API_KEY"):
        model_spec["api_key"] = os.environ["MODEL_COMPAT_OLLAMA_API_KEY"]
    return {
        "task": {
            "id": f"compat-{model}",
            "title": "Crear saludo.txt",
            "description": (
                "Crea en el directorio de trabajo un fichero llamado saludo.txt cuyo "
                f"contenido sea exactamente esta línea: {_EXPECTED}\n"
                "Cuando esté escrito, termina con submit_result."
            ),
            "acceptance_criteria": [f"saludo.txt existe y contiene «{_EXPECTED}»."],
        },
        "model": model_spec,
        "allowed_tools": _TOOLS,
        "tool_specs": tool_specs,
        "budgets": {"max_iterations": 12},
    }


def _events(out: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


@pytest.mark.parametrize("model", _MODELS)
def test_a_real_model_writes_the_file_and_finishes(
    model: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_runtime.__main__ import run_task

    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    rc = run_task(_spec(model))
    events = _events(capsys.readouterr().out)
    steps = [e["step"] for e in events if e.get("event") == "step"]
    finished = next((e for e in events if e.get("event") == "execution.finished"), {})
    error = next((e for e in events if e.get("event") == "execution.error"), {})
    result: dict[str, Any] = finished.get("result") or {"status": "error", **error}
    tools_used = [s.get("tool") for s in steps if s.get("node") == "act"]
    summary = {
        "model": model,
        "rc": rc,
        "status": result.get("status"),
        "abort_code": result.get("abort_code"),
        "iterations": result.get("iterations"),
        "tools": tools_used,
    }
    print("MODEL_COMPAT_RESULT", json.dumps(summary, ensure_ascii=False))
    written = tmp_path / "saludo.txt"
    assert written.is_file(), f"el modelo no escribió el fichero: {summary}"
    assert written.read_text(encoding="utf-8").strip() == _EXPECTED, summary
    assert result.get("abort_code") in (None, ""), summary
