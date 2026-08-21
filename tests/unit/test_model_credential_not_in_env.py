"""prod-07 task_prod07_10 — la credencial del proveedor NO viaja en el env.

Lo que se prueba aquí no es «existe una función que separa campos», que es
trivialmente cierto en cuanto se escribe. Es la propiedad observable desde
fuera: **el valor literal del secreto no aparece en NINGUNA variable de entorno
del contenedor**, que es lo que ve un `docker inspect`, un volcado del daemon o
cualquier proceso hijo del sandbox. Por eso las aserciones buscan la cadena del
secreto en el env COMPLETO serializado, no la ausencia de una clave concreta:
un refactor que renombre el campo pero lo siga metiendo en el JSON del spec
seguiría rojo, que es justo lo que se quiere.

La otra mitad —que el runtime sí la recibe— vive en
``docker/agent-runtimes/agent-runtime/tests/test_spec_secret_file.py``: separar
la credencial sin que el agente pueda usarla no sería un arreglo, sería una
avería.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from shared_llm.credential_fields import CREDENTIAL_FIELDS
from workers.execution import _build_runtime_env
from workers.model_secret import (
    CREDENTIALS_FILE_KEY,
    MODEL_CREDENTIALS_PATH,
    credential_spec_fields,
    split_model_credentials,
    stage_model_credentials,
)
from workers.run_contract import ExecutionRequest

pytestmark = pytest.mark.unit

# Marcadores opacos: lo que se prueba es que el VALOR viaja (o no), no su forma.
_SECRET = "OPAQUE-CREDENTIAL-MARKER-9f2c"
_OTHER_SECRET = "OPAQUE-CREDENTIAL-MARKER-b71e"


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id="00000000-0000-0000-0000-0000000000bb",
        task_id="00000000-0000-0000-0000-0000000000aa",
        agent_id=None,
        task={"title": "haiku", "description": "escribe un haiku"},
        model={"kind": "scripted"},
    )


# ---------------------------------------------------------------------------
# El split
# ---------------------------------------------------------------------------
def test_credential_field_names_come_from_the_shared_table() -> None:
    """La lista de campos se DERIVA de la tabla única (task_prod07_08).

    Sin esta atadura, añadir un kind con un campo nuevo dejaría ese campo en el
    env sin que nada se pusiera rojo — el modo de fallo silencioso que la tabla
    compartida existe para evitar.
    """
    fields = credential_spec_fields()
    assert fields, "la guarda dejó de encontrar campos: la tabla cambió de forma"
    for mapping in CREDENTIAL_FIELDS.values():
        for _vault_field, spec_field in mapping.secret_fields:
            assert spec_field in fields, f"{spec_field} quedaría en el env"


def test_split_moves_every_credential_out_and_leaves_the_pointer() -> None:
    public, secrets = split_model_credentials(
        {
            "kind": "azure_foundry",
            "model": "gpt-4o",
            "apim_base_url": "https://apim.example.invalid",
            "subscription_key": _SECRET,
            "bearer_token": _OTHER_SECRET,
        }
    )
    assert secrets == {"subscription_key": _SECRET, "bearer_token": _OTHER_SECRET}
    assert public is not None
    assert public[CREDENTIALS_FILE_KEY] == MODEL_CREDENTIALS_PATH
    assert _SECRET not in json.dumps(public)
    assert _OTHER_SECRET not in json.dumps(public)
    # Lo público sigue entero: mover la credencial no puede llevarse el endpoint.
    assert public["apim_base_url"] == "https://apim.example.invalid"
    assert public["model"] == "gpt-4o"


def test_a_model_without_credentials_is_returned_untouched() -> None:
    """Ollama local no tiene credencial: ni fichero, ni mount, ni puntero.

    Poner el puntero igualmente haría que el runtime buscase un fichero que
    nadie montó y avisara en cada run sano."""
    spec: dict[str, Any] = {"kind": "ollama", "model": "llama3", "base_url": "http://ollama:11434"}
    public, secrets = split_model_credentials(spec)
    assert secrets == {}
    assert public == spec
    assert CREDENTIALS_FILE_KEY not in (public or {})


def test_an_empty_credential_is_not_a_credential() -> None:
    """Un `""` no se mueve: dejaría al provider SIN el campo donde antes tenía
    una cadena vacía, un cambio de comportamiento que nadie pidió."""
    public, secrets = split_model_credentials({"kind": "ollama", "api_key": ""})
    assert secrets == {}
    assert public == {"kind": "ollama", "api_key": ""}


def test_split_does_not_mutate_the_input() -> None:
    original = {"kind": "copilot", "github_token": _SECRET}
    snapshot = dict(original)
    split_model_credentials(original)
    assert original == snapshot


# ---------------------------------------------------------------------------
# La propiedad que importa: el env del contenedor
# ---------------------------------------------------------------------------
def test_the_container_env_never_carries_the_credential() -> None:
    resolved = {
        "kind": "claude_sdk",
        "model": "claude-opus-4",
        "oauth_token": _SECRET,
    }
    public, secrets = split_model_credentials(resolved)
    env = _build_runtime_env(
        _request(),
        None,
        agent_internal_api_url="http://api-server:8000",
        model_spec=public,
    )
    serialized = json.dumps(env)
    assert _SECRET in secrets.values(), "el secreto no llegó al fichero: nadie lo usaría"
    assert _SECRET not in serialized, (
        "la credencial del proveedor sigue en el entorno del contenedor: la ve"
        " cualquier `docker inspect` y la heredan los procesos hijos del sandbox"
    )
    # ...y el puntero SÍ viaja, o el runtime no sabría dónde mirar.
    assert MODEL_CREDENTIALS_PATH in serialized


def test_without_the_split_the_credential_would_be_in_the_env() -> None:
    """El contraste que hace no-vacío al test anterior.

    Si `_build_runtime_env` recibe el spec SIN partir —el comportamiento de
    antes de esta tarea— el secreto aparece en el env. Sin esta comprobación, un
    `_build_runtime_env` que devolviese `{}` pasaría el test de arriba."""
    env = _build_runtime_env(
        _request(),
        None,
        agent_internal_api_url="http://api-server:8000",
        model_spec={"kind": "claude_sdk", "model": "claude-opus-4", "oauth_token": _SECRET},
    )
    assert _SECRET in json.dumps(env)


# ---------------------------------------------------------------------------
# El staging en disco
# ---------------------------------------------------------------------------
def test_staged_file_is_read_only_and_holds_the_credentials(tmp_path: Path) -> None:
    staged = stage_model_credentials({"api_key": _SECRET}, base_dir=str(tmp_path))
    try:
        files = list(staged.staging_dir.glob("*"))
        assert len(files) == 1
        assert json.loads(files[0].read_text(encoding="utf-8")) == {"api_key": _SECRET}
        # 0444: el uid 1000 del contenedor lee; nadie escribe. En Windows el bit
        # de grupo/otros no existe, así que se comprueba sólo el de escritura.
        assert not os.access(files[0], os.W_OK) or os.name == "nt"
        mount = staged.mounts[0]
        target = mount["Target"] if isinstance(mount, dict) else mount.get("Target")
        read_only = mount["ReadOnly"] if isinstance(mount, dict) else mount.get("ReadOnly")
        assert target == "/run/secrets"
        assert read_only is True
    finally:
        staged.cleanup()


def test_cleanup_removes_the_staged_credential(tmp_path: Path) -> None:
    staged = stage_model_credentials({"api_key": _SECRET}, base_dir=str(tmp_path))
    staging_dir = staged.staging_dir
    assert staging_dir.exists()
    staged.cleanup()
    assert not staging_dir.exists(), "la credencial se queda en el disco del host tras el run"
