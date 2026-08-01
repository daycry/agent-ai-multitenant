"""La salvaguarda de backups del ADR 0146 — los secretos de columna no viajan.

El problema, en una frase
-------------------------
El ADR 0146 bendice que tres familias de secretos vivan cifradas con Fernet en
columnas de Postgres en vez de en Vault (porque el ADR 0145 decidió desellado
manual, y migrarlas dejaría el login SSO caído tras cada reinicio del host).
Pero lo firma **con una condición que llama no opcional**: hoy un `pg_dump`
lleva ese ciphertext, así que quien tenga el backup **y** la variable de entorno
tiene los secretos — y el backup viaja a MinIO y a destinos externos.

    «Sin (1) esta decisión sería peor que el statu quo, porque habría bendecido
    el riesgo sin quitarlo.»

Por qué EXCLUIR y no «cifrar con una clave distinta»
-----------------------------------------------------
El ADR daba las dos vías y la elección era de quien implementase. Se elige
**excluir**, y conviene dejar escrito por qué, porque la otra parecía más fina:

1. **La otra no protege el caso real.** «Cifrar con una clave distinta» sólo
   añade seguridad si el bundle se cifra, y el instalador emite
   ``WORKERS_BACKUP_ENCRYPTION_ENABLED=false``
   (``installer_backend/compose_generator.py``). O sea que en un stack recién
   instalado —el que hay— el segundo sobre no existiría y el ciphertext viajaría
   igual. Hacer que el backup FALLE si no hay cifrado tampoco vale: convertiría
   la ventana nocturna en una caída, que es justo lo que el ADR 0149 acaba de
   descartar para el quiesce.
2. **Excluir no necesita ninguna clave, ninguna custodia y ningún segundo
   custodio.** Un dump robado no tiene el ciphertext: no hace falta razonar
   sobre qué clave lo abre.
3. **El coste está acotado y es visible.** Lo que no vuelve tras un DR es la
   configuración de integración con terceros, y su ausencia se ve (el botón de
   SSO no aparece, la lista de canales está vacía). No es el modo de fallo
   silencioso —Redis restaurando ``DBSIZE 0`` sin un solo error— que este motor
   ya se comió una vez.

Qué se excluye exactamente, y qué NO
------------------------------------
Se excluyen los **datos** de las tres tablas que el ADR nombra, no su
definición: ``--exclude-table-data``, nunca ``--exclude-table``. La tabla vuelve
del restore vacía y la aplicación arranca; lo que hay que rehacer es la
configuración, y eso está en
``docs/06-runbooks/04-disaster-recovery.md``.

La frontera es la del ADR 0146 y es lo que impide que la excepción crezca: aquí
sólo entra el secreto que un TENANT configura para integrarse con un TERCERO.
Las credenciales de PLATAFORMA (proveedores LLM, contraseñas de base de datos,
claves de MinIO, tokens de servicio) siguen en Vault sin excepción, y por tanto
no están en ninguna columna que pudiera acabar en esta lista.

Fuera de la lista a propósito: la semilla TOTP de MFA
(``user_mfa_credentials.secret_encrypted``). No es un secreto que un tenant
configure para un tercero sino una credencial de un usuario, tiene su propia
clave desde el ADR 0143, y excluirla obligaría a re-enrolar el segundo factor de
toda la organización tras un DR. Si algún día se decide lo contrario, es un
nombre más en ``WORKERS_BACKUP_COLUMN_SECRET_TABLES`` — pero es una decisión, no
un descuido.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Las tres familias del ADR 0146, por tabla. El orden es el del ADR.
COLUMN_SECRET_TABLES: tuple[str, ...] = (
    "sso_configurations",
    "notification_channels",
    "incoming_webhook_configs",
)

#: Qué columna cifrada vive en cada una. No lo lee el motor: documenta la
#: frontera y la comprueba un test contra el ORM real, para que un renombrado no
#: deje esta lista diciendo una cosa y el esquema otra.
COLUMN_SECRET_COLUMNS: dict[str, tuple[str, ...]] = {
    # Client secret de OIDC + clave privada del SP de SAML.
    "sso_configurations": ("client_secret_encrypted", "sp_private_key_encrypted"),
    # Credencial del canal (token de bot, contraseña SMTP, secreto de webhook…).
    "notification_channels": ("secret_encrypted",),
    # Secreto con el que se firma/verifica cada webhook entrante.
    "incoming_webhook_configs": ("signing_secret_encrypted",),
}


def exclude_table_data_args(tables: Sequence[str]) -> list[str]:
    """Los flags de ``pg_dump`` que dejan fuera los DATOS de esas tablas.

    ``--exclude-table-data`` y no ``--exclude-table``: la definición tiene que
    viajar o el restore dejaría una base sin esas tablas y la aplicación no
    arrancaría. Lista vacía → ninguna exclusión (la palanca del operador para
    volver al comportamiento anterior al ADR 0146).
    """
    return [f"--exclude-table-data={table}" for table in tables if table]


__all__ = [
    "COLUMN_SECRET_COLUMNS",
    "COLUMN_SECRET_TABLES",
    "exclude_table_data_args",
]
