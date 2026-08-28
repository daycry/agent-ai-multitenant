"""La línea de REVELADO: el contrato entre las dos mitades del paso 8 del ADR 0161.

Una sola línea de JSON en stdout, con ``"event": "bootstrap.reveal"`` dentro. La
fijó la otra mitad (`installer_backend.real_step_executor`) y aquí **no se
cambia**: se implementa. El parser de allí recorta las llaves *dentro* de la
línea —no hace `json.loads` de la línea entera— para sobrevivir al prefijo que
antepone Compose (``bootstrap-1  | {...}``), así que la única obligación de este
lado es que el JSON quepa en una línea. `json.dumps` la garantiza: escapa
cualquier salto de línea que hubiera dentro de un valor.

## Por qué esto NO puede salir por structlog

`configure_logging` mete `mask_pii_processor` en la cadena, que enmascara
**recursivamente todo string del `event_dict`**, y su regex de claves de API
incluye ``\\bhvs\\.[A-Za-z0-9_-]{16,}``: el root token de Vault saldría como
``hvs.***REDACTED***`` y el revelado sería inútil justo en el dato que no tiene
recuperación. La contraseña de admin (`token_urlsafe`) sobreviviría, el token
raíz no. Por eso la línea se escribe directamente en el stream, fuera del
pipeline, aunque el resto del one-shot loguee normal.

## Por qué se emite UNA vez y no se repite

Lleva dentro material de una sola vez y sin recuperación. El instalador lo
deposita nada más leerlo y filtra sus líneas de progreso por valor para que no
vuelva a aparecer; repetirlo aquí «para que se vea mejor» convertiría un revelado
único en un revelado permanente escrito donde nadie lo va a borrar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TextIO

#: El marcador del contrato. Tiene que coincidir con
#: ``installer_backend.real_step_executor.BOOTSTRAP_REVEAL_EVENT``.
BOOTSTRAP_REVEAL_EVENT = "bootstrap.reveal"


@dataclass(frozen=True)
class Reveal:
    """Lo que el one-shot le cuenta al instalador, y sólo una vez.

    ``__repr__`` va redactado por el mismo motivo que el de
    :class:`~api_server.bootstrap.vault.VaultInitResult`: aquí dentro hay cinco
    unseal keys, un root token y una contraseña de admin que no tienen
    recuperación, y un frame de traceback bastaría para dejarlos escritos.
    """

    already_initialized: bool
    #: Vacías en un re-bootstrap. NUNCA se inventan: un Vault ya inicializado no
    #: se re-inicializa, así que no hay material nuevo que enseñar.
    unseal_keys: tuple[str, ...]
    root_token: str
    key_threshold: int
    kv_mount: str
    kv_enabled: bool
    policies_written: tuple[str, ...]
    #: Vacía cuando el usuario admin YA EXISTÍA. `init_tenant` es idempotente y
    #: **no toca la contraseña de un usuario existente**, así que revelar la
    #: recién minteada sería enseñar una que la base de datos no ha visto nunca:
    #: el operador la guarda, el instalador se autodestruye, y en el primer login
    #: recibe credenciales inválidas sin ninguna pista de por qué.
    admin_password: str
    admin_user_created: bool

    def __repr__(self) -> str:
        return (
            f"Reveal(already_initialized={self.already_initialized}, "
            f"kv_mount={self.kv_mount!r}, kv_enabled={self.kv_enabled}, "
            f"policies_written={self.policies_written!r}, "
            f"admin_user_created={self.admin_user_created}, "
            "<resto redactado: se muestra una vez, sin recuperación>)"
        )

    __str__ = __repr__

    @property
    def secret_values(self) -> tuple[str, ...]:
        """Todo lo que no puede reaparecer en un log ni en un mensaje de error."""

        return tuple(v for v in (self.root_token, self.admin_password, *self.unseal_keys) if v)

    def as_payload(self) -> dict[str, Any]:
        """El diccionario del contrato, con el orden en el que está documentado."""

        return {
            "event": BOOTSTRAP_REVEAL_EVENT,
            "already_initialized": self.already_initialized,
            "unseal_keys": list(self.unseal_keys),
            "root_token": self.root_token,
            "key_threshold": self.key_threshold,
            "kv_mount": self.kv_mount,
            "kv_enabled": self.kv_enabled,
            "policies_written": list(self.policies_written),
            "admin_password": self.admin_password,
            "admin_user_created": self.admin_user_created,
        }

    def render(self) -> str:
        """La línea, sin el salto final. `ensure_ascii=False` no se usa: el
        material es base64/urlsafe y un escape ASCII no cambia nada, pero evita
        depender de la codificación de la terminal del operador."""

        return json.dumps(self.as_payload())


def emit_reveal(reveal: Reveal, *, stream: TextIO) -> None:
    """Escribe la línea y la vacía del buffer.

    El `flush` no es adorno: el one-shot sigue con la siembra del catálogo
    —minutos contra Ollama— y si el proceso muriera ahí con la línea todavía en
    el buffer, esas cinco unseal keys no volverían a existir.
    """

    stream.write(reveal.render() + "\n")
    stream.flush()
