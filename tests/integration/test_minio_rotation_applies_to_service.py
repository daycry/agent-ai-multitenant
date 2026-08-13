"""prod-05 task_prod05_07 (gap2-2): la rotación de MinIO contra **MinIO de verdad**.

Por qué este fichero existe teniendo ya `tests/unit/test_vault_rotation_client_hvac.py`:
aquella suite prueba el **orden** (mint antes de KV, revoke sólo en el paso
posterior) contra un doble que registra llamadas. Es la mitad correcta. Pero el
hallazgo gap2-2 no era de orden, era de **efecto**: escribir un valor nuevo en
`secret/platform/minio` no rotaba nada porque MinIO seguía aceptando la
credencial vieja. Un doble no puede refutar eso — un doble dice «me llamaste» y
lo que hay que demostrar es «MinIO cambió».

Este repo ya se ha comido dos veces un `SUCCEEDED` contra un doble. Así que aquí
las aserciones son sobre el servicio:

  * la credencial que `MinioServiceAccountRotator.mint()` acuña **autentica**
    contra MinIO (no es una cadena bonita escrita en KV);
  * tras `revoke()`, esa misma credencial **deja de autenticar**;
  * el ciclo entero (`HvacVaultRotationClient.rotate_static_secret` → propagación
    → `revoke_previous_minio_credential`) deja EXACTAMENTE una credencial viva:
    la nueva funciona y la anterior ya no.

Lo único que sigue siendo doble aquí es Vault (KV en memoria): lo que se está
verificando es el lado MinIO, y meter Vault real sólo añadiría motivos de skip.

## Cómo correrlo

Necesita un MinIO alcanzable y sus credenciales de root. Por defecto se prueba
`localhost:9000` (el del compose de desarrollo) con `MINIO_ROOT_USER` /
`MINIO_ROOT_PASSWORD` del entorno; se puede apuntar a otro con
`ROTATION_TEST_MINIO_ENDPOINT` / `_ROOT_USER` / `_ROOT_PASSWORD`.

Sin credenciales, los tests que tocan el servicio se **saltan** — y eso es una
debilidad conocida, no una virtud: es exactamente el modo de fallo del §4 de
`verificar-antes-de-implementar.md` (una guarda que deja de encontrar nada pasa
vacíamente). Dos contrapesos: `test_the_probe_configuration_is_reachable_or_the_skip_is_explicit`
deja el motivo del salto por escrito en la salida de pytest, y
`test_an_unreachable_minio_refuses_before_touching_kv` usa el rotador REAL contra
un puerto muerto, así que **corre siempre** y falla si alguien quita el
fallo-ruidoso. El drill de `task_prod05_10` es donde esto se ejercita en verde.
"""

from __future__ import annotations

import contextlib
import os
import socket
from typing import Any
from urllib.parse import urlsplit

import pytest
from workers.credential_rotation import CredentialRotationError
from workers.credential_rotation_hvac import (
    KV_FIELD_ACCESS_KEY,
    KV_FIELD_PENDING_APPLY,
    KV_FIELD_PREVIOUS_ACCESS_KEY,
    KV_FIELD_SECRET_KEY,
    HvacVaultRotationClient,
    MinioServiceAccountRotator,
    revoke_previous_minio_credential,
)

pytestmark = pytest.mark.integration

_DEFAULT_ENDPOINT = "localhost:9000"


def _endpoint() -> str:
    raw = os.environ.get("ROTATION_TEST_MINIO_ENDPOINT") or os.environ.get(
        "MINIO_ENDPOINT", _DEFAULT_ENDPOINT
    )
    parts = urlsplit(raw if "//" in raw else f"//{raw}")
    return parts.netloc or raw


def _root_credentials() -> tuple[str, str] | None:
    user = os.environ.get("ROTATION_TEST_MINIO_ROOT_USER") or os.environ.get("MINIO_ROOT_USER")
    password = os.environ.get("ROTATION_TEST_MINIO_ROOT_PASSWORD") or os.environ.get(
        "MINIO_ROOT_PASSWORD"
    )
    if not user or not password:
        return None
    return user, password


def _reachable(endpoint: str, *, timeout: float = 1.0) -> bool:
    host, _, port = endpoint.partition(":")
    try:
        with socket.create_connection((host, int(port or 9000)), timeout=timeout):
            return True
    except OSError:
        return False


def _skip_reason() -> str | None:
    """El motivo EXACTO por el que este fichero no puede probar nada, o None."""
    endpoint = _endpoint()
    if _root_credentials() is None:
        return (
            "sin credenciales de root de MinIO "
            "(ROTATION_TEST_MINIO_ROOT_USER/_PASSWORD o MINIO_ROOT_USER/_PASSWORD): "
            "la rotación no se puede probar contra el servicio, y contra un doble "
            "ya está probada en tests/unit/test_vault_rotation_client_hvac.py"
        )
    if not _reachable(endpoint):
        return f"MinIO no responde en {endpoint}"
    return None


_SKIP = _skip_reason()
requires_minio = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


def _rotator() -> MinioServiceAccountRotator:
    credentials = _root_credentials()
    assert credentials is not None  # el skipif ya lo garantiza
    user, password = credentials
    return MinioServiceAccountRotator(endpoint=_endpoint(), root_user=user, root_password=password)


def _authenticates(access_key: str, secret_key: str) -> bool:
    """¿Esta credencial vale HOY contra MinIO? La pregunta que un doble no responde."""
    from minio import Minio
    from minio.error import S3Error

    client = Minio(_endpoint(), access_key=access_key, secret_key=secret_key, secure=False)
    try:
        client.list_buckets()
    except S3Error:
        # InvalidAccessKeyId / SignatureDoesNotMatch / AccessDenied: la credencial
        # no sirve. Cualquier otro error (red, TLS) sube y rompe el test, que es
        # lo correcto: un fallo de transporte NO es "no autentica".
        return False
    return True


# ---------------------------------------------------------------------------
# Doble de Vault: KV v2 en memoria. Lo que se prueba aquí es el lado MinIO.
# ---------------------------------------------------------------------------
class _FakeKvV2:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self.versions: dict[str, int] = {}

    def create_or_update_secret(
        self, *, mount_point: str, path: str, secret: dict[str, str]
    ) -> dict[str, Any]:
        del mount_point
        self.store[path] = dict(secret)
        self.versions[path] = self.versions.get(path, 0) + 1
        return {"data": {"version": self.versions[path]}}

    def read_secret_version(self, *, mount_point: str, path: str) -> dict[str, Any]:
        del mount_point
        if path not in self.store:
            raise _InvalidPath(path)
        return {"data": {"data": dict(self.store[path])}}


class _InvalidPath(Exception):  # noqa: N818 - el NOMBRE es el contrato (ver _is_invalid_path)
    pass


_InvalidPath.__name__ = "InvalidPath"


class _FakeHvacClient:
    def __init__(self) -> None:
        self.kv_v2 = _FakeKvV2()
        self.secrets = type("_S", (), {})()
        self.secrets.kv = type("_KV", (), {})()
        self.secrets.kv.v2 = self.kv_v2


# ---------------------------------------------------------------------------
# 1. Que el salto, si lo hay, sea explícito
# ---------------------------------------------------------------------------
def test_the_probe_configuration_is_reachable_or_the_skip_is_explicit() -> None:
    """No asserta MinIO: asserta que sabemos POR QUÉ no lo probamos.

    Un `skipif` con un motivo vacío es cómo un fichero entero deja de probar
    nada sin que nadie se entere."""
    if _SKIP is not None:
        pytest.skip(_SKIP)
    assert _root_credentials() is not None
    assert _reachable(_endpoint())


# ---------------------------------------------------------------------------
# 2. El fallo ruidoso — corre SIEMPRE, sin MinIO
# ---------------------------------------------------------------------------
def test_an_unreachable_minio_refuses_before_touching_kv() -> None:
    """Rotador REAL contra un puerto muerto: `CredentialRotationError` y KV intacto.

    Una entrada KV que nombra una credencial que MinIO nunca emitió es PEOR que
    no rotar: todos los servicios reinician sobre un valor que no autentica."""
    dead = MinioServiceAccountRotator(
        endpoint="127.0.0.1:1", root_user="nobody", root_password="nothing"
    )
    hvac = _FakeHvacClient()
    client = HvacVaultRotationClient(hvac, minio_rotator=dead)

    with pytest.raises(CredentialRotationError) as excinfo:
        client.rotate_static_secret(path="platform/minio", mount="secret")

    assert "MinIO" in str(excinfo.value)
    assert hvac.kv_v2.store == {}, "KV se escribió pese a que MinIO no contestó"


def test_the_error_never_echoes_the_generated_secret() -> None:
    """El mensaje va a la auditoría y a la alerta: no puede llevar el secreto."""
    dead = MinioServiceAccountRotator(
        endpoint="127.0.0.1:1", root_user="nobody", root_password="s3cret-root-pw"
    )
    with pytest.raises(CredentialRotationError) as excinfo:
        dead.mint()
    assert "s3cret-root-pw" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. Contra MinIO de verdad
# ---------------------------------------------------------------------------
@requires_minio
def test_a_minted_credential_actually_authenticates_against_minio() -> None:
    rotator = _rotator()
    access_key, secret_key = rotator.mint()
    try:
        assert _authenticates(access_key, secret_key), (
            "MinIO no reconoce la credencial recién acuñada: escribirla en KV "
            "habría dejado a todos los servicios sobre un valor muerto (gap2-2)"
        )
    finally:
        rotator.revoke(access_key)


@requires_minio
def test_a_revoked_credential_stops_authenticating() -> None:
    """La otra mitad de add-then-remove. Sin esto, «rotar» sólo suma credenciales."""
    rotator = _rotator()
    access_key, secret_key = rotator.mint()
    assert _authenticates(access_key, secret_key), "precondición: la credencial nace viva"

    rotator.revoke(access_key)

    assert not _authenticates(access_key, secret_key), (
        "la credencial revocada sigue autenticando: la revocación no llegó a MinIO"
    )


@requires_minio
def test_revoking_an_unknown_credential_does_not_explode() -> None:
    """Idempotencia declarada en el Protocol: revocar dos veces no puede romper
    el ciclo (el paso 4 se puede reintentar tras un fallo de propagación)."""
    rotator = _rotator()
    access_key, _ = rotator.mint()
    rotator.revoke(access_key)
    rotator.revoke(access_key)  # no debe lanzar


@requires_minio
def test_the_full_cycle_leaves_exactly_one_live_credential() -> None:
    """El ciclo completo, con MinIO real y KV doble:

    rotación 1 → credencial A viva
    rotación 2 → A y B vivas a la vez (no hay ventana de corte)
    revoke del paso 4 → sólo B viva
    """
    rotator = _rotator()
    hvac = _FakeHvacClient()
    client = HvacVaultRotationClient(hvac, minio_rotator=rotator)
    minted: list[str] = []

    try:
        version_1 = client.rotate_static_secret(path="platform/minio", mount="secret")
        entry_1 = hvac.kv_v2.store["platform/minio"]
        key_a, secret_a = entry_1[KV_FIELD_ACCESS_KEY], entry_1[KV_FIELD_SECRET_KEY]
        minted.append(key_a)
        assert version_1 == 1
        assert entry_1[KV_FIELD_PENDING_APPLY] == "true"
        assert _authenticates(key_a, secret_a), "la primera credencial no autentica"

        client.rotate_static_secret(path="platform/minio", mount="secret")
        entry_2 = hvac.kv_v2.store["platform/minio"]
        key_b, secret_b = entry_2[KV_FIELD_ACCESS_KEY], entry_2[KV_FIELD_SECRET_KEY]
        minted.append(key_b)
        assert key_b != key_a
        assert entry_2[KV_FIELD_PREVIOUS_ACCESS_KEY] == key_a
        # ADD-THEN-REMOVE: entre el paso 2 y el 4 las DOS valen. Si aquí la vieja
        # ya no valiera, la propagación (reinicio de servicios) ocurriría con el
        # object storage caído para todo el mundo.
        assert _authenticates(key_a, secret_a), "la credencial anterior se revocó ANTES de tiempo"
        assert _authenticates(key_b, secret_b), "la credencial nueva no autentica"

        # Paso 4 — sólo después de propagar.
        revoked = revoke_previous_minio_credential(client, rotator, mount="secret")
        assert revoked == key_a
        assert hvac.kv_v2.store["platform/minio"][KV_FIELD_PENDING_APPLY] == "false"
        assert KV_FIELD_PREVIOUS_ACCESS_KEY not in hvac.kv_v2.store["platform/minio"]
        assert not _authenticates(key_a, secret_a), "la vieja sigue viva tras el paso 4"
        assert _authenticates(key_b, secret_b), "el paso 4 se llevó por delante la nueva"
    finally:
        for key in minted:
            # `revoke` ya es idempotente; el suppress cubre un MinIO que se cae a
            # mitad del test — la limpieza nunca debe tapar el fallo real.
            with contextlib.suppress(CredentialRotationError):
                rotator.revoke(key)
