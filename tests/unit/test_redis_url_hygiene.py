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
    # La fuente de verdad. Aquí es donde la URL se construye (con credencial), y
    # el docstring cita el literal roto para explicar por qué existe el módulo.
    "_redis_url.py": ("es el módulo que resuelve la URL: el único sitio donde puede escribirse"),
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


def _literales_de_redis(ruta: Path) -> list[tuple[int, str]]:
    """Los literales de cadena del fichero que contienen una URL de Redis local."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    hallazgos: list[tuple[int, str]] = []
    for nodo in ast.walk(arbol):
        es_cadena = isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
        if es_cadena and any(p in nodo.value for p in _PATRONES):
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
