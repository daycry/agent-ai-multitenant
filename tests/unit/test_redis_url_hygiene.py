"""Guarda: ningún test de integración escribe la URL de Redis a mano.

El defecto que cierra
---------------------
Desde que el plan prod-10 arrancó Redis con ``--requirepass``, un literal como
``redis://localhost:6379/15`` dejó de conectar: no lleva credencial y el
servidor contesta ``NOAUTH Authentication required``. Lo caro no es el fallo,
es cómo se presenta — ``redis.exceptions.AuthenticationError`` levantado
**dentro de una fixture**, así que pytest lo marca ERROR y no FAILED (el test ni
se ejecutó) y el traceback apunta al parser de ``redis-py`` sin mencionar ni la
contraseña ni el fichero que escribió la URL. Está documentado en
``docs/03-guides/gotchas/redis-con-contrasena-rompe-la-integracion.md``.

El barrido dejó una sola fuente de verdad, ``tests/integration/_redis_url.py``.
Pero un barrido no se sostiene solo: el patrón que se retiró de 26 ficheros es
justo el que cualquiera vuelve a teclear de memoria al escribir el 27º, y el
rojo que produce no señala a la causa. Esta guarda es lo que hace que el
barrido no haya que repetirlo.

Por qué mira TODOS los literales y no solo las asignaciones
-----------------------------------------------------------
La forma que se retiró era ``TEST_REDIS_URL = "redis://…"``, pero
``Redis.from_url("redis://localhost:6379/15")`` en línea rompe exactamente
igual. Una guarda que solo vigile la forma histórica del defecto certifica que
no se repite la forma, no que no se repite el defecto.

De ahí que haga falta la allowlist de abajo: hay literales de Redis legítimos en
la suite —datos de configuración que nunca abren una conexión— y sin una lista
explícita esta guarda sería ruido y acabaría desactivada.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTEGRATION = _REPO_ROOT / "tests" / "integration"

# Cualquier URL de Redis apuntando al host local. Se busca en los literales del
# AST, NO en el texto crudo: un comentario que cite la URL para explicar la
# trampa es documentación, no una conexión.
_PATRONES = ("redis://localhost", "redis://127.0.0.1")

# Ficheros exentos, con el motivo. La regla para entrar aquí es UNA: el literal
# no puede acabar en una conexión a Redis.
_ALLOWLIST: dict[str, str] = {
    # `_redis_url.py` NO está aquí, y conviene decir por qué: es la fuente de
    # verdad, pero construye la URL con un f-string —que es un `JoinedStr`, no un
    # `Constant`— y sus únicas menciones literales viven en docstrings, que desde
    # el 2026-08-20 esta guarda ya no confunde con código. O sea que no necesita
    # exención. Si algún día apareciera ahí un literal de verdad, esta guarda lo
    # señalaría, y ESO merece una mirada aunque el fichero sea el sitio legítimo.
    # Los cinco de abajo comparten forma: construyen un `Settings(...)` de
    # workers y se lo pasan a `build_celery_app()`, que solo RELLENA la config
    # del app de Celery. Ninguno publica una tarea ni abre el broker — lo que
    # comprueban es que la cadena de configuración se propaga (o que la entrada
    # de beat queda registrada). Cambiarlos por la URL real no arreglaría nada y
    # borraría el dato que el test afirma.
    "test_celery_queues.py": (
        "broker/backend como DATO: build_celery_app() configura, no conecta;"
        " el test afirma que las DB 1 y 2 llegan a app.conf"
    ),
    "test_backup_schedule.py": (
        "Settings(broker_url=…) para registrar la entrada de beat del backup; no abre el broker"
    ),
    "test_credential_rotation.py": (
        "Settings(broker_url=…) para registrar la entrada de beat de rotación; no abre el broker"
    ),
    "test_fx_fetcher.py": (
        "Settings(broker_url=…) para registrar la entrada de beat de FX; no abre el broker"
    ),
    "test_scheduled_sync.py": (
        "Settings(broker_url=…) para registrar la entrada de beat de price-sync; no abre el broker"
    ),
    # Y estos tres son el caso contrario, y por eso son intocables: apuntan a un
    # puerto MUERTO a propósito. Lo que prueban es el fail-open — con Redis caído
    # la autorización y la caché siguen contestando contra PostgreSQL, y `/readyz`
    # vuelve a 200 sin reiniciar el proceso. Sustituir el literal por la URL buena
    # dejaría los tests en verde sin comprobar nada.
    "test_health_readiness.py": (
        "puerto muerto deliberado: /readyz tiene que dar 503 y luego recuperarse"
    ),
    "test_redis_cache_and_chat_rate_limit.py": (
        "puerto muerto deliberado: la caché de platform_settings hace fail-open"
    ),
    "test_redis_cache_membership_settings.py": (
        "puerto muerto deliberado: require_tenant_member hace fail-open a PostgreSQL"
    ),
}


def _docstrings(arbol: ast.AST) -> set[int]:
    """Los ``id()`` de los nodos que son docstring, para no confundirlos con código.

    El encabezado de este fichero fija el criterio —«un comentario que cite la
    URL para explicar la trampa es documentación, no una conexión»— y se cumplía
    solo a medias: un comentario ``#`` no llega al AST, pero un docstring **sí**
    es un ``ast.Constant``, así que la guarda lo leía como si alguien fuera a
    conectarse con él. Saltaba justo donde más aparece el literal: en la prosa
    que explica por qué no debe escribirse.

    El caso que lo destapó (2026-08-20): el docstring de
    ``_purge_platform_setting_cache`` en ``tests/integration/conftest.py`` cita
    ``redis://localhost:6379/0`` para decir que si la caché apunta AHÍ, el arnés
    no debe borrar nada. La función usa ``TEST_REDIS_URL``. Un falso positivo en
    una guarda no es gratis: es lo que hace que alguien la mande a la allowlist
    entera —y con ella el fichero donde una URL cableada haría más daño—.
    """
    marcados: set[int] = set()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        cuerpo = getattr(nodo, "body", None)
        if not cuerpo:
            continue
        primero = cuerpo[0]
        if (
            isinstance(primero, ast.Expr)
            and isinstance(primero.value, ast.Constant)
            and isinstance(primero.value.value, str)
        ):
            marcados.add(id(primero.value))
    return marcados


def _literales_de_redis(ruta: Path) -> list[tuple[int, str]]:
    """Los literales de cadena del fichero que contienen una URL de Redis local.

    Se excluyen los docstrings: son documentación, igual que los comentarios.
    """
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    prosa = _docstrings(arbol)
    hallazgos: list[tuple[int, str]] = []
    for nodo in ast.walk(arbol):
        es_cadena = isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
        if es_cadena and id(nodo) not in prosa and any(p in nodo.value for p in _PATRONES):
            hallazgos.append((nodo.lineno, nodo.value.strip().splitlines()[0][:80]))
    return hallazgos


def _ficheros_de_integracion() -> list[Path]:
    return sorted(p for p in _INTEGRATION.glob("*.py") if p.name != "__init__.py")


def test_ningun_test_de_integracion_cablea_la_url_de_redis() -> None:
    """Ningún fichero fuera de la allowlist escribe una URL de Redis literal."""
    ficheros = _ficheros_de_integracion()
    # Guarda estática ⇒ afirma que encontró algo que mirar
    # (`docs/03-guides/verificar-antes-de-implementar.md` §4): un glob vacío
    # pasaría este test sin haber comprobado nada.
    assert len(ficheros) > 100, f"solo {len(ficheros)} ficheros de integración: ¿glob roto?"

    infractores: list[str] = []
    for ruta in ficheros:
        if ruta.name in _ALLOWLIST:
            continue
        for linea, texto in _literales_de_redis(ruta):
            infractores.append(f"{ruta.name}:{linea}: {texto}")

    assert not infractores, (
        "URL de Redis cableada en tests/integration. Sin credencial fallará con"
        " AuthenticationError DENTRO de una fixture (ERROR, no FAILED). Importa"
        " la resuelta: `from ._redis_url import TEST_REDIS_URL`. Si el literal es"
        " un dato de configuración que nunca conecta, añade el fichero a"
        " _ALLOWLIST con su motivo.\n  " + "\n  ".join(infractores)
    )


def test_la_allowlist_no_se_pudre() -> None:
    """Cada exención sigue existiendo y sigue haciendo falta.

    Una entrada cuyo fichero ya no tiene el literal es una exención huérfana: da
    permiso donde ya no se usa, y el día que alguien cablee una URL en ese
    fichero la guarda callará por un motivo que caducó hace meses.
    """
    huerfanas: list[str] = []
    for nombre, motivo in _ALLOWLIST.items():
        assert motivo.strip(), f"{nombre} está exento sin motivo escrito"
        ruta = _INTEGRATION / nombre
        if not ruta.is_file():
            huerfanas.append(f"{nombre}: el fichero ya no existe")
        elif not _literales_de_redis(ruta):
            huerfanas.append(f"{nombre}: ya no tiene ninguna URL de Redis literal")

    assert not huerfanas, "exenciones caducadas en _ALLOWLIST:\n  " + "\n  ".join(huerfanas)


def test_la_fuente_de_verdad_existe_y_resuelve_con_credencial() -> None:
    """`_redis_url.py` es lo que la guarda manda usar: tiene que estar y servir.

    Sin esto, el mensaje de error de arriba podría estar apuntando a un módulo
    borrado en un refactor, y la guarda mandaría a la gente a un import roto.
    """
    from tests.integration._redis_url import TEST_REDIS_URL, default_redis_url

    assert TEST_REDIS_URL.startswith("redis://")
    # DB 15 por defecto: no puede pisar la DB 0 de desarrollo.
    assert default_redis_url().endswith("/15")


# ===========================================================================
# La otra mitad de la higiene: la base ELEGIDA tampoco puede ser del stack vivo
# ===========================================================================
def test_las_bases_del_stack_vivo_estan_prohibidas() -> None:
    """Apuntar `TEST_REDIS_URL` a la 0, la 1 o la 2 tiene que abortar.

    El stack de docker-compose usa la 0 (event streams), la 1 (broker de Celery)
    y la 2 (result backend) mientras uno corre la suite en la máquina de
    desarrollo. Con el worker vivo bloqueado en `BRPOP default` sobre la base 1,
    un test que encola y lee se queda sin su mensaje: el rojo sale tres capas
    más allá como `assert len(raw) == 1` con cero elementos, y no menciona
    Docker por ningún lado. Pasó de verdad —una tanda de 4 shards repartida
    sobre las bases 1-4 tumbó seis tests de despacho— y el diagnóstico natural
    («flaky») es el que borra la guarda en vez del defecto.

    Encima el daño va en las dos direcciones: los tests hacen `DEL default`, o
    sea que tirarían trabajo real encolado del stack.
    """
    from tests.integration._redis_url import PLATFORM_REDIS_DATABASES, _reject_platform_database

    assert sorted(PLATFORM_REDIS_DATABASES) == [0, 1, 2]
    for indice in sorted(PLATFORM_REDIS_DATABASES):
        with pytest.raises(RuntimeError, match=f"base de Redis {indice}"):
            _reject_platform_database(f"redis://:pwd@localhost:6379/{indice}")
    # Una URL sin path es la base 0 en redis-py: también prohibida.
    with pytest.raises(RuntimeError, match="base de Redis 0"):
        _reject_platform_database("redis://:pwd@localhost:6379")


def test_las_bases_del_arnes_siguen_permitidas() -> None:
    """La guarda de arriba no puede pasarse de frenada.

    La 15 es el default del arnés y la que usa la CI; de la 5 en adelante es lo
    que se reparte al paralelizar en local. Y lo que no se sabe leer no se
    bloquea: la guarda es contra un despiste concreto, no un validador de URLs.
    """
    from tests.integration._redis_url import _reject_platform_database

    for url in (
        "redis://:pwd@localhost:6379/15",
        "redis://:pwd@localhost:6379/5",
        "redis://:pwd@localhost:6379/3",
        "redis://:pwd@redis:6379/no-es-un-numero",
    ):
        _reject_platform_database(url)  # no levanta


def test_la_lista_de_bases_del_stack_no_se_queda_corta() -> None:
    """La lista prohibida se compara con lo que declara el compose.

    `PLATFORM_REDIS_DATABASES` es una constante escrita a mano, y una constante
    escrita a mano se pudre en silencio: el día que un servicio nuevo use la
    base 3, la guarda seguiria diciendo que la 3 es libre y volveria el mismo
    rojo mentiroso. Asi que aqui se lee del `docker/*.yml` que bases usa el
    stack y se exige que todas esten cubiertas.

    Al reves NO se exige: la lista puede ser mas ancha que el compase de hoy (no
    cuesta nada reservar una base y evita que un servicio futuro reabra el
    agujero).
    """
    declaradas = set()
    for compose in sorted((_REPO_ROOT / "docker").glob("*.yml")):
        texto = compose.read_text(encoding="utf-8")
        declaradas.update(int(m) for m in re.findall(r"redis:6379/(\d+)", texto))

    from tests.integration._redis_url import PLATFORM_REDIS_DATABASES

    assert declaradas, "ningun compose declara una base de Redis: ha cambiado el formato"
    sin_cubrir = sorted(declaradas - set(PLATFORM_REDIS_DATABASES))
    assert not sin_cubrir, (
        "el stack usa bases de Redis que la guarda del arnes NO prohibe: "
        f"{sin_cubrir}. Anadelas a PLATFORM_REDIS_DATABASES en "
        "tests/integration/_redis_url.py, o los tests que encolen sobre ellas "
        "perderan sus mensajes contra el worker vivo."
    )
