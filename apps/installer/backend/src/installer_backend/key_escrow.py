"""Las unseal keys de Vault, entre que nacen y que se muestran.

El hueco
--------
``bootstrap_vault`` termina bien: Vault inicializado y desellado, y las cinco
unseal keys + el root token existen **sólo en la memoria del proceso**. El paso
siguiente, ``seed_tenant``, arranca la api-server y siembra el catálogo built-in
entero: minutos. Si en ese rato se cae la sesión SSH —el operador instaló sin
``tmux``, que es lo normal— o la siembra devuelve rc≠0, el proceso muere sin
haber impreso nada.

Y al relanzar ocurre lo peor: ``bootstrap_vault`` detecta ``is_initialized()`` y
se niega —correctamente— a re-inicializar, devolviendo ``init=None``; el revelado
aborta entonces con «no hay credenciales reales que revelar». Esas cinco claves
**no vuelven a existir por ningún camino**. Sin ellas, el Vault de esa
instalación no se puede desellar nunca más, y la única salida documentada hasta
el 2026-08-27 era destruir la instalación entera.

El depósito de emergencia
-------------------------
Un fichero a 0600 bajo la raíz de datos que se escribe **antes** de imprimir nada
y se borra **justo después** del revelado. Entre esos dos momentos hay noventa
segundos; sin él hay una lotería.

**Por qué esto NO contradice el ADR 0145** (desellado manual, reparto de Shamir
real con cinco custodias separadas): el fichero no es custodia. Las cinco claves
siguen saliendo por pantalla exactamente igual, para que el operador las reparta
entre las cinco personas; lo que el depósito cubre es el tramo en que aún no ha
salido nada y ya no se puede recuperar. Un fichero a 0600 durante minuto y medio
es un riesgo acotado y visible; perder el árbol de Vault es irreversible.

Tres cosas lo mantienen siendo un apaño y no una segunda copia permanente:

* se borra en el revelado, que es el camino feliz;
* si sobrevive es porque algo se rompió, y entonces :meth:`FileKeyEscrow.pending_path`
  lo encuentra y el CLI lo NOMBRA en el error, en vez de dejar al operador con
  «no hay credenciales que revelar» y unas claves a un ``cat`` de distancia;
* el nombre del fichero y su cabecera dicen que hay que borrarlo.

El lector (:func:`read_unseal_keys`) cierra el círculo: el mismo fichero que
escribe el depósito es el que ``--vault-unseal-keys-from`` lee para reintentar
sobre un Vault ya inicializado y sellado. Se lee de un FICHERO y no de un flag
con la clave dentro a propósito: un share en ``argv`` queda a la vista de
cualquier usuario del host en ``ps`` y en el historial del shell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from installer_backend.vault_bootstrap import VaultInitResult

#: El nombre grita a propósito. Quien se lo encuentre haciendo un ``ls`` en la
#: raíz de datos tiene que saber en un segundo que eso no debería seguir ahí.
UNSEAL_KEYS_FILENAME = "UNSEAL-KEYS-BORRAME.txt"

#: Prefijo de las líneas de clave. El fichero lleva TAMBIÉN el root token, y
#: colarlo entre los shares haría que el desellado gastara un intento con un
#: valor que no puede funcionar — con un error que apunta al sitio equivocado.
_KEY_PREFIX = "unseal_key:"
_TOKEN_PREFIX = "root_token:"

_HEADER = f"""\
# ============================================================================
#  CLAVES DE EMERGENCIA DE VAULT — COPIA TEMPORAL. BÓRRAME.
# ============================================================================
#  Este fichero lo escribe el instalador entre que `vault operator init`
#  devuelve las claves y que consigue enseñártelas por pantalla. Si estás
#  leyéndolo es porque ese revelado NO llegó a ocurrir: la instalación se
#  interrumpió después de inicializar Vault.
#
#  QUÉ HACER, en este orden:
#    1. Copia las cinco unseal keys y el root token a donde vayan a vivir.
#       Con desellado manual (ADR 0145) los cinco shares se reparten entre
#       cinco custodias distintas; este fichero NO es una de ellas.
#    2. Reanuda la instalación con:
#         --vault-unseal-keys-from {UNSEAL_KEYS_FILENAME}
#    3. BORRA este fichero. Mientras exista, las cinco claves están juntas en
#       la misma máquina que el propio Vault, que es exactamente lo que el
#       reparto de Shamir existe para evitar.
# ============================================================================
"""


@runtime_checkable
class EscrowFile(Protocol):
    """Seam mínimo del depósito: escribir con modo, comprobar y borrar.

    Es propio y no el ``EnvFileWriter`` de la configuración porque este seam
    necesita las tres operaciones —y sobre todo el ``remove``, que es lo que
    impide que el apaño se convierta en una copia permanente.
    """

    def write(self, path: str, content: str, *, mode: int) -> None: ...

    def exists(self, path: str) -> bool: ...

    def remove(self, path: str) -> None: ...


@dataclass
class FakeEscrowFile:
    """Depósito en memoria para los tests. No toca disco."""

    files: dict[str, str] = field(default_factory=dict)
    modes: dict[str, int] = field(default_factory=dict)

    def write(self, path: str, content: str, *, mode: int) -> None:
        self.files[path] = content
        self.modes[path] = mode

    def exists(self, path: str) -> bool:
        return path in self.files

    def remove(self, path: str) -> None:
        self.files.pop(path, None)
        self.modes.pop(path, None)


@runtime_checkable
class KeyEscrow(Protocol):
    """Deposita el resultado del init de Vault y lo retira tras el revelado."""

    def store_init(self, init: VaultInitResult) -> str: ...

    def discard(self) -> None: ...

    def pending_path(self) -> str | None: ...


@dataclass
class FileKeyEscrow:
    """Depósito de emergencia en un fichero bajo la raíz de datos.

    Construirlo no toca el host: el seam sólo se usa en :meth:`store_init` /
    :meth:`discard` / :meth:`pending_path`.
    """

    data_root: str
    store: EscrowFile

    @property
    def path(self) -> str:
        return f"{self.data_root}/{UNSEAL_KEYS_FILENAME}"

    def store_init(self, init: VaultInitResult) -> str:
        """Escribe las claves + el root token a 0600 y devuelve la ruta.

        Se llama en cuanto ``bootstrap_vault`` devuelve un init no nulo, ANTES
        de cualquier otro paso: el valor del depósito está entero en ser lo
        primero que ocurre después del init.
        """

        lines = [_HEADER, f"{_TOKEN_PREFIX} {init.root_token}", ""]
        lines += [f"{_KEY_PREFIX} {key}" for key in init.unseal_keys]
        lines.append("")
        self.store.write(self.path, "\n".join(lines), mode=0o600)
        return self.path

    def discard(self) -> None:
        """Retira el depósito. No es un error que no esté.

        Que no esté es el caso normal cuando Vault ya estaba inicializado y esta
        ejecución no acuñó nada; hacerlo fallar mataría un ``install`` correcto
        en su último paso por un fichero que no tenía por qué existir.
        """

        if self.store.exists(self.path):
            self.store.remove(self.path)

    def pending_path(self) -> str | None:
        """La ruta del depósito si sobrevivió a una ejecución anterior.

        Un depósito presente al arrancar significa que un intento previo
        inicializó Vault y murió antes de enseñar las claves. El CLI lo nombra
        en el error, que es la diferencia entre «no hay credenciales que
        revelar» y «tus claves están en este fichero».
        """

        return self.path if self.store.exists(self.path) else None


def read_unseal_keys(text: str) -> tuple[str, ...]:
    """Lee las unseal keys de un depósito — o de un fichero escrito a mano.

    Acepta las dos formas que se dan en la práctica: las líneas
    ``unseal_key: <share>`` que escribe :meth:`FileKeyEscrow.store_init`, y una
    clave pelada por línea, que es como salen de ``vault operator init`` y como
    las va a teclear quien junte los fragmentos de las cinco custodias.
    Comentarios (``#``) y líneas en blanco se ignoran, y la línea del root token
    NO se cuenta como share.
    """

    keys: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(_TOKEN_PREFIX):
            continue
        if line.startswith(_KEY_PREFIX):
            line = line[len(_KEY_PREFIX) :].strip()
        if line:
            keys.append(line)
    return tuple(keys)
