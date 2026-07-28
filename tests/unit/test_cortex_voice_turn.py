"""Córtex F5 — adaptador de turno de voz: frame afectivo + lectura del afecto.

Las dos costuras testeables del WS de voz del córtex (``routers/cortex_voice.py``):

  * :func:`affect_frame` — builder PURO ``AffectState -> {type:'affect', valence,
    arousal, dominance, mood_label, drives}`` para el avatar del front.
  * :func:`load_current_affect` — lee el afecto vigente (caché Redis con decay
    lazy → BD), **fail-open** a baseline neutro si todo falla (Redis vacío, BD
    sin snapshot). El reloj entra como ``now`` (determinismo).

El pipeline completo del turno (STT→cerebro→persistencia→TTS) se ejercita en el
test de integración del WS con DB real; aquí sólo los seams puros/casi-puros.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel, ToolInvocation
from api_server.cortex.affective import AffectState, Drives, PADState, neutral_affect_state
from api_server.cortex.self_context import SelfContext
from api_server.cortex.voice_turn import affect_frame, load_current_affect, run_cortex_voice_turn

pytestmark = pytest.mark.unit


def _state(*, valence: float, arousal: float, dominance: float) -> AffectState:
    return AffectState(
        emotion=PADState(valence=valence, arousal=arousal, dominance=dominance, intensity=0.4),
        mood=PADState(valence=valence, arousal=arousal, dominance=dominance, intensity=0.0),
        drives=Drives(curiosity=0.7, bonding=0.6, coherence=0.5, competence=0.4),
    )


# ---------------------------------------------------------------------------
# affect_frame — builder puro
# ---------------------------------------------------------------------------
def test_affect_frame_has_pad_mood_and_drives() -> None:
    frame = affect_frame(_state(valence=0.5, arousal=0.6, dominance=0.2))
    assert frame["type"] == "affect"
    assert frame["valence"] == pytest.approx(0.5)
    assert frame["arousal"] == pytest.approx(0.6)
    assert frame["dominance"] == pytest.approx(0.2)
    assert isinstance(frame["mood_label"], str) and frame["mood_label"]
    assert set(frame["drives"]) == {"curiosity", "bonding", "coherence", "competence"}
    assert frame["drives"]["curiosity"] == pytest.approx(0.7)


def test_affect_frame_mood_label_is_bilingual() -> None:
    happy = _state(valence=0.5, arousal=0.6, dominance=0.0)
    assert affect_frame(happy, language="es")["mood_label"] == "alegría"
    assert affect_frame(happy, language="en")["mood_label"] == "joy"


def test_affect_frame_neutral_baseline() -> None:
    frame = affect_frame(neutral_affect_state())
    assert frame["type"] == "affect"
    assert frame["mood_label"] == "neutral"


# ---------------------------------------------------------------------------
# load_current_affect — Redis → BD, fail-open a neutro
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Redis-like con sólo ``get``: devuelve lo que se le inyecte (o None)."""

    def __init__(self, value: object = None) -> None:
        self._value = value

    async def get(self, _key: str) -> object:
        return self._value


@pytest.mark.asyncio
async def test_load_current_affect_reads_redis_first() -> None:
    """Con clave Redis viva, NO toca la BD (sessionmaker que lanzaría si se usa)."""
    import json

    from api_server.cortex.affect_cache import affect_cache_key  # noqa: F401

    now = datetime.now(UTC)
    payload = json.dumps(
        {
            "updated_at": now.isoformat(),
            "emotion": {"valence": 0.4, "arousal": 0.7, "dominance": 0.1, "intensity": 0.5},
            "mood": {"valence": 0.4, "arousal": 0.7, "dominance": 0.1},
            "drives": {"curiosity": 0.8, "bonding": 0.5, "coherence": 0.5, "competence": 0.5},
        }
    )

    def _boom_sessionmaker() -> object:  # pragma: no cover - must NOT be called
        raise AssertionError("DB must not be touched when Redis has the state")

    state = await load_current_affect(
        _FakeRedis(payload), _boom_sessionmaker, owner_user_id=uuid4(), now=now
    )
    assert state.emotion.arousal == pytest.approx(0.7)
    assert state.emotion.valence == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_load_current_affect_fail_open_to_neutral() -> None:
    """Redis vacío + BD que falla ⇒ baseline neutro (nunca lanza)."""

    class _BoomSession:
        async def __aenter__(self) -> object:
            raise RuntimeError("db down")

        async def __aexit__(self, *a: object) -> bool:  # pragma: no cover
            return False

    def _failing_sessionmaker() -> _BoomSession:
        return _BoomSession()

    state = await load_current_affect(
        _FakeRedis(None), _failing_sessionmaker, owner_user_id=uuid4(), now=datetime.now(UTC)
    )
    # Baseline neutro (lo que devuelve neutral_affect_state): arousal 0.3, valence 0.
    assert state.emotion.valence == pytest.approx(0.0)
    assert state.emotion.arousal == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# run_cortex_voice_turn — criterio de aceptación B2: «persiste EXACTAMENTE UN
# TURNO por llamada (sin duplicados)».
#
# Ese criterio no tenía test en ninguna parte. El de integración del WS
# (tests/integration/test_cortex_voice_ws.py) hace TRUNCATE de `cortex_turns` y
# jamás vuelve a consultarla: cero SELECT, cero COUNT — si el adaptador
# duplicara filas o no persistiera ninguna, la suite seguiría verde. Aquí se fija
# el contrato con el doble de cerebro scripted que pedía el paso 1 del TDD del
# plan, sustituyendo la capa de persistencia por un libro de asientos.
#
# Lectura del criterio que estos tests DECIDEN (la letra del plan decía «un
# turno» y la implementación escribe dos): una llamada = UN intercambio = la fila
# `user` + la fila `cortex`, en ese orden y ni una más. Es lo que hace el chat de
# F1 (`POST /owner/cortex/turns`), cuyo pipeline reutiliza este adaptador; que
# ambos escriban lo mismo es justamente el invariante que interesa.
# ---------------------------------------------------------------------------
_OWNER = UUID("11111111-1111-1111-1111-111111111111")
_TENANT = UUID("22222222-2222-2222-2222-222222222222")


@dataclass
class _PersistedTurn:
    """Una fila que `append_turn` habría escrito en `cortex_turns`."""

    conversation_id: UUID
    owner_user_id: UUID
    role: str
    content: str
    metadata: dict[str, Any]
    id: UUID = field(default_factory=uuid4)


@dataclass
class _Ledger:
    """Libro de asientos de la persistencia del turno (sin BD)."""

    turns: list[_PersistedTurn] = field(default_factory=list)
    conversations: list[UUID] = field(default_factory=list)

    @property
    def roles(self) -> list[str]:
        return [t.role for t in self.turns]


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> _Ledger:
    """Sustituye la capa de BD del adaptador por un libro de asientos en memoria.

    Se dobla SÓLO lo que toca disco (tenant, hilo, turnos, settings, self-context,
    historial, surfacing). El grafo del córtex (`run_cortex_turn`) corre de verdad
    con un cerebro scripted, que es lo que el TDD del plan pedía ejercitar.
    """
    book = _Ledger()

    async def _resolve_tenant(_session: Any, _owner: UUID) -> UUID:
        return _TENANT

    async def _create_conversation(
        _session: Any, *, owner_user_id: UUID, tenant_id: UUID, model_id: str | None = None
    ) -> SimpleNamespace:
        conv_id = uuid4()
        book.conversations.append(conv_id)
        return SimpleNamespace(id=conv_id, owner_user_id=owner_user_id, tenant_id=tenant_id)

    async def _append_turn(
        _session: Any,
        *,
        conversation_id: UUID,
        owner_user_id: UUID,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        **_kw: Any,
    ) -> _PersistedTurn:
        turn = _PersistedTurn(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            role=role,
            content=content,
            metadata=metadata or {},
        )
        book.turns.append(turn)
        return turn

    async def _web_enabled(_session: Any) -> bool:
        return False

    async def _self_context(
        _session: Any,
        _redis: Any,
        *,
        owner_user_id: UUID,
        tenant_id: UUID,
        query: str,
        now: datetime,
        affect: AffectState | None = None,
        **_kw: Any,
    ) -> SelfContext:
        return SelfContext(
            identity_state={},
            affect=affect or neutral_affect_state(),
            known_facts=[],
            now=now,
        )

    async def _history(
        _session: Any, *, conversation_id: UUID, owner_user_id: UUID, **_kw: Any
    ) -> list[dict[str, str]]:
        return [
            {"role": "assistant" if t.role == "cortex" else t.role, "content": t.content}
            for t in book.turns
            if t.conversation_id == conversation_id
        ]

    async def _mark_surfaced(_session: Any, **_kw: Any) -> int:
        return 0

    monkeypatch.setattr("api_server.cortex.voice_turn.resolve_cortex_tenant_id", _resolve_tenant)
    monkeypatch.setattr("api_server.cortex.voice_turn.create_conversation", _create_conversation)
    monkeypatch.setattr("api_server.cortex.voice_turn.append_turn", _append_turn)
    monkeypatch.setattr("api_server.cortex.voice_turn.get_cortex_web_enabled", _web_enabled)
    monkeypatch.setattr("api_server.cortex.voice_turn.load_self_context", _self_context)
    monkeypatch.setattr("api_server.cortex.voice_turn.recent_history_for_prompt", _history)
    monkeypatch.setattr("api_server.cortex.voice_turn.mark_pursuits_surfaced", _mark_surfaced)
    return book


@pytest.mark.asyncio
async def test_un_turno_de_voz_persiste_el_par_user_cortex_y_nada_mas(ledger: _Ledger) -> None:
    """Una llamada = dos filas: la del owner y la del córtex, en ese orden.

    Atrapa las dos regresiones simétricas que hoy nadie ve: que se dejen de
    persistir turnos (la voz hablaría sin memoria del hilo) o que se persistan de
    más (el historial del siguiente turno se llenaría de duplicados y el owner los
    vería repetidos en el chat, que lee la misma tabla).
    """
    model = ScriptedAssistantModel(turns=[ModelTurn(content="Te escucho, dime.")])

    result, conversation_id, cortex_turn_id = await run_cortex_voice_turn(
        object(),  # type: ignore[arg-type]  # la persistencia está doblada
        model,
        owner_user_id=_OWNER,
        user_text="¿me oyes?",
        conversation_id=None,
    )

    assert result.content == "Te escucho, dime."
    assert ledger.roles == ["user", "cortex"]
    assert [t.content for t in ledger.turns] == ["¿me oyes?", "Te escucho, dime."]
    # Todo el SQL va filtrado por el owner explícito (tablas del córtex sin RLS).
    assert {t.owner_user_id for t in ledger.turns} == {_OWNER}
    # Ambas filas en el hilo devuelto, y el id devuelto es el del turno del córtex
    # (el caller lo usa para disparar el distilador afectivo tras el COMMIT).
    assert {t.conversation_id for t in ledger.turns} == {conversation_id}
    assert cortex_turn_id == ledger.turns[-1].id
    assert ledger.turns[-1].metadata["channel"] == "voice"


@pytest.mark.asyncio
async def test_segundo_turno_reusa_el_hilo_y_no_reescribe_el_primero(ledger: _Ledger) -> None:
    """Dos llamadas ⇒ cuatro filas y UN solo hilo: el conteo escala 1:1.

    Es la otra mitad de «sin duplicados»: un adaptador que reabriera hilo por
    turno, o que re-persistiera el historial que acaba de leer, se delataría aquí
    con más filas o más de una conversación creada.
    """
    first_model = ScriptedAssistantModel(turns=[ModelTurn(content="Hola.")])
    _, conversation_id, _ = await run_cortex_voice_turn(
        object(),  # type: ignore[arg-type]
        first_model,
        owner_user_id=_OWNER,
        user_text="hola",
        conversation_id=None,
    )

    second_model = ScriptedAssistantModel(turns=[ModelTurn(content="Sigo aquí.")])
    _, same_conversation, _ = await run_cortex_voice_turn(
        object(),  # type: ignore[arg-type]
        second_model,
        owner_user_id=_OWNER,
        user_text="¿sigues ahí?",
        conversation_id=conversation_id,
    )

    assert same_conversation == conversation_id
    assert len(ledger.conversations) == 1  # el hilo se crea UNA vez
    assert ledger.roles == ["user", "cortex", "user", "cortex"]
    assert [t.content for t in ledger.turns] == ["hola", "Hola.", "¿sigues ahí?", "Sigo aquí."]


@pytest.mark.asyncio
async def test_un_turno_con_ronda_de_tool_sigue_persistiendo_un_solo_par(
    ledger: _Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El contrato es por LLAMADA, no por vuelta del grafo.

    Un cerebro que llama una tool y responde después da dos vueltas al loop
    `decide→run_tools→decide→answer`. Si la persistencia colgara de la vuelta en
    vez de la llamada, aquí saldrían cuatro filas: el owner vería su pregunta
    duplicada y una respuesta vacía intermedia.
    """

    recalls: list[str] = []

    async def _fake_recall(_session: Any, *, query: str, **_kw: Any) -> list[str]:
        recalls.append(query)
        return ["al owner le interesa la prosodia"]

    monkeypatch.setattr("api_server.cortex.tools.cortex_recall", _fake_recall)

    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(
                tool_calls=(
                    ToolInvocation(name="cortex_recall_more", arguments={"query": "prosodia"}),
                )
            ),
            ModelTurn(content="Sí, lo recuerdo."),
        ]
    )

    result, _, _ = await run_cortex_voice_turn(
        object(),  # type: ignore[arg-type]
        model,
        owner_user_id=_OWNER,
        user_text="¿te acuerdas de lo de la prosodia?",
        conversation_id=None,
    )

    # El grafo corrió la tool DE VERDAD (el loop registra el intento incluso si
    # la tool falla, así que se comprueba la llamada real, no sólo el nombre).
    assert recalls == ["prosodia"]
    assert result.tools_called == ("cortex_recall_more",)
    assert result.content == "Sí, lo recuerdo."
    assert ledger.roles == ["user", "cortex"]
