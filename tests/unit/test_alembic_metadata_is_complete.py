"""``Base.metadata`` ve TODAS las tablas, y el `env.py` de Alembic la carga entera.

## El fallo que motiva este fichero

`apps/api-server/migrations/env.py` importaba **un solo módulo** de la capa de
datos::

    from api_server.db import models as _models  # noqa: F401

`db/models.py` es el agregador de la fase 0. Arrastra córtex, marketplace,
invitaciones y LLM usage, pero **no importa `db/domain`** ni los quince módulos
sueltos que llegaron después. Medido el 2026-08-20: con sólo `db.models`,
``Base.metadata`` tiene **34 tablas de 84**.

Y no falla en silencio, que sería malo: falla **peor**. Como
`incoming_webhook_configs.project_id` referencia `projects`, que se queda fuera,
`alembic check` muere con::

    sqlalchemy.exc.NoReferencedTableError: Foreign key associated with column
    'incoming_webhook_configs.project_id' could not find table 'projects'

Un traceback de SQLAlchemy que se lee como problema de configuración local del
que lo ejecuta, y se ignora. O sea que la detección de deriva del esquema —la
única que avisaría de que un modelo y su tabla dejaron de coincidir— llevaba sin
funcionar el tiempo que llevan existiendo esas tablas, y varios planes la
declaran como criterio de cierre.

## Por qué estas cuatro guardas y no una lista de imports

La tentación es arreglarlo añadiendo a `env.py` los imports que faltan. Es el
**mismo modo de fallo un piso más arriba**: una lista escrita a mano envejece en
cuanto alguien añada `db/foo.py`, exactamente como envejeció `db/models.py`. El
arreglo es un cargador que recorre el paquete
(:func:`api_server.db.model_registry.import_all_models`), y las guardas de aquí
son las que impiden que el cargador se degrade a una lista:

1. :func:`test_the_loader_covers_every_module_on_disk` compara lo que el cargador
   importa contra el **directorio**, no contra una constante. Si alguien lo
   sustituye por imports a mano y olvida uno, esto se pone rojo.
2. :func:`test_every_declared_table_reaches_the_metadata` recorre el fuente
   buscando ``__tablename__`` y exige que las 84 lleguen.
3. :func:`test_no_model_lives_outside_the_walked_package` cierra el agujero de
   arriba: el cargador recorre `api_server.db`, así que un modelo colocado en
   otro paquete volvería a ser invisible.
4. :func:`test_every_foreign_key_resolves` reproduce el fallo concreto: es la
   aserción que estaba roja antes del arreglo.

Todas llevan un mínimo (``>= 80``, ``>= 40``) para que no puedan pasar en vacío
el día que el descubrimiento deje de encontrar nada — §4 de
`docs/03-guides/verificar-antes-de-implementar.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_DB_DIR = _REPO / "apps" / "api-server" / "src" / "api_server" / "db"
_ENV_PY = _REPO / "apps" / "api-server" / "migrations" / "env.py"

#: `__tablename__ = "algo"`. Las 84 declaraciones del repo son literales
#: (comprobado el 2026-08-20), así que un regex basta y no hace falta AST.
_TABLENAME = re.compile(r"""^\s*__tablename__\s*(?::[^=]+)?=\s*["']([a-zA-Z0-9_]+)["']""", re.M)

#: Árboles de código PRODUCTIVO donde puede vivir un modelo. Deliberadamente sin
#: `tests/` (define modelos de juguete) y sin los venvs: la primera versión de
#: este fichero recorría el repo entero y la guarda de outsiders se llenó de
#: `celery_taskmeta` y los ejemplos de la documentación de SQLAlchemy.
_SCAN_ROOTS = ("apps", "packages", "docker", "scripts")

#: Directorios que nunca contienen modelos de este proyecto.
_SKIP_PARTS = frozenset({"__pycache__", "site-packages", "node_modules", ".next", "dist"})


def _module_name_for(path: Path) -> str:
    """Nombre de módulo importable de un fichero bajo `api_server/db/`."""
    relative = path.relative_to(_DB_DIR)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(["api_server", "db", *parts])


def _modules_on_disk() -> set[str]:
    """Todo módulo bajo `api_server/db/`, salvo el paquete raíz."""
    return {
        _module_name_for(path)
        for path in _DB_DIR.rglob("*.py")
        if "__pycache__" not in path.parts and _module_name_for(path) != "api_server.db"
    }


def _declared_tables() -> dict[str, str]:
    """`{nombre_de_tabla: fichero}` para cada ``__tablename__`` del código productivo."""
    declared: dict[str, str] = {}
    for root in _SCAN_ROOTS:
        for path in (_REPO / root).rglob("*.py"):
            if _SKIP_PARTS & set(path.parts) or any(p.startswith(".venv") for p in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover - ficheros raros
                continue
            if "__tablename__" not in source:
                continue
            for table in _TABLENAME.findall(source):
                declared[table] = str(path.relative_to(_REPO))
    return declared


def _loaded_metadata() -> object:
    from api_server.db.base import Base
    from api_server.db.model_registry import import_all_models

    import_all_models()
    return Base.metadata


def test_the_loader_covers_every_module_on_disk() -> None:
    """El cargador se mide contra el DIRECTORIO, no contra una lista.

    Es la guarda que impide volver al modo de fallo original: cualquier forma de
    enumerar módulos a mano (la de `env.py`, la de `db/models.py`) queda roja en
    cuanto el disco tenga uno más.
    """
    from api_server.db.model_registry import import_all_models

    imported = set(import_all_models())
    on_disk = _modules_on_disk()

    missing = sorted(on_disk - imported)
    assert not missing, (
        "el cargador de modelos NO importa estos módulos, así que sus tablas no "
        "llegan a Base.metadata y el autogenerate propondría BORRARLAS:\n"
        + "\n".join(f"  {name}" for name in missing)
    )
    assert len(on_disk) >= 40, (
        f"la guarda dejó de encontrar los módulos de la capa de datos (vio "
        f"{len(on_disk)}): ¿se movió `api_server/db/`?"
    )


def test_every_declared_table_reaches_the_metadata() -> None:
    """Toda tabla declarada en el fuente está en la metadata que ve Alembic."""
    metadata = _loaded_metadata()
    declared = _declared_tables()
    registered = set(metadata.tables)  # type: ignore[attr-defined]

    missing = sorted(f"{table} ({declared[table]})" for table in set(declared) - registered)
    assert not missing, (
        "estas tablas se declaran en el fuente pero NO están en Base.metadata:\n"
        + "\n".join(f"  {item}" for item in missing)
    )
    assert len(declared) >= 80, (
        f"la guarda dejó de encontrar los `__tablename__` del repo (vio "
        f"{len(declared)}): el regex o el árbol han cambiado"
    )


def test_no_model_lives_outside_the_walked_package() -> None:
    """Un modelo fuera de `api_server/db/` sería invisible para el cargador.

    El cargador recorre ese paquete y sólo ese. Si mañana aparece un modelo en
    `workers/` o en `packages/shared-*`, esta guarda avisa de que hay que
    ampliar el recorrido — en vez de descubrirlo por un `DROP TABLE` propuesto.
    """
    declared = _declared_tables()
    allowed = Path("apps") / "api-server" / "src" / "api_server" / "db"
    outsiders = sorted(
        f"{table} -> {source}"
        for table, source in declared.items()
        if not Path(source).is_relative_to(allowed)
    )
    assert not outsiders, (
        "estos modelos viven fuera de `api_server/db/`, que es lo único que "
        "recorre `import_all_models()`: amplía el cargador o muévelos:\n"
        + "\n".join(f"  {item}" for item in outsiders)
    )
    assert len(declared) >= 80, "la guarda dejó de encontrar declaraciones de tabla"


def test_every_foreign_key_resolves() -> None:
    """`sorted_tables` resuelve cada FK: es la aserción que estaba roja.

    Con la metadata a medias esto levanta
    ``NoReferencedTableError: ... 'incoming_webhook_configs.project_id' could not
    find table 'projects'``, que es literalmente lo que devolvía `alembic check`.
    """
    metadata = _loaded_metadata()
    ordered = metadata.sorted_tables  # type: ignore[attr-defined]
    assert len(ordered) >= 80, f"sólo {len(ordered)} tablas ordenables: metadata a medias"


def test_the_alembic_env_loads_the_whole_model_layer() -> None:
    """`env.py` usa el cargador exhaustivo, no un import suelto.

    No se puede importar `env.py` para comprobarlo (al importarse EJECUTA las
    migraciones), así que la guarda es sobre el fuente. Basta: lo que se quiere
    impedir es que alguien vuelva a dejar la metadata a merced de un agregador.
    """
    source = _ENV_PY.read_text(encoding="utf-8")
    assert "import_all_models()" in source, (
        "migrations/env.py ya no llama a `import_all_models()`: la metadata del "
        "autogenerate vuelve a depender de qué importe el agregador de turno, y "
        "`alembic check` volverá a morir con NoReferencedTableError"
    )
