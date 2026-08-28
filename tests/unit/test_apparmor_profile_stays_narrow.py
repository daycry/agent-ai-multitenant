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


# ---------------------------------------------------------------------------
# El perfil del socket-proxy: uno solo, y con UNA diferencia (2026-08-28)
# ---------------------------------------------------------------------------
#
# `agentic-default` deniega el socket de Docker a todo el mundo — Principio 2,
# «a socket leak == host takeover». Y el `docker-socket-proxy` es el único
# servicio que EXISTE para sostenerlo: lo monta en solo lectura y expone sobre
# él una API con ACL por endpoint, para que los workers lancen runtimes sin ver
# el socket jamás.
#
# Con el perfil compartido puesto, HAProxy arrancaba y sus peticiones morían con
# `503 … SC--`: no alcanzaba su propio backend (e2e run 33177824929).
#
# El arreglo que NO se hizo, y que es el que alguien intentará dentro de seis
# meses: abrir el socket en `agentic-default`. Habría funcionado, y se lo habría
# dado también a los workers — que son quienes ejecutan código no confiable. Un
# servicio roto cambiado por el agujero exacto que el Principio 2 cierra.

_PERFIL_PROXY = _PERFIL.with_name("agentic-socket-proxy.profile")


def _permite_el_socket(perfil: Path) -> bool:
    for linea in perfil.read_text(encoding="utf-8").splitlines():
        texto = linea.split("#", 1)[0].strip()
        if "docker.sock" in texto and not texto.startswith("deny") and texto.endswith(","):
            return True
    return False


def test_el_perfil_compartido_sigue_denegando_el_socket() -> None:
    """La regla que sostiene el Principio 2. Si cae, cae el aislamiento entero."""
    texto = _PERFIL.read_text(encoding="utf-8")
    reglas = [
        linea.split("#", 1)[0].strip()
        for linea in texto.splitlines()
        if "docker.sock" in linea.split("#", 1)[0]
    ]
    assert reglas, "el perfil compartido ya no dice nada del socket de Docker"
    for regla in reglas:
        assert regla.startswith("deny"), (
            f"`agentic-default` tiene la regla `{regla}` sobre el socket de Docker. "
            "Ese perfil lo llevan los workers, que ejecutan código no confiable: "
            "abrirlo ahí es entregar el host. Si un servicio concreto necesita el "
            "socket, dale su propio perfil como se hizo con el docker-socket-proxy."
        )


def test_solo_el_perfil_del_socket_proxy_permite_el_socket() -> None:
    """Y ese perfil existe. Las dos mitades: ni de más, ni de menos."""
    assert _PERFIL_PROXY.is_file(), f"falta {_PERFIL_PROXY.name}"
    assert _permite_el_socket(_PERFIL_PROXY), (
        "el perfil del socket-proxy ya no permite el socket: el proxy volverá a "
        "responder 503 y con él se cae todo lo que lanza runtimes"
    )
    assert not _permite_el_socket(_PERFIL), "el perfil compartido lo permite"


def test_los_dos_perfiles_solo_se_diferencian_en_el_socket() -> None:
    """Cuanto más se parezcan, más difícil es que uno derive del otro sin verse.

    Un endurecimiento que se añada a `agentic-default` y no aquí deja al servicio
    MÁS sensible del stack con la postura más floja — y no habría nada que lo
    dijera, porque los dos ficheros pasarían sus propias guardas.
    """

    def reglas(perfil: Path) -> set[str]:
        vistas = set()
        for linea in perfil.read_text(encoding="utf-8").splitlines():
            texto = linea.split("#", 1)[0].strip()
            if texto and texto.endswith(",") and "docker.sock" not in texto:
                vistas.add(texto)
        return vistas

    solo_default = reglas(_PERFIL) - reglas(_PERFIL_PROXY)
    solo_proxy = reglas(_PERFIL_PROXY) - reglas(_PERFIL)
    assert not solo_default and not solo_proxy, (
        f"los perfiles han derivado.\n  sólo en agentic-default: {sorted(solo_default)}"
        f"\n  sólo en agentic-socket-proxy: {sorted(solo_proxy)}\n"
        "La única diferencia legítima es la regla del socket. Si has endurecido "
        "uno, endurece el otro; si la diferencia es deliberada, escríbela aquí."
    )


# ---------------------------------------------------------------------------
# `w` y `m` en la misma regla es ejecución de código arbitrario (2026-08-28)
# ---------------------------------------------------------------------------
#
# `m` permite mapear un fichero como memoria EJECUTABLE, y hubo que concederlo:
# sin él ninguna extensión C de Python se puede importar y no arranca ni una
# migración (e2e run 33181382229, `failed to map segment from shared object`).
#
# Pero se concedió SÓLO donde el código ya es de solo lectura para el proceso.
# Una ruta que tenga `w` y `m` a la vez deja escribir un fichero y ejecutarlo
# después: es la primitiva de la que protege todo lo demás del perfil, y con la
# que quedaría abierta sin que ninguna otra regla lo notara.


@pytest.mark.parametrize("perfil", [_PERFIL, _PERFIL.with_name("agentic-socket-proxy.profile")])
def test_ninguna_ruta_es_escribible_y_mapeable_a_la_vez(perfil: Path) -> None:
    """La invariante que hace segura la concesión de `m`."""
    culpables = [
        f"{ruta} ({permisos})"
        for ruta, permisos in _reglas_de(perfil)
        if "w" in permisos and "m" in permisos
    ]
    assert not culpables, (
        f"en {perfil.name} estas reglas conceden escritura Y mapeo ejecutable: "
        f"{culpables}.\nEso permite dejar un fichero y ejecutarlo después, que "
        "es exactamente la primitiva de la que protege el resto del perfil. "
        "`m` sólo va donde el proceso no puede escribir."
    )


def _reglas_de(perfil: Path) -> list[tuple[str, str]]:
    reglas: list[tuple[str, str]] = []
    for linea in perfil.read_text(encoding="utf-8").splitlines():
        texto = linea.split("#", 1)[0].strip()
        if not texto.startswith("/") or not texto.endswith(","):
            continue
        casa = re.match(r"^(\S+)\s+([a-zA-Z]+),$", texto)
        if casa:
            reglas.append((casa.group(1), casa.group(2)))
    return reglas


def test_las_extensiones_c_se_pueden_cargar() -> None:
    """La otra mitad: que `m` no se retire «limpiando» y vuelva el fallo.

    El venv de las imágenes vive en `/opt/venv`, y ahí están los `.so` de
    pydantic-core, asyncpg, rpds… Sin `m` sobre `/opt`, el stack entero deja de
    arrancar y el síntoma —`failed to map segment`— no se parece en nada a un
    problema de permisos de AppArmor.
    """
    for perfil in (_PERFIL, _PERFIL.with_name("agentic-socket-proxy.profile")):
        mapeables = {ruta for ruta, permisos in _reglas_de(perfil) if "m" in permisos}
        assert "/opt/**" in mapeables, (
            f"{perfil.name} ya no permite mapear `/opt/**`: ninguna extensión C "
            "de Python podrá importarse y no arrancará ni una migración."
        )


@pytest.mark.parametrize("perfil", [_PERFIL, _PERFIL.with_name("agentic-socket-proxy.profile")])
def test_el_venv_se_puede_mapear_Y_ejecutar(perfil: Path) -> None:
    """En `/opt/venv` viven los `.so` Y los ejecutables: hacen falta `m` y `x`.

    La primera versión de la regla concedía sólo `rm`. Con eso las extensiones C
    cargaban —el fallo anterior desaparecía— y el arranque moría un paso después
    (e2e run 33186222329):

        setpriv: failed to execute celery: Permission denied

    Dos permisos distintos para dos cosas distintas que viven en el mismo sitio.
    Conceder uno sin el otro deja al proceso pudiendo cargar librerías y sin
    poder lanzar el programa — y el síntoma cambia lo suficiente como para
    parecer un problema nuevo.
    """
    permisos = dict(_reglas_de(perfil)).get("/opt/**", "")
    assert "m" in permisos, f"{perfil.name}: /opt/** sin `m`, las extensiones C no cargan"
    assert "x" in permisos, (
        f"{perfil.name}: /opt/** sin `x`, no se puede ejecutar nada del venv "
        "(`celery`, `alembic`, `uvicorn`…) y el contenedor muere al arrancar"
    )


@pytest.mark.parametrize("perfil", [_PERFIL, _PERFIL.with_name("agentic-socket-proxy.profile")])
def test_la_memoria_compartida_es_escribible(perfil: Path) -> None:
    """Sin `/dev/shm` escribible no arranca ningún pool de Celery.

    El pool `prefork` crea un `RLock` por worker, y por debajo eso es un
    semáforo POSIX que vive en `/dev/shm`. Sin escritura ahí (e2e run
    33189020440):

        Unrecoverable error: PermissionError(13, 'Permission denied')
          billiard/context.py … RLock()

    No es cosa de un servicio: lo necesita cualquier proceso Python con
    multiprocessing, o sea todos los pools del stack. Salió en el dispatcher por
    ser el primero que llegó a arrancar el suyo — otro caso de un fallo general
    que aparece en el primero que pasa por ahí.
    """
    permisos = dict(_reglas_de(perfil)).get("/dev/shm/**", "")
    assert "w" in permisos, (
        f"{perfil.name}: /dev/shm/** no es escribible. Ningún pool de Celery "
        "arrancará, y el error —PermissionError sobre un RLock— no se parece "
        "en nada a un problema de AppArmor."
    )
    assert "m" not in permisos, (
        f"{perfil.name}: /dev/shm/** concede `m`. Un tmpfs escribible que además "
        "se pueda mapear como ejecutable es ejecución de código arbitrario: ahí "
        "sólo van semáforos y buffers."
    )
