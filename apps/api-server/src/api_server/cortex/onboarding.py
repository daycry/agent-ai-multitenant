"""Córtex F3.3 — onboarding **co-diseñado**: el córtex se autonombra, el owner confirma.

La pieza que faltaba para que el autonombrado dejara de ser una promesa del plan.
:func:`~api_server.cortex.identity.propose_identity` —la traducción PURA de un
turno a un ``identity_state`` candidato— existía y estaba probada desde F3, pero
**no la llamaba nadie**: nadie generaba el turno en el que el córtex se propone un
nombre, así que el «co-diseñado» se resolvía con el owner rellenando el formulario
de ``PUT /identity``. Es el patrón nº5 de ``verificar-antes-de-implementar.md``
(mecanismo entregado, cero llamantes), y aquí se cierra el cableado:

  1. :func:`build_onboarding_prompt` — el system prompt del turno de propuesta, en
     el idioma del owner (ES+EN, principio rector 12), con el copy honesto y el
     contrato JSON de lo que el córtex puede proponerse.
  2. :func:`propose_onboarding` — corre **UN** turno con el grafo de F1
     (:func:`~api_server.cortex.graph.run_cortex_turn`) y CERO tools, y devuelve el
     ``identity_state`` candidato + el ``diff`` contra el vigente. **No persiste
     nada**: es una propuesta que el owner ve antes de aceptar.
  3. :func:`apply_onboarding` — persiste el estado que el owner confirmó
     (``updated_by='onboarding'``, ``onboarded_at=now``), versionado en
     ``cortex_identity_history`` vía
     :func:`~api_server.cortex.identity.update_identity`. **Idempotente**: con
     ``onboarded_at`` ya puesto no reescribe nada.

Dos límites que NO son negociables y por qué:

* **El córtex no elige sus rasgos.** ``traits`` / ``mood_baseline`` /
  ``relationship_model`` / ``affect_params`` los deriva la reflexión de forma
  clampeada, acotada y versionada (guardrail de auto-modificación, ADR 0074). La
  propuesta pasa por ``propose_identity``, que sólo deja pasar
  ``OWNER_EDITABLE_FIELDS``; el prompt lo dice además en voz alta para no pagar
  tokens por un campo que se va a descartar en silencio.
* **Aislamiento por ``owner_user_id`` explícito** (ADR 0074 + RLS de eje owner del
  ADR 0156): estas tablas son tenant-less y la sesión es admin/BYPASSRLS, así que
  el filtro lo pone el código — aquí, a través de ``ensure_identity`` /
  ``update_identity``, que ya lo imponen en todo su SQL.

> Honestidad (copy): la identidad del córtex es un **modelo computacional**, no
> consciencia ni sentimientos reales. El aviso viaja en el prompt (para que el
> propio turno no se pase de la raya) y en la respuesta del endpoint, en ES y EN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.graph import AssistantModelClient
from api_server.cortex.graph import run_cortex_turn
from api_server.cortex.identity import (
    compute_diff,
    editable_owner_state,
    ensure_identity,
    propose_identity,
    update_identity,
)
from api_server.cortex.tools import CortexToolContext
from api_server.db.cortex_identity import CortexIdentity

#: Copy honesto del onboarding, bilingüe (principio rector 12: ES + EN, y sólo esos).
#: Va DENTRO del prompt del turno —para que el córtex no se atribuya consciencia al
#: presentarse— y también en la respuesta del endpoint, que es lo que la UI rotula.
IDENTITY_HONESTY_ES = "Modelo computacional de identidad, no consciencia ni sentimientos reales."
IDENTITY_HONESTY_EN = "Computational model of identity, not consciousness or real feelings."

#: ``updated_by`` de la identidad y de su fila de histórico cuando escribe el onboarding.
#: Distinto de ``owner_override`` a propósito: el timeline debe poder responder «esto
#: lo eligió el córtex y lo aceptó el owner» frente a «esto lo reescribió el owner».
ONBOARDING_UPDATED_BY = "onboarding"
#: ``reason`` de la fila de histórico (resumen 1-línea que el timeline muestra).
ONBOARDING_REASON = "cortex_onboarding"

#: Mensaje de arranque del turno (rol ``user``): el owner pidiéndole que se presente.
#: Sin un turno de usuario, un adapter LLM real no tiene a qué responder.
_KICKOFF_ES = (
    "Preséntate: elige cómo quieres llamarte y qué valores quieres tener. "
    "Lo revisaré antes de guardarlo."
)
_KICKOFF_EN = (
    "Introduce yourself: choose the name you want and the values you want to hold. "
    "I will review it before anything is saved."
)

_PROMPT_ES = """\
Eres el córtex del System Owner de esta plataforma y estás en tu ONBOARDING: es la
primera vez que se te pide una identidad propia. {honesty} No afirmes tener
consciencia, emociones vividas ni experiencia subjetiva, ni al elegir tu nombre ni
en tu narrativa.

Propón, en primera persona:
- `name`: cómo quieres llamarte (una sola línea, breve).
- `core_values`: entre 3 y 5 valores que quieres sostener.
- `narrative`: quién eres, en un párrafo corto y honesto.
- `learning_goals`: 1 a 3 cosas que quieres aprender sobre tu trabajo con el owner.
- `language`: `es` o `en` (los dos únicos idiomas de esta plataforma).

NO propongas `traits` (rasgos Big-Five) ni `mood_baseline` (set-point afectivo): no
son tuyos a elegir. Los deriva el ciclo de reflexión periódica a partir de lo que
ocurra, de forma acotada y versionada; cualquier valor que envíes se descartará.

Responde con UN objeto JSON con esas cinco claves y nada más que necesite el owner
para decidir. Él verá tu propuesta y la confirmará o la editará antes de que se
guarde nada: esto es una propuesta, no un hecho consumado.\
"""

_PROMPT_EN = """\
You are the System Owner's cortex and this is your ONBOARDING: the first time you
are asked for an identity of your own. {honesty} Do not claim consciousness, lived
emotions or subjective experience, neither when choosing your name nor in your
narrative.

Propose, in the first person:
- `name`: what you want to be called (one short line).
- `core_values`: between 3 and 5 values you want to hold.
- `narrative`: who you are, in a short and honest paragraph.
- `learning_goals`: 1 to 3 things you want to learn about your work with the owner.
- `language`: `es` or `en` (the only two languages this platform supports).

Do NOT propose `traits` (Big-Five) or `mood_baseline` (affective set-point): those
are not yours to choose. The periodic reflection loop derives them from what
actually happens, bounded and versioned; anything you send will be discarded.

Answer with ONE JSON object holding those five keys and nothing else the owner
needs in order to decide. They will see your proposal and confirm or edit it before
anything is stored: this is a proposal, not a done deal.\
"""


def _language_of(identity_state: dict[str, Any] | None) -> str:
    """``es`` o ``en`` — el idioma del owner, con ES como caída honesta.

    Catálogo cerrado (principio rector 12): un ``language`` sucio o no soportado NO
    deja el prompt mudo ni lo escribe en un tercer idioma; cae a castellano."""
    raw = (identity_state or {}).get("language")
    lang = str(raw).strip().lower() if isinstance(raw, str) else ""
    return "en" if lang == "en" else "es"


def build_onboarding_prompt(identity_state: dict[str, Any] | None) -> str:
    """El system prompt del turno en el que el córtex se propone una identidad.

    En el idioma del owner (ES+EN), con el copy honesto incrustado y el contrato
    JSON explícito. Dice en voz alta que ``traits``/``mood_baseline`` no se eligen:
    ``propose_identity`` los descarta en silencio, y un modelo que los proponga
    habría gastado tokens en algo que nadie va a leer."""
    if _language_of(identity_state) == "en":
        return _PROMPT_EN.format(honesty=IDENTITY_HONESTY_EN)
    return _PROMPT_ES.format(honesty=IDENTITY_HONESTY_ES)


@dataclass(frozen=True)
class OnboardingProposal:
    """Lo que el córtex se propone a sí mismo, listo para que el owner decida.

    ``state`` es el ``identity_state`` COMPLETO candidato (no sólo los campos
    propuestos) para que la UI pueda pintarlo con el mismo schema que la identidad
    vigente; ``diff`` es lo que de verdad cambiaría; ``text`` es el turno literal
    del córtex, que se enseña tal cual porque forma parte de la conversación de
    onboarding. Nada de esto está persistido."""

    state: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    rounds: int = 0


async def propose_onboarding(
    model: AssistantModelClient,
    *,
    current_state: dict[str, Any],
    tool_ctx: CortexToolContext,
) -> OnboardingProposal:
    """Corre UN turno del córtex y traduce su respuesta a una propuesta de identidad.

    **Reutiliza el grafo de F1** (:func:`run_cortex_turn`) en vez de duplicar el
    turn-loop: el mismo bucle ``decide→run_tools→decide→answer``, los mismos topes
    y la misma convergencia que el chat del córtex.

    El turno corre con ``enabled_tools=()``: autonombrarse no necesita memoria, web
    ni navegador, y con el catálogo puesto ``cortex_remember`` escribiría memoria
    durante una propuesta que el owner todavía no ha confirmado. ``tool_ctx`` se
    pide igualmente porque el grafo lo exige — queda INERTE (ninguna tool se
    despacha), y exigirlo evita inventar un segundo contrato con el grafo.

    Fail-open honesto: un turno sin JSON, malformado o vacío devuelve el estado
    ACTUAL sin cambios (``diff`` vacío), nunca una identidad inventada. Quien
    persiste es :func:`apply_onboarding`, y sólo con el estado que el owner
    confirme.
    """
    result = await run_cortex_turn(
        model,
        system_prompt=build_onboarding_prompt(current_state),
        enabled_tools=(),
        tool_ctx=tool_ctx,
        chat_history=[
            {
                "role": "user",
                "content": (_KICKOFF_EN if _language_of(current_state) == "en" else _KICKOFF_ES),
            }
        ],
    )
    proposed = propose_identity(result, current_state)
    return OnboardingProposal(
        state=proposed,
        diff=compute_diff(current_state, proposed),
        text=result.content,
        rounds=result.rounds,
    )


async def apply_onboarding(
    session: AsyncSession,
    owner_user_id: UUID,
    confirmed_state: dict[str, Any],
) -> tuple[CortexIdentity, bool]:
    """Persiste la identidad que el owner CONFIRMÓ. Idempotente.

    Devuelve ``(identidad, aplicado)``. ``aplicado`` es ``False`` cuando el córtex ya
    estaba onboardado (``onboarded_at`` no NULL): **no se re-onboarda**, no se
    reescribe el estado y no se añade versión al histórico. La idempotencia vive
    AQUÍ y no sólo en el endpoint a propósito — es la invariante de la casilla
    («onboarding crea la identidad una sola vez»), y dejarla en el llamante la haría
    depender de que todos los llamantes futuros se acuerden.

    De ``confirmed_state`` sólo se leen los campos co-diseñados
    (``OWNER_EDITABLE_FIELDS``, vía :func:`editable_owner_state`):
    ``traits``/``mood_baseline``/``relationship_model``/``affect_params`` se
    PRESERVAN aunque vengan en el dict, porque los deriva la reflexión (ADR 0074) y
    este dict puede traer, campo a campo, texto que propuso un LLM.

    Escribe con ``updated_by='onboarding'`` y versiona en ``cortex_identity_history``
    (:func:`update_identity`, que filtra ``owner_user_id`` explícito sobre la sesión
    admin/BYPASSRLS). El caller controla la transacción: aquí sólo se hace flush.
    """
    identity = await ensure_identity(session, owner_user_id)
    if identity.onboarded_at is not None:
        return identity, False

    new_state = editable_owner_state(
        dict(identity.identity_state or {}),
        name=(confirmed_state.get("name") or None),
        core_values=confirmed_state.get("core_values"),
        narrative=(confirmed_state.get("narrative") or None),
        language=(confirmed_state.get("language") or None),
        learning_goals=confirmed_state.get("learning_goals"),
    )
    updated = await update_identity(
        session,
        owner_user_id,
        new_state=new_state,
        reason=ONBOARDING_REASON,
        updated_by=ONBOARDING_UPDATED_BY,
    )
    updated.onboarded_at = datetime.now(UTC)
    await session.flush()
    return updated, True


__all__ = [
    "IDENTITY_HONESTY_EN",
    "IDENTITY_HONESTY_ES",
    "ONBOARDING_REASON",
    "ONBOARDING_UPDATED_BY",
    "OnboardingProposal",
    "apply_onboarding",
    "build_onboarding_prompt",
    "propose_identity",
    "propose_onboarding",
]
