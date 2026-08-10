"""prod-07 task_prod07_10 — el runtime resuelve la credencial desde el mount.

La otra mitad de `tests/unit/test_model_credential_not_in_env.py`: sacar la
credencial del entorno sólo es un arreglo si el agente puede seguir usándola.
Aquí se comprueba lo que el worker no puede comprobar — que el spec que llega al
constructor del provider vuelve a tener la credencial dentro, leída del fichero
read-only, y que una imagen con este código **sigue funcionando con un worker
antiguo** que la manda en línea (el orden de despliegue seguro: imagen primero).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_runtime.spec_secrets import CREDENTIALS_FILE_KEY, hydrate_model_credentials

_SECRET = "OPAQUE-CREDENTIAL-MARKER-9f2c"


def _spec_with_pointer(path: Path) -> dict[str, object]:
    return {
        "task": {"title": "haiku"},
        "model": {
            "kind": "azure_foundry",
            "model": "gpt-4o",
            "apim_base_url": "https://apim.example.invalid",
            CREDENTIALS_FILE_KEY: str(path),
        },
    }


def test_the_credential_is_read_from_the_mounted_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "model-credentials.json"
    secret_file.write_text(json.dumps({"subscription_key": _SECRET}), encoding="utf-8")

    hydrated = hydrate_model_credentials(_spec_with_pointer(secret_file))

    assert hydrated is not None
    model = hydrated["model"]
    assert isinstance(model, dict)
    assert model["subscription_key"] == _SECRET
    # El resto del spec intacto: hidratar no puede llevarse el endpoint.
    assert model["apim_base_url"] == "https://apim.example.invalid"
    assert model["model"] == "gpt-4o"


def test_the_pointer_is_dropped_after_hydrating(tmp_path: Path) -> None:
    """La ruta del mount no es un secreto, pero sí un mapa: los volcados de spec
    de depuración no tienen por qué publicarla."""
    secret_file = tmp_path / "model-credentials.json"
    secret_file.write_text(json.dumps({"subscription_key": _SECRET}), encoding="utf-8")

    hydrated = hydrate_model_credentials(_spec_with_pointer(secret_file))

    assert hydrated is not None
    assert CREDENTIALS_FILE_KEY not in hydrated["model"]  # type: ignore[operator]


def test_a_spec_without_pointer_is_returned_untouched() -> None:
    """Compatibilidad de formato: esta imagen tiene que funcionar con un worker
    ANTERIOR al cambio, que manda la credencial dentro del spec. Sin esto el
    orden de despliegue no tendría solución — el worker y la imagen se
    reconstruyen por separado."""
    spec = {
        "task": {"title": "haiku"},
        "model": {"kind": "claude_sdk", "model": "claude-opus-4", "oauth_token": _SECRET},
    }
    assert hydrate_model_credentials(dict(spec)) == spec


def test_a_missing_mount_warns_but_does_not_crash_the_boot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un mount que no llegó tiene que dar un run que falla con 401 y una PISTA,
    no un arranque muerto sin diagnóstico."""
    hydrated = hydrate_model_credentials(_spec_with_pointer(tmp_path / "nope.json"))

    assert hydrated is not None
    assert "subscription_key" not in hydrated["model"]  # type: ignore[operator]
    warning = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert warning["event"] == "runtime.warning"
    assert warning["warning"] == "model_credentials"


def test_a_corrupt_file_warns_but_does_not_crash_the_boot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_file = tmp_path / "model-credentials.json"
    secret_file.write_text("{not json", encoding="utf-8")

    hydrated = hydrate_model_credentials(_spec_with_pointer(secret_file))

    assert hydrated is not None
    warning = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert warning["warning"] == "model_credentials"


def test_the_boot_loader_hydrates_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La costura de verdad: `_load_spec` es la única puerta por la que entra un
    spec, y es donde se hidrata. Probar sólo la función pura dejaría pasar el
    modo de fallo nº1 de esta base — mecanismo entregado, sin llamante."""
    from agent_runtime.__main__ import _load_spec

    secret_file = tmp_path / "model-credentials.json"
    secret_file.write_text(json.dumps({"subscription_key": _SECRET}), encoding="utf-8")
    monkeypatch.setenv("AGENT_TASK_SPEC", json.dumps(_spec_with_pointer(secret_file)))

    spec = _load_spec()

    assert spec is not None
    assert spec["model"]["subscription_key"] == _SECRET  # type: ignore[index]
