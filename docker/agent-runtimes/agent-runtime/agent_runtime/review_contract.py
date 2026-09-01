"""Single source of the reviewer's verdict wire-format (hallazgo H3, 2026-07-07).

The ``<verdict>`` tag is the WIRE CONTRACT between a REVIEW run and the worker
(``api_server.reviewer_bridge.parse_reviewer_output`` regex-scans the run's final
prose for it). Its format used to be spelled out literally in FIVE prompt sites
(the worker-threaded preamble, the reviewer system prompt and the three review
nudges) — a wording drift in any of them silently degrades verdict parsing into
defensive rejects. Every prompt now interpolates these constants instead of
re-typing the tag; the cross-package test (`tests/unit/test_review_verdict_wire_
contract.py`) pins that what these constants advertise is exactly what the
worker parses.
"""

from __future__ import annotations

# Import de MÓDULO, no de atributo del paquete. `from shared_domain import
# reject_taxonomy` funciona en runtime —la maquinaria de imports resuelve el
# submódulo— pero mypy lo rechaza: el `__init__.py` no lo importa, así que a
# ojos del type-checker el paquete no tiene ese atributo. Era el único error
# del gate en todo el árbol. El resto del repo usa la forma
# `from shared_domain.reject_taxonomy import X`; aquí se importa el módulo
# entero porque este fichero RE-EXPORTA cinco nombres y con la otra forma el
# bloque de abajo quedaría en autoasignaciones.
import shared_domain.reject_taxonomy as _rt

# The canonical tag tokens the worker's `_VERDICT_RE` reads.
VERDICT_APPROVE = "<verdict>approve</verdict>"
VERDICT_REJECT = "<verdict>reject</verdict>"

# The shared closing sentence of every review NUDGE (graph.py) — the actionable
# "stop and deliver the verdict" push. One wording, one place.
REVIEW_FINISH_SUMMARY = (
    f"reply with your final summary ending in exactly one {VERDICT_APPROVE} or {VERDICT_REJECT} tag"
)


# ---------------------------------------------------------------------------
# Veredicto POR CRITERIO (`task_wf_61`)
# ---------------------------------------------------------------------------
# Hasta ahora el reviewer emitía prosa con UN `<failed_criterion>`: el humano no
# sabía qué criterios pasaron, el `what_to_fix` no tenía diana cuando fallaban
# dos, y no había nada medible entre runs (el sistema de evals se quedaba
# ciego). El bloque de abajo es ADITIVO — el `<verdict>` sigue siendo la fuente
# autoritativa, así que un reviewer que no lo emita se comporta exactamente como
# hoy.
#
# Formato de LÍNEA, no de tags anidados, a propósito: el modelo lo produce sin
# equivocarse (una línea por criterio), un humano lo lee tal cual en la UI, y el
# marcador `[pass]`/`[fail]` resiste la deriva de redacción que ya obligó a
# parsear el `<verdict>` con tolerancia.
CRITERIA_OPEN = "<criteria>"
CRITERIA_CLOSE = "</criteria>"

CRITERIA_INSTRUCTION = (
    f"Before the verdict tag, emit ONE line per acceptance criterion inside "
    f"{CRITERIA_OPEN}…{CRITERIA_CLOSE}, in the order they were given:\n"
    "  - [pass] <the criterion, verbatim> — evidence: <what proves it>\n"
    "  - [fail] <the criterion, verbatim> — evidence: <what proves it fails>\n"
    "Judge every criterion, including the ones that pass: a criterion you do not "
    "mention reads as 'not checked'."
)


# ---------------------------------------------------------------------------
# Taxonomía del rechazo: `target` x `class` (`task_gov_10`)
# ---------------------------------------------------------------------------
# El rechazo se registraba SOLO como prosa, así que no agregaba: nadie podía
# responder «¿qué se rechaza más en este proyecto?». Estos dos tags añaden un par
# acotado al veredicto — ADITIVO, como el bloque de criteria: un reviewer que no
# los emita se comporta exactamente como hoy, y el `<verdict>` sigue mandando.
#
# El vocabulario NO se escribe aquí: se DERIVA de `shared_domain.reject_taxonomy`,
# la única declaración, que es también la que parsea la api-server
# (`reviewer_bridge`). Por eso el prompt no puede anunciar un valor que el parser
# rechace, que es la forma en que este contrato se rompería en silencio (pasó con
# las 13 categorías de aprobación, hallazgo g6).
REJECT_TARGET_OPEN = _rt.REJECT_TARGET_OPEN
REJECT_TARGET_CLOSE = _rt.REJECT_TARGET_CLOSE
REJECT_CLASS_OPEN = _rt.REJECT_CLASS_OPEN
REJECT_CLASS_CLOSE = _rt.REJECT_CLASS_CLOSE

#: Se resuelve al importar (una vez) y se expone como constante para que los
#: prompts la interpolen igual que interpolan `CRITERIA_INSTRUCTION`.
REJECT_TAXONOMY_INSTRUCTION = _rt.reject_taxonomy_instruction()
