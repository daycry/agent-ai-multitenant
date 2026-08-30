"""Qué significa de verdad que un check «pasó» (ADR 0162, opción A).

`expected_signal` llevaba desde el Plan 06 guardándose en cada criterio de
aceptación **sin que nadie lo leyera**: el veredicto de un check salía —y sigue
saliendo— sólo del código de salida. Este módulo es el evaluador que faltaba.

**Por qué hacía falta, y no es teoría.** De la base de datos de la instalación
viva:

```text
vendor/bin/phpunit --testsuite E2E --colors=never   =>  ok=true
No tests executed!
```

Dos ejecuciones **en verde habiendo ejecutado cero tests**. Es decir:
``exit_code == 0`` —que es el ``expected_signal`` por defecto— **no significa
«los tests pasaron»**; puede significar «no había tests»: un ``--filter`` que no
casa con nada, una suite mal nombrada, un ``phpunit.xml`` que no ve el
directorio.

Con la opción A firmada eso deja de ser un accidente y pasa a ser una tentación:
el mismo agente que escribe el test declara el comando que lo verifica, y un
comando que pasa trivialmente es la salida barata. Por eso un criterio ejecutable
puede exigir además que el recuento de tests sea mayor que cero.

**Tres estados, y no se colapsan** — es la misma disciplina de
:mod:`shared_test_runtimes.counts`, un piso más arriba:

  ``True``   la señal se cumple
  ``False``  la señal NO se cumple — y consta por qué
  ``None``   **no se pudo evaluar**. Jamás ``False``.

El tercero es el que importa. Si «no supimos leer la salida» se leyera como «no
ejecutó ni un test», la plataforma fabricaría un **falso fallo**: acusaría al
código del tenant de algo que sólo dice que nuestro reconocedor no entendió el
texto. Evitar los falsos fallos manda sobre todo lo demás.

**Esto MIDE, no decide.** Ningún valor de aquí cambia si un check se da por
pasado o fallado: ``TestRuntimeResult.all_passed()`` sigue saliendo sólo del
código de salida. El gate es la opción C del ADR 0162 y **no está firmada**.
"""

from __future__ import annotations

import re

from shared_test_runtimes.counts import TestCounts

# El default histórico de todo criterio (`test_runtime.AcceptanceCheck`). Mira
# SÓLO el código de salida, y tiene que seguir haciéndolo: si evaluarlo empezara
# a exigir un recuento, cada criterio ya escrito pasaría a pedir algo que nadie
# declaró — la opción C por la puerta de atrás.
SIGNAL_EXIT_ZERO = "exit_code == 0"

# La señal que cierra la trampa del §«La trampa que hay que cerrar CON A»: el
# proceso salió bien Y se ejecutó al menos un test. Es OPT-IN — la escribe quien
# declara el criterio, no la plataforma.
SIGNAL_EXIT_ZERO_AND_TESTS = "exit_code == 0 and tests > 0"

# Tolerancia de forma, no de vocabulario: se admiten espacios de más y
# mayúsculas, y `tests > 0` / `tests >= 1`, porque quien escribe esto es un
# humano en un formulario o un modelo. Lo que NO se admite es inventar: una
# expresión que no case con ninguno de los dos patrones queda sin evaluar.
_EXIT_ZERO_RE = re.compile(r"^exit_?code\s*==\s*0$", re.IGNORECASE)
_EXIT_ZERO_AND_TESTS_RE = re.compile(
    r"^exit_?code\s*==\s*0\s*(?:and|&&)\s*tests\s*(?:>\s*0|>=\s*1)$",
    re.IGNORECASE,
)


def normalise_signal(raw: str | None) -> str | None:
    """La forma canónica de una señal, o ``None`` si no la reconocemos.

    Devolver ``None`` para lo desconocido es deliberado y es la regla del ADR
    aplicada aquí: *un valor ausente no puede significar nada más fuerte que
    «desconocido»*. Una expresión como ``coverage >= 80%`` es una intención
    legítima que este módulo todavía no sabe comprobar; tratarla como falsa
    inventaría un fallo, y tratarla como cierta inventaría una garantía.
    """
    text = " ".join((raw or "").split())
    if not text:
        return None
    if _EXIT_ZERO_AND_TESTS_RE.match(text):
        return SIGNAL_EXIT_ZERO_AND_TESTS
    if _EXIT_ZERO_RE.match(text):
        return SIGNAL_EXIT_ZERO
    return None


def requires_test_count(raw: str | None) -> bool:
    """Si la señal exige haber ejecutado al menos un test.

    Lo usa quien decide si merece la pena medir, y quien explica a un humano por
    qué un check con exit 0 no cuenta como verificado.
    """
    return normalise_signal(raw) == SIGNAL_EXIT_ZERO_AND_TESTS


def evaluate_signal(
    raw: str | None,
    *,
    exit_code: int,
    counts: TestCounts | None,
) -> bool | None:
    """¿Se cumple la señal que el criterio declaró? ``None`` = no se pudo saber.

    El orden de las comprobaciones no es casual:

    1. **Señal desconocida → ``None``.** No se adivina.
    2. **``exit_code != 0`` → ``False``.** Sin ambigüedad y sin necesidad de
       medir nada: el proceso falló. Devolver ``None`` aquí perdería una certeza
       que sí tenemos, y dejaría sin señal justo el caso que más claro está.
    3. **Sólo código de salida → el código de salida.** Comportamiento histórico,
       intacto.
    4. **Con recuento exigido y recuento AUSENTE → ``None``.** El eje del módulo:
       si no se pudo medir, no se puede afirmar que fallara. Ver
       :mod:`shared_test_runtimes.counts` para los tres estados del recuento.
    5. **Con recuento exigido y recuento presente → ``total > 0``.** Aquí es
       donde ``No tests executed!`` con exit 0 deja de pasar por bueno.
    """
    signal = normalise_signal(raw)
    if signal is None:
        return None
    if exit_code != 0:
        return False
    if signal == SIGNAL_EXIT_ZERO:
        return True
    if counts is None:
        return None
    return counts.total > 0


__all__ = [
    "SIGNAL_EXIT_ZERO",
    "SIGNAL_EXIT_ZERO_AND_TESTS",
    "evaluate_signal",
    "normalise_signal",
    "requires_test_count",
]
