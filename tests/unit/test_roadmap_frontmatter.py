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


#: Planes ENTREGADOS —todas sus casillas marcadas, ninguna abierta— que siguen
#: etiquetados `pending_approval`, que en el enum de CLAUDE.md significa «plan
#: definido pero **no empezado**». Inventario medido el 2026-08-12.
#:
#: No es una allowlist: es deuda con una salida escrita. A cada uno de estos
#: ocho sólo le falta su entrada en `docs/07-changelog/` para poder pasar a
#: `pending_human_validation` — el guarda que la exige es
#: `test_every_started_phase_has_changelog`, así que cambiar el estado sin
#: escribirla pone la suite roja, que es exactamente lo que debe pasar.
#:
#: **Y ojo con la población, porque el titular es peor que estos ocho**: los
#: CATORCE planes en `pending_approval` tienen casillas marcadas, o sea que
#: «`pending_approval` == nunca empezado» ya no describe a ninguno. Estos ocho
#: son sólo los que además no tienen NADA abierto, que es lo que los hace
#: accionables sin emitir un juicio sobre trabajo a medias. El resto exige
#: decidir si lo hecho vale, y eso no lo cierra un test.
_DELIVERED_BUT_UNSTARTED_2026_08_12 = frozenset(
    {
        "cadena-pr-plan",
        "prod-03-guardrails-validacion-humana",
        # Se unió el 2026-08-12, al cerrarse su última casilla
        # (`task_prod_04_06`, quiesce del ADR 0149). Se anota aquí en vez de
        # cambiarle el estado porque pasar a `pending_human_validation` exige
        # su entrada de changelog, y escribirla es auditar catorce tareas.
        "prod-04-backup-dr-restaurable",
        "prod-05-rotacion-claves",
        "prod-07-fiabilidad-llm-costes",
        "prod-09-sesiones-autorizacion-frontend",
        # Se unió el 2026-08-19, y por la vía (3) que este propio guarda ofrece: su
        # última casilla abierta (`task_prod14_10`) NO se cerró implementando nada, se
        # cerró al comprobar que el trabajo llevaba hecho desde la migración
        # `20260730_0126_perf_indexes_uniqueness` y que el checkbox era lo único que
        # seguía diciendo lo contrario — cuatro notas del propio plan (08-01, 08-02,
        # 08-10, 08-12) ya lo habían constatado sin tocarlo. Cambiarle el estado exige
        # su entrada de changelog, o sea auditar las cuarenta y pico tareas del plan, y
        # eso no lo decide el carril que reparó un checkbox.
        "prod-14-tenancy-defensa-profundidad",
        # Se unió el 2026-08-19, por la misma vía (3). Su última casilla abierta era
        # `task_prod13_01` (las puertas del marketplace fuera del event loop), que
        # llevaba tres pasadas aplazada a propósito y se cerró al caducar el motivo
        # del aplazamiento: `marketplace-v2-despliegue` quedó entregado. Cerrar UNA
        # casilla no autoriza a declarar el plan entregado — pasar a
        # `pending_human_validation` exige su entrada de changelog, o sea auditar las
        # veintitrés tareas de prod-13, incluidas las de particionado y pool que
        # cerraron otros carriles.
        "prod-13-rendimiento-y-datos",
    }
)

#: Una casilla de tarea, al principio de línea. Las anotaciones indentadas de
#: los planes (`  - ⏳ …`) no cuentan: sólo el enunciado de la tarea.
_TASK_BOX = re.compile(r"^- \[( |x)\] ", re.M)


def _delivered_but_labelled_unstarted() -> set[str]:
    """`{plan_id}` de los planes con TODAS las casillas marcadas y, aun así,
    `status: pending_approval`."""
    out: set[str] = set()
    for path, fm in _plans():
        if fm["status"] != "pending_approval":
            continue
        boxes = _TASK_BOX.findall(path.read_text(encoding="utf-8"))
        if boxes and " " not in boxes:
            out.add(str(fm["plan_id"]))
    return out


def test_no_new_plan_is_delivered_while_still_labelled_unstarted() -> None:
    """Un plan terminado no puede seguir diciendo que no ha empezado.

    El estado es lo primero que se lee al retomar el trabajo, y un plan con
    16/16 casillas marcadas etiquetado «no empezado» manda a quien lo lea a
    reimplementar lo que ya existe — el modo de fallo nº1 de
    `verificar-antes-de-implementar.md`. Este guarda no arregla los ocho que
    ya están así (necesitan changelog); impide que aparezca el noveno.
    """
    new_drift = sorted(_delivered_but_labelled_unstarted() - _DELIVERED_BUT_UNSTARTED_2026_08_12)

    assert not new_drift, (
        f"planes con todas las casillas marcadas y `status: pending_approval`, "
        f"que no estaban en el inventario del 2026-08-12: {new_drift}.\n"
        "Tres salidas, por orden de honestidad: (1) si el trabajo está entregado "
        "de verdad, el estado es `pending_human_validation` — y entonces hace "
        "falta su entrada en `docs/07-changelog/`, que exige "
        "`test_every_started_phase_has_changelog`; (2) si no lo está, desmarca "
        "las casillas que no lo estén; (3) si acabas de cerrar su última casilla "
        "en esta misma ola y el cambio de estado no te toca, añádelo a "
        "`_DELIVERED_BUT_UNSTARTED_2026_08_12` — eso lo deja anotado como deuda "
        "en vez de invisible, que es lo único que este guarda persigue."
    )


def test_the_delivered_but_unstarted_inventory_has_no_dead_entries() -> None:
    """El inventario caduca solo.

    Una entrada que ya no se cumple afirma una incoherencia que alguien
    arregló, y la próxima revisión la busca en vano — el mismo modo de fallo
    que `test_the_debt_inventory_has_no_dead_entries` evita en la frontera de
    apps.
    """
    stale = sorted(_DELIVERED_BUT_UNSTARTED_2026_08_12 - _delivered_but_labelled_unstarted())

    assert not stale, (
        f"entradas del inventario que ya no describen la realidad: {stale}. "
        "Retíralas de `_DELIVERED_BUT_UNSTARTED_2026_08_12`."
    )


#: Cardinales en castellano que puede llevar la prosa del inventario. La ventana
#: es corta A PROPÓSITO: fuera de ella quedan las cifras que hablan de OTRA
#: población (los «CATORCE planes en `pending_approval`») y los ordinales («el
#: séptimo»), que no cuentan entradas de este frozenset y no deben colisionar.
_CARDINALES_ES = {
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}


def _prose_about_the_inventory() -> str:
    """El texto que describe el inventario: su bloque `#:` y el docstring del
    guarda que lo usa. Se lee del fuente de ESTE módulo, que es donde vive."""
    src = Path(__file__).read_text(encoding="utf-8")
    block = re.search(r"((?:^#:.*\n)+)_DELIVERED_BUT_UNSTARTED_2026_08_12 = frozenset\(", src, re.M)
    doc = re.search(
        r"def test_no_new_plan_is_delivered_while_still_labelled_unstarted\(\) -> None:\n"
        r'    """(.*?)"""',
        src,
        re.S,
    )
    assert block is not None, "no encuentro el bloque `#:` del inventario"
    assert doc is not None, "no encuentro el docstring del guarda del inventario"
    return block.group(1) + doc.group(1)


def test_the_delivered_but_unstarted_prose_matches_the_inventory_size() -> None:
    """La prosa del inventario no puede decir un número distinto del que hay.

    Este fichero nació de un hallazgo —una fila de estado duplicada que se
    desincronizó en 22 de 51 planes— y reincidió en su propio texto: el
    frozenset creció a seis entradas y las cuatro menciones en prosa siguieron
    diciendo «cinco». Una medida que miente cuesta más que ninguna medida, y en
    un inventario de deuda es peor todavía: quien lo lea contará mal lo que
    falta por cerrar. Se ata aquí para que la próxima entrada obligue a tocar
    el texto en el mismo commit.
    """
    prose = _prose_about_the_inventory()
    dichos = {w: n for w, n in _CARDINALES_ES.items() if re.search(rf"\b{w}\b", prose, re.I)}

    # Autocomprobación: si la prosa dejara de decir el número, este guarda
    # pasaría en vacío para siempre — el §4 de verificar-antes-de-implementar.
    assert dichos, (
        "la prosa del inventario ya no menciona su tamaño con un cardinal en "
        f"castellano ({sorted(_CARDINALES_ES)}), así que nada la ata al frozenset."
    )

    esperado = len(_DELIVERED_BUT_UNSTARTED_2026_08_12)
    desajuste = {w: n for w, n in dichos.items() if n != esperado}
    assert not desajuste, (
        f"`_DELIVERED_BUT_UNSTARTED_2026_08_12` tiene {esperado} entradas, pero su "
        f"prosa dice {sorted(desajuste)}. Actualiza el texto del bloque `#:` y el "
        "docstring de `test_no_new_plan_is_delivered_while_still_labelled_unstarted`."
    )


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


# ---------------------------------------------------------------------------
# `completed` con casillas abiertas — el agujero que nadie vigilaba
# ---------------------------------------------------------------------------
#: Planes `status: completed` que HOY tienen casillas `- [ ]`, medidos el
#: 2026-08-19. Es una contradicción con el enum de CLAUDE.md —`completed` es
#: «plan cerrado completamente»— y hasta hoy **ninguna guarda la veía**: los
#: tests de este fichero comprueban el gate, el override, el changelog y la cola
#: de validación, pero nadie preguntaba lo más simple.
#:
#: Salió al desmarcar seis casillas del plan `06` que estaban `[x]` describiendo
#: el pool elástico de runtimes, código **borrado del repo el 2026-07-26**
#: (commit `7959cdcb`). Dejarlas marcadas mandaba a cualquiera a buscar un módulo
#: que no existe; desmarcarlas deja el plan `completed` con seis huecos. Las dos
#: cosas son incoherentes, y la segunda al menos lo es **a la vista**.
#:
#: **2026-08-20: son siete.** `task_06_07` (el modo testcontainers) se desmarcó
#: por lo MISMO y por el MISMO commit — `7959cdcb` se llevó también
#: `TestcontainersMode`, el proxy DinD y `test_testcontainers_mode.py`, que la
#: casilla declaraba. La entrada de este inventario no cambia (es por plan, no
#: por casilla), pero la decisión pendiente del operador crece: el recorte de
#: alcance del plan `06` son dos fases, no una.
#:
#: Por qué no se arregla cambiando el `status:`: pasar `06` a otro estado es
#: aceptar un recorte de alcance o partir la fase, y las dos salidas están
#: escritas en sus Criterios de Cierre esperando una decisión humana. CLAUDE.md
#: prohíbe reordenar el roadmap sin que un humano apruebe el cambio.
#:
#: Se retira esta entrada cuando el operador elija: (a) aceptar el recorte y
#: anotarlo en el changelog, o (b) mover la Fase E2 a un plan propio en
#: `pending_approval`.
_COMPLETED_WITH_OPEN_BOXES_2026_08_19 = frozenset({"06-testing-revision-git"})


def _plans_completed_with_open_boxes() -> dict[str, int]:
    """`{plan_id: nº de casillas abiertas}` de los planes `completed`."""
    fuera: dict[str, int] = {}
    for path, fm in _plans():
        if fm["status"] != "completed":
            continue
        abiertas = sum(
            1 for linea in path.read_text(encoding="utf-8").split("\n") if linea.startswith("- [ ]")
        )
        if abiertas:
            fuera[str(fm["plan_id"])] = abiertas
    return fuera


def test_no_new_completed_plan_has_open_boxes() -> None:
    """`completed` significa cerrado. El inventario de arriba sólo puede encoger.

    No-vacuidad: se afirma también que el descubrimiento encontró planes
    `completed` que mirar. Sin eso, un `_plans()` roto haría pasar este test
    describiendo un mundo sin planes cerrados.
    """
    completados = sum(1 for _p, fm in _plans() if fm["status"] == "completed")
    assert completados >= 10, f"esperaba decenas de planes `completed`, vi {completados}"

    con_huecos = _plans_completed_with_open_boxes()
    nuevos = set(con_huecos) - _COMPLETED_WITH_OPEN_BOXES_2026_08_19
    assert not nuevos, (
        "planes con `status: completed` y casillas `- [ ]` que NO estaban en el "
        f"inventario del 2026-08-19: { ({k: con_huecos[k] for k in sorted(nuevos)}) }.\n"
        "El enum de CLAUDE.md define `completed` como «plan cerrado completamente». "
        "O se cierran las casillas, o el plan no está `completed`: las dos son "
        "arreglos; dejarlo así es que el estado del roadmap deje de significar algo."
    )


def test_the_completed_with_open_boxes_inventory_has_no_dead_entries() -> None:
    """Un plan que ya no tiene huecos tiene que salir del inventario."""
    con_huecos = _plans_completed_with_open_boxes()
    muertas = _COMPLETED_WITH_OPEN_BOXES_2026_08_19 - set(con_huecos)
    assert not muertas, (
        "estos planes ya no tienen casillas abiertas (o ya no están `completed`): "
        f"{sorted(muertas)}. Retíralos de `_COMPLETED_WITH_OPEN_BOXES_2026_08_19`."
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


def test_gate_override_names_a_gate_that_actually_exists() -> None:
    """Un `gate_override` sobre un plan SIN `blocking_plan` es decoración.

    El agujero que este test tapa, medido el 2026-08-01: la guarda de caducidad
    de arriba solo mira planes con dependencias (`if deps and not sin_cerrar`),
    así que un override sobre un `blocking_plan: null` pasaba los once tests de
    este fichero en silencio. Y `unmet_gates()` tampoco lo ve, porque descarta
    los planes sin bloqueantes antes de mirar el override.

    Por qué no es hipotético: `blocking_plan` es una lista YAML multilínea en
    varios planes, y basta vaciarla —o que el frontmatter se reescriba sin
    ella— para que el gate deje de declararse. El override sobrevive como
    afirmación de que hubo una excepción sobre un gate que el plan ya no
    reconoce: el mismo modo de fallo que docsroadmap-6 (dos sitios diciendo
    cosas distintas, gana el que leas primero), pero sin nadie que lo delate.
    """
    sin_gate: list[str] = []
    for _path, fm in _plans():
        if not fm.get("gate_override"):
            continue
        deps = fm.get("blocking_plan") or []
        deps = deps if isinstance(deps, list) else [deps]
        if not [d for d in deps if str(d).strip()]:
            sin_gate.append(str(fm["plan_id"]))
    assert not sin_gate, (
        "fases con `gate_override` y `blocking_plan` vacío: la excepción no se "
        "refiere a ningún gate declarado. O se declara el bloqueante que se está "
        f"saltando, o se retira el override: {sin_gate}"
    )


def test_readme_declares_the_real_size_of_the_validation_queue() -> None:
    """El tamaño de la cola de validación del README sale del frontmatter.

    Encontrado el 2026-08-01: `README.md` decía «35 planes están en
    `pending_human_validation`» cuando en disco eran **46**. Es el hallazgo
    docsroadmap-3 reapareciendo por el mismo sitio por el que entró la primera
    vez —un recuento tecleado a mano en un índice— y es el número que un humano
    usa para dimensionar la campaña de validación: subestimarlo en 11 planes es
    subestimar el trabajo en casi un tercio.

    El recuento hermano (`**N planes de construcción**`) ya tenía guarda en
    `test_docs_governance.py`; éste no la tenía, que es justo por qué derivó.
    """
    readme = (_ROADMAP / "README.md").read_text(encoding="utf-8")
    real = sum(1 for _p, fm in _plans() if fm["status"] == "pending_human_validation")
    assert real >= 20, f"el descubrimiento de planes falló (vio {real} en validación)"

    declarado = re.search(r"(\d+)\s+planes están en `pending_human_validation`", readme)
    assert declarado is not None, (
        "el README ya no declara el tamaño de la cola de validación humana (o "
        "cambió el formato `N planes están en \\`pending_human_validation\\``)"
    )
    assert int(declarado.group(1)) == real, (
        f"README dice {declarado.group(1)} planes en `pending_human_validation`; "
        f"el frontmatter dice {real}"
    )


#: Ficheros de `docs/roadmap/` que llevan un `status:` DEL ENUM DE PLANES pero
#: NO declaran `plan_id`, medidos el 2026-08-01. Son **17**.
#:
#: Por qué son deuda y no ruido: `_plans()` exige los dos campos, así que sin
#: `plan_id` un fichero es invisible para TODOS los guardas de gate de este
#: módulo — `test_at_most_one_phase_in_progress` incluido. Ocho de los diecisiete
#: son las fases del córtex, con casillas `- [ ]` y `blocking_plan` propio: son
#: planes en todo menos en el campo que los haría auditables.
#:
#: No entran aquí los ~14 ficheros con vocabulario propio (`published`,
#: `informe`, `open`, `delivered`, `remediation_implemented`): esos declaran a
#: gritos que no son planes, y confundirlos con fases fue justo el error que
#: destapó este agujero (un recuento que daba 46 por fichero y 35 por plan).
#:
#: Igual que `_GATE_DEBT_2026_07_29`, NO es una allowlist permanente. Ponerles
#: `plan_id` los somete de golpe a los guardas de changelog y de gate, que es
#: trabajo real y de otro carril; lo que este test garantiza mientras tanto es
#: que **nadie añade el número dieciocho**.
_STATUS_WITHOUT_PLAN_ID_2026_08_01 = frozenset(
    {
        "auditoria-2026-06-memoria-tools-marketplace.md",
        "auditoria-gestion-proyectos-2026-07-25.md",
        "auditoria-hallazgos-implementados-2026-07-10.md",
        "cortex-f1-memoria-cognitiva.md",
        "cortex-f2-afectivo.md",
        "cortex-f3-identidad.md",
        "cortex-f4-autonomia.md",
        "cortex-f5-voz-avatar.md",
        "cortex-fases.md",
        "cortex-identidad-real.md",
        "cortex-system-owner.md",
        "fixes-pesados-auditoria.md",
        "hallazgos-pendientes-2026-07-07.md",
        "mejoras-2026-06-chat-coste-cortex.md",
        "plan-unificacion-provider-id.md",
        "refactor-pipeline-ejecucion-review.md",
        "refactorizacion-por-partes-2026-07-07.md",
    }
)


def test_no_new_roadmap_file_escapes_the_guards_by_omitting_plan_id() -> None:
    """Un estado de fase sin `plan_id` es una fase que ningún guarda ve.

    El agujero, medido el 2026-08-01: `_plans()` exige `plan_id` **y** `status`,
    así que un fichero con un estado del enum pero sin identificador se salta en
    silencio la regla de «como mucho una fase `in_progress`», la del gate, la del
    `gate_override` y la del changelog. No es hipotético: son diecisiete
    ficheros, y ocho de ellos llevan casillas de tarea.

    Se descubrió por el lado tonto —un recuento de `pending_human_validation` que
    daba 46 por fichero y 35 por plan—, que es exactamente el síntoma de que dos
    poblaciones distintas se estaban llamando igual.
    """
    ofensores = {
        p.name
        for p in _md_files()
        if _frontmatter(p).get("status") in VALID_STATUS and "plan_id" not in _frontmatter(p)
    }
    total_con_estado = sum(1 for p in _md_files() if isinstance(_frontmatter(p).get("status"), str))
    assert total_con_estado >= 50, f"el descubrimiento falló (vio {total_con_estado})"

    nuevos = sorted(ofensores - _STATUS_WITHOUT_PLAN_ID_2026_08_01)
    assert not nuevos, (
        "ficheros de `docs/roadmap/` con un `status:` del enum de planes y SIN "
        f"`plan_id`, que no estaban en la deuda medida el 2026-08-01: {nuevos}. "
        "Sin `plan_id` ningún guarda de gate de este módulo los ve: añádeselo, o "
        "si de verdad no es un plan, dale un `status:` de vocabulario propio "
        "(`published`, `informe`…) para que no se confunda con una fase."
    )
