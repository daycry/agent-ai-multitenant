"""Fixtures de los tests UNITARIOS.

## Por qué existe: un test unitario no puede depender de una Redis viva

`db.platform_settings` cachea las lecturas de ajustes de plataforma en Redis. Con
el stack de desarrollo levantado —que es lo normal en esta máquina—, esa Redis
**existe**, así que un test unitario que lee un ajuste escribe en ella y el
siguiente lo lee. Dos tests del mismo fichero dejan de ser independientes sin que
nada lo anuncie.

Eso salió a la luz el 2026-08-02 y de la peor manera posible: `test_budget_models`
llevaba pasando desde siempre **porque la caché estaba rota**. Cada test abre su
propio event loop, el cliente Redis cacheado quedaba atado al loop anterior, la
llamada levantaba, `_cached_read` la capturaba —a propósito: un Redis caído no
debe romper una lectura— y devolvía «no hay caché». Al arreglar ese defecto de
verdad, la caché empezó a acertar y el segundo test del fichero leyó el valor que
había dejado el primero.

Es decir: **arreglar un defecto de producción puso rojo un test que se apoyaba en
él sin saberlo**. El test no estaba mal escrito por descuido; estaba escrito
contra un mundo en el que la caché no funcionaba.

La respuesta no es tocar esos tests uno a uno, sino declarar la regla: en
`tests/unit/` la base de datos es la única fuente de verdad y la caché no
participa. Quien quiera probar la caché lo dice con el marcador.
"""

from __future__ import annotations

from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "uses_platform_cache: el test ejercita la caché de platform_settings "
        "y trae su propio doble de Redis (ver tests/unit/conftest.py)",
    )


@pytest.fixture(autouse=True)
def _hermetic_platform_setting_cache(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La caché de ajustes no participa en los tests unitarios.

    Se corta en `_redis_for_this_loop` y no en `get_redis`: así el único
    consumidor afectado es la caché de ajustes, y todo lo demás que use Redis en
    un test unitario sigue viéndola tal cual.

    El fallo se simula con la excepción que el propio módulo ya captura, en vez
    de devolver un doble vacío: recorre el MISMO camino que un Redis caído en
    producción, así que si algún día ese camino deja de degradar a la base de
    datos, estos tests lo notan.
    """
    if request.node.get_closest_marker("uses_platform_cache"):
        return

    from api_server.db import platform_settings

    def _sin_cache() -> Any:
        raise RuntimeError("caché de platform_settings desactivada en tests/unit/")

    monkeypatch.setattr(platform_settings, "_redis_for_this_loop", _sin_cache)
