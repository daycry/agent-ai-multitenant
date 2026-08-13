"""El informe previo del ADR 0153: qué le haría la 0133 a cada política.

El operador firmó la opción (D) con UNA condición: ver antes, proyecto a
proyecto, qué cambiaría. Este módulo es esa condición. Es **solo lectura** —
abre su transacción con ``SET TRANSACTION READ ONLY``, así que ni un bug futuro
puede colar una escritura— y se puede correr en producción sin riesgo.

Lo que informa, y por qué son DOS columnas distintas
----------------------------------------------------

* **Se ESCRIBEN**: las categorías canónicas que la política no menciona. Pasan
  de implícitas a explícitas en TODOS los proyectos, sin excepción. Una política
  incompleta delega su comportamiento en el ``"auto"`` fijo de
  ``requires_human_approval``, y ése es el agujero que el ADR cierra.
* **CAMBIAN de decisión**: el subconjunto que además pasa de ``auto`` a
  ``human_required``. Solo ocurre en ``production`` y ``customer-external``.

Un informe que contase solo las segundas haría creer que los proyectos de
desarrollo no se tocan, y sí se tocan: lo que no cambia en ellos es el
COMPORTAMIENTO, porque lo ausente se escribe ``auto``, que es lo que ya hacían
de facto.

Dónde vive la clave ``unlisted_category``
-----------------------------------------

En la RAÍZ de la política, hermana de ``preset`` y ``categories`` — «una clave
``unlisted_category: auto|human_required`` en la política» (prod-03, decisión
clave 3). No dentro de ``categories``: ahí parecería una 14ª categoría y la UI,
que renderiza ese mapa como la tabla de las 13, la pintaría como tal.

Fuera de alcance: los proyectos SIN política
--------------------------------------------

``NULL`` y ``{}`` no son políticas incompletas, son proyectos sin política, y el
ADR 0104 ya les da una completa (heredan el preset de plataforma, vivo y
configurable). Escribirles una explícita los congelaría contra ese ajuste, y si
la escribiéramos en ``auto`` además AFLOJARÍA lo que hoy sí gatean. El propio
ADR 0153 los deja fuera («el agujero de aquí es el proyecto que SÍ tiene
política, pero incompleta»).

Este módulo usa las constantes VIVAS (``APPROVAL_CATEGORIES``,
``preset_decisions``). La migración 0133 lleva su propia copia congelada, como
debe ser. Que las dos coincidan lo prueban dos tests: uno unitario que compara
las tablas y otro de integración que corre el informe y la migración sobre el
mismo banco y exige que el informe haya predicho exactamente lo escrito.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import structlog
from shared_domain.approval_categories import APPROVAL_CATEGORIES
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.approval_repo import (
    UNLISTED_CATEGORY_KEY,
    UNLISTED_DEFAULT_BY_PRESET,
    UNLISTED_FALLBACK_DECISION,
)
from api_server.seeds.builtin_approval_policies import BUILTIN_POLICIES

_log = structlog.get_logger("api_server.cli.approval_policy_audit")

#: Las dos decisiones que una categoría admite.
AUTO = "auto"
HUMAN_REQUIRED = "human_required"
_DECISIONS = frozenset({AUTO, HUMAN_REQUIRED})

#: Los cuatro presets sembrados.
PRESET_SLUGS: tuple[str, ...] = tuple(policy.slug for policy in BUILTIN_POLICIES)

#: Los únicos dos presets donde lo ausente se escribe con el criterio ESTRICTO
#: del preset. En los otros dos se escribe `auto`: gatear `external_http_post`
#: en desarrollo pararía los runs autónomos constantemente, y una cola de
#: aprobaciones que nadie atiende es PEOR que no tener gate — enseña a aprobar
#: sin leer.
STRICT_PRESETS: frozenset[str] = frozenset({"production", "customer-external"})

#: Etiqueta de una política que NO declara `preset`.
UNDECLARED_PRESET_LABEL = "(sin preset declarado)"

#: Claves de la raíz de la política que NO son categorías.
_NON_CATEGORY_KEYS = frozenset({"preset", "categories", UNLISTED_CATEGORY_KEY})

_MISSING = object()


# ---------------------------------------------------------------------------
# Lectura de la política
# ---------------------------------------------------------------------------
def has_explicit_policy(policy: Any) -> bool:
    """¿Este proyecto tiene política propia?

    Espejo EXACTO del `if project.human_approval_policy:` de
    ``workers.execution._resolve_effective_approval_policy``: un dict vacío —y
    el literal JSON `null`, y cualquier otro valor falsy— hereda hoy el preset
    de plataforma igual que un NULL. Completarlo con `auto` le quitaría gates
    que hoy sí tiene.

    Un valor TRUTHY que no sea un objeto (una lista, p. ej.) devuelve True a
    propósito: hoy revienta el gate con ``AttributeError``, así que tiene que
    llegar al pre-check y aparecer en el informe, no colarse en la cuenta de
    «proyectos sin política».
    """
    return bool(policy)


def policy_categories(policy: Any) -> dict[str, Any]:
    """El mapa categoría→decisión que el gate consulta.

    Espejo de ``requires_human_approval``: ``policy["categories"]`` si existe y
    es un mapa; si no, la política ENTERA (la forma «bare map» que esa función
    acepta explícitamente).
    """
    if not isinstance(policy, dict):
        return {}
    nested = policy.get("categories", _MISSING)
    if nested is _MISSING:
        return policy
    return nested if isinstance(nested, dict) else {}


def classify_preset(policy: Any) -> str | None:
    """El `preset` que la política DECLARA, o ``None`` si no declara ninguno.

    No se adivina. Un calco exacto del mapa de un preset sembrado parece
    evidencia —es lo que deja en la BD el botón «Guardar» de
    `admin/approval-policy/page.tsx`, que copia el mapa y no guarda el slug—
    pero clasificar por ahí AFLOJARÍA: hoy `requires_human_approval` resuelve
    una política sin `preset` con :data:`UNLISTED_FALLBACK_DECISION`
    (fail-closed), y escribirle `auto` porque «parece sandbox» le quitaría ese
    cierre. Sin declaración se escribe lo que el código ya decide, y así queda
    VISIBLE y editable en vez de implícita.
    """
    declared = policy.get("preset") if isinstance(policy, dict) else None
    if isinstance(declared, str) and declared in PRESET_SLUGS:
        return declared
    return None


def decision_for_absent(preset: str | None) -> str:
    """Con qué decisión se escribe una categoría AUSENTE bajo este preset.

    Es, exactamente, lo que ``requires_human_approval`` decide hoy para esa
    categoría: :data:`UNLISTED_DEFAULT_BY_PRESET` cuando hay preset y
    :data:`UNLISTED_FALLBACK_DECISION` cuando no. De ahí sale la propiedad que
    hace segura toda la migración: **escribir no cambia ninguna decisión**, solo
    la traslada del default del código a la política.

    Para los dos presets estrictos coincide además con el criterio del propio
    preset (`production` y `customer-external` son `human_required` en las 13),
    que es como el ADR 0153 lo enuncia. El test
    ``test_the_written_decision_is_what_the_gate_already_decides`` fija esa
    coincidencia: si algún día dejan de coincidir, alguien tiene que mirarlo.
    """
    if preset is None:
        return UNLISTED_FALLBACK_DECISION
    if preset in STRICT_PRESETS:
        return HUMAN_REQUIRED
    return UNLISTED_DEFAULT_BY_PRESET[preset]


# ---------------------------------------------------------------------------
# Pre-check ruidoso — abortar antes que «elegir»
# ---------------------------------------------------------------------------
def check_policy(policy: Any) -> list[str]:
    """Incoherencias que deben ABORTAR la migración, en castellano.

    El patrón de la 0124: si los datos no permiten decidir sin inventarse un
    valor, se para y se dice qué mirar. Rellenar «como se pueda» consolidaría el
    desastre en silencio, y una decisión con un typo (`human-required`) es
    exactamente una intención escrita que hoy NO gatea nada.
    """
    if not isinstance(policy, dict):
        return [f"la política no es un objeto JSON sino un {type(policy).__name__}"]

    reasons: list[str] = []
    nested = policy.get("categories", _MISSING)
    if nested is not _MISSING and not isinstance(nested, dict):
        reasons.append(
            f"`categories` no es un mapa sino un {type(nested).__name__}:"
            " el gate lo lee como «sin categorías» y falla abierto en silencio"
        )
    categories = policy_categories(policy)

    for category in APPROVAL_CATEGORIES:
        if category not in categories:
            continue
        value = categories[category]
        if not isinstance(value, str) or value not in _DECISIONS:
            reasons.append(
                f"categoría `{category}` con decisión inválida {value!r};"
                f" solo valen {AUTO!r} y {HUMAN_REQUIRED!r}"
            )

    declared = policy.get("preset")
    if declared is not None and (not isinstance(declared, str) or declared not in PRESET_SLUGS):
        reasons.append(
            f"preset desconocido {declared!r}; sin saber si es estricto habría que"
            f" adivinar qué escribir. Conocidos: {', '.join(PRESET_SLUGS)}"
        )

    unlisted = policy.get(UNLISTED_CATEGORY_KEY)
    if unlisted is not None and (not isinstance(unlisted, str) or unlisted not in _DECISIONS):
        reasons.append(
            f"`{UNLISTED_CATEGORY_KEY}` con valor inválido {unlisted!r};"
            f" solo valen {AUTO!r} y {HUMAN_REQUIRED!r}"
        )
    return reasons


# ---------------------------------------------------------------------------
# El plan por política
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PolicyPlan:
    """Qué le pasaría a UNA política. Nada aquí escribe."""

    #: El preset que la política DECLARA, o None si no declara ninguno.
    preset: str | None
    #: Categorías canónicas ausentes → decisión con la que se escribirán.
    writes: Mapping[str, str] = field(default_factory=dict)
    #: Valor que se escribiría en `unlisted_category`, o None si ya está.
    unlisted: str | None = None
    #: Claves del mapa que NO son canónicas (`external_http`, `all`, …). Se
    #: dejan intactas: el contrato de la 0133 es SOLO añadir.
    stray_keys: tuple[str, ...] = ()

    @property
    def preset_label(self) -> str:
        return self.preset or UNDECLARED_PRESET_LABEL

    @property
    def changes(self) -> dict[str, str]:
        """Las que quedan escritas `human_required` donde no había nada escrito.

        Frente al gate PREVIO al ADR 0153 —que resolvía lo no listado con un
        `auto` fijo— esto es un cambio de decisión real, y es el riesgo
        operativo que el operador pidió medir antes de firmar. Frente al gate
        NUEVO la decisión es la misma y lo que cambia es que deja de depender de
        un default del código. Las dos lecturas importan: la primera dice cuánta
        cola de aprobaciones aparece, la segunda por qué migrar es seguro.
        """
        return {
            category: decision
            for category, decision in self.writes.items()
            if decision == HUMAN_REQUIRED
        }

    @property
    def touches(self) -> bool:
        return bool(self.writes) or self.unlisted is not None


def plan_policy(policy: Any) -> PolicyPlan:
    """El plan para una política ya validada por :func:`check_policy`."""
    preset = classify_preset(policy)
    absent_decision = decision_for_absent(preset)
    categories = policy_categories(policy)

    writes = {
        category: absent_decision for category in APPROVAL_CATEGORIES if category not in categories
    }
    unlisted: str | None = None
    if not (isinstance(policy, dict) and UNLISTED_CATEGORY_KEY in policy):
        unlisted = absent_decision

    stray = tuple(
        sorted(
            key
            for key in categories
            if key not in APPROVAL_CATEGORIES and key not in _NON_CATEGORY_KEYS
        )
    )
    return PolicyPlan(preset=preset, writes=writes, unlisted=unlisted, stray_keys=stray)


def _writable_categories(policy: dict[str, Any]) -> dict[str, Any]:
    """Dónde escribir, respetando la forma que ya tiene la política.

    Tres formas conviven en la BD y el gate acepta las tres:

      * ``{"preset": …, "categories": {…}}`` — la de las plantillas y la UI;
      * ``{"code_changes": "auto"}`` — «bare map», que
        ``requires_human_approval`` soporta con ``policy.get("categories", policy)``;
      * ``{"preset": "production"}`` — sin mapa; se le crea uno.

    Cambiar de forma al completar sería un cambio de comportamiento gratis.
    """
    nested = policy.get("categories", _MISSING)
    if isinstance(nested, dict):
        return nested
    if any(key in policy for key in APPROVAL_CATEGORIES):
        return policy
    created: dict[str, Any] = {}
    policy["categories"] = created
    return created


def complete_policy(policy: Any) -> dict[str, Any]:
    """La política completa: las 13 canónicas + ``unlisted_category``.

    Devuelve una copia; NUNCA muta la entrada, y NUNCA pisa una decisión ya
    escrita. Idempotente por construcción: la segunda pasada no encuentra nada
    ausente.
    """
    completed: dict[str, Any] = deepcopy(policy) if isinstance(policy, dict) else {}
    plan = plan_policy(completed)
    if plan.writes:
        target = _writable_categories(completed)
        target.update(plan.writes)
    if plan.unlisted is not None:
        completed[UNLISTED_CATEGORY_KEY] = plan.unlisted
    return completed


# ---------------------------------------------------------------------------
# El informe
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectFinding:
    project_id: str
    tenant_id: str
    name: str
    is_template: bool
    plan: PolicyPlan
    deleted: bool = False


@dataclass(frozen=True)
class PresetTotals:
    projects: int = 0
    written: int = 0
    changed: int = 0


@dataclass
class AuditReport:
    """Lo que el operador lee antes de dar el visto bueno a la 0133."""

    findings: list[ProjectFinding] = field(default_factory=list)
    #: Proyectos con `human_approval_policy` NULL o `{}`. Fuera de alcance.
    without_policy: int = 0
    #: ``(project_id, razones)`` de cada política que ABORTARÍA la migración.
    incoherent: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def would_abort(self) -> bool:
        return bool(self.incoherent)

    @property
    def written(self) -> int:
        return sum(len(f.plan.writes) for f in self.findings)

    @property
    def changed(self) -> int:
        return sum(len(f.plan.changes) for f in self.findings)

    @property
    def touched(self) -> list[ProjectFinding]:
        return [f for f in self.findings if f.plan.touches]

    def by_preset(self) -> dict[str, PresetTotals]:
        totals: dict[str, PresetTotals] = {}
        for finding in self.findings:
            label = finding.plan.preset_label
            current = totals.get(label, PresetTotals())
            totals[label] = PresetTotals(
                projects=current.projects + 1,
                written=current.written + len(finding.plan.writes),
                changed=current.changed + len(finding.plan.changes),
            )
        return totals

    # -- render ------------------------------------------------------------
    def render(self, *, show_complete: bool = False) -> str:
        lines = [
            "audit-approval-policies — SOLO LECTURA (no se escribe nada)",
            "",
            f"Proyectos con política propia: {len(self.findings)}",
            f"Proyectos SIN política (heredan el preset de plataforma, ADR 0104):"
            f" {self.without_policy}",
            "",
        ]
        if self.would_abort:
            lines.extend(self._render_incoherent())
        lines.extend(self._render_projects(show_complete=show_complete))
        lines.extend(self._render_totals())
        lines.append("")
        lines.append(
            "Se ESCRIBEN todas las categorías ausentes, en TODOS los proyectos:"
            " una política incompleta deja su comportamiento en manos de un default"
            " del código en vez de en su política."
        )
        lines.append(
            "CAMBIAN de decisión solo en `production` y `customer-external`"
            " (y en las políticas que no declaran preset, donde el gate YA falla"
            " cerrado). En `sandbox` y `development` lo ausente se escribe `auto`,"
            " que es lo que ya hacían: se toca la política, no cambia el"
            " comportamiento."
        )
        lines.append(
            "«cambian» se mide contra el gate PREVIO al ADR 0153, que resolvía lo no"
            " listado con un `auto` fijo. Contra el gate nuevo la decisión es la"
            " misma y lo único que cambia es que deja de depender del código."
        )
        return "\n".join(lines)

    def _render_incoherent(self) -> list[str]:
        lines = [
            "!! LA MIGRACIÓN ABORTARÍA: hay políticas incoherentes.",
            "   Arréglalas antes de migrar; la 0133 no se inventa un valor.",
            "",
        ]
        for project_id, reasons in self.incoherent:
            lines.append(f"  proyecto {project_id}")
            lines.extend(f"      · {reason}" for reason in reasons)
        lines.append("")
        return lines

    def _render_projects(self, *, show_complete: bool) -> list[str]:
        lines = ["PROYECTO A PROYECTO", "-" * 70]
        listed = self.findings if show_complete else self.touched
        if not listed:
            lines.append("  (ninguna política incompleta)")
        for finding in listed:
            lines.extend(self._render_one(finding))
        lines.append("")
        return lines

    @staticmethod
    def _render_one(finding: ProjectFinding) -> list[str]:
        plan = finding.plan
        marks = []
        if finding.is_template:
            marks.append("PLANTILLA")
        if finding.deleted:
            marks.append("borrado")
        suffix = f"  [{' · '.join(marks)}]" if marks else ""
        lines = [
            f"[{plan.preset_label}] {finding.name}{suffix}",
            f"    proyecto {finding.project_id} · tenant {finding.tenant_id}",
        ]
        if not plan.touches:
            lines.append("    política completa: nada que hacer")
            lines.append("")
            return lines
        if plan.writes:
            lines.append(f"    se ESCRIBEN {len(plan.writes)} categorías ausentes:")
            for category, decision in plan.writes.items():
                mark = "  <-- CAMBIA la decisión" if decision == HUMAN_REQUIRED else ""
                lines.append(f"        {category:<24} (implícita) -> {decision}{mark}")
            if not plan.changes:
                lines.append(
                    "    todas en `auto`: el comportamiento NO cambia, solo deja de ser implícito"
                )
        if plan.unlisted is not None:
            lines.append(f"    {UNLISTED_CATEGORY_KEY}: (ausente) -> {plan.unlisted}")
        if plan.stray_keys:
            lines.append(
                "    claves no canónicas que se dejan INTACTAS"
                f" (no gatean nada): {', '.join(plan.stray_keys)}"
            )
        lines.append("")
        return lines

    def _render_totals(self) -> list[str]:
        header = f"{'preset':<22}{'proyectos':>11}{'se escriben':>13}{'cambian':>10}"
        lines = ["RESUMEN POR PRESET", "-" * len(header), header, "-" * len(header)]
        totals = self.by_preset()
        for slug in (*PRESET_SLUGS, UNDECLARED_PRESET_LABEL):
            row = totals.get(slug)
            if row is None:
                continue
            lines.append(f"{slug:<22}{row.projects:>11}{row.written:>13}{row.changed:>10}")
        lines.append("-" * len(header))
        lines.append(f"{'TOTAL':<22}{len(self.findings):>11}{self.written:>13}{self.changed:>10}")
        return lines


# ---------------------------------------------------------------------------
# La pasada sobre la base de datos
# ---------------------------------------------------------------------------
_PROJECTS_SQL = text("""
    SELECT id, tenant_id, name, is_template, deleted_at, human_approval_policy
      FROM projects
     ORDER BY tenant_id, name, id
    """)


async def audit_approval_policies(session: AsyncSession) -> AuditReport:
    """Recorre TODOS los proyectos y devuelve el informe. No escribe nada.

    La transacción se marca ``READ ONLY`` antes de la primera consulta: es una
    garantía de la base de datos, no una promesa de este módulo. Debe ser, por
    tanto, la primera sentencia de la transacción — la sesión que se le pase
    tiene que venir limpia.
    """
    await session.execute(text("SET TRANSACTION READ ONLY"))
    rows = (await session.execute(_PROJECTS_SQL)).all()

    report = AuditReport()
    for row in rows:
        policy = row.human_approval_policy
        if not has_explicit_policy(policy):
            report.without_policy += 1
            continue
        reasons = check_policy(policy)
        if reasons:
            report.incoherent.append((str(row.id), reasons))
            continue
        report.findings.append(
            ProjectFinding(
                project_id=str(row.id),
                tenant_id=str(row.tenant_id),
                name=str(row.name),
                is_template=bool(row.is_template),
                plan=plan_policy(policy),
                deleted=row.deleted_at is not None,
            )
        )

    _log.info(
        "approval_policy_audit.completed",
        projects_with_policy=len(report.findings),
        projects_without_policy=report.without_policy,
        incoherent=len(report.incoherent),
        categories_written=report.written,
        decisions_changed=report.changed,
    )
    return report


__all__ = [
    "AUTO",
    "HUMAN_REQUIRED",
    "PRESET_SLUGS",
    "STRICT_PRESETS",
    "UNLISTED_CATEGORY_KEY",
    "AuditReport",
    "PolicyPlan",
    "PresetTotals",
    "ProjectFinding",
    "audit_approval_policies",
    "check_policy",
    "classify_preset",
    "complete_policy",
    "has_explicit_policy",
    "plan_policy",
    "policy_categories",
]
