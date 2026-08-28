"""Las unseal keys de Vault, entre que nacen y que se muestran.

El hueco que cierra este módulo dura unos minutos y no perdona. ``bootstrap_vault``
termina bien: Vault inicializado y desellado, y las cinco unseal keys + el root
token existen SÓLO en la memoria del proceso. El paso siguiente, ``seed_tenant``,
arranca la api-server y siembra el catálogo built-in entero — minutos. Si en ese
rato se cae la sesión SSH, o la siembra devuelve rc≠0, el proceso muere sin haber
impreso nada. Y al relanzar ocurre lo peor: ``bootstrap_vault`` detecta
``is_initialized()`` y se niega —correctamente— a re-inicializar, así que el
revelado aborta con «no hay credenciales reales que revelar». Esas claves no
vuelven a existir por ningún camino, y sin ellas el Vault de esa instalación no
se puede desellar nunca más.

La solución es un depósito de emergencia: un fichero a 0600 bajo la raíz de datos
que se escribe ANTES de imprimir nada y se borra JUSTO DESPUÉS del revelado. No
contradice el ADR 0145 (desellado manual, cinco custodias separadas) porque no es
custodia: las cinco claves siguen saliendo por pantalla para que el operador las
reparta, y el fichero vive los noventa segundos que separan el init del revelado.
Perder el árbol de Vault para siempre es infinitamente peor.
"""

from __future__ import annotations

import pytest
from installer_backend.key_escrow import (
    UNSEAL_KEYS_FILENAME,
    FakeEscrowFile,
    FileKeyEscrow,
    read_unseal_keys,
)
from installer_backend.vault_bootstrap import VaultInitResult

pytestmark = pytest.mark.unit

_ROOT = "/data/agent-platform"
_PATH = f"{_ROOT}/{UNSEAL_KEYS_FILENAME}"

_INIT = VaultInitResult(
    unseal_keys=("clave-uno", "clave-dos", "clave-tres", "clave-cuatro", "clave-cinco"),
    root_token="token-raiz",
    key_threshold=3,
)


def _escrow() -> tuple[FileKeyEscrow, FakeEscrowFile]:
    store = FakeEscrowFile()
    return FileKeyEscrow(data_root=_ROOT, store=store), store


# ---------------------------------------------------------------------------
# El depósito
# ---------------------------------------------------------------------------
def test_the_keys_reach_disk_before_anything_is_printed() -> None:
    """Las cinco claves y el root token, en un fichero a 0600, en el acto."""

    escrow, store = _escrow()

    path = escrow.store_init(_INIT)

    assert path == _PATH
    assert store.modes[_PATH] == 0o600
    body = store.files[_PATH]
    for key in _INIT.unseal_keys:
        assert key in body
    assert _INIT.root_token in body


def test_the_file_says_what_it_is_and_that_hay_que_borrarlo() -> None:
    """Un fichero con las cinco claves juntas es un incidente si se queda.

    Se escribe a sabiendas y por eso tiene que explicarse solo: qué contiene,
    por qué existe, qué hacer con ello y que hay que borrarlo. El nombre ya lo
    grita; el contenido lo argumenta, porque quien se lo encuentre dentro de seis
    meses no va a tener este test delante.
    """

    escrow, store = _escrow()
    escrow.store_init(_INIT)

    body = store.files[_PATH].lower()
    assert "emergencia" in body
    assert "borra" in body
    assert "0145" in body, "el fichero debe remitir a la decisión que lo enmarca"


def test_the_escrow_is_discarded_once_the_reveal_has_been_printed() -> None:
    """Tras el revelado, el fichero se va. Es lo que lo mantiene siendo un apaño."""

    escrow, store = _escrow()
    escrow.store_init(_INIT)

    escrow.discard()

    assert _PATH not in store.files
    assert escrow.pending_path() is None


def test_discarding_a_deposit_that_is_not_there_is_not_an_error() -> None:
    """Nunca hubo init (Vault ya estaba inicializado) → no hay nada que borrar.

    Si esto lanzara, un `install` correcto sobre un Vault ya inicializado moriría
    en el último paso por no encontrar un fichero que no tenía por qué existir.
    """

    escrow, _store = _escrow()

    escrow.discard()  # no raise


def test_a_leftover_deposit_from_a_crashed_run_is_found_and_named() -> None:
    """El reintento tiene que poder decir dónde quedaron las claves del anterior.

    Es el remate del arreglo: sin esto, el operador que relanza recibe «no hay
    credenciales reales que revelar» y no tiene forma de saber que sus claves
    están en un fichero, en esa misma máquina, a un `cat` de distancia.
    """

    escrow, store = _escrow()
    store.files[_PATH] = "lo que dejó el intento anterior"

    assert escrow.pending_path() == _PATH


# ---------------------------------------------------------------------------
# Y se puede volver a leer: es lo que permite reintentar sobre un Vault sellado
# ---------------------------------------------------------------------------
def test_the_deposit_round_trips_back_into_unseal_keys() -> None:
    """El fichero que se escribe es el mismo que ``--vault-unseal-keys-from`` lee.

    Cerrar el círculo importa: un depósito que hay que transcribir a mano para
    reintentar es un depósito que no se usa. Y el formato se afirma aquí en vez
    de en dos sitios sueltos porque escritor y lector son dos funciones distintas
    que sólo un test cruza.
    """

    escrow, store = _escrow()
    escrow.store_init(_INIT)

    assert read_unseal_keys(store.files[_PATH]) == _INIT.unseal_keys


def test_the_reader_ignores_comments_and_blank_lines() -> None:
    """El operador puede escribir su propio fichero de claves, y lo hará.

    Las cinco custodias del ADR 0145 son personas: lo normal es que el fichero de
    reintento lo teclee alguien juntando fragmentos, con líneas en blanco y algún
    comentario. Un parser que se atragante con eso convierte la recuperación en
    un segundo incidente.
    """

    text = "\n".join(
        (
            "# claves recuperadas del sobre",
            "",
            "unseal_key: clave-uno",
            "   ",
            "unseal_key: clave-dos",
            "# la tercera la trae Marta",
            "unseal_key: clave-tres",
        )
    )

    assert read_unseal_keys(text) == ("clave-uno", "clave-dos", "clave-tres")


def test_a_bare_key_per_line_also_reads_back() -> None:
    """Sin el prefijo también vale: es lo que sale de `vault operator init`.

    Exigir un formato propio para recuperar una instalación caída sería añadir
    ceremonia en el peor momento posible.
    """

    assert read_unseal_keys("clave-uno\nclave-dos\n") == ("clave-uno", "clave-dos")


def test_the_root_token_line_is_not_mistaken_for_an_unseal_key() -> None:
    """El fichero lleva las dos cosas; sólo una desella.

    Colar el root token en la lista de shares haría que el desellado consumiera
    un intento con un valor que nunca puede funcionar, y el error resultante
    («Vault sigue sellado tras aplicar el umbral») apuntaría al sitio equivocado.
    """

    escrow, store = _escrow()
    escrow.store_init(_INIT)

    assert _INIT.root_token not in read_unseal_keys(store.files[_PATH])
