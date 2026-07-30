"""Guidance nudges + the sterility backstop of the agent loop (refactor P5).

Pure text generators (and their calibrated thresholds) that steer a run out of
degenerate patterns BEFORE the hard safeguards trip: byte-identical repetition,
single-file re-write churn, exact-repeat research, per-target re-reads and
sterile read-churn. `_research_exhausted` is the HARD backstop the loop checks
once a run has something worth preserving.

`agent_runtime.graph` re-exports everything here (its historical home).
"""

from __future__ import annotations

from agent_runtime.review_contract import REVIEW_FINISH_SUMMARY
from agent_runtime.tool_classification import (
    _base_tool_name,
    _is_mutating_tool,
    _is_research_tool,
)

# ADR 0089-D4 + plan guardas-research-por-novedad: after this many STERILE
# research calls in a row (no NEW target gathered — re-reads, untargetable calls
# and ERRORED reads), trip a HARD backstop. The soft nudge fires at 3 but a model
# can ignore it and churn to max_iterations. Floor of the budget-relative limit
# (see `_sterile_hard_limit`). La CANTIDAD de research ya no corta nada: explorar
# N ficheros NUEVOS es legítimo y lo acota el presupuesto de iteraciones.
_RESEARCH_HARD_LIMIT = 10
# Per-target read counters (plan guardas-research A1): a la N.ª lectura del MISMO
# target, nudge específico nombrando el fichero; a la M.ª, el backstop duro. Caza
# el patrón INTERCALADO (A,A,B,A,A,C…) que la racha consecutiva no ve.
_SAME_TARGET_NUDGE_LIMIT = 3
_SAME_TARGET_HARD_LIMIT = 5
# After this many CONSECUTIVE sterile research calls (re-reads of already-seen
# targets / errored reads), nudge the agent to stop re-reading and produce/finish
# (fires before the hard backstop).
_REREAD_CHURN_NUDGE_LIMIT = 3
# ADR 0089: after this many writes to the SAME path (ANY content), nudge the agent to
# stop re-writing and FINISH — a 'churn' the byte-exact detector/nudge cannot see.
_PATH_CHURN_THRESHOLD = 4


def _sterile_hard_limit(max_iterations: int) -> int:
    """Límite duro de esterilidad RELATIVO al presupuesto (25 %, suelo fijo).

    Con 50 iteraciones (claude_sdk implementador) → 12; con 25 (review/HTTP) →
    el suelo de 10. Evita la trampa del umbral estático calibrado una vez
    (lección del budget de 100k tokens, auditoría 2026-07-02)."""
    return max(_RESEARCH_HARD_LIMIT, max_iterations // 4)


def _repetition_nudge(
    *, tool: str | None, repeat_count: int, threshold: int, has_produced: bool
) -> str | None:
    """Warn the agent one turn BEFORE the loop detector aborts a repeated action.

    The detector aborts on the ``(threshold + 1)``-th IDENTICAL action (same tool +
    same args → same bytes). This fires earlier, once ``repeat_count >= threshold``,
    so the agent gets a chance to break the rut itself. Returns ``None`` until the
    action has repeated enough to warn. The wording branches by tool CLASS:

      * a MUTATING tool (``write_file``/``edit_file``/…) — the bytes are already
        saved; repeating writes nothing new, so the fix is to apply the review
        feedback or FINISH via ``submit_result``, not to re-write;
      * a READ-ONLY / verification tool — the result is already in hand; reuse it
        instead of re-running the identical query.
    """
    if tool is None or repeat_count < threshold:
        return None
    name = _base_tool_name(tool) or "that tool"
    if _is_mutating_tool(tool):
        finish_hint = (
            "apply the REVIEW FEEDBACK or FINISH now by calling submit_result"
            if has_produced
            else "apply the REVIEW FEEDBACK or move on to a DIFFERENT step"
        )
        return (
            f"You have written the SAME bytes with '{name}' {repeat_count} times — it "
            f"is already saved and repeating it changes nothing. Stop repeating: {finish_hint}."
        )
    return (
        f"You have already run '{name}' with these exact arguments {repeat_count} times — "
        "use the result you already have instead of repeating it."
    )


def _path_churn_nudge(*, path: str | None, write_count: int, threshold: int) -> str | None:
    """Advisory nudge when the agent keeps re-writing the SAME file without finishing.

    A model can churn one file with slightly DIFFERENT content each turn — never
    byte-identical, so the loop detector (content-aware fingerprint) and the
    identical-args repetition nudge both miss it, yet it burns the iteration budget
    without converging (observed: a migration re-written 17 times before MAX_ITERATIONS).
    Fires once a path has been written ``threshold`` times, pushing the agent to FINISH
    (and let the review / a human judge) or fix the SPECIFIC cross-file problem instead
    of rewriting the whole file again. Advisory only — it never aborts; the iteration /
    wall-clock budgets remain the hard ceiling.
    """
    if not path or write_count < threshold:
        return None
    return (
        f"You have re-written '{path}' {write_count} times without finishing. STOP "
        "re-writing it: either FINISH now by calling submit_result (the review / a human "
        "will judge it), or fix the SPECIFIC cross-file inconsistency the review pointed "
        "out — do NOT rewrite the whole file again."
    )


def _research_nudge(
    *,
    tool: str | None,
    repeat_count: int,
    has_produced: bool = False,
    is_review: bool = False,
) -> str | None:
    """Guidance when a research tool is repeated with the SAME args.

    Plan guardas-research-por-novedad A2: el trigger por CANTIDAD de research
    (streak ciego de 5) se retiró — explorar N ficheros NUEVOS es legítimo y no
    debe empujar a `write_file` prematuro. Queda solo la repetición exacta; la
    esterilidad la cubre `_reread_churn_nudge` y el per-target
    `_same_target_nudge`. Returns ``None`` when no nudge applies.
    """
    if not (_is_research_tool(tool) and repeat_count > 1):
        return None
    if is_review:
        # ADR 0095: a reviewer is FORBIDDEN to write_file; push it to conclude with
        # its verdict (claude_sdk FINISH = a no-tool-call prose turn) — never to
        # "produce the deliverable".
        return (
            "You have enough context to judge this task. STOP researching and FINISH "
            f"your review now: {REVIEW_FINISH_SUMMARY} — do NOT call "
            "more tools and do NOT write files."
        )
    if has_produced:
        # C0 (ADR 0087): provider-neutral wording — do NOT prescribe "no tool call".
        # FINISH on the HTTP providers IS a `submit_result` tool call; on claude_sdk
        # it is a prose summary. Either way: report the final result and stop.
        return (
            "You have ALREADY produced the deliverable. Stop verifying/re-reading and "
            "FINISH now: report the final result and stop working."
        )
    return (
        f"You already ran '{tool}' with these exact arguments {repeat_count} times. "
        "Do not repeat it — use the result you already have and move forward."
    )


def _same_target_nudge(
    *, target: str | None, count: int, has_produced: bool = False, is_review: bool = False
) -> str | None:
    """Nudge específico cuando el MISMO target se ha leído demasiadas veces
    (plan guardas-research A1) — caza el patrón intercalado (A,A,B,A,A) que la
    racha consecutiva no ve, y nombra el fichero exacto (mensaje accionable)."""
    if target is None or count < _SAME_TARGET_NUDGE_LIMIT:
        return None
    name = target.split(":", 1)[-1]
    if is_review:
        return (
            f"You have already read '{name}' {count} times — you have its content. "
            f"FINISH your review now: {REVIEW_FINISH_SUMMARY}."
        )
    if has_produced:
        return (
            f"You have already read '{name}' {count} times and ALREADY produced the "
            "deliverable. Stop re-reading it and FINISH now: report the final result."
        )
    return (
        f"You have already read '{name}' {count} times — you already have its content "
        "(see PROGRESS). Use what you learned; do not read it again."
    )


def _reread_churn_nudge(
    *, churn_streak: int, limit: int, has_produced: bool, is_review: bool = False
) -> str | None:
    """Sharp nudge when the agent RE-reads the SAME targets in a row (read-churn).

    Preferred over the generic ``research_streak`` nudge so a research-heavy run gets
    an ACTIONABLE next move (produce, finish, or — for an analysis-only task — report
    a conclusion) instead of a mis-aimed "write a file". Returns ``None`` below the
    limit."""
    if churn_streak < limit:
        return None
    if is_review:
        return (
            f"You have re-read the same files {churn_streak} times in a row. You have "
            f"enough to judge — FINISH your review now: {REVIEW_FINISH_SUMMARY}. "
            "Do NOT keep reading."
        )
    if has_produced:
        return (
            f"You have re-read the same files {churn_streak} times in a row and ALREADY "
            "produced the deliverable. Stop re-reading and FINISH now: report the result."
        )
    return (
        f"You have re-read the same files {churn_streak} times in a row without producing "
        "anything. Produce the task's deliverable now; OR, if this task is analysis-only, "
        "FINISH by reporting your findings/conclusion. Do not keep re-reading."
    )


def _research_exhausted(
    *,
    sterile_streak: int,
    max_same_target_reads: int,
    has_produced: bool,
    review_retries: int,
    sterile_limit: int,
    same_target_limit: int,
    is_review: bool = False,
) -> bool:
    """The HARD backstop, keyed on STERILITY — never on research volume
    (plan guardas-research-por-novedad A3).

    Eligible only when the run has something worth preserving (produced a deliverable,
    OR a prior self-review failed, OR it is a review). A sterile analysis-only run
    that legitimately only reads is NOT cut here — its termination stays bounded by
    ``max_iterations``/``wall_clock`` (D3 invariant). When eligible, it trips on ANY of:

      1. ``sterile_streak >= sterile_limit`` — N research calls seguidas SIN target
         nuevo (re-reads, calls sin target, lecturas con error); el límite es
         relativo al presupuesto (:func:`_sterile_hard_limit`).
      2. ``max_same_target_reads >= same_target_limit`` — CUALQUIER target leído
         demasiadas veces, aunque sea intercalado con lecturas nuevas (el patrón
         A,A,B,A,A que la racha consecutiva no ve).

    El techo de lecturas DISTINTAS (22) se retiró: la amplitud es exploración
    legítima y ya la acota el presupuesto de iteraciones; cortarla castigaba a
    reviewers y verificaciones amplias (falso positivo del 2026-07-03). El
    carve-out antiguo de review por research_streak bruto cae por lo mismo.
    """
    if not (has_produced or review_retries > 0 or is_review):
        return False
    return sterile_streak >= sterile_limit or max_same_target_reads >= same_target_limit
