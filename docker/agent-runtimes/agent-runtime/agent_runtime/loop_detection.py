"""Repetitive-loop detection (task_02_14).

A model that has lost the plot tends to retry the same action forever.
`LoopDetector` fingerprints each action (tool + args) and flags the
execution once one fingerprint has been seen **more than** `threshold`
times — with the default threshold of 3, the 4th identical action
aborts the run with `SafeguardCode.REPETITIVE_LOOP`.
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
        """
        self._counts.clear()

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
