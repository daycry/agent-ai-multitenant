"""Doc-lint: el frontmatter del roadmap es la ÚNICA fuente de verdad del estado.

Plan prod-15 (`task_gov_reestado_04`, `task_gov_changelogs_05`,
`task_gov_cabeceras_07`). Hallazgo docsroadmap-6: la tabla de cabecera de cada
plan duplicaba una fila `| **Estado** | ... |` que se desincronizó en 22 de 51
planes — un plan decía dos cosas distintas sobre sí mismo y ganaba la que
leyeras primero.

La regla que estos tests fijan: **el estado vive en el frontmatter YAML y en
ningún otro sitio**.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_ROADMAP = _ROOT / "docs" / "roadmap"
_CHANGELOG = _ROOT / "docs" / "07-changelog"

#: Enum de `status` declarado en CLAUDE.md §"Estados Válidos del Frontmatter".
VALID_STATUS = frozenset(
    {
        "pending_approval",
        "approved",
        "in_progress",
        "blocked",
        "pending_human_validation",
        "completed",
        "cancelled",
        "rejected",
        "archived",
    }
)

#: Ficheros de `docs/roadmap/` que NO son planes (índices, auditorías,
#: investigaciones): no llevan `plan_id` y no entran en el gate de fases.
_NON_PLAN_PREFIXES = ("README", "EXECUTION-SEQUENCE")


def _md_files() -> list[Path]:
    return sorted(p for p in _ROADMAP.glob("*.md") if not p.name.startswith(_NON_PLAN_PREFIXES))


def _frontmatter(path: Path) -> dict[str, object]:
    """Frontmatter parseado, o `{}` si no hay bloque o el YAML está roto.

    Tolerante a propósito: si esto explotara, la colección del módulo entero
    caería y NINGÚN test de gobernanza correría — el modo de fallo que ya
    encontró `cortex-system-owner.md` (un `: ` sin comillas en `author`).
    `test_frontmatter_yaml_is_valid` es quien delata el roto.
    """
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if m is None:
        return {}
    try:
        loaded = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _has_frontmatter_block(path: Path) -> bool:
    return re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S) is not None


def _plans() -> list[tuple[Path, dict[str, object]]]:
    """Ficheros con `plan_id` y `status` en el frontmatter."""
    out = []
    for path in _md_files():
        fm = _frontmatter(path)
        if "plan_id" in fm and "status" in fm:
            out.append((path, fm))
    return out


# ---------------------------------------------------------------------------
# Descubrimiento: si esto deja de encontrar planes, todo lo demás pasa vacío
# ---------------------------------------------------------------------------
def test_discovery_finds_the_plans() -> None:
    plans = _plans()
    assert len(plans) >= 50, (
        f"el descubrimiento de planes del roadmap falló (vio {len(plans)}): "
        "sin esto los demás tests de este fichero pasan vacíos"
    )


# ---------------------------------------------------------------------------
# task_gov_cabeceras_07 — una sola fuente de estado
# ---------------------------------------------------------------------------
def test_no_estado_field_in_plan_headers() -> None:
    """Ninguna tabla de cabecera duplica el estado del frontmatter.

    docsroadmap-6: `06.8` decía `pending_approval` en la cabecera con
    frontmatter `pending_human_validation`; `15` decía `in_progress`. Un campo
    duplicado no se mantiene: se desincroniza.
    """
    offenders: list[str] = []
    for path in _md_files():
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\|\s*\*\*Estado\*\*\s*\|", text, re.M):
            offenders.append(path.name)
    assert not offenders, (
        "estos planes duplican el estado en su tabla de cabecera (la fuente de "
        f"verdad es el frontmatter): {offenders}"
    )


def test_status_is_in_the_declared_enum() -> None:
    """Todo `status:` está en el enum de CLAUDE.md."""
    plans = _plans()
    assert plans, "descubrimiento vacío"
    bad = [(path.name, fm["status"]) for path, fm in plans if fm["status"] not in VALID_STATUS]
    assert not bad, f"status fuera del enum de CLAUDE.md: {bad}"


def test_frontmatter_yaml_is_valid() -> None:
    """Ningún fichero del roadmap tiene YAML roto en su frontmatter.

    Riesgo 4 de prod-15: editar 51 ficheros a mano introduce typos, y un
    frontmatter que no parsea deja el `status:` INVISIBLE para cualquier
    herramienta — que es lo que pasaba en `cortex-system-owner.md` (`author:
    claude-opus (workflow multi-agente: research …)`, un `: ` sin comillas).
    """
    files = [p for p in _md_files() if _has_frontmatter_block(p)]
    assert len(files) >= 50, f"descubrimiento vacío ({len(files)})"
    broken = [p.name for p in files if not _frontmatter(p)]
    assert not broken, f"frontmatter con YAML no parseable en: {broken}"


# ---------------------------------------------------------------------------
# task_gov_reestado_04 — el protocolo se cumple o el override está escrito
# ---------------------------------------------------------------------------
def test_at_most_one_phase_in_progress() -> None:
    """Regla dura de CLAUDE.md: como mucho una fase `in_progress`."""
    active = [path.name for path, fm in _plans() if fm["status"] == "in_progress"]
    assert len(active) <= 1, f"más de una fase in_progress a la vez: {active}"


#: Planes empezados cuyo `blocking_plan` NO está `completed` y que no declaran
#: ninguna excepción. Inventario medido el 2026-07-29 y volcado al ADR 0138
#: (decisión D1 de prod-15). NO es una allowlist permanente: es la deuda que el
#: ADR viene a cerrar. El test de abajo es `xfail(strict=True)`, así que el día
#: que un humano firme el ADR y aparezcan los `gate_override`, el test PASA,
#: pytest lo marca XPASS y falla la suite obligando a retirar el marcador.
_GATE_DEBT_2026_07_29 = frozenset(
    {
        "06.10-kb-categories",
        "06.17-capacitacion-agentes",
        "11.1-budgets-fx",
        "15-instalador-produccion",
        "16-human-agents",
        "prod-17-bucle-ai-reviewer",
    }
)


def unmet_gates() -> dict[str, list[str]]:
    """`{plan_id: [dependencias no completed]}` de las fases ya empezadas."""
    started = {"in_progress", "pending_human_validation", "completed", "blocked"}
    plans = _plans()
    by_id = {str(fm.get("plan_id")): fm for _, fm in plans}
    out: dict[str, list[str]] = {}
    for _path, fm in plans:
        if fm["status"] not in started or "gate_override" in fm:
            continue
        blocking = fm.get("blocking_plan")
        if not blocking:
            continue
        deps = blocking if isinstance(blocking, list) else [blocking]
        unmet = [
            str(dep) for dep in deps if str(by_id.get(str(dep), {}).get("status")) != "completed"
        ]
        if unmet:
            out[str(fm["plan_id"])] = unmet
    return out


def test_gate_debt_inventory_has_not_grown() -> None:
    """El inventario de gates incumplidos no crece a espaldas de nadie.

    Este SÍ debe estar verde: mide que nadie añada una fase nueva empezada con
    el gate saltado. Lo que está pendiente de decisión humana es la deuda ya
    existente, no su crecimiento.
    """
    current = set(unmet_gates())
    new_debt = sorted(current - _GATE_DEBT_2026_07_29)
    assert not new_debt, (
        "fases empezadas con `blocking_plan` sin completar y SIN `gate_override`, "
        f"que no estaban en la deuda medida el 2026-07-29: {new_debt}. "
        "O se cumple el gate, o se documenta el override (ADR 0138)."
    )


def test_started_phase_declares_its_gate() -> None:
    """Toda fase empezada tiene su `blocking_plan` cumplido o un `gate_override`.

    Sin esto, el protocolo de CLAUDE.md es decorativo: se empezaron fases con
    el gate incumplido y nada lo registró (docsroadmap-2). Ojo con el número:
    la auditoría de 2026-06 dijo "~26 fases"; medido el 2026-07-29 son **6** —
    el resto de las 35 en `pending_human_validation` sí tenían su gate
    cumplido cuando arrancaron. Ver ADR 0138 para el inventario exacto.
    """
    unmet = unmet_gates()
    assert not unmet, f"fases empezadas con el gate incumplido: {unmet}"


# ---------------------------------------------------------------------------
# task_gov_changelogs_05
# ---------------------------------------------------------------------------
def test_every_started_phase_has_changelog() -> None:
    """Todo plan con código mergeado tiene entrada de changelog.

    Los dos únicos huecos eran `06.8` y `06.9`. La entrada se busca por prefijo
    del `plan_id` porque el nombre del fichero de changelog lleva el slug
    (`06.8-rbac-enforcement.md`).
    """
    entries = {p.name for p in _CHANGELOG.glob("*.md")}
    assert len(entries) >= 40, f"el descubrimiento de changelogs falló ({len(entries)})"

    missing: list[str] = []
    checked = 0
    for _path, fm in _plans():
        if fm["status"] not in {"pending_human_validation", "completed"}:
            continue
        checked += 1
        plan_id = str(fm["plan_id"])
        if plan_id in _CHANGELOG_DEBT_2026_07_29:
            continue
        stem = plan_id.split("-", 1)[0] if re.match(r"^[\d.]+-", plan_id) else plan_id
        if not any(e == f"{plan_id}.md" or e.startswith(f"{stem}-") for e in entries):
            missing.append(plan_id)
    assert checked >= 20, f"casi ningún plan quedó cubierto por el test ({checked})"
    assert not missing, f"planes empezados SIN entrada en docs/07-changelog/: {missing}"


#: Planes `completed`/`pending_human_validation` sin entrada de changelog,
#: medidos el 2026-07-29. `06.8` y `06.9` (los que prod-15 iba a crear) YA la
#: tienen; el único hueco real que quedaba es este, y además es el peor caso
#: posible: `status: completed` con `completed_at: 2026-07-08` y 11/11 casillas,
#: lo que viola la regla dura de CLAUDE.md «NUNCA cambiar status: completed sin
#: la entrada de changelog generada». Escribirla exige verificar 11 hallazgos
#: (c1-c11) contra `apps/`, fuera del carril documental de esta pasada.
_CHANGELOG_DEBT_2026_07_29 = frozenset({"ciclo-vida-planes-fixes"})


def test_changelog_debt_has_not_grown() -> None:
    """Nadie añade un plan cerrado sin changelog aprovechando la excepción."""
    entries = {p.name for p in _CHANGELOG.glob("*.md")}
    still_missing = {
        plan_id
        for plan_id in _CHANGELOG_DEBT_2026_07_29
        if not any(e.startswith(plan_id) for e in entries)
    }
    assert still_missing == set(_CHANGELOG_DEBT_2026_07_29), (
        "la deuda de changelogs se saldó: retira de _CHANGELOG_DEBT_2026_07_29 "
        f"lo que ya tiene entrada ({set(_CHANGELOG_DEBT_2026_07_29) - still_missing})"
    )


#: Longitud mínima de la justificación de un `gate_override`. No es un número
#: mágico: es lo que separa «pendiente de validar» —que no explica nada— de una
#: razón que alguien pueda auditar dentro de seis meses. El ADR 0138 eligió la
#: opción híbrida precisamente porque un override que no cuesta nada de escribir
#: deja de ser una excepción.
_MIN_JUSTIFICACION = 80


def test_gate_override_carries_a_written_justification() -> None:
    """Un `gate_override` sin justificación auditable no vale.

    El mecanismo lo firmó el operador en el ADR 0138 (opción C, 2026-07-31) con
    una condición explícita: la justificación es OBLIGATORIA y por escrito. Sin
    esta guarda, el campo se convierte en la forma barata de saltarse el
    protocolo de CLAUDE.md, que es justo el riesgo que el ADR nombra al descartar
    la opción B a secas.
    """
    ofensores: dict[str, str] = {}
    for _path, fm in _plans():
        override = fm.get("gate_override")
        if not override:
            continue
        plan_id = str(fm.get("plan_id"))
        if not isinstance(override, dict):
            ofensores[plan_id] = "no es un mapa con approved_by/date/adr/reason"
            continue
        faltan = [k for k in ("approved_by", "date", "adr", "reason") if not override.get(k)]
        if faltan:
            ofensores[plan_id] = f"le faltan campos: {faltan}"
            continue
        razon = str(override["reason"]).strip()
        if len(razon) < _MIN_JUSTIFICACION:
            ofensores[plan_id] = (
                f"justificación de {len(razon)} caracteres, mínimo {_MIN_JUSTIFICACION}"
            )
    assert not ofensores, (
        "gate_override sin justificación auditable (ADR 0138 exige approved_by, "
        f"date, adr y un reason escrito): {ofensores}"
    )


def test_gate_override_only_where_the_gate_is_actually_unmet() -> None:
    """Nadie deja un override puesto cuando su bloqueante ya se cerró.

    Un override huérfano es peor que ninguno: dice que hubo una excepción donde
    ya no la hay, y la próxima lectura del roadmap se la cree. Al cerrarse de
    verdad un `blocking_plan`, hay que retirar el override de sus dependientes.
    """
    plans = _plans()
    by_id = {str(fm.get("plan_id")): fm for _, fm in plans}
    huerfanos: list[str] = []
    for _path, fm in plans:
        if not fm.get("gate_override"):
            continue
        deps = fm.get("blocking_plan") or []
        deps = deps if isinstance(deps, list) else [deps]
        sin_cerrar = [d for d in deps if str(by_id.get(str(d), {}).get("status")) != "completed"]
        if deps and not sin_cerrar:
            huerfanos.append(str(fm["plan_id"]))
    assert not huerfanos, (
        "fases con `gate_override` cuyo bloqueante YA está completed: retíralo, "
        f"la excepción caducó: {huerfanos}"
    )
