"""El worker deja el spec y el token interno en `/run/secrets` (`task_cv_20`, D-01).

La otra mitad del test del runtime (`test_el_env_del_agente_no_lleva_secretos.py`):
el patrón ya existía para la credencial del modelo (prod-07, `model_secret.py`);
aquí se aplica al spec entero (cabeceras de MCP, `approved_actions`, código de
python_function) y al token interno. En el env del contenedor sólo viajan los
punteros, y `/run/secrets` se monta UNA sola vez: si la credencial del modelo ya
tiene staging, el spec y el token se escriben en ese mismo directorio.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from workers.config import Settings
from workers.execution import _stage_runtime_secrets
from workers.model_secret import STAGING_SUBDIR
from workers.secrets import stage_secrets

pytestmark = pytest.mark.unit


def _settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    return Settings(data_root=str(tmp_path), model_credential_file=enabled)


def test_the_env_carries_only_pointers_and_the_files_hold_the_secrets(tmp_path: Path) -> None:
    env = {
        "AGENT_TASK_SPEC": '{"task": {"title": "t"}, "mcp_servers": [{"headers": {"X": "s"}}]}',
        "AGENTIC_INTERNAL_TOKEN": "tok-1",
        "AGENTIC_API_URL": "http://api-server:8000",
    }

    public_env, staged = _stage_runtime_secrets(env, settings=_settings(tmp_path))

    assert staged is not None
    assert "AGENT_TASK_SPEC" not in public_env and "AGENTIC_INTERNAL_TOKEN" not in public_env
    assert public_env["AGENT_TASK_SPEC_FILE"] == "/run/secrets/task-spec.json"
    assert public_env["AGENTIC_INTERNAL_TOKEN_FILE"] == "/run/secrets/internal-token"
    assert public_env["AGENTIC_API_URL"] == "http://api-server:8000"
    assert [m["Target"] for m in staged.mounts] == ["/run/secrets"]
    assert staged.mounts[0]["ReadOnly"] is True
    assert '"X": "s"' in (staged.staging_dir / "task-spec.json").read_text(encoding="utf-8")
    assert (staged.staging_dir / "internal-token").read_text(encoding="utf-8") == "tok-1"
    staged.cleanup()
    assert not staged.staging_dir.exists()


def test_when_the_model_credential_is_already_staged_the_files_join_that_directory(
    tmp_path: Path,
) -> None:
    """Un contenedor sólo puede montar `/run/secrets` una vez."""
    existing = stage_secrets({"model-credentials.json": "{}"}, base_dir=str(tmp_path))
    env = {"AGENT_TASK_SPEC": "{}", "AGENTIC_INTERNAL_TOKEN": "tok-2"}

    public_env, staged = _stage_runtime_secrets(
        env, settings=_settings(tmp_path), existing=existing
    )

    assert staged is None, "un segundo mount en /run/secrets pisaría al de la credencial"
    assert (existing.staging_dir / "task-spec.json").read_text(encoding="utf-8") == "{}"
    assert (existing.staging_dir / "internal-token").read_text(encoding="utf-8") == "tok-2"
    assert public_env["AGENT_TASK_SPEC_FILE"] == "/run/secrets/task-spec.json"
    existing.cleanup()
    assert not existing.staging_dir.exists()


def test_without_a_token_only_the_spec_is_staged(tmp_path: Path) -> None:
    public_env, staged = _stage_runtime_secrets(
        {"AGENT_TASK_SPEC": "{}"}, settings=_settings(tmp_path)
    )
    assert staged is not None
    assert (staged.staging_dir / "task-spec.json").is_file()
    assert not (staged.staging_dir / "internal-token").exists()
    assert "AGENTIC_INTERNAL_TOKEN_FILE" not in public_env
    staged.cleanup()


def test_the_flag_off_keeps_the_inline_format(tmp_path: Path) -> None:
    env = {"AGENT_TASK_SPEC": "{}", "AGENTIC_INTERNAL_TOKEN": "tok"}
    public_env, staged = _stage_runtime_secrets(env, settings=_settings(tmp_path, enabled=False))
    assert staged is None and public_env == env


def test_a_staging_failure_falls_back_to_inline_with_a_warning(tmp_path: Path) -> None:
    """Falla en abierto como la credencial del modelo: un disco lleno del worker no
    puede convertirse en «ninguna tarea del tenant corre»."""
    blocker = tmp_path / STAGING_SUBDIR
    blocker.write_text("no soy un directorio", encoding="utf-8")
    env = {"AGENT_TASK_SPEC": "{}", "AGENTIC_INTERNAL_TOKEN": "tok"}
    public_env, staged = _stage_runtime_secrets(env, settings=_settings(tmp_path))
    assert staged is None and public_env == env
