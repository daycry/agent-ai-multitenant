"""El paso 4 de add-then-remove tiene por fin quien lo llame.

Plan prod-05 `task_prod05_06` · ADR 0144.

`revoke_previous_minio_credential` existía desde `task_prod05_07` con un
docstring impecable sobre por qué NO puede correr dentro del ciclo de rotación…
y sin una sola forma de invocarla: ni task, ni comando, ni endpoint. El runbook
la nombraba como si fuese ejecutable. Este módulo es el llamante, y aquí se
prueba lo único que puede romperse sin MinIO delante: que **se niegue en vez de
fingir** cuando le falta con qué trabajar.

Que la revocación en sí funciona contra un MinIO de verdad lo cubre
`tests/integration/test_minio_rotation_applies_to_service.py`; repetirlo con un
doble no probaría nada nuevo.
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.rotation_apply import main, revoke_previous_minio

pytestmark = pytest.mark.unit


def _settings(**overrides: Any) -> Any:
    from workers.config import Settings

    return Settings(**overrides)


def test_without_vault_it_fails_loudly_instead_of_reporting_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin Vault no se puede revocar nada. Devolver 0 aquí le diría al script que
    la ventana está cerrada cuando la credencial vieja sigue viva — el mismo
    «SUCCEEDED de mentira» que gap2-1 costó cerrar."""
    from workers import credential_rotation_task

    monkeypatch.setattr(credential_rotation_task, "_build_vault_client", lambda settings: None)
    monkeypatch.setattr("workers.config.get_settings", lambda: _settings())

    assert revoke_previous_minio() == 2


def test_without_the_minio_admin_credential_it_also_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con Vault pero sin credencial de administración de MinIO, la service
    account vieja no se puede borrar. Fallar es lo correcto: dejar
    `pending_apply` puesto es información honesta."""
    from workers import credential_rotation_task

    monkeypatch.setattr(credential_rotation_task, "_build_vault_client", lambda settings: object())
    monkeypatch.setattr(
        "workers.config.get_settings",
        lambda: _settings(cred_rotation_minio_root_user=""),
    )

    assert revoke_previous_minio() == 2


def test_the_cli_does_nothing_without_an_explicit_action() -> None:
    """Un comando de cierre de ventana no puede tener acción por defecto: se
    invoca en caliente y una pulsación de más no puede revocar nada."""
    assert main([]) == 1
