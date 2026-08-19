"""El troceo de `pricing/litellm_sync.py` en paquete no puede cambiar el contrato.

Plan prod-16, ``task_prod16_12``: «`pricing/litellm_sync.py` (1338): extraer
sub-módulos cohesivos». Es la cuarta y última pieza de esa tarea, tras
`routers/agents`, `workers/backup_destinations` y `routers/marketplace`.

## Antes de partirlo hubo que comprobar que no es lo que su nombre sugiere

El principio nº 9 de CLAUDE.md dice «**LiteLLM ya no se usa**». Un fichero de
1338 líneas llamado `litellm_sync` es, leído desde ahí, un candidato a borrarse
antes que a trocearse. No lo es, y su propio docstring lo dice: LiteLLM publica
un JSON comunitario de precios públicos y este módulo lo lee **como fuente de
datos**, por `httpx`, sin dependencia de `litellm` y sin tocar el catálogo
cerrado de runtimes del ADR 0021. Trocear es lo correcto; borrar habría dejado
la plataforma sin catálogo de precios.

## Qué se puede romper aquí, y no es un import

1. **`__all__` y los 33 importadores.** El paquete es una fachada de re-export;
   si un nombre se cae, se cae en el arranque y es ruidoso. Barato de cubrir,
   se cubre.

2. **`KIND_TO_LITELLM_FAMILIES`.** Es la tabla que decide **qué precios entran
   al catálogo**, atada a dos ADR (0021 y 0028) y con un comentario que pide
   extenderla «deliberadamente, nunca en silencio». Perder una familia al mover
   el bloque no rompe nada visible: el sync sigue corriendo y simplemente deja
   de importar los precios de esos modelos. Se descubriría por un coste mal
   calculado, semanas después.

3. **La frontera entre calcular y escribir.** La pantalla de precios enseña el
   diff ANTES de que un humano confirme nada, así que `compute_sync_diff` no
   puede escribir. En el monolito eso era una convención invisible; el troceo la
   convierte en estructura —`feed`, `families`, `diff` y `classification` no
   escriben; `apply` y `retire` sí— y el test de abajo la sostiene.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


#: `__all__` literal del monolito de 1338 líneas, capturado el 2026-08-19.
PUBLIC_API_BEFORE_THE_SPLIT: tuple[str, ...] = (
    "DEFAULT_LITELLM_FEED_URL",
    "KIND_TO_LITELLM_FAMILIES",
    "LARGE_INCREASE_THRESHOLD",
    "SKIP_FAMILY_NOT_ACTIVE",
    "DiffStatus",
    "DiscontinuedModel",
    "HttpxPriceFeedFetcher",
    "LargeIncrease",
    "LargeIncreaseNotConfirmedError",
    "MappedPrice",
    "ModelClassification",
    "ModelClassificationSet",
    "ModelStatus",
    "PriceDiffRow",
    "PriceFeedError",
    "PriceFeedFetcher",
    "SkippedEntry",
    "StaticPriceFeedFetcher",
    "SyncDiff",
    "SyncSummary",
    "active_litellm_families",
    "apply_sync_from_litellm",
    "classify_models",
    "close_out_of_scope_families",
    "compute_sync_diff",
    "discontinue_dropped_models",
    "families_for_kinds",
    "map_entry",
    "parse_feed",
    "sync_prices_from_litellm",
)

#: La tabla `kind` -> familias del feed, capturada del monolito. Decide qué
#: precios entran al catálogo (ADR 0021 + ADR 0028).
FAMILIES_BEFORE_THE_SPLIT: dict[str, frozenset[str]] = {
    "claude_sdk": frozenset({"anthropic"}),
    "azure_foundry": frozenset({"azure", "azure_ai", "openai"}),
    "copilot": frozenset({"openai", "anthropic"}),
    "ollama": frozenset({"ollama"}),
}

#: Los dos vocabularios que viajan a la UI y al audit de sync, capturados del
#: monolito con `git show HEAD:`. **Escritos a mano la primera vez y estaban
#: mal**: los cinco valores de `DiffStatus` eran inventados
#: (`created`/`large_increase_deferred`/`skipped`) y el test los cazó a la
#: primera pasada. Se deja anotado porque la lección es la del §2 de
#: `verificar-antes-de-implementar`: un contrato se COPIA de la fuente, nunca se
#: recuerda — si lo hubiera escrito «para que pasara», habría fijado un
#: vocabulario que no existe.
DIFF_STATUS_VALUES = ("added", "updated", "unchanged", "increased", "removed")
MODEL_STATUS_VALUES = ("new", "discontinued", "changed", "unchanged")

#: Módulos que NO pueden escribir en la sesión, y el porqué de cada uno.
READ_ONLY_MODULES = {
    "feed": "lee y mapea el JSON del feed; no conoce la base",
    "families": "deriva las familias de los providers activos; sólo SELECT",
    "diff": "calcula qué cambiaría — la UI lo enseña ANTES de que nadie confirme",
    "classification": "traduce el diff a etiquetas de pantalla; ni siquiera ve la sesión",
}
WRITING_MODULES = ("apply", "retire")

#: Marcas de escritura sobre la sesión de SQLAlchemy.
_WRITES = ("session.add", ".flush(", ".commit(", "update(", "insert(", "delete(")


def _package_dir() -> Path:
    import api_server.pricing.litellm_sync as package

    assert hasattr(package, "__path__"), (
        "pricing/litellm_sync sigue siendo un módulo suelto: task_prod16_12 sin acabar"
    )
    return Path(package.__path__[0])


def test_every_public_name_survives_the_split() -> None:
    """Los 30 nombres de `__all__` siguen colgando de la fachada, y son los mismos."""
    import api_server.pricing.litellm_sync as package

    assert tuple(package.__all__) == PUBLIC_API_BEFORE_THE_SPLIT
    missing = [name for name in PUBLIC_API_BEFORE_THE_SPLIT if not hasattr(package, name)]
    assert not missing, f"`__all__` los nombra pero la fachada no los sirve: {missing}"


def test_the_family_map_is_unchanged() -> None:
    """La tabla que decide qué precios entran al catálogo, intacta.

    Perder una familia aquí no rompe nada visible: el sync corre igual y deja de
    importar esos precios. Se descubre por un coste mal calculado.
    """
    from api_server.pricing.litellm_sync import KIND_TO_LITELLM_FAMILIES

    assert KIND_TO_LITELLM_FAMILIES == FAMILIES_BEFORE_THE_SPLIT


def test_the_two_status_vocabularies_are_unchanged() -> None:
    """`DiffStatus` viaja al audit de sync y `ModelStatus` a la pantalla."""
    from api_server.pricing.litellm_sync import DiffStatus, ModelStatus

    assert tuple(m.value for m in DiffStatus) == DIFF_STATUS_VALUES
    assert tuple(m.value for m in ModelStatus) == MODEL_STATUS_VALUES


def test_litellm_sync_is_a_package_split_by_the_direction_of_the_data() -> None:
    """Es un paquete con un módulo por etapa, no un fichero de 1338 líneas.

    El test que estaba ROJO antes de esta pieza de `task_prod16_12`.
    """
    modules = sorted(
        path.stem
        for path in _package_dir().glob("*.py")
        if path.stem != "__init__" and not path.stem.startswith("_")
    )
    assert len(modules) >= 5, f"un paquete de {len(modules)} módulo(s) no es un troceo"
    expected = sorted([*READ_ONLY_MODULES, *WRITING_MODULES])
    assert modules == expected, f"cambiaron las etapas del paquete: {modules} != {expected}"


def test_the_facade_defines_nothing_of_its_own() -> None:
    """La fachada re-exporta; no define.

    «Mover el bulto no es partir» —la lección que el panel pagó con
    `mcp-server-sections.tsx`, 1125 líneas mudadas de fichero con la guarda de
    tamaño dando OK—. Aquí el atajo equivalente es dejar el monolito dentro del
    `__init__.py`.
    """
    source = (_package_dir() / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    definitions = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert not definitions, f"la fachada define código propio: {definitions}"


def test_computing_the_diff_cannot_write_to_the_catalog() -> None:
    """`feed`, `families`, `diff` y `classification` no escriben. `apply` y `retire`, sí.

    Es la frontera que el troceo hace visible, y no es estética: la pantalla de
    precios enseña el diff ANTES de que un humano confirme las subidas grandes.
    Un `session.add` que se cuele en `compute_sync_diff` haría que **mirar** el
    diff aplicara precios — y como el flujo de la UI llama a las dos cosas
    seguidas cuando el humano confirma, el síntoma sería un precio duplicado en
    el histórico, no un error.
    """
    package_dir = _package_dir()

    offenders = []
    for module, reason in READ_ONLY_MODULES.items():
        source = (package_dir / f"{module}.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        found = [mark for mark in _WRITES if mark in code]
        if found:
            offenders.append(f"{module}.py escribe ({found}) y no debería: {reason}")
    assert not offenders, "\n".join(offenders)

    # Y la otra mitad, para que la guarda no pase en vacío el día que la
    # detección deje de funcionar: los que SÍ escriben tienen que verse.
    for module in WRITING_MODULES:
        source = (package_dir / f"{module}.py").read_text(encoding="utf-8")
        assert any(mark in source for mark in _WRITES), (
            f"{module}.py debería escribir en el catálogo y la detección no ve "
            "ninguna escritura: la guarda está pasando en vacío"
        )
