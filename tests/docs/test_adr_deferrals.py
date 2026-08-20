"""Una decisión aplazada con condición de reapertura, comprobable (`task_gov_11`).

Plan [`gov-01`](../../docs/roadmap/gov-01-precedencia-prompts-y-rigor.md),
fase 5. Hermana de [`test_adr_precedence.py`](test_adr_precedence.py) —de donde
importa el parseo del frontmatter y el descubrimiento de casillas, para que no
haya dos parsers de YAML-a-mano que se bifurquen— y del mismo linaje que los
tests del `gate_override` de `CLAUDE.md`: **una excepción escrita caduca, y hay
que poder ver que caducó**.

## El modo de fallo que cubre

Aplazar algo «con disparador escrito» sólo es mejor que aplazarlo sin él si
alguien se entera **el día que el disparador salta**. Si la condición vive en
prosa, lo que pasa es exactamente lo que pasó aquí: el operador aplazó SkillOpt
el 2026-08-12 condicionándolo a dos casillas (`task_gov_02` y `task_gov_05`), las
dos se cerraron en pasadas posteriores —el 2026-08-19 y el 2026-08-20— y quien
las cerró no tenía forma de saber que al marcarlas estaba venciendo un
aplazamiento. Una nota de aplazamiento cuyo disparador ya saltó y nadie notó es
una decisión que sigue vigente por inercia, no por criterio.

## El campo, y las cuatro reglas

Un ADR que aplaza algo lo declara en su frontmatter:

```yaml
reopen_when: [task_gov_02, task_gov_05]
reopen_triggered_on: 2026-08-20
```

* `reopen_when` — las casillas de roadmap (o los `plan_id`) que tienen que
  existir para que la decisión se reabra. Cada id **existe** y **cita de vuelta
  al ADR**, por la misma razón que el `rejects:` de `task_gov_01`: quien cierra
  la casilla abre el plan, no el corpus de ADR, y es justo esa persona la que
  tiene que enterarse de que su `[x]` dispara una reapertura.
* `reopen_triggered_on` — la fecha en que se cumplió la última condición. Es
  **obligatorio** en cuanto todas las casillas están `[x]`, y está **prohibido**
  mientras alguna siga abierta. Las dos direcciones importan: sin la primera el
  aplazamiento sobrevive a su propia condición en silencio; sin la segunda
  cualquiera puede declarar un disparo que no ha ocurrido.

## Por qué no usa PyYAML

Por lo mismo que la guarda hermana: hay **dos** ADR (`0107` y `0108`) cuyo
frontmatter no carga con `yaml.safe_load` (`related: [hallazgo #11 (…)]`, donde
el `#` abre un comentario dentro de una secuencia de flujo). Un parser que
dependiera de PyYAML pasaría en verde ignorando justo los ficheros rotos.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.docs.test_adr_precedence import (
    _ADR_NUMBER_RE,
    _adr_files,
    _cites_adr,
    _clean,
    _frontmatter_block,
    _key_line_re,
    _parse_list_field,
    _plan_files,
    _task_blocks,
)

#: La clave que declara de qué depende la reapertura.
REOPEN_WHEN = "reopen_when"

#: La clave que declara que el disparador YA saltó, con su fecha.
REOPEN_TRIGGERED_ON = "reopen_triggered_on"

#: Fecha ISO a secas. El corpus escribe así el `date:` del frontmatter y no hay
#: motivo para que ésta se escriba distinto.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _scalar_field(block: str, key: str) -> str | None:
    """El valor escalar de una clave del frontmatter, o ``None`` si no está."""
    key_re = _key_line_re(key)
    for line in block.split("\n"):
        match = key_re.match(line)
        if match is not None:
            value = _clean(match.group(1))
            return value or None
    return None


def _deferrals() -> list[tuple[str, Path, tuple[str, ...], str | None]]:
    """``(número, fichero, condiciones, fecha de disparo)`` por ADR aplazado."""
    out: list[tuple[str, Path, tuple[str, ...], str | None]] = []
    for path in _adr_files():
        block = _frontmatter_block(path)
        numbered = _ADR_NUMBER_RE.match(path.name)
        if block is None or numbered is None:
            continue
        conditions = _parse_list_field(block, REOPEN_WHEN)
        triggered = _scalar_field(block, REOPEN_TRIGGERED_ON)
        if not conditions and triggered is None:
            continue
        out.append((numbered.group(1), path, conditions, triggered))
    return out


def _all_conditions_met(conditions: tuple[str, ...]) -> bool:
    """¿Están TODAS las casillas nombradas cerradas `[x]`?

    Un `plan_id` (en vez de un `task_id`) no tiene casilla; se resuelve por su
    `status: completed`, que es el equivalente al nivel del plan.
    """
    tasks, plans = _task_blocks(), _plan_files()
    for target in conditions:
        if target in tasks:
            if tasks[target].marked is not True:
                return False
        elif target in plans:
            if "status: completed" not in plans[target].read_text(encoding="utf-8"):
                return False
        else:  # referencia muerta: la cubre su propio test, aquí no decide nada
            return False
    return True


# ---------------------------------------------------------------------------
# No-vacuidad
# ---------------------------------------------------------------------------
def test_the_deferral_field_is_actually_used() -> None:
    """El campo tiene que estar USADO, no sólo documentado.

    Es la guarda contra el patrón dominante de esta base
    (`docs/03-guides/verificar-antes-de-implementar.md` §5): mecanismo
    entregado, cero llamantes. Sin un solo `reopen_when:` en el corpus, los
    cuatro chequeos de abajo pasan en vacío y el aplazamiento vuelve a ser
    prosa.
    """
    deferrals = _deferrals()
    assert deferrals, (
        "ningun ADR declara `reopen_when:`. El caso real esta medido: SkillOpt, "
        "aplazado el 2026-08-12 con disparador escrito (`task_gov_02` + "
        "`task_gov_05`), y esa nota es el ADR de `task_gov_11`"
    )


def test_every_deferral_declares_at_least_one_condition() -> None:
    """Un aplazamiento sin condición es un «no» indefinido con otro nombre.

    Cubre además el `reopen_triggered_on:` huérfano: una fecha de disparo sin
    `reopen_when:` no dice qué se disparó.
    """
    empty = [(adr, path.name) for adr, path, conditions, _ in _deferrals() if not conditions]
    assert not empty, (
        "estos ADR aplazan algo sin decir CUANDO se reabre; un aplazamiento sin "
        "condicion es indefinido, que es justo lo que `task_gov_11` vino a "
        "evitar:\n" + "\n".join(f"  ADR {adr} ({name})" for adr, name in sorted(empty))
    )


# ---------------------------------------------------------------------------
# Las reglas del campo
# ---------------------------------------------------------------------------
def test_reopen_when_points_at_something_that_exists() -> None:
    """Una condición que apunta a un id inexistente no se puede cumplir nunca."""
    tasks, plans = _task_blocks(), _plan_files()
    dead = [
        (adr, target)
        for adr, _, conditions, _ in _deferrals()
        for target in conditions
        if target not in tasks and target not in plans
    ]
    assert not dead, (
        "estos `reopen_when:` apuntan a un plan_id / task_id que NO existe en "
        "docs/roadmap/:\n" + "\n".join(f"  ADR {adr} -> {target}" for adr, target in sorted(dead))
    )


def test_each_condition_cites_the_adr_back() -> None:
    """La relación consta en los DOS lados, como el `rejects:` de `task_gov_01`.

    Quien cierra la casilla abre el plan, no el corpus de ADR. Si su nota de
    cierre no nombra al ADR que depende de ella, marcarla `[x]` vence un
    aplazamiento sin que nadie se entere — que es literalmente el fallo que este
    fichero existe para impedir.
    """
    tasks, plans = _task_blocks(), _plan_files()
    orphan: list[tuple[str, str]] = []
    for adr, _, conditions, _ in _deferrals():
        for target in conditions:
            if target in tasks:
                text = tasks[target].text
            elif target in plans:
                text = plans[target].read_text(encoding="utf-8")
            else:
                continue  # cubierto por el test de referencias muertas
            if not _cites_adr(text, adr):
                orphan.append((adr, target))
    assert not orphan, (
        "estas condiciones de reapertura solo constan en el ADR; quien cierre la "
        "casilla no vera que su `[x]` vence un aplazamiento:\n"
        + "\n".join(f"  ADR {adr} <- {target}" for adr, target in sorted(orphan))
    )


def test_a_fired_trigger_is_declared_and_not_silent() -> None:
    """El disparador que ya saltó tiene que constar, con su fecha.

    Es la mitad que hace que el mecanismo no envejezca: sin ella el aplazamiento
    sobrevive a su propia condición y sigue vigente por inercia. Mismo espíritu
    que `test_gate_override_only_where_the_gate_is_actually_unmet` de la guarda
    del roadmap: una excepción escrita caduca.
    """
    silent = [
        (adr, path.name, conditions)
        for adr, path, conditions, triggered in _deferrals()
        if conditions and triggered is None and _all_conditions_met(conditions)
    ]
    assert not silent, (
        "estos ADR siguen aplazando algo cuyas condiciones YA se cumplen, y no "
        "lo dicen. Anade `reopen_triggered_on: AAAA-MM-DD` y reabre la "
        "decision:\n"
        + "\n".join(
            f"  ADR {adr} ({name}): {', '.join(conditions)} estan cerradas"
            for adr, name, conditions in sorted(silent)
        )
    )


def test_a_declared_trigger_is_really_fired() -> None:
    """Y la dirección contraria: no se declara un disparo que no ha ocurrido.

    Sin este test, `reopen_triggered_on:` sería la forma barata de silenciar el
    de arriba.
    """
    premature = [
        (adr, path.name, [t for t in conditions if not _all_conditions_met((t,))])
        for adr, path, conditions, triggered in _deferrals()
        if triggered is not None and not _all_conditions_met(conditions)
    ]
    assert not premature, (
        "estos ADR declaran que su disparador salto y sus condiciones NO estan "
        "cumplidas:\n"
        + "\n".join(
            f"  ADR {adr} ({name}): siguen abiertas {', '.join(pending)}"
            for adr, name, pending in sorted(premature)
        )
    )


def test_the_trigger_date_is_an_iso_date() -> None:
    """Una fecha ilegible no permite auditar cuánto lleva vencido el aplazamiento."""
    malformed = [
        (adr, triggered)
        for adr, _, _, triggered in _deferrals()
        if triggered is not None and not _ISO_DATE_RE.match(triggered)
    ]
    assert not malformed, (
        "`reopen_triggered_on:` se escribe como el `date:` del corpus, "
        "AAAA-MM-DD:\n" + "\n".join(f"  ADR {adr} -> {value}" for adr, value in sorted(malformed))
    )
