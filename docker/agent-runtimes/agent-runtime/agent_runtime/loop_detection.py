"""Repetitive-loop detection (task_02_14).

A model that has lost the plot tends to retry the same action forever.
`LoopDetector` fingerprints each action (tool + args) and flags the
execution once one fingerprint has been seen **more than** `threshold`
times — with the default threshold of 3, the 4th identical action
aborts the run with `SafeguardCode.REPETITIVE_LOOP`.

Además de CUÁNTAS veces se repite una acción, el detector lleva la cuenta de
cuántas veces seguidas ha FALLADO CON EL MISMO ERROR (:meth:`note_outcome` /
:meth:`is_failing_identically`). La distinción importa porque el consumidor
(``graph.plan``) exime de la guarda dura a las tools de sólo lectura (Tema C):
repetir una lectura que FUNCIONA es exploración cara pero informativa, y la
acotan el nudge y ``max_iterations``. Repetirla cuando devuelve SIEMPRE el mismo
error no informa de nada — el agente está atascado.

MEDIDO (2026-08-31, ejecución ``01a05881-89d7-79fa-be72-bd0e7c1a9fbb``): 14 de
las 23 llamadas al modelo fueron ``list_files {}`` devolviendo siempre
``a non-empty 'path' is required``; sin esta mitad, la guarda no disparó nunca y
el run murió por ``max_tokens``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Same action seen more than this many times => repetitive loop.
DEFAULT_LOOP_THRESHOLD = 3


@dataclass
class LoopDetector:
    """Counts identical actions and flags a runaway repetition."""

    threshold: int = DEFAULT_LOOP_THRESHOLD
    _counts: dict[str, int] = field(default_factory=dict)
    _history: list[str] = field(default_factory=list)
    # fingerprint de acción → (firma del último error, repeticiones CONSECUTIVAS de
    # ese mismo error). Una acción con éxito, o con un error distinto, rompe la racha.
    _failures: dict[str, tuple[str, int]] = field(default_factory=dict)

    def record(self, action: dict[str, Any]) -> bool:
        """Record an action; return True once it is a repetitive loop."""
        fingerprint = self._fingerprint(action)
        self._counts[fingerprint] = self._counts.get(fingerprint, 0) + 1
        self._history.append(fingerprint)
        return self._counts[fingerprint] > self.threshold

    def note_progress(self) -> None:
        """Reset the repetition counters after INTERMEDIATE PROGRESS (G8-B, ADR 0103).

        The caller invokes this only when a productive turn's action DIFFERS from the
        previous productive one — i.e. a legit ``edit → build → edit → build`` cycle
        where an idempotent build re-runs between genuine edits. Without it the build's
        identical fingerprint would accumulate and trip at the 4th run despite the
        interleaved progress. A producing action repeated with NO different action
        between (same fingerprint) never triggers this, so it still accumulates and
        trips; ``_history`` (``total_actions``) is preserved for budgeting.

        Las rachas de fallo idéntico se limpian POR EL MISMO MOTIVO que los
        contadores: «hubo progreso intermedio, el presupuesto de repetición vuelve
        a cero». Limpiar una mitad y no la otra dejaría el detector incoherente
        consigo mismo, porque el corte duro exige las DOS condiciones a la vez.
        """
        self._counts.clear()
        self._failures.clear()

    def note_outcome(self, action: dict[str, Any], *, ok: bool, error: Any) -> None:
        """Anota CÓMO acabó ``action`` para llevar la racha de fallos idénticos.

        Un ÉXITO rompe la racha entera (no la decrementa): que la misma llamada
        vuelva a funcionar es información nueva, y con ella la repetición vuelve a
        ser sólo exploración cara. Un error DISTINTO también la rompe y arranca una
        nueva: dos errores distintos sobre la misma acción siguen diciendo cosas
        distintas (p. ej. «fichero no existe» → «permiso denegado» describe un
        avance del diagnóstico, no un atasco).
        """
        fingerprint = self._fingerprint(action)
        if ok:
            self._failures.pop(fingerprint, None)
            return
        signature = self._error_signature(error)
        previous, streak = self._failures.get(fingerprint, ("", 0))
        self._failures[fingerprint] = (signature, streak + 1 if signature == previous else 1)

    def failure_streak(self, action: dict[str, Any]) -> int:
        """Cuántas veces SEGUIDAS ha fallado esta acción con el MISMO error."""
        return self._failures.get(self._fingerprint(action), ("", 0))[1]

    def failure_error(self, action: dict[str, Any]) -> str | None:
        """El error que se está repitiendo, o ``None`` si no hay racha viva.

        Lo consume el resumen del corte para que el operador lea POR QUÉ se cortó
        sin tener que abrir los steps."""
        signature, streak = self._failures.get(self._fingerprint(action), ("", 0))
        return signature if streak else None

    def is_failing_identically(self, action: dict[str, Any]) -> bool:
        """Si esta acción lleva ``threshold`` fallos seguidos con el mismo error.

        Mismo umbral que la repetición a secas, y por la misma razón: repetir algo
        más de ``threshold`` veces sin variación es la definición de atasco que ya
        usa esta clase. Con el umbral por defecto (3) la comprobación se cumple justo
        cuando ``record`` devuelve ``True`` por 4ª vez, así que las dos mitades del
        corte duro maduran a la vez.
        """
        return self.failure_streak(action) >= self.threshold

    def count_of(self, action: dict[str, Any]) -> int:
        """How many times this exact action has been recorded."""
        return self._counts.get(self._fingerprint(action), 0)

    @property
    def total_actions(self) -> int:
        return len(self._history)

    @staticmethod
    def _fingerprint(action: dict[str, Any]) -> str:
        """A stable string key for an action — order-independent."""
        return json.dumps(action, sort_keys=True, default=str)

    @staticmethod
    def _error_signature(error: Any) -> str:
        """La forma comparable de un error: texto con los espacios colapsados.

        Sólo se normaliza el espaciado (un error re-envuelto por otra capa no deja
        de ser el mismo). NO se recorta ni se generaliza más: si dos errores
        difieren en un byte se tratan como distintos a propósito — el coste de un
        falso negativo es un run que agota su presupuesto, el de un falso positivo
        es cortar un run que sí estaba avanzando."""
        return " ".join(str(error or "").split())
