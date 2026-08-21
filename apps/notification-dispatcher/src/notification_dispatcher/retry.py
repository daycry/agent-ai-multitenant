"""Exponential-backoff math for the dispatcher's automatic retries (task_10_13).

Split out from :mod:`notification_dispatcher.tasks` so the backoff schedule is a
pure, deterministic-when-asked function — unit-testable without Celery, the
broker, or a DB. The Celery ``send_notification`` task calls
:func:`compute_backoff` to pick the ``countdown`` for ``self.retry(...)``; the
tunables (``base``, ``max_backoff``, ``jitter``, the retry ceiling) all live on
:class:`~notification_dispatcher.config.Settings` so there is never a magic
number in the task body.

Schedule (full-jitter, AWS-style):

    raw    = base * 2 ** (retries)          # retries is 0-based (0 = first retry)
    capped = min(raw, max_backoff)          # never grows unbounded
    delay  = U[capped * (1 - jitter), capped]   # decorrelate a fleet of retries

``retries`` is the number of retries ALREADY made (Celery's
``self.request.retries``): the first retry passes ``0`` and waits ~``base``, the
second passes ``1`` and waits ~``2*base``, and so on, each clamped to
``max_backoff``. A ``jitter`` of 0 makes the delay deterministic (handy in
tests); the default subtracts up to ``jitter`` of the capped delay.
"""

from __future__ import annotations

import random

__all__ = ["compute_backoff"]

# Module-level RNG instance so the typed ``random.Random`` interface is used
# (the bare ``random`` module is an opaque object to mypy) and so jitter draws
# share one stream rather than re-seeding.
_default_rng = random.Random()


def compute_backoff(
    retries: int,
    *,
    base_backoff_s: float,
    max_backoff_s: float,
    jitter: float,
    rng: random.Random | None = None,
) -> float:
    """Return the backoff (seconds) for the retry after ``retries`` prior tries.

    Args:
        retries: number of retries already performed (0-based). The first
            retry passes ``0``.
        base_backoff_s: base delay; the raw delay is ``base * 2**retries``.
        max_backoff_s: hard clamp on the (pre-jitter) delay so it never grows
            unbounded.
        jitter: full-jitter fraction in ``[0, 1]``; the returned delay is
            sampled uniformly from ``[capped * (1 - jitter), capped]``. ``0``
            makes the result deterministic (the capped delay).
        rng: injectable RNG for deterministic tests; defaults to the module
            ``random`` source.

    The result is non-negative and monotonically non-decreasing in expectation
    until it saturates at ``max_backoff_s``.
    """
    retries = max(retries, 0)
    # base * 2**retries, guarding against an absurd exponent blowing up math.
    # Clamp the exponent so 2**retries can't overflow before the min() clamp.
    safe_exponent = min(retries, 32)
    raw: float = base_backoff_s * float(2**safe_exponent)
    capped: float = min(raw, max_backoff_s)

    if jitter <= 0:
        return capped
    jitter = min(jitter, 1.0)
    source: random.Random = rng if rng is not None else _default_rng
    low = capped * (1.0 - jitter)
    return source.uniform(low, capped)
