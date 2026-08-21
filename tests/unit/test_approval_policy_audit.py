"""Unit — el informe previo del ADR 0153 y la lógica que la 0133 aplica.

Estos tests fijan la mitad que decide el operador al firmar la opción (D):

* **Se COMPLETAN todas las políticas.** Al terminar no queda ni una categoría
  implícita: las 13 canónicas escritas y ``unlisted_category`` presente. Una
  política a medias delega su comportamiento en el ``"auto"`` fijo de
  ``requires_human_approval``, que es justo el agujero que el ADR cierra.
* **Solo CAMBIA el comportamiento en ``production`` y ``customer-external``.**
  En ``sandbox`` y ``development`` lo ausente se escribe ``auto``: se hace
  explícito lo que ya pasaba, y ninguna decisión se mueve.

Las dos mitades son instrucción explícita del operador, no una omisión. El test
:func:`test_development_decides_exactly_the_same_before_and_after` es el que
demuestra la segunda: completar no es endurecer.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from shared_domain.approval_categories import APPROVAL_CATEGORIES

pytestmark = pytest.mark.unit


# El esqueleto que copian las siete plantillas de proyecto (el hecho que motiva
# el ADR): cuatro claves, y una de ellas ni siquiera es canónica.
_SKELETON: dict[str, Any] = {
    "preset": "development",
    "categories": {
        "code_changes": "auto",
        "git_push": "human_required",
        "external_http": "human_required",  # NO existe en APPROVAL_CATEGORIES
        "secrets_access": "human_required",
    },
}

# `legacy-migration`: la UI la presenta como «Producción» y hereda los mismos
# diez huecos del esqueleto.
_PRODUCTION_TEMPLATE: dict[str, Any] = {
    **_SKELETON,
    "preset": "production",
    "categories": {
        **_SKELETON["categories"],
        "data_migration": "human_required",
        "production_deploy": "human_required",
    },
}

_SANDBOX_TEMPLATE: dict[str, Any] = {"preset": "sandbox", "categories": {"all": "auto"}}


# ---------------------------------------------------------------------------
# Completar: nadie se queda a medias
# ---------------------------------------------------------------------------
def test_every_canonical_category_is_written_plus_the_unlisted_key() -> None:
    """La invariante del ADR: cero categorías implícitas tras completar."""
    from api_server.cli.approval_policy_audit import (
        UNLISTED_CATEGORY_KEY,
        complete_policy,
        policy_categories,
    )

    for policy in (_SKELETON, _PRODUCTION_TEMPLATE, _SANDBOX_TEMPLATE):
        completed = complete_policy(policy)
        categories = policy_categories(completed)
        assert set(APPROVAL_CATEGORIES) <= set(categories), (
            f"la política quedó incompleta: faltan "
            f"{sorted(set(APPROVAL_CATEGORIES) - set(categories))}"
        )
        assert UNLISTED_CATEGORY_KEY in completed


def test_a_development_policy_writes_the_missing_ten_all_in_auto() -> None:
    """Se tocan los diez huecos, y ninguno cambia de decisión."""
    from api_server.cli.approval_policy_audit import plan_policy

    plan = plan_policy(_SKELETON)

    assert plan.preset == "development"
    assert len(plan.writes) == 10, plan.writes
    assert set(plan.writes.values()) == {"auto"}
    assert plan.changes == {}, "development no debe endurecerse: la cola la atendería nadie"
    assert plan.unlisted == "auto"


def test_a_production_policy_writes_the_missing_ones_strict() -> None:
    """En producción lo implícito se escribe con el criterio del preset."""
    from api_server.cli.approval_policy_audit import plan_policy

    plan = plan_policy(_PRODUCTION_TEMPLATE)

    assert plan.preset == "production"
    # 5 canónicas ya escritas (code_changes, git_push, secrets_access,
    # data_migration, production_deploy) → faltan 8.
    assert len(plan.writes) == 8, plan.writes
    assert set(plan.writes.values()) == {"human_required"}
    assert plan.changes == plan.writes, "en producción todo lo escrito cambia la decisión"
    assert "data_export_pii" in plan.changes
    assert "user_management" in plan.changes
    assert plan.unlisted == "human_required"


def test_a_sandbox_policy_stays_entirely_auto() -> None:
    from api_server.cli.approval_policy_audit import plan_policy

    plan = plan_policy(_SANDBOX_TEMPLATE)

    assert plan.preset == "sandbox"
    assert len(plan.writes) == 13
    assert set(plan.writes.values()) == {"auto"}
    assert plan.changes == {}
    assert plan.unlisted == "auto"


def test_customer_external_is_the_other_strict_preset() -> None:
    from api_server.cli.approval_policy_audit import plan_policy

    plan = plan_policy({"preset": "customer-external", "categories": {"code_changes": "auto"}})

    assert set(plan.writes.values()) == {"human_required"}
    assert plan.unlisted == "human_required"
    # Y la decisión escrita a mano sigue en pie.
    assert "code_changes" not in plan.writes


# ---------------------------------------------------------------------------
# Lo escrito a mano no se pisa
# ---------------------------------------------------------------------------
def test_a_hand_written_decision_is_never_overwritten() -> None:
    """`data_export_pii: auto` puesto a conciencia bajo `production` se respeta."""
    from api_server.cli.approval_policy_audit import complete_policy, plan_policy

    policy = {
        "preset": "production",
        "categories": {**_PRODUCTION_TEMPLATE["categories"], "data_export_pii": "auto"},
    }

    plan = plan_policy(policy)
    assert "data_export_pii" not in plan.writes

    completed = complete_policy(policy)
    assert completed["categories"]["data_export_pii"] == "auto"


def test_an_already_present_unlisted_key_is_respected() -> None:
    from api_server.cli.approval_policy_audit import complete_policy, plan_policy

    policy = {"preset": "production", "categories": {}, "unlisted_category": "auto"}

    assert plan_policy(policy).unlisted is None
    assert complete_policy(policy)["unlisted_category"] == "auto"


def test_stray_non_canonical_keys_survive() -> None:
    """`external_http` no gatea nada, pero borrarlo no es «rellenar lo ausente».

    El contrato de la 0133 es SOLO añadir. Borrar una clave que un operador
    escribió es la única forma en que esta migración podría sorprender a nadie,
    y el arreglo del origen es del carril de seeds.
    """
    from api_server.cli.approval_policy_audit import complete_policy, plan_policy

    assert plan_policy(_SKELETON).stray_keys == ("external_http",)
    assert complete_policy(_SKELETON)["categories"]["external_http"] == "human_required"
    assert complete_policy(_SANDBOX_TEMPLATE)["categories"]["all"] == "auto"


def test_completing_twice_changes_nothing_the_second_time() -> None:
    from api_server.cli.approval_policy_audit import complete_policy, plan_policy

    for policy in (_SKELETON, _PRODUCTION_TEMPLATE, _SANDBOX_TEMPLATE):
        once = complete_policy(policy)
        assert complete_policy(once) == once
        assert plan_policy(once).touches is False


def test_completing_does_not_mutate_the_input() -> None:
    from api_server.cli.approval_policy_audit import complete_policy

    original = {"preset": "production", "categories": {"code_changes": "auto"}}
    complete_policy(original)

    assert original == {"preset": "production", "categories": {"code_changes": "auto"}}


# ---------------------------------------------------------------------------
# El test que demuestra que completar NO es endurecer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("policy", [_SKELETON, _SANDBOX_TEMPLATE], ids=["development", "sandbox"])
def test_development_decides_exactly_the_same_before_and_after(policy: dict[str, Any]) -> None:
    """El gate real decide lo MISMO en las 13 canónicas y en una desconocida.

    Si este test se pone rojo, alguien ha «arreglado» la migración endureciendo
    desarrollo — y una cola de aprobaciones que nadie atiende enseña a aprobar
    sin leer, que es el hábito que luego se lleva al proyecto donde sí importaba.
    """
    from api_server.cli.approval_policy_audit import complete_policy
    from api_server.db.approval_repo import requires_human_approval

    completed = complete_policy(policy)

    for category in (*APPROVAL_CATEGORIES, "una_categoria_que_no_existe"):
        assert requires_human_approval(policy, category) == requires_human_approval(
            completed, category
        ), f"la categoría {category} cambió de decisión al completar una política no estricta"


def test_production_writes_the_gate_where_the_policy_said_nothing() -> None:
    """El cambio que el operador pidió medir: 8 categorías dejan de ser implícitas.

    Se mide sobre los DATOS —qué queda escrito `human_required` donde no había
    nada— porque es lo que sobrevive a un cambio del lector. Contra el gate
    previo al ADR 0153 eso era además un cambio de decisión real.
    """
    from api_server.cli.approval_policy_audit import complete_policy, plan_policy

    before = dict(_PRODUCTION_TEMPLATE["categories"])
    after = complete_policy(_PRODUCTION_TEMPLATE)["categories"]
    newly_gated = sorted(
        category
        for category in APPROVAL_CATEGORIES
        if category not in before and after[category] == "human_required"
    )

    assert newly_gated == sorted(plan_policy(_PRODUCTION_TEMPLATE).changes)
    assert newly_gated == sorted(
        [
            "external_communication",
            "external_http_get",
            "external_http_post",
            "git_commit",
            "infra_provision",
            "secret_rotation",
            "data_export_pii",
            "user_management",
        ]
    )


def test_the_written_decision_is_what_the_gate_already_decides() -> None:
    """La propiedad de la que cuelga todo: escribir no mueve ninguna decisión.

    Si esto se pone rojo, la 0133 ha dejado de ser «hacer explícito lo que ya
    pasa» y ha pasado a cambiar comportamiento sin decirlo.
    """
    from api_server.cli.approval_policy_audit import decision_for_absent
    from api_server.db.approval_repo import requires_human_approval

    for preset in (None, "sandbox", "development", "production", "customer-external"):
        policy: dict[str, Any] = {"categories": {}}
        if preset is not None:
            policy["preset"] = preset
        gate_says = requires_human_approval(policy, "external_http_post")
        assert (decision_for_absent(preset) == "human_required") is gate_says, preset


# ---------------------------------------------------------------------------
# Clasificación del preset
# ---------------------------------------------------------------------------
def test_a_policy_without_a_declared_preset_is_written_fail_closed() -> None:
    """No se adivina el preset: se escribe lo que el gate ya decide (cerrado).

    Clasificarla por parecido —«su mapa calca al de sandbox»— la AFLOJARÍA:
    `requires_human_approval` resuelve hoy una política sin preset con
    `UNLISTED_FALLBACK_DECISION`. Esta migración nunca afloja.
    """
    from api_server.cli.approval_policy_audit import classify_preset, plan_policy
    from api_server.db.approval_repo import UNLISTED_FALLBACK_DECISION
    from api_server.seeds.builtin_approval_policies import preset_decisions

    assert classify_preset({"categories": {"code_changes": "auto"}}) is None
    # Ni siquiera un calco exacto del mapa de un preset cuenta como declaración.
    assert classify_preset({"categories": preset_decisions("sandbox")}) is None

    plan = plan_policy({"categories": {"code_changes": "auto"}})
    assert plan.preset is None
    assert set(plan.writes.values()) == {UNLISTED_FALLBACK_DECISION}
    assert plan.unlisted == UNLISTED_FALLBACK_DECISION


def test_a_ui_saved_policy_only_gains_the_unlisted_key() -> None:
    """Lo que la UI deja en la BD: las 13 explícitas y ni rastro del slug.

    Ahí no hay nada que completar salvo la clave nueva, así que el «riesgo» de
    esta migración sobre las políticas editadas a mano es exactamente cero
    categorías.
    """
    from api_server.cli.approval_policy_audit import plan_policy
    from api_server.seeds.builtin_approval_policies import preset_decisions

    plan = plan_policy({"categories": preset_decisions("production")})

    assert plan.writes == {}
    assert plan.unlisted is not None


def test_the_strict_presets_write_exactly_the_preset_criterion() -> None:
    """«Se escriben con el criterio estricto del preset» (ADR 0153), literal."""
    from api_server.cli.approval_policy_audit import plan_policy
    from api_server.seeds.builtin_approval_policies import preset_decisions

    for slug in ("production", "customer-external"):
        plan = plan_policy({"preset": slug, "categories": {}})
        assert plan.writes == preset_decisions(slug), slug


# ---------------------------------------------------------------------------
# Pre-check ruidoso: abortar antes que «elegir»
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("policy", "needle"),
    [
        ({"categories": {"git_push": "human-required"}}, "human-required"),
        ({"categories": {"git_push": True}}, "git_push"),
        ({"categories": ["git_push"]}, "categories"),
        ({"preset": "produccion", "categories": {}}, "produccion"),
        ({"categories": {}, "unlisted_category": "quizas"}, "unlisted_category"),
    ],
    ids=[
        "typo-decision",
        "bool-decision",
        "categories-not-a-map",
        "unknown-preset",
        "bad-unlisted",
    ],
)
def test_incoherent_policies_are_reported_not_guessed(policy: Any, needle: str) -> None:
    from api_server.cli.approval_policy_audit import check_policy

    reasons = check_policy(policy)

    assert reasons, "una política incoherente tiene que abortar, no consolidarse en silencio"
    assert any(needle in reason for reason in reasons), reasons


def test_a_coherent_policy_has_no_complaints() -> None:
    from api_server.cli.approval_policy_audit import check_policy

    for policy in (_SKELETON, _PRODUCTION_TEMPLATE, _SANDBOX_TEMPLATE):
        assert check_policy(policy) == []


# ---------------------------------------------------------------------------
# Formas raras de la política
# ---------------------------------------------------------------------------
def test_a_bare_map_policy_is_completed_in_place() -> None:
    """`requires_human_approval` acepta `{cat: decision}` sin envoltorio."""
    from api_server.cli.approval_policy_audit import complete_policy, policy_categories

    completed = complete_policy({"code_changes": "auto"})

    assert "categories" not in completed
    assert set(APPROVAL_CATEGORIES) <= set(policy_categories(completed))
    assert completed["code_changes"] == "auto"


def test_a_policy_with_only_a_preset_gets_a_categories_map() -> None:
    from api_server.cli.approval_policy_audit import complete_policy

    completed = complete_policy({"preset": "production"})

    assert set(APPROVAL_CATEGORIES) <= set(completed["categories"])


@pytest.mark.parametrize("policy", [None, {}, [], "", 0], ids=["none", "empty", "list", "str", "0"])
def test_a_project_without_a_policy_is_out_of_scope(policy: Any) -> None:
    """NULL y `{}` no son políticas incompletas: son proyectos SIN política.

    `_resolve_effective_approval_policy` los hace heredar el preset de
    plataforma (ADR 0104), que ya cubre las 13. Escribirles una política
    explícita los CONGELARÍA contra el `default_approval_policy_preset` vivo —
    y si la escribiéramos en `auto`, además AFLOJARÍA lo que hoy gatean.
    """
    from api_server.cli.approval_policy_audit import has_explicit_policy

    assert has_explicit_policy(policy) is False


# ---------------------------------------------------------------------------
# El informe que el operador lee
# ---------------------------------------------------------------------------
def _finding(name: str, policy: dict[str, Any]) -> Any:
    from api_server.cli.approval_policy_audit import ProjectFinding, plan_policy

    return ProjectFinding(
        project_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        name=name,
        is_template=False,
        plan=plan_policy(policy),
    )


def test_the_report_says_development_is_written_but_not_changed() -> None:
    """Un informe que solo contase los cambios haría creer que dev no se toca."""
    from api_server.cli.approval_policy_audit import AuditReport

    report = AuditReport(
        findings=[_finding("Mi API", _SKELETON)],
        without_policy=3,
        incoherent=[],
    )
    rendered = report.render()

    assert "Mi API" in rendered
    assert "10" in rendered  # las diez que se escriben
    assert "development" in rendered
    assert "no cambia" in rendered.lower()


def test_the_report_totals_by_preset() -> None:
    from api_server.cli.approval_policy_audit import AuditReport

    report = AuditReport(
        findings=[
            _finding("dev-1", _SKELETON),
            _finding("dev-2", _SKELETON),
            _finding("prod-1", _PRODUCTION_TEMPLATE),
        ],
        without_policy=0,
        incoherent=[],
    )

    totals = report.by_preset()

    assert totals["development"].projects == 2
    assert totals["development"].written == 20
    assert totals["development"].changed == 0
    assert totals["production"].projects == 1
    assert totals["production"].changed == 8
    assert "development" in report.render()


def test_the_report_flags_that_the_migration_would_abort() -> None:
    from api_server.cli.approval_policy_audit import AuditReport

    clean = AuditReport(findings=[], without_policy=0, incoherent=[])
    dirty = AuditReport(
        findings=[],
        without_policy=0,
        incoherent=[("33333333-3333-3333-3333-333333333333", ["valor inválido"])],
    )

    assert clean.would_abort is False
    assert dirty.would_abort is True
    assert "ABORT" in dirty.render().upper()


# ---------------------------------------------------------------------------
# El vocabulario que la migración congela
# ---------------------------------------------------------------------------
def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "api-server"
        / "migrations"
        / "versions"
        / "20260802_0133_complete_approval_policies.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0133", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_freezes_todays_vocabulary_and_says_so_when_it_moves() -> None:
    """La 0133 lleva su propia copia de las 13: una migración no se ata a una
    constante viva, o lo que le hace a los datos de un cliente cambia solo.

    Si este test se pone rojo es que alguien añadió una categoría canónica: la
    0133 ya no la cubre y hace falta OTRA migración de relleno para ella.
    """
    migration = _load_migration()

    assert migration.CANONICAL_CATEGORIES == APPROVAL_CATEGORIES


def test_the_migration_freezes_the_strict_preset_decisions() -> None:
    from api_server.seeds.builtin_approval_policies import preset_decisions

    migration = _load_migration()

    for slug in ("sandbox", "development", "production", "customer-external"):
        assert migration.PRESET_DECISIONS[slug] == preset_decisions(slug), slug


def test_the_migration_and_the_report_agree_policy_by_policy() -> None:
    """Una copia congelada y una viva pueden divergir. Aquí se comprueba que no.

    El informe es la condición que puso el operador al firmar; si predijese algo
    distinto de lo que la migración escribe, sería peor que no tenerlo.
    """
    from api_server.cli.approval_policy_audit import complete_policy

    migration = _load_migration()
    bank: list[dict[str, Any]] = [
        _SKELETON,
        _PRODUCTION_TEMPLATE,
        _SANDBOX_TEMPLATE,
        {"preset": "customer-external", "categories": {"code_changes": "auto"}},
        {"categories": {"code_changes": "auto"}},
        {"code_changes": "auto"},
        {"preset": "production"},
        {"preset": "development", "categories": {}, "unlisted_category": "human_required"},
    ]

    for policy in bank:
        assert migration.complete(policy) == complete_policy(policy), policy


def test_the_migration_never_loosens_a_decision() -> None:
    """Ninguna categoría pasa de `human_required` a `auto`. En ningún preset."""
    from api_server.db.approval_repo import requires_human_approval

    migration = _load_migration()
    for policy in (_SKELETON, _PRODUCTION_TEMPLATE, _SANDBOX_TEMPLATE, {"code_changes": "auto"}):
        completed = migration.complete(policy)
        for category in APPROVAL_CATEGORIES:
            if requires_human_approval(policy, category):
                assert requires_human_approval(completed, category), (category, policy)


def test_the_migration_docstring_records_both_halves_of_the_operator_decision() -> None:
    """Dentro de seis meses alguien va a leer esto y querrá «arreglarlo».

    En cualquiera de las dos direcciones: completando también producción-en-dev
    (endurecer) o dejando dev sin completar (huecos). Las dos son instrucción
    explícita del operador, y el sitio donde eso vive es el docstring.
    """
    migration = _load_migration()
    doc = (migration.__doc__ or "").lower()

    assert "aprobar sin leer" in doc, "falta POR QUÉ no se endurece desarrollo"
    assert "default del código" in doc, "falta POR QUÉ se completan todas"
