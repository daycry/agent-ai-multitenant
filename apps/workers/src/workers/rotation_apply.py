"""El paso 4 de add-then-remove, como comando.

Plan prod-05 `task_prod05_06` (hallazgo gap2-2) · ADR 0144.

## Por qué un módulo ejecutable y no una task de Celery

:func:`workers.credential_rotation_hvac.revoke_previous_minio_credential` existía
desde `task_prod05_07`, con un docstring que explica muy bien por qué NO puede
correr dentro del ciclo de rotación: hay que llamarla **después** de que el valor
nuevo haya llegado a todos los servicios. Lo que no existía era una forma de
llamarla. `scripts/rotate-platform-secret.sh` la necesita justo después del
reinicio, así que aquí está, con dos propiedades que una task de Celery no daría:

* **síncrona** — el script tiene que saber si la revocación funcionó antes de
  decirle al operador que la ventana está cerrada. Una task encolada devuelve un
  id, no un resultado;
* **fuera del beat** — encolarla la pondría a un `apply_async` de distancia de
  ejecutarse en el momento equivocado, que es exactamente el fallo (riesgo 4 del
  plan: revocar antes de propagar deja la plataforma sin object storage).

No imprime valores: el access key revocado sí (ya no sirve para nada y sirve de
rastro en la ventana), nunca un secret key.
"""

from __future__ import annotations

import argparse
import sys

import structlog

_log = structlog.get_logger("workers.rotation_apply")


def revoke_previous_minio() -> int:
    """Revoca la credencial MinIO anterior. Código de salida de proceso.

    ``0`` también cuando no había nada que revocar: la función subyacente es
    idempotente a propósito (primera rotación, o reintento tras una propagación a
    medias), y hacer fallar el script ahí obligaría al operador a distinguir a
    mano entre «ya estaba hecho» y «se rompió».
    """
    from workers.config import get_settings
    from workers.credential_rotation_hvac import (
        HvacVaultRotationClient,
        MinioServiceAccountRotator,
        revoke_previous_minio_credential,
    )
    from workers.credential_rotation_task import _build_vault_client

    settings = get_settings()
    client = _build_vault_client(settings)
    if not isinstance(client, HvacVaultRotationClient):
        # El resolver ya no puede devolver el fake (gap2-1), pero su tipo
        # declarado es el Protocol, y la revocación necesita el acceso KV del
        # adaptador concreto. Se comprueba en vez de castear: un `None` y un
        # cliente que no sea el real tienen que salir por la misma puerta, la
        # ruidosa.
        client = None
    if client is None:
        print(
            "Vault is not wired (WORKERS_VAULT_URL / WORKERS_VAULT_TOKEN): "
            "nothing can be revoked from here.",
            file=sys.stderr,
        )
        return 2
    if not settings.cred_rotation_minio_root_user:
        print(
            "WORKERS_CRED_ROTATION_MINIO_ROOT_USER is unset: without the admin "
            "credential the previous service account cannot be deleted.",
            file=sys.stderr,
        )
        return 2

    rotator = MinioServiceAccountRotator(
        endpoint=settings.cred_rotation_minio_url,
        root_user=settings.cred_rotation_minio_root_user,
        root_password=settings.cred_rotation_minio_root_password.get_secret_value(),
    )
    revoked = revoke_previous_minio_credential(client, rotator)
    if revoked is None:
        print("nothing to revoke (first rotation, or already revoked)")
    else:
        # El access key ya no autentica: dejarlo en el log es el rastro de que la
        # ventana se cerró, y no es material sensible.
        _log.info("rotation_apply.minio.revoked", revoked_access_key=revoked)
        print(f"revoked previous MinIO access key: {revoked}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m workers.rotation_apply",
        description=(
            "Cierra una ventana de rotación: revoca lo que el valor nuevo "
            "sustituyó. Llamar SÓLO tras propagar y reiniciar (ADR 0144)."
        ),
    )
    parser.add_argument(
        "--revoke-previous-minio",
        action="store_true",
        help="borra la service account de MinIO que la rotación reemplazó",
    )
    args = parser.parse_args(argv)

    if args.revoke_previous_minio:
        return revoke_previous_minio()
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
