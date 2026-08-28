"""El perfil AppArmor no puede abrir `/usr` para arreglar un caso concreto.

**Por qué existe (2026-08-28).** La primera ejecución real de
`docker/apparmor/agentic-default.profile` —e2e run 33171640034, el perfil llevaba
en el árbol sin haberse aplicado nunca: el stack de desarrollo corre con
`SecurityOpt=[]`— tumbó el `docker-socket-proxy`:

    docker-entrypoint.sh: line 18: can't create
      /usr/local/etc/haproxy/haproxy.cfg: Permission denied

HAProxy genera su configuración al arrancar, y `/usr/** rix` no lleva `w`. Con
el proxy en bucle, su healthcheck en `Error` y la instalación abortando en
`start_stack` — y no es un servicio accesorio: es el que sostiene el socket de
Docker por el Principio 2.

**El arreglo fácil habría sido `/usr/** rwk`**, y habría funcionado. También
habría convertido el perfil en decoración: escribir en `/usr` es sustituir
binarios que otro proceso va a ejecutar, que es exactamente de lo que la regla
general protege.

Se abrió un directorio, y este fichero es lo que mantiene esa decisión. Una
excepción sin un test que la acote se ensancha sola: alguien la copia para el
siguiente servicio que no arranca, luego para otro, y a los seis meses el perfil
concede `/usr` entero con tres comentarios explicando por qué estuvo bien cada
paso.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PERFIL = Path(__file__).resolve().parents[2] / "docker" / "apparmor" / "agentic-default.profile"

#: Las rutas con permiso de ESCRITURA que se aceptan bajo `/usr`. Añadir una
#: entrada aquí es un acto deliberado que deja rastro en el diff, que es
#: justamente el efecto que se busca.
_ESCRITURAS_PERMITIDAS_BAJO_USR = frozenset(
    {
        # HAProxy escribe aquí la config que genera de sus variables de ACL.
        "/usr/local/etc/haproxy/",
        "/usr/local/etc/haproxy/**",
    }
)


def _reglas() -> list[tuple[str, str]]:
    """(ruta, permisos) de cada regla de fichero del perfil, sin comentarios."""
    reglas: list[tuple[str, str]] = []
    for linea in _PERFIL.read_text(encoding="utf-8").splitlines():
        texto = linea.split("#", 1)[0].strip()
        if not texto.startswith("/") or not texto.endswith(","):
            continue
        casa = re.match(r"^(\S+)\s+([a-zA-Z]+),$", texto)
        if casa:
            reglas.append((casa.group(1), casa.group(2)))
    return reglas


def test_el_perfil_se_puede_leer_y_tiene_reglas() -> None:
    """Sin esto, un renombrado dejaría los demás tests pasando en vacío."""
    assert _PERFIL.is_file(), f"no existe {_PERFIL}"
    assert len(_reglas()) > 10, (
        "se han leído muy pocas reglas: el formato del perfil ha cambiado y "
        "estas guardas estarían comprobando una lista vacía"
    )


def test_usr_no_es_escribible_en_bloque() -> None:
    """La regla general. Si cae, el perfil deja de proteger lo que importa."""
    for ruta, permisos in _reglas():
        if ruta in ("/usr/**", "/usr/", "/usr"):
            assert "w" not in permisos, (
                f"`{ruta}` concede `{permisos}`: escribir en /usr es poder "
                "sustituir binarios que otro proceso va a ejecutar. Si un "
                "servicio necesita escribir, ábrele SU directorio y anótalo en "
                "_ESCRITURAS_PERMITIDAS_BAJO_USR, no el árbol entero."
            )


def test_las_escrituras_bajo_usr_son_las_declaradas() -> None:
    """Cada excepción nueva tiene que pasar por aquí, y por tanto explicarse."""
    escribibles = {
        ruta for ruta, permisos in _reglas() if ruta.startswith("/usr") and "w" in permisos
    }
    assert escribibles == set(_ESCRITURAS_PERMITIDAS_BAJO_USR), (
        f"las escrituras bajo /usr del perfil son {sorted(escribibles)} y las "
        f"declaradas son {sorted(_ESCRITURAS_PERMITIDAS_BAJO_USR)}.\n"
        "Si has abierto una ruta nueva: añádela arriba CON el motivo, para que "
        "el siguiente que la lea sepa si sigue haciendo falta. Si has quitado "
        "una: bórrala de la lista."
    )


def test_la_excepcion_de_haproxy_sigue_estando() -> None:
    """La otra mitad: que no se retire sin darse cuenta.

    Si alguien limpia el perfil y se lleva estas dos líneas por delante, el
    `docker-socket-proxy` vuelve a no arrancar — y el síntoma aparecerá en una
    instalación real, no aquí. Ese viaje ya lo hemos hecho una vez.
    """
    escribibles = {
        ruta for ruta, permisos in _reglas() if ruta.startswith("/usr") and "w" in permisos
    }
    assert "/usr/local/etc/haproxy/**" in escribibles, (
        "falta la excepción de HAProxy: el docker-socket-proxy genera su "
        "configuración al arrancar y sin permiso de escritura entra en bucle, "
        "tumbando la instalación en `start_stack`."
    )


# ---------------------------------------------------------------------------
# El perfil y el generador tienen que estar de acuerdo sobre las capacidades
# ---------------------------------------------------------------------------
#
# Ésta es la guarda que habría ahorrado la sexta ejecución del e2e
# (run 33174222896). El compose generado concedía `cap_add: [IPC_LOCK, SETFCAP]`
# a Vault y este perfil no las listaba, así que AppArmor las denegaba DESPUÉS de
# que Docker las concediera:
#
#   vault-1 | unable to set CAP_SETFCAP effective capability: Operation not permitted
#
# Dos ficheros que tienen que decir lo mismo y ningún test cruzándolos: la deriva
# estaba garantizada, y el sitio donde se manifiesta es una instalación real.


def _capacidades_del_perfil() -> set[str]:
    encontradas: set[str] = set()
    for linea in _PERFIL.read_text(encoding="utf-8").splitlines():
        texto = linea.split("#", 1)[0].strip()
        casa = re.match(r"^capability\s+([a-z_]+),$", texto)
        if casa:
            encontradas.add(casa.group(1))
    return encontradas


def _capacidades_que_concede_el_generador() -> set[str]:
    """Las que el compose generado pone en algún `cap_add`, leídas del árbol."""
    from installer_backend import compose_generator as generador

    concedidas: set[str] = set()
    for nombre in dir(generador):
        if not nombre.startswith("_") or "CAPS" not in nombre:
            continue
        valor = getattr(generador, nombre)
        if isinstance(valor, (list, tuple)):
            concedidas.update(str(c).lower() for c in valor)
    # Las que un builder concreto añade además de la lista compartida.
    fuente = (
        _PERFIL.parents[2] / "apps/installer/backend/src/installer_backend" / "compose_generator.py"
    ).read_text(encoding="utf-8")
    for bloque in re.findall(r'"cap_add":\s*\[([^\]]*)\]', fuente):
        concedidas.update(c.strip().strip('"').lower() for c in bloque.split(",") if '"' in c)
    return {c for c in concedidas if c and c.isidentifier()}


def test_el_perfil_permite_toda_capacidad_que_el_generador_concede() -> None:
    """Docker concede y AppArmor deniega: el fallo se ve en producción, no aquí."""
    concedidas = _capacidades_que_concede_el_generador()
    assert concedidas, (
        "no se ha leído ninguna capacidad del generador: la derivación se ha "
        "roto y esta guarda estaría comparando conjuntos vacíos"
    )
    faltan = sorted(concedidas - _capacidades_del_perfil())
    assert not faltan, (
        f"el compose generado concede {faltan} y el perfil AppArmor no las "
        "permite. Docker se las dará y AppArmor se las quitará, y el servicio "
        "entrará en bucle sin que nada lo explique hasta ver sus logs.\n"
        "Añádelas al perfil CON el motivo: listarlas es un techo, no una "
        "concesión — quien tenga `cap_drop: ALL` sin `cap_add` sigue sin ellas."
    )
