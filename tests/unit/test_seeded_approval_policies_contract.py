"""Contrato: TODA política de aprobación SEMBRADA lista las 13 canónicas (ADR 0153).

Este es el test que habría cazado `external_http` el día que se escribió.

El [ADR 0104](../../docs/05-architecture-decisions/0104-default-approval-policy-preset.md)
razonó que no hacía falta ninguna guarda porque «todos los presets construyen sus
`decisions` sobre `_all(CATEGORIES, ...)`». Cierto para los cuatro presets del
catálogo; **falso para los esqueletos de las plantillas de proyecto**, que es lo
que la adopción copia a `projects.human_approval_policy` y, por tanto, lo que de
verdad decide si una acción para. Aquel esqueleto listaba **4** claves, una de
ellas (`external_http`) inexistente en `APPROVAL_CATEGORIES`: una intención
escrita que ningún `review()` consultaba jamás, con diez categorías cayendo a
`auto` por omisión — incluso en plantillas que la UI presenta como «Producción».

Lo que se fija aquí, para las dos familias a la vez:

  1. el mapa `categories` cubre **exactamente** las 13 canónicas — ni una menos
     (categoría implícita = decisión en manos de un default del código, no de la
     política) ni una más (clave fantasma que no gatea nada);
  2. cada decisión es `auto` o `human_required`;
  3. la política trae `unlisted_category`, de modo que una categoría futura
     tenga una respuesta ESCRITA en vez de heredar el fail-open.

La lista canónica se DERIVA de `APPROVAL_CATEGORIES`. Copiarla a mano aquí
reproduciría el fallo que el test existe para impedir: dos listas que se creen la
misma y divergen en silencio.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Any

import pytest
from shared_domain.approval_categories import APPROVAL_CATEGORIES

pytestmark = pytest.mark.unit

_VALID_DECISIONS = frozenset({"auto", "human_required"})

#: `__main__` corre el seeder por CLI; no aporta políticas y no queremos su
#: cuerpo de módulo en una suite unitaria.
_SKIP_MODULES = frozenset({"api_server.seeds.__main__"})


def _seed_modules() -> list[ModuleType]:
    """Todos los módulos de `api_server.seeds`, importados.

    Se descubren en vez de enumerarse a mano: un fichero de seeds NUEVO con una
    política a mano entra solo en el contrato, que es justo el agujero por el que
    se coló el esqueleto de las plantillas.
    """
    import api_server.seeds as seeds_pkg

    modules: list[ModuleType] = [seeds_pkg]
    for info in pkgutil.walk_packages(seeds_pkg.__path__, prefix="api_server.seeds."):
        if info.name in _SKIP_MODULES:
            continue
        modules.append(importlib.import_module(info.name))
    return modules


def _flatten(value: Any) -> list[Any]:
    if isinstance(value, tuple | list):
        return [item for entry in value for item in _flatten(entry)]
    return [value]


def _seeded_policies() -> list[tuple[str, dict[str, Any]]]:
    """`(origen, política)` de cada política sembrada: presets Y esqueletos.

    La política se normaliza a la forma que acaba en la BD —un dict con
    `categories` y `unlisted_category`— venga de un preset del catálogo
    (`approval_policy_templates.categories`) o de una plantilla de proyecto
    (`projects.human_approval_policy`).
    """
    from api_server.seeds.builtin_approval_policies import BuiltinPolicy
    from api_server.seeds.builtin_project_templates import BuiltinProjectTemplate

    found: list[tuple[str, dict[str, Any]]] = []
    seen: set[int] = set()
    for module in _seed_modules():
        for attr_name, attr_value in vars(module).items():
            if attr_name.startswith("__"):
                continue
            for obj in _flatten(attr_value):
                if id(obj) in seen:
                    continue
                if isinstance(obj, BuiltinPolicy):
                    seen.add(id(obj))
                    found.append(
                        (
                            f"preset {obj.slug!r} ({module.__name__}.{attr_name})",
                            {
                                "preset": obj.slug,
                                "categories": obj.decisions,
                                "unlisted_category": obj.unlisted_category,
                            },
                        )
                    )
                elif isinstance(obj, BuiltinProjectTemplate):
                    seen.add(id(obj))
                    if obj.human_approval_policy is None:
                        # Sin política explícita hereda el preset por defecto
                        # (ADR 0104), que sí está completo. No hay hueco.
                        continue
                    found.append(
                        (
                            f"plantilla {obj.slug!r} ({module.__name__}.{attr_name})",
                            obj.human_approval_policy,
                        )
                    )
    return found


#: Se resuelve una vez, al importar: `parametrize` necesita la lista en tiempo
#: de recolección y las tres afirmaciones de abajo miran la MISMA.
_SEEDED: list[tuple[str, dict[str, Any]]] = _seeded_policies()

_each_seeded_policy = pytest.mark.parametrize(
    "origin,policy", _SEEDED, ids=[origin for origin, _ in _SEEDED]
)


def test_discovery_actually_finds_the_seeded_policies() -> None:
    """Guarda anti-vacío: un contrato que no encuentra nada pasa siempre.

    Sin esto, un rename de `BuiltinPolicy` o un cambio de estructura del paquete
    dejaría los tests de abajo en verde sobre una lista vacía — el modo de fallo
    nº2 de `verificar-antes-de-implementar.md`.
    """
    origins = [origin for origin, _ in _seeded_policies()]

    # 4 presets del catálogo + 8 plantillas built-in + la de CodeIgniter 4.
    assert len(origins) >= 13, origins
    assert any(o.startswith("preset ") for o in origins)
    assert any(o.startswith("plantilla ") for o in origins)


@_each_seeded_policy
def test_seeded_policy_lists_exactly_the_canonical_categories(
    origin: str, policy: dict[str, Any]
) -> None:
    """Ni categorías implícitas ni claves fantasma. Las 13, exactamente."""
    categories = policy.get("categories")
    assert isinstance(categories, dict), f"{origin}: la política no trae mapa `categories`"

    canonical = set(APPROVAL_CATEGORIES)
    listed = set(categories)

    assert not (canonical - listed), (
        f"{origin}: categorías canónicas SIN decidir → las resuelve un default "
        f"del código, no la política: {sorted(canonical - listed)}"
    )
    assert not (listed - canonical), (
        f"{origin}: claves que NO son categorías canónicas → ningún review() las "
        f"consulta jamás: {sorted(listed - canonical)}"
    )


@_each_seeded_policy
def test_seeded_policy_decisions_are_valid(origin: str, policy: dict[str, Any]) -> None:
    bad = {
        cat: dec
        for cat, dec in policy["categories"].items()
        if not isinstance(dec, str) or dec not in _VALID_DECISIONS
    }
    assert not bad, f"{origin}: decisiones fuera del vocabulario {sorted(_VALID_DECISIONS)}: {bad}"


@_each_seeded_policy
def test_seeded_policy_declares_what_happens_with_an_unlisted_category(
    origin: str, policy: dict[str, Any]
) -> None:
    """ADR 0153: la política decide su propio fail-open/fail-closed, no el código."""
    assert "unlisted_category" in policy, (
        f"{origin}: sin `unlisted_category`, lo no listado lo decide un default "
        f"del código (hoy `auto`, fail-open)"
    )
    assert (
        policy["unlisted_category"] in _VALID_DECISIONS
    ), f"{origin}: `unlisted_category` = {policy['unlisted_category']!r}"
    # Y no puede colarse dentro del mapa de categorías, donde nadie la leería.
    assert "unlisted_category" not in policy["categories"], (
        f"{origin}: `unlisted_category` va como clave HERMANA de `categories`, " f"no dentro"
    )


def test_production_grade_presets_gate_the_categories_they_advertise() -> None:
    """Una plantilla `production` no puede dejar PII ni altas de usuario en auto.

    Es la afirmación concreta del ADR 0153: la UI presentaba como «Producción»
    plantillas que dejaban `data_export_pii`, `user_management` y
    `external_communication` corriendo sin humano.
    """
    strict = {"data_export_pii", "user_management", "external_communication", "production_deploy"}
    checked = 0
    for origin, policy in _SEEDED:
        if policy.get("preset") not in {"production", "customer-external"}:
            continue
        checked += 1
        for category in strict:
            assert (
                policy["categories"][category] == "human_required"
            ), f"{origin}: preset {policy['preset']!r} deja {category!r} en auto"
    assert checked >= 2, "no se ha comprobado ninguna plantilla de producción"


def test_a_policy_is_never_laxer_than_the_preset_it_declares() -> None:
    """El nombre del preset es un SUELO, no una etiqueta.

    Este es el defecto exacto que había: las dos plantillas de producción se
    escribían como ``{**_POLICY_DEV_SKELETON, "preset": "production"}`` — decían
    `production` y decidían como `development`. El test de arriba no lo
    distingue, porque el preset `development` ya gatea PII, altas de usuario y
    despliegue; lo que las delataba era `code_changes` / `git_commit` /
    `external_http_get` corriendo en auto bajo una etiqueta «Producción».

    Se permite ENDURECER (una plantilla puede gatear más que su preset); nunca
    aflojar. Sin esta regla, `preset` es prosa.
    """
    from api_server.seeds.builtin_approval_policies import BUILTIN_POLICIES, preset_decisions

    known = {p.slug for p in BUILTIN_POLICIES}
    checked = 0
    for origin, policy in _SEEDED:
        preset = policy.get("preset")
        if preset not in known:
            continue
        checked += 1
        floor = preset_decisions(str(preset))
        laxer = {
            cat
            for cat, decision in floor.items()
            if decision == "human_required" and policy["categories"].get(cat) != "human_required"
        }
        assert not laxer, (
            f"{origin}: declara el preset {preset!r} pero deja en auto categorías "
            f"que ese preset gatea: {sorted(laxer)}"
        )
    assert checked >= 13, f"solo se han comprobado {checked} políticas con preset declarado"


# ---------------------------------------------------------------------------
# Regresiones con nombre propio
# ---------------------------------------------------------------------------
def test_the_phantom_external_http_key_is_gone() -> None:
    """`external_http` no existe en `APPROVAL_CATEGORIES`; nunca gateó nada.

    El test genérico de arriba ya lo cubre, pero este deja el nombre escrito:
    dentro de seis meses, un `grep external_http` tiene que llevar a un test que
    explique por qué esa clave no puede volver.
    """
    assert "external_http" not in APPROVAL_CATEGORIES
    assert {"external_http_get", "external_http_post"} <= set(APPROVAL_CATEGORIES)

    from api_server.seeds.builtin_project_templates import _POLICY_DEV_SKELETON

    assert "external_http" not in _POLICY_DEV_SKELETON["categories"]
    assert _POLICY_DEV_SKELETON["categories"]["external_http_get"] == "auto"


def test_development_leaves_external_http_post_in_auto_on_purpose() -> None:
    """`auto`, y NO es un hueco: es decisión del operador del 2026-08-02.

    Este test existe porque la corrección obvia salta a la vista —«desarrollo
    gatea casi todo menos esto, será un olvido»— y sería un error caro.

    `external_http_post` no cubre «una llamada HTTP»: cubre **todas las tools
    MCP** del proyecto. `spec_approval_category` mapea aquí `mcp_tool` y
    `http_endpoint`, y `import_mcp_tools` da de alta las tools con
    `security_level="sandboxed"` por defecto. O sea que ponerla en
    `human_required` hace que CADA integración del proyecto —Jira, GitHub, la
    que sea— pida aprobación desde el primer día. Eso no supervisa: amontona una
    cola que se despacha aprobando sin leer, y ese hábito se lleva luego al
    proyecto donde sí importaba.

    La palanca para apretar aquí es por-herramienta, no por-categoría: marcar
    `security_level="safe"` las tools de confianza y dejar gateadas las demás.
    Quien quiera el interruptor de todo o nada tiene el preset `production`.

    Si algún día se cambia, que sea leyendo esto y no por simetría.
    """
    from api_server.seeds.builtin_approval_policies import preset_policy
    from api_server.seeds.builtin_project_templates import _POLICY_DEV_SKELETON

    assert preset_policy("development")["categories"]["external_http_post"] == "auto"
    assert _POLICY_DEV_SKELETON["categories"]["external_http_post"] == "auto"
    # Y lo que sí para en desarrollo, que es el contrapeso: las dos acciones que
    # SALEN del proyecto. Son raras, así que no atascan; y cuando disparan, es
    # por algo que el operador querría haber visto.
    for categoria in ("external_communication", "data_migration"):
        assert _POLICY_DEV_SKELETON["categories"][categoria] == "human_required", (
            f"{categoria} sale del proyecto (notifica a personas / mueve datos a "
            "otra KB del tenant): en desarrollo se para"
        )


def test_policy_skeleton_rejects_a_non_canonical_override() -> None:
    """La guarda en construcción: el error revienta donde se escribe, no en la BD."""
    from api_server.seeds.builtin_project_templates import _policy_skeleton

    with pytest.raises(ValueError, match="external_http"):
        _policy_skeleton("development", external_http="human_required")


def test_policy_skeleton_applies_a_canonical_override_without_losing_the_rest() -> None:
    from api_server.seeds.builtin_project_templates import _policy_skeleton

    skeleton = _policy_skeleton("development", external_http_post="auto")

    assert skeleton["categories"]["external_http_post"] == "auto"
    # y el override no descabala el resto del mapa
    assert set(skeleton["categories"]) == set(APPROVAL_CATEGORIES)
    assert skeleton["categories"]["user_management"] == "human_required"


def test_preset_policy_is_the_shape_that_lands_in_the_database() -> None:
    """`preset_policy()` devuelve lo que debe vivir en `projects.human_approval_policy`."""
    from api_server.seeds.builtin_approval_policies import preset_policy

    policy = preset_policy("production")

    assert set(policy) == {"preset", "categories", "unlisted_category"}
    assert policy["preset"] == "production"
    assert set(policy["categories"]) == set(APPROVAL_CATEGORIES)
    assert policy["unlisted_category"] == "human_required"
    # Y una copia: mutar el resultado no puede tocar el preset sembrado.
    policy["categories"]["code_changes"] = "auto"
    assert preset_policy("production")["categories"]["code_changes"] == "human_required"


def test_unlisted_category_follows_the_preset_strictness() -> None:
    """ADR 0153 (C): fail-closed donde el preset es estricto, `auto` donde no."""
    from api_server.seeds.builtin_approval_policies import preset_unlisted_category

    assert preset_unlisted_category("sandbox") == "auto"
    assert preset_unlisted_category("development") == "auto"
    assert preset_unlisted_category("production") == "human_required"
    assert preset_unlisted_category("customer-external") == "human_required"
    # Un slug desconocido no puede caer a fail-open silencioso.
    assert preset_unlisted_category("does-not-exist") == preset_unlisted_category("development")
