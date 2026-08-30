"""El espejo TypeScript de `expected_signal` no puede divergir del original.

**Por qué existe (hallazgo MENOR 9 de la ola 2 del ADR 0162).**
``apps/admin-panel/lib/acceptance-criteria.ts`` se declara a sí mismo «espejo»
de dos piezas del worker y copia a mano su default. Durante la ola 1 el worker
empezó a evaluar ``expected_signal``
(:mod:`shared_test_runtimes.signals`) y el espejo se quedó afirmando lo
contrario — «NO evalúa `expected_signal` en ningún punto» — durante todo el
tiempo que separó a las dos olas.

Eso no es una molestia de mantenimiento: la misma frase estaba en el texto de
ayuda que lee el operador («todavía no se evalúan»), o sea que la UI desanimaba
de escribir ``exit_code == 0 and tests > 0``, que es exactamente la señal con la
que el ADR cierra su §«La trampa que hay que cerrar CON A». Un espejo que miente
sobre la otra mitad es el modo de fallo que este ADR persigue.

**Qué ata y qué no, dicho sin adornos.** La prosa de un comentario no se puede
verificar a máquina sin escribir una guarda frágil que se rompa al reescribir una
frase. Lo que SÍ se puede atar es el trozo mecánico del espejo: la cadena del
default, que existe literalmente en los dos lados. Si alguien cambia uno y no el
otro, la UI sembrará criterios con una señal que el evaluador no reconoce y
`evaluate_signal` los dejará **sin evaluar** — el estado `None`, que no es falso
pero tampoco mide nada.
"""

from __future__ import annotations

import re
from pathlib import Path

# Se importa con alias porque pytest intenta COLECCIONAR cualquier nombre que
# empiece por `Test` y avisa de que no puede (tiene `__init__`). El alias evita
# el ruido sin renombrar un tipo del dominio.
from shared_test_runtimes.counts import TestCounts as Counts
from shared_test_runtimes.signals import (
    SIGNAL_EXIT_ZERO,
    SIGNAL_EXIT_ZERO_AND_TESTS,
    evaluate_signal,
)

_ROOT = Path(__file__).resolve().parents[2]
_MIRROR = _ROOT / "apps" / "admin-panel" / "lib" / "acceptance-criteria.ts"

_DEFAULT_RE = re.compile(
    r'export const DEFAULT_EXPECTED_SIGNAL\s*=\s*"(?P<signal>[^"]*)"',
)


def _mirrored_default() -> str:
    """El default que el panel siembra en cada criterio nuevo."""
    match = _DEFAULT_RE.search(_MIRROR.read_text(encoding="utf-8"))
    # La aserción de «encontré algo» que exige
    # `docs/03-guides/verificar-antes-de-implementar.md` §4: un parser que deja
    # de casar pasaría vacío y esta guarda envejecería sin avisar.
    assert match is not None, f"no se encontró DEFAULT_EXPECTED_SIGNAL en {_MIRROR}"
    return match.group("signal")


def test_el_default_del_panel_es_el_mismo_que_el_del_worker() -> None:
    assert _mirrored_default() == SIGNAL_EXIT_ZERO


def test_el_default_del_panel_lo_sabe_evaluar_el_worker() -> None:
    """La comprobación que de verdad importa: no que las cadenas coincidan, sino
    que el evaluador RECONOZCA la que siembra la UI.

    Sin esto, un retoque cosmético en cualquiera de los dos lados —un espacio,
    otra forma de escribir lo mismo— dejaría a todo criterio nuevo con una señal
    que `evaluate_signal` no casa con ningún patrón y devuelve ``None``: «no se
    pudo evaluar». Nadie vería un fallo; sencillamente dejaría de medirse.
    """
    counts = Counts(total=3, passed=3, failed=0, errored=0, skipped=0, source="junit_xml")
    assert evaluate_signal(_mirrored_default(), exit_code=0, counts=counts) is True
    assert evaluate_signal(_mirrored_default(), exit_code=1, counts=counts) is False


def test_la_senal_opt_in_que_la_ayuda_recomienda_tambien_se_reconoce() -> None:
    """El texto de ayuda del panel le dice al operador que escriba
    ``exit_code == 0 and tests > 0``. Recomendar una señal que el evaluador no
    reconociera sería la misma mentira por el otro lado."""
    hint = (_ROOT / "apps" / "admin-panel" / "lib" / "i18n" / "dictionary.ts").read_text(
        encoding="utf-8"
    )
    assert SIGNAL_EXIT_ZERO_AND_TESTS in hint, (
        "la ayuda de «Señal esperada» ya no recomienda la señal que cierra el falso verde"
    )
    sin_tests = Counts(total=0, passed=0, failed=0, errored=0, skipped=0, source="junit_xml")
    assert evaluate_signal(SIGNAL_EXIT_ZERO_AND_TESTS, exit_code=0, counts=sin_tests) is False
