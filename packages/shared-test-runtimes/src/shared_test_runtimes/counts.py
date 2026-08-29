"""Cuántos tests corrieron de verdad (ADR 0162, ola 1).

Los ocho parsers de :mod:`shared_test_runtimes.parsers` llevaban escritos desde
el Plan 06 y **nadie en `apps/` los importaba**. Este módulo es la boca por la
que se encienden: dado el texto que produjo un runtime y los ``output_parsers``
que su plantilla declara en el catálogo, devuelve el recuento de tests… **o la
constancia explícita de que no se pudo medir**.

La distinción de arriba es el módulo entero, así que conviene enunciarla como
regla: **«no se pudo parsear» NUNCA puede convertirse en «cero tests»**. Son
tres estados y no se pueden colapsar:

  (a) parseado, N tests    → :class:`TestCounts` con ``total == N``
  (b) parseado, CERO tests → :class:`TestCounts` con ``total == 0``
  (c) no parseable         → ``None``

Confundir (c) con (b) fabrica un falso fallo —le diría al reviewer «este cambio
no ejecutó ni un test» cuando lo único cierto es «no supimos leer la salida»—, y
evitar los falsos fallos es la mitad del encargo que manda sobre la otra. Por
eso, ante cualquier ambigüedad, la respuesta de este módulo es ``None``.

**Esto MIDE, no decide.** Ningún valor de aquí cambia si un check se da por
pasado o fallado; el gate es la opción C del ADR 0162 y no está firmada.

Por qué existe la mitad de reconocimiento por texto (:func:`_from_runner_text`)
en vez de leer sólo los parsers estructurados: hoy **nadie genera ni recoge un
``junit.xml``**. Lo único que la plataforma tiene en la mano es el stdout/stderr
concatenado de los checks, y ahí es donde los runners imprimen su epílogo. Los
parsers estructurados quedan cableados igualmente —cuestan una línea y el día
que alguien recoja el fichero mandan ellos, que son más fiables—, pero mientras
tanto quien contesta es el reconocedor de epílogos.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shared_test_runtimes.parsers import PARSERS
from shared_test_runtimes.test_report import TestSummary

# ``raw_text`` NO cuenta, y esta constante es la pieza más importante del módulo.
#
# Ese parser está declarado en el ``output_parsers`` de las catorce plantillas,
# NUNCA devuelve ``None`` (es el suelo de la cadena, por contrato) y siempre trae
# un ``TestSummary()`` a ceros porque no parsea nada: es una heurística de
# ESTADO ("¿el texto menciona 'fail'?"), no una medición. Si entrara en la
# cadena de recuento ganaría siempre, y CUALQUIER salida desconocida se
# convertiría en «cero tests» — la confusión (c)→(b) en su forma más fácil de
# cometer y la más difícil de ver, porque el resultado parece un dato.
_NON_COUNTING_PARSERS = frozenset({"raw_text"})


@dataclass(frozen=True)
class TestCounts:
    """Un recuento MEDIDO. Su mera existencia significa «se pudo leer»."""

    total: int
    passed: int
    failed: int
    errored: int
    skipped: int
    source: str
    """De dónde salió el número: el id del parser del catálogo que lo produjo
    (``junit_xml``, ``jest_json``…) o el reconocedor de epílogo que lo leyó del
    stdout (``phpunit_text``, ``pytest_text``). Va en el outcome porque no es lo
    mismo un número que viene de un informe estructurado que uno leído de una
    línea de texto, y quien lo consuma tiene derecho a saberlo."""

    def as_dict(self) -> dict[str, Any]:
        """Forma JSON-safe. Acaba en un JSONB de auditoría y en el prompt."""
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "skipped": self.skipped,
            "source": self.source,
        }


def _from_summary(summary: TestSummary, *, source: str) -> TestCounts:
    return TestCounts(
        total=summary.total,
        passed=summary.passed,
        failed=summary.failed,
        errored=summary.errored,
        skipped=summary.skipped,
        source=source,
    )


# ---------------------------------------------------------------------------
# Reconocedores de epílogo por stdout
# ---------------------------------------------------------------------------
#
# Cada patrón de aquí es una oportunidad de dar un número EQUIVOCADO, que es
# peor que no dar ninguno: un recuento falso se lee como un hecho. Por eso están
# anclados a la forma canónica de cada runner y no a algo parecido.


@dataclass(frozen=True)
class _Tally:
    """Acumulador de un reconocedor. ``matched`` es lo que distingue (b) de (c).

    Un ``_Tally`` sin ``matched`` NO es «cero tests»: es «no encontré mi
    epílogo». Que sean dos cosas distintas y no un total a cero es la razón de
    que este campo exista en vez de mirar ``total == 0``."""

    matched: bool = False
    total: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    def plus(self, *, total: int, failed: int, errored: int, skipped: int) -> _Tally:
        return _Tally(
            matched=True,
            total=self.total + total,
            failed=self.failed + failed,
            errored=self.errored + errored,
            skipped=self.skipped + skipped,
        )

    def as_counts(self, source: str) -> TestCounts | None:
        if not self.matched:
            return None
        return TestCounts(
            total=self.total,
            # `passed` se DERIVA en vez de leerse: PHPUnit no lo imprime nunca,
            # y «lo que quedó sin fallar, sin errar y sin saltarse» es la única
            # lectura que no inventa un número.
            passed=max(0, self.total - self.failed - self.errored - self.skipped),
            failed=self.failed,
            errored=self.errored,
            skipped=self.skipped,
            source=source,
        )


# --- PHPUnit ----------------------------------------------------------------

# `OK (14 tests, 28 assertions)`. PHPUnit sólo la imprime cuando TODO pasó, y
# siempre con las aserciones detrás; exigirlas descarta cualquier "OK (" suelto
# de un log de build.
_PHPUNIT_OK = re.compile(r"^OK \((?P<total>\d+) tests?, \d+ assertions?\)", re.M)

# La salida literal del ADR 0162: exit 0 y cero tests. El caso (b) por
# excelencia, y el que no puede confundirse con «no supimos leer».
_PHPUNIT_NONE = re.compile(r"^No tests executed!", re.M)

# `Tests: 5, Assertions: 9, Failures: 1.` — el epílogo cuando hubo fallos, o
# cuando hubo saltados/incompletos/arriesgados. Se exige `, Assertions:` a
# propósito: Pest imprime `Tests:  2 failed, 1 passed`, donde el primer número
# NO es el total, y sin ese anclaje contaríamos 2 de 3.
_PHPUNIT_TOTALS = re.compile(
    r"^\s*Tests:\s*(?P<total>\d+),\s*Assertions:\s*\d+(?P<rest>[^\n]*)", re.M
)
_PHPUNIT_BUCKET = re.compile(r"(?P<bucket>Failures|Errors|Skipped):\s*(?P<n>\d+)")


def _phpunit_tally(text: str) -> _Tally:
    tally = _Tally()
    for _none in _PHPUNIT_NONE.finditer(text):
        # Cada `No tests executed!` es un check que corrió y no ejecutó nada:
        # suma CERO tests, pero marca `matched` — es el estado (b) del ADR.
        tally = tally.plus(total=0, failed=0, errored=0, skipped=0)
    for ok in _PHPUNIT_OK.finditer(text):
        tally = tally.plus(total=int(ok.group("total")), failed=0, errored=0, skipped=0)
    for totals in _PHPUNIT_TOTALS.finditer(text):
        buckets = {
            m.group("bucket"): int(m.group("n"))
            for m in _PHPUNIT_BUCKET.finditer(totals.group("rest"))
        }
        tally = tally.plus(
            total=int(totals.group("total")),
            failed=buckets.get("Failures", 0),
            errored=buckets.get("Errors", 0),
            skipped=buckets.get("Skipped", 0),
        )
    return tally


# --- pytest -----------------------------------------------------------------

# La línea de resumen de pytest, `==== 1 failed, 13 passed in 0.42s ====`.
# pytest imprime MÁS líneas de `=` que no son resúmenes (`FAILURES`, `short test
# summary info`); contar cualquiera de ellas daría un número inventado, así que
# se exige el sufijo ` in <t>s` que sólo lleva la última.
_PYTEST_BANNER = re.compile(r"^={2,}\s+(?P<body>.+?)\s+={2,}\s*$", re.M)
_PYTEST_TAIL = re.compile(r"^(?P<items>.*?)\s+in\s+[\d.]+s(?:\s*\(.*\))?$")
_PYTEST_ITEM = re.compile(r"^(?P<n>\d+)\s+(?P<word>[a-z]+)$")

# Qué palabra de pytest cuenta como qué. `xfailed`/`xpassed` SÍ se ejecutaron.
_PYTEST_BUCKETS: dict[str, str] = {
    "passed": "passed",
    "xpassed": "passed",
    "failed": "failed",
    "error": "errored",
    "errors": "errored",
    "skipped": "skipped",
    "xfailed": "skipped",
}
# Palabras que aparecen en la misma línea y NO son tests ejecutados. Un test
# `deselected` no corrió; un `warning` ni siquiera es un test.
_PYTEST_IGNORED = frozenset({"deselected", "warning", "warnings", "rerun", "reruns"})


def _pytest_summary_bodies(text: str) -> list[str]:
    """Los candidatos a línea de resumen de pytest, con y sin banner de `=`.

    El banner sólo lo imprime pytest en modo normal. Con ``-q`` —que es el modo
    canónico de ESTE repo, y por tanto el que más se va a encontrar— el resumen
    sale a pelo:

        5347 passed, 9 skipped, 2 warnings in 231.07s (0:03:51)

    Exigir el banner dejaba sin medir precisamente al runtime más usado. Lo que
    NO se relaja es el discriminante: sigue haciendo falta el sufijo ``in <t>s``,
    y que TODOS los elementos casen ``<n> <palabra>``; cualquier forma
    desconocida abandona el reconocimiento entero y devuelve ausencia. Sin esa
    doble condición, una línea cualquiera del log podría pasar por un recuento.
    """
    con_banner = [m.group("body") for m in _PYTEST_BANNER.finditer(text)]
    if con_banner:
        return con_banner
    return [line.strip() for line in text.splitlines() if line.strip()]


def _pytest_tally(text: str) -> _Tally:
    tally = _Tally()
    for body in _pytest_summary_bodies(text):
        tail = _PYTEST_TAIL.match(body)
        if tail is None:
            continue  # `FAILURES`, `short test summary info`, … no son el resumen
        items = tail.group("items").strip()
        if items == "no tests ran":
            tally = tally.plus(total=0, failed=0, errored=0, skipped=0)
            continue
        buckets: dict[str, int] = {}
        for chunk in items.split(","):
            item = _PYTEST_ITEM.match(chunk.strip())
            if item is None:
                return _Tally()  # forma desconocida → se abandona TODO (estado c)
            word = item.group("word")
            if word in _PYTEST_IGNORED:
                continue
            bucket = _PYTEST_BUCKETS.get(word)
            if bucket is None:
                # Una palabra que no conocemos podría ser tests o podría no
                # serlo. No se adivina: se abandona el reconocimiento entero.
                return _Tally()
            buckets[bucket] = buckets.get(bucket, 0) + int(item.group("n"))
        line_total = sum(buckets.values())
        tally = tally.plus(
            total=line_total,
            failed=buckets.get("failed", 0),
            errored=buckets.get("errored", 0),
            skipped=buckets.get("skipped", 0),
        )
    return tally


_RECOGNISERS: tuple[tuple[str, Callable[[str], _Tally]], ...] = (
    ("phpunit_text", _phpunit_tally),
    ("pytest_text", _pytest_tally),
)


def _from_runner_text(text: str) -> TestCounts | None:
    """Leer el epílogo que el runner imprimió por stdout.

    Si **más de un** reconocedor encuentra su epílogo en el mismo texto, el
    resultado es ``None``: podría ser un log contaminado o dos runners mezclados,
    y sumar números de dos formatos distintos es justamente inventar. La regla
    del ADR aplicada al pie de la letra: ante la duda, ausencia."""
    found: list[TestCounts] = []
    for source, recognise in _RECOGNISERS:
        counts = recognise(text).as_counts(source)
        if counts is not None:
            found.append(counts)
    if len(found) != 1:
        return None
    return found[0]


# ---------------------------------------------------------------------------
# La boca pública
# ---------------------------------------------------------------------------


def count_tests(text: str, *, runtime: str, parsers: tuple[str, ...]) -> TestCounts | None:
    """Cuántos tests reporta ``text``, o ``None`` si no se pudo saber.

    ``parsers`` es el ``output_parsers`` que el catálogo declara para la
    plantilla, y se recorre **en ese orden**: el primero que devuelva algo gana,
    porque el catálogo ya expresa la preferencia (``java-maven`` prueba
    ``surefire_xml`` antes que ``junit_xml`` por una razón). ``raw_text`` se
    salta siempre — ver :data:`_NON_COUNTING_PARSERS`.

    Si ningún parser estructurado reconoce el texto, se intenta leer el epílogo
    del runner (:func:`_from_runner_text`), que hoy es la única fuente real
    porque nadie recoge los ficheros de informe.

    **No lanza nunca.** Un parser que reviente o un id que el registro no
    implemente valen «este parser no dice nada», no una excepción que se lleve
    por delante la fase de tests: la medición es nueva y el veredicto lleva años
    funcionando.
    """
    for parser_id in parsers:
        if parser_id in _NON_COUNTING_PARSERS:
            continue
        parse = PARSERS.get(parser_id)
        if parse is None:
            # `OutputParser` (el Literal del esquema) declara `go_test_json` y
            # `rust_test_json`, que el registro NO implementa. `try_parsers`
            # hace `PARSERS[id]` y revienta con KeyError para la plantilla
            # `go-test`; aquí no se hereda ese fallo.
            continue
        try:
            report = parse(text, runtime=runtime)
        except Exception:
            continue
        if report is not None:
            return _from_summary(report.summary, source=parser_id)
    try:
        return _from_runner_text(text)
    except Exception:
        return None


__all__ = ["TestCounts", "count_tests"]
