"""Completa TODAS las políticas de aprobación; endurece SOLO producción (ADR 0153 (D)).

Lo que arregla
--------------

`requires_human_approval` (`db/approval_repo.py`) y su espejo del sandbox
`requires_human` (`agent_runtime/approval.py`) resuelven una categoría que la
política NO menciona con `categories.get(category, "auto")`: fail-open. Y las
políticas que de verdad viven en `projects.human_approval_policy` están
incompletas, porque los proyectos nacidos de plantilla copian
`_POLICY_DEV_SKELETON`, que lista CUATRO claves —una de ellas, `external_http`,
ni siquiera existe en `APPROVAL_CATEGORIES`, así que no gatea nada—. Medido en
el ADR: **diez de trece categorías en `auto` por omisión**, también en las dos
plantillas que la UI presenta como «Producción».

Las DOS mitades, que son instrucción explícita del operador
-----------------------------------------------------------

Esto es el corazón de la migración y quien lo lea dentro de seis meses va a
querer «arreglarlo» en alguna de las dos direcciones. Las dos son deliberadas:

**(a) Se COMPLETAN todas las políticas, sin excepción.** Al terminar ninguna
tiene categorías implícitas: las 13 canónicas escritas y `unlisted_category`
presente. Dejar un proyecto a medias deja su comportamiento en manos de un
**default del código** en vez de en su política, y ése es exactamente el estado
indefinido que el ADR 0153 viene a cerrar. Por eso se tocan TODOS los
proyectos, también los de `sandbox` y `development`.

**(b) Solo se CAMBIA lo que la política decide en `production` y
`customer-external`.** Ahí las categorías implícitas se escriben con el criterio
estricto del preset. En `sandbox` y `development` se escriben con `auto`: se
hace explícito lo que ya estaba pasando y el comportamiento no cambia ni un
ápice. Razón del operador: en desarrollo, gatear `external_http_post` pararía
los runs autónomos constantemente, y una cola de aprobaciones que nadie atiende
es PEOR que no tener gate — enseña a **aprobar sin leer**, y ese hábito se lleva
luego al proyecto donde sí importaba.

Dicho de otro modo: **todos los proyectos se tocan; solo cambia el
comportamiento de los de producción.**

Y la política que NO declara `preset`
--------------------------------------

No se adivina cuál es. Un calco exacto del mapa de un preset sembrado parece
evidencia —es lo que deja en la BD el botón «Guardar» de la pantalla de política,
que copia el mapa y no guarda el slug— pero clasificar por ahí AFLOJARÍA:
`requires_human_approval` resuelve hoy una política sin `preset` con
`UNLISTED_FALLBACK_DECISION`, o sea fail-closed, y escribirle `auto` porque
«parece sandbox» le quitaría ese cierre. Se escribe lo que el código ya decide
(`human_required`), y así queda VISIBLE y editable en vez de implícita. En la
práctica esas políticas vienen de la UI con las 13 explícitas, así que lo único
que se les añade es la clave `unlisted_category`.

De ahí sale la propiedad que hace segura toda la migración: **la decisión que se
escribe para una categoría ausente es, exactamente, la que el gate ya toma para
ella**. Completar traslada la decisión del default del código a la política; no
la mueve.

Reglas que esta migración cumple
--------------------------------

* **Solo rellena lo ausente.** Una decisión escrita a mano NUNCA se pisa: si un
  tenant puso `data_export_pii: auto` a conciencia bajo `production`, se
  respeta. Tampoco se BORRA nada — las claves no canónicas (`external_http`,
  `all`) se dejan intactas. No gatean nada, y borrar una clave que alguien
  escribió es la única forma en que esto podría sorprender a un operador; el
  arreglo del origen es del seed, no de los datos.
* **Reversible de verdad.** El estado previo se GUARDA fila a fila en
  `approval_policy_backfill_0133` antes de tocar nada, y el `downgrade` lo
  restaura desde ahí. No se reconstruye por inferencia: quitar lo que esta
  migración añadió no devolvería una política escrita a mano a su forma exacta.
* **Idempotente.** Correrla dos veces no cambia nada la segunda: la segunda
  pasada no encuentra ninguna categoría ausente.
* **Pre-check ruidoso** (patrón de la 0124): si encuentra una política
  incoherente —una decisión con un typo, un `preset` desconocido, un
  `categories` que no es un mapa— ABORTA con la lista de proyectos ofensivos en
  vez de «elegir» un valor y consolidar el desastre en silencio. El comando
  `python -m api_server.cli audit-approval-policies` lo pre-vuela y sale con
  código 2 si esto fuese a pasar.

Fuera de alcance: los proyectos SIN política
--------------------------------------------

`human_approval_policy` NULL o `{}` no es una política incompleta, es la
ausencia de política, y el ADR 0104 ya le da una completa: hereda el preset de
plataforma (`default_approval_policy_preset`, vivo). Escribirle una explícita lo
congelaría contra ese ajuste, y escribírsela en `auto` además AFLOJARÍA lo que
hoy gatea. El propio ADR 0153 lo dice: «el agujero de aquí es el proyecto que SÍ
tiene política, pero incompleta».

Por qué las tablas van copiadas aquí y no importadas
----------------------------------------------------

Ninguna migración de este repo importa código de la app, y ésta tampoco.
`CANONICAL_CATEGORIES` y `PRESET_DECISIONS` son una foto del 2026-08-02: una
migración de datos tiene que producir el MISMO resultado cuando quiera que se
aplique, y atarla a una constante viva significa que un cambio futuro en
`APPROVAL_CATEGORIES` o en un preset altera en silencio lo que una migración
antigua le hace a los datos de un cliente. Si mañana se añade una 14ª categoría,
`tests/unit/test_approval_policy_audit.py` se pone rojo para decir justo eso:
hace falta OTRA migración de relleno, no editar ésta.

Revision ID: 0133_complete_approval_policies
Revises: 0132_guardrail_configs
Create Date: 2026-08-02
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0133_complete_approval_policies"
down_revision: str | Sequence[str] | None = "0132_guardrail_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Vocabulario CONGELADO el 2026-08-02 (ver docstring)
# ---------------------------------------------------------------------------
#: Las 13 categorías canónicas de acción sensible (spec §7.7-7.8).
CANONICAL_CATEGORIES: tuple[str, ...] = (
    "code_changes",
    "git_commit",
    "git_push",
    "external_http_get",
    "external_http_post",
    "secrets_access",
    "data_migration",
    "production_deploy",
    "infra_provision",
    "secret_rotation",
    "external_communication",
    "data_export_pii",
    "user_management",
)

AUTO = "auto"
HUMAN_REQUIRED = "human_required"
_DECISIONS = frozenset({AUTO, HUMAN_REQUIRED})

#: Clave que decide qué pasa con una categoría no listada. Vive en la RAÍZ de la
#: política, hermana de `preset` y `categories` — no dentro del mapa, donde
#: parecería una 14ª categoría y la UI la pintaría como tal.
UNLISTED_CATEGORY_KEY = "unlisted_category"


def _all(decision: str) -> dict[str, str]:
    return dict.fromkeys(CANONICAL_CATEGORIES, decision)


#: Foto de `seeds/builtin_approval_policies.BUILTIN_POLICIES` al 2026-08-02.
PRESET_DECISIONS: dict[str, dict[str, str]] = {
    "sandbox": _all(AUTO),
    "development": {
        **_all(HUMAN_REQUIRED),
        "code_changes": AUTO,
        "git_commit": AUTO,
        "external_http_get": AUTO,
        # `external_http_post` en AUTO por decisión del operador (2026-08-02):
        # esta categoría cubre TODAS las tools MCP del proyecto, así que gatearla
        # haría que cada integración pidiese aprobación desde el primer día. El
        # razonamiento completo, con la tensión que abre respecto al hallazgo g6,
        # está en tests/unit/test_mcp_tool_approval_category.py.
        "external_http_post": AUTO,
    },
    "production": _all(HUMAN_REQUIRED),
    "customer-external": _all(HUMAN_REQUIRED),
}

#: Los DOS únicos presets donde esta migración escribe con criterio estricto.
STRICT_PRESETS = frozenset({"production", "customer-external"})

#: Con qué decisión se escribe una categoría AUSENTE cuando la política NO
#: declara `preset`. Es lo que `requires_human_approval` ya decide en ese caso
#: (`UNLISTED_FALLBACK_DECISION`, fail-closed): escribirlo no cambia nada, y
#: escribir `auto` porque la política «parece» laxa sí la AFLOJARÍA. Esta
#: migración nunca afloja.
UNDECLARED_PRESET_DECISION = HUMAN_REQUIRED

#: Tabla de respaldo. Sobrevive al `upgrade` (el `downgrade` la lee y la borra):
#: es el ÚNICO sitio donde vive el estado previo exacto.
BACKUP_TABLE = "approval_policy_backfill_0133"

_BACKUP_COMMENT = (
    "Respaldo de la 0133 (ADR 0153): política de aprobación previa al relleno. "
    "El downgrade restaura desde aquí. Borrarla hace la migración irreversible."
)

_MISSING = object()


# ---------------------------------------------------------------------------
# Lógica pura (espejo de `api_server.cli.approval_policy_audit`; un test de
# integración corre las dos sobre el mismo banco y exige que coincidan)
# ---------------------------------------------------------------------------
def _categories_of(policy: dict[str, Any]) -> dict[str, Any]:
    """El mapa que el gate consulta. Espejo de `requires_human_approval`."""
    nested = policy.get("categories", _MISSING)
    if nested is _MISSING:
        return policy
    return nested if isinstance(nested, dict) else {}


def _declared_preset(policy: dict[str, Any]) -> str | None:
    """El preset que la política DECLARA. No se adivina por el contenido."""
    declared = policy.get("preset")
    if isinstance(declared, str) and declared in PRESET_DECISIONS:
        return declared
    return None


def _decision_for_absent(preset: str | None) -> str:
    """Con qué se escribe una categoría ausente. Ver el docstring del módulo.

    Coincide, categoría a categoría, con lo que ``requires_human_approval``
    decide hoy para ella. De ahí que completar no mueva ninguna decisión.
    """
    if preset is None:
        return UNDECLARED_PRESET_DECISION
    if preset in STRICT_PRESETS:
        return HUMAN_REQUIRED
    return AUTO


def _incoherences(policy: Any) -> list[str]:
    """Lo que hace ABORTAR. Vacío = se puede completar sin inventarse nada."""
    if not isinstance(policy, dict):
        return [f"la política no es un objeto JSON sino un {type(policy).__name__}"]
    reasons: list[str] = []
    nested = policy.get("categories", _MISSING)
    if nested is not _MISSING and not isinstance(nested, dict):
        reasons.append(f"`categories` no es un mapa sino un {type(nested).__name__}")
    for category in CANONICAL_CATEGORIES:
        value = _categories_of(policy).get(category, _MISSING)
        if value is _MISSING:
            continue
        if not isinstance(value, str) or value not in _DECISIONS:
            reasons.append(f"categoría `{category}` con decisión inválida {value!r}")
    declared = policy.get("preset")
    if declared is not None and (not isinstance(declared, str) or declared not in PRESET_DECISIONS):
        reasons.append(f"preset desconocido {declared!r}")
    unlisted = policy.get(UNLISTED_CATEGORY_KEY)
    if unlisted is not None and (not isinstance(unlisted, str) or unlisted not in _DECISIONS):
        reasons.append(f"`{UNLISTED_CATEGORY_KEY}` con valor inválido {unlisted!r}")
    return reasons


def _writable_map(policy: dict[str, Any]) -> dict[str, Any]:
    """Dónde escribir, respetando la forma que la política ya tiene."""
    nested = policy.get("categories", _MISSING)
    if isinstance(nested, dict):
        return nested
    if any(key in policy for key in CANONICAL_CATEGORIES):
        return policy  # forma «bare map», que el gate acepta
    created: dict[str, Any] = {}
    policy["categories"] = created
    return created


def complete(policy: dict[str, Any]) -> dict[str, Any]:
    """La política completa. Solo AÑADE; nunca pisa ni borra. Idempotente."""
    completed = json.loads(json.dumps(policy))  # copia profunda barata
    decision = _decision_for_absent(_declared_preset(completed))
    categories = _categories_of(completed)

    missing = {
        category: decision for category in CANONICAL_CATEGORIES if category not in categories
    }
    if missing:
        _writable_map(completed).update(missing)
    if UNLISTED_CATEGORY_KEY not in completed:
        completed[UNLISTED_CATEGORY_KEY] = decision
    return completed


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------
_SELECT_PROJECTS = sa.text(
    "SELECT id, human_approval_policy FROM projects"
    " WHERE human_approval_policy IS NOT NULL ORDER BY id"
)


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Respaldo: el downgrade restaura DESDE AQUÍ, no por inferencia ----
    op.create_table(
        BACKUP_TABLE,
        sa.Column("project_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("previous_policy", postgresql.JSONB, nullable=True),
        sa.Column(
            "backed_up_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # `COMMENT ON` es una sentencia de utilidad: PostgreSQL no admite parámetros
    # ligados ahí, así que el literal va incrustado. Es una constante de este
    # módulo, no entra nada del exterior.
    op.execute(f"COMMENT ON TABLE {BACKUP_TABLE} IS '{_BACKUP_COMMENT}'")

    rows = bind.execute(_SELECT_PROJECTS).all()

    # Fuera de alcance: sin política que completar. El `WHERE` filtra el NULL de
    # SQL; esto atrapa además `{}` y el literal JSON `null`, que el gate trata
    # igual (`if not policy: return False`) y el ADR 0104 ya resuelve heredando
    # el preset de plataforma. Se descartan ANTES del pre-check para que un
    # `'null'::jsonb` inofensivo no aborte la migración.
    candidates = [row for row in rows if row.human_approval_policy]

    # --- 2. Pre-check ruidoso: nunca completar sobre datos incoherentes ------
    offenders: list[str] = []
    for row in candidates:
        reasons = _incoherences(row.human_approval_policy)
        if reasons:
            offenders.append(f"{row.id}: {'; '.join(reasons)}")
    if offenders:
        raise RuntimeError(
            "migración 0133 abortada: hay políticas de aprobación incoherentes, y"
            " completarlas exigiría INVENTARSE un valor. Arréglalas y vuelve a"
            " migrar. Pre-vuélalo con"
            " `python -m api_server.cli audit-approval-policies` (sale con código 2)."
            f" Primeras {min(len(offenders), 20)} de {len(offenders)}:"
            f" {offenders[:20]}"
        )

    # --- 3. Completar, guardando el estado previo de lo que cambia -----------
    updated = 0
    for row in candidates:
        policy = row.human_approval_policy
        completed = complete(policy)
        if completed == policy:
            continue  # ya completa: idempotencia
        bind.execute(
            sa.text(
                f"INSERT INTO {BACKUP_TABLE} (project_id, previous_policy)"
                " VALUES (:project_id, CAST(:previous AS jsonb))"
                " ON CONFLICT (project_id) DO NOTHING"
            ),
            {"project_id": str(row.id), "previous": json.dumps(policy)},
        )
        bind.execute(
            sa.text(
                "UPDATE projects SET human_approval_policy = CAST(:policy AS jsonb)"
                " WHERE id = :project_id"
            ),
            {"policy": json.dumps(completed), "project_id": str(row.id)},
        )
        updated += 1

    op.execute(
        f"COMMENT ON COLUMN {BACKUP_TABLE}.previous_policy IS"
        f" 'Política tal cual estaba antes de la 0133 ({updated} proyecto(s) completado(s)).'"
    )


def downgrade() -> None:
    """Devuelve cada política EXACTAMENTE a como estaba, desde el respaldo.

    No se «quita lo que la 0133 puso»: una política escrita a mano no se
    reconstruye por inferencia, y una categoría que el operador haya editado
    DESPUÉS de la migración volvería a un valor que nunca tuvo. Se restaura la
    foto, que es la única definición honesta de reversible.
    """
    op.execute(
        sa.text(
            "UPDATE projects p SET human_approval_policy = b.previous_policy"
            f"  FROM {BACKUP_TABLE} b WHERE b.project_id = p.id"
        )
    )
    op.drop_table(BACKUP_TABLE)
