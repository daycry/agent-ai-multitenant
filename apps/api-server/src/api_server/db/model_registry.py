"""Carga exhaustiva de la capa de modelos, para que ``Base.metadata`` esté completa.

## Para qué existe

Un mapeador de SQLAlchemy se registra en ``Base.metadata`` **al importarse su
módulo**. Quien necesite la metadata COMPLETA —hoy sólo el `env.py` de
Alembic— tiene que haber importado los ~50 módulos de `api_server.db`, no un
agregador.

El `env.py` importaba uno solo (``api_server.db.models``, el agregador de la
fase 0) y por eso veía **34 tablas de 84**. Lo que hacía eso peor que un falso
verde: `incoming_webhook_configs.project_id` referencia `projects`, que quedaba
fuera, así que `alembic check` no decía «no hay deriva» ni «hay deriva» — moría
con ``NoReferencedTableError``, un traceback de SQLAlchemy que se lee como
problema local de quien lo ejecuta. La detección de deriva del esquema llevaba
así todo el tiempo que llevan existiendo esas tablas.

## Por qué recorre el paquete y no es una lista de imports

Arreglarlo añadiendo los imports que faltaban a `env.py` habría reproducido el
**mismo modo de fallo un piso más arriba**: una lista escrita a mano envejece en
cuanto llega `db/foo.py`, que es exactamente cómo envejeció `db/models.py`. Aquí
la fuente de verdad es el directorio.

Dos detalles que no son adorno:

* ``walk_packages`` **se come los ImportError en silencio** si no le pasas
  ``onerror``. Sin él, un módulo que no importe volvería a dejar sus tablas fuera
  de la metadata sin decir nada, que es el fallo que este módulo viene a cerrar.
* El recorrido cubre `api_server.db` y sólo ese paquete. Que no haya modelos
  fuera lo vigila
  ``tests/unit/test_alembic_metadata_is_complete.py::test_no_model_lives_outside_the_walked_package``;
  el día que aparezca uno en otro árbol, hay que ampliar :data:`_MODEL_PACKAGES`.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

#: Paquetes que contienen modelos declarativos. Ampliar exige actualizar la
#: guarda de `test_no_model_lives_outside_the_walked_package`, que hoy exige que
#: no haya ``__tablename__`` fuera de aquí.
_MODEL_PACKAGES: tuple[str, ...] = ("api_server.db",)


def _reraise(module_name: str) -> None:
    """``onerror`` de ``walk_packages``: convierte el silencio en un fallo.

    Sin esto, ``walk_packages`` captura y descarta el ImportError de cualquier
    subpaquete que no cargue, y las tablas de ese subárbol desaparecen de la
    metadata sin una línea de log.
    """
    _, error, _ = sys.exc_info()
    raise ImportError(
        f"«{module_name}» no se pudo importar, así que sus tablas quedarían fuera "
        "de Base.metadata y el siguiente autogenerate propondría BORRARLAS. "
        "Arregla el import en vez de saltárselo."
    ) from error


def discover_model_modules() -> tuple[str, ...]:
    """Los nombres de todos los módulos de la capa de datos, sin importarlos aún.

    (``walk_packages`` sí importa los *paquetes* intermedios para poder
    recorrerlos; los módulos hoja los deja para :func:`import_all_models`.)
    """
    found: set[str] = set()
    for package_name in _MODEL_PACKAGES:
        package = importlib.import_module(package_name)
        found.update(
            info.name
            for info in pkgutil.walk_packages(
                package.__path__,
                prefix=f"{package.__name__}.",
                onerror=_reraise,
            )
        )
    return tuple(sorted(found))


def import_all_models() -> tuple[str, ...]:
    """Importa TODOS los módulos de modelos y devuelve sus nombres, ordenados.

    Idempotente (los módulos ya cargados salen de ``sys.modules``), así que se
    puede llamar sin miedo desde el `env.py`, un test o un script.
    """
    names = discover_model_modules()
    for name in names:
        importlib.import_module(name)
    return names
