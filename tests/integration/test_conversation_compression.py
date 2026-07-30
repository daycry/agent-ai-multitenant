"""Integration tests for hierarchical conversation compression
(Plan 03 task_03_04).

Drives `compress_old_messages` and `load_context_window` against a real
PostgreSQL with the conversation/messages tables in place. The
summariser is scripted so the test stays deterministic and offline.

What we check:

  - With fewer messages than the threshold nothing gets compressed.
  - At/above the threshold a system summary row is persisted with
    `is_summary=True` and `attachments` listing the replaced ids.
  - `load_context_window` returns the summary in place of the chain
    of messages it replaces (so the LLM sees one message, not ten).
  - Hierarchical: running compression a second time can fold previous
    summaries into a higher-level summary, the recursive case that
    keeps the chat in bounds no matter how long it runs.
  - Tenant scoping is preserved end-to-end (the summary row inherits
    the conversation's tenant_id).
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

# Load the full domain so SQLAlchemy can resolve Conversation.project_id
# -> projects.id and Message.* foreign keys when these tests instantiate
# the ORM classes outside an Alembic context.
from api_server.db import domain  # noqa: F401
from api_server.db.conversation import Conversation, Message, MessageAuthorKind
from api_server.db.conversation_compression import (
    SUMMARY_REPLACES_KIND,
    ScriptedSummariser,
    compress_old_messages,
    load_context_window,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _seed_tenant_project(dsn: str) -> tuple[UUID, UUID]:
    tenant_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE messages, conversations, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant Compression",
            "tenant-compression",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@compression.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Compression Project",
        )
    finally:
        await conn.close()

    return tenant_id, project_id


# ---------------------------------------------------------------------------
# Async helpers built on top of a migrations_user engine (BYPASSRLS so the
# tests can seed and assert across the table without setting app.tenant_id).
# ---------------------------------------------------------------------------
def _engine(admin_database_url: str):
    return create_async_engine(admin_database_url, echo=False)


async def _create_conversation(session_factory, tenant_id: UUID, project_id: UUID) -> UUID:
    async with session_factory() as session:
        # BYPASSRLS users still need a transaction to honour FORCE RLS;
        # the migrations_user role bypasses it altogether.
        conv = Conversation(
            tenant_id=tenant_id,
            project_id=project_id,
            current_mode="planning",
        )
        session.add(conv)
        await session.flush()
        await session.commit()
        return conv.id


async def _add_user_messages(
    session_factory,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    count: int,
    user_id: UUID | None = None,
    starting_idx: int = 1,
) -> None:
    """Insert `count` consecutive ``system``-authored messages so we don't
    need to seed a real user row for each. The test cares about counts
    and identity, not who sent each line."""
    async with session_factory() as session:
        for i in range(count):
            msg = Message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                author_kind=MessageAuthorKind.SYSTEM.value,
                content=f"message {starting_idx + i}",
                mode="planning",
                attachments=[],
            )
            session.add(msg)
        await session.flush()
        await session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def schema_ready(alembic_config) -> None:
    """Bring the test database up to head before each test."""
    command.upgrade(alembic_config, "head")


# ===========================================================================
# Tests
# ===========================================================================
def test_below_threshold_compression_is_a_noop(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """5 messages, threshold 10 -> nothing happens, summariser never called."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run() -> Message | None:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            await _add_user_messages(session_factory, tenant_id, conv_id, count=5)
            summariser = ScriptedSummariser(summaries=["should-not-be-called"])
            async with session_factory() as session:
                return await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=5,
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) is None


def test_compression_creates_summary_replacing_window(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """20 msgs, threshold 10, window 10 -> 1 summary, replaces 10 oldest."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run() -> Message | None:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            await _add_user_messages(session_factory, tenant_id, conv_id, count=20)
            summariser = ScriptedSummariser(summaries=["Summary of msgs 1..10"])
            async with session_factory() as session:
                summary = await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=10,
                )
                await session.commit()
            return summary
        finally:
            await engine.dispose()

    summary = asyncio.run(_run())
    assert summary is not None
    assert summary.is_summary is True
    assert summary.content == "Summary of msgs 1..10"
    assert summary.tenant_id == tenant_id
    assert summary.author_kind == MessageAuthorKind.SYSTEM.value
    attachments = summary.attachments
    assert len(attachments) == 1
    assert attachments[0]["kind"] == SUMMARY_REPLACES_KIND
    assert len(attachments[0]["message_ids"]) == 10


def test_context_window_returns_summary_in_place_of_old_messages(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """After compression, load_context_window returns 1 summary + the
    remaining unsummarised tail — not the original 20 messages."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run() -> list[Message]:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            await _add_user_messages(session_factory, tenant_id, conv_id, count=20)
            summariser = ScriptedSummariser(summaries=["S1"])
            async with session_factory() as session:
                await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=10,
                )
                await session.commit()
            async with session_factory() as session:
                return await load_context_window(session, conv_id, max_messages=50)
        finally:
            await engine.dispose()

    ctx = asyncio.run(_run())
    # 20 originals - 10 summarised + 1 summary row = 11 messages.
    assert len(ctx) == 11
    # Exactly one of them is the summary.
    summaries = [m for m in ctx if m.is_summary]
    assert len(summaries) == 1
    assert summaries[0].content == "S1"


def test_hierarchical_compression_folds_earlier_summary(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """When older summaries themselves accumulate, a fresh compression
    pass folds them — proof the procedure is hierarchical."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run() -> tuple[Message, Message, Message, list[Message]]:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            # Step 1: 20 messages -> first summary S1 over msgs 1..10.
            await _add_user_messages(session_factory, tenant_id, conv_id, count=20)
            summariser = ScriptedSummariser(summaries=["S1", "S2", "S3-hier"])
            async with session_factory() as session:
                s1 = await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=10,
                )
                await session.commit()

            # Step 2: 20 more messages -> uncovered =
            #   [m11..m20, S1, m21..m40]. Compress -> S2 over m11..m20.
            await _add_user_messages(
                session_factory,
                tenant_id,
                conv_id,
                count=20,
                starting_idx=21,
            )
            async with session_factory() as session:
                s2 = await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=10,
                )
                await session.commit()

            # Step 3: now uncovered = [S1, S2, m21..m40] (22 items).
            # Window=10 -> hierarchical S3 over [S1, S2, m21..m28].
            async with session_factory() as session:
                s3 = await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=10,
                )
                await session.commit()

            async with session_factory() as session:
                ctx = await load_context_window(session, conv_id, max_messages=50)
            assert s1 is not None and s2 is not None and s3 is not None
            return s1, s2, s3, ctx
        finally:
            await engine.dispose()

    s1, s2, s3, ctx = asyncio.run(_run())

    # Chronological order: m1..m20, S1, m21..m40, S2, S3.
    # Pass 3 sees uncovered = [S1, m21..m40, S2] (22 items); the oldest
    # 10 are [S1, m21..m29], so S3 folds the prior summary S1 plus 9 raw
    # messages. The "hierarchical" claim is that a summary now covers
    # another summary, which is exactly what we assert here. S2 is too
    # recent to land in the oldest-10 window and stays uncompressed.
    replaces_ids = {UUID(str(x)) for x in s3.attachments[0]["message_ids"]}
    assert s1.id in replaces_ids, "S3 must fold the older S1 (hierarchical)"
    assert s2.id not in replaces_ids, "S2 is the newest summary — not yet covered"
    assert len(replaces_ids) == 10

    # Context view: S3 stands in for S1+m21..m29, then m30..m40 remain
    # raw, and S2 + S3 are uncovered summaries -> 11 + 2 = 13 entries.
    assert len(ctx) == 13
    summaries_in_ctx = [m for m in ctx if m.is_summary]
    assert {m.id for m in summaries_in_ctx} == {s2.id, s3.id}


# ===========================================================================
# task_wf_06 b/c — el pliegue híbrido y la alineación a turnos
# ===========================================================================
_REQUISITO = "debe funcionar sin conexión, en un portátil sin red"
_DESCARTADO = "nada de Electron: el cliente no quiere 300 MB"


class _ExtractingSummariser:
    """Un `StructuredSummariser` que imita a un LLM realista.

    Extrae el registro de las marcas que trae el texto — pero **solo de los
    mensajes que le llegan**, como haría un modelo — y devuelve una prosa
    deliberadamente degradada. Esa degradación es el punto: si el requisito
    sobrevive tres pisos, es porque lo lleva el registro estructurado, no la
    prosa.
    """

    def __init__(self) -> None:
        self.windows: list[list[str]] = []

    async def summarise_window(self, messages):  # type: ignore[no-untyped-def]
        from api_server.db.conversation_compression import SummaryRecord, WindowSummary

        self.windows.append([m.content for m in messages])
        requisitos: list[str] = []
        descartado: list[str] = []
        for message in messages:
            for line in (message.content or "").splitlines():
                if line.startswith("REQUISITO: "):
                    requisitos.append(line.removeprefix("REQUISITO: "))
                elif line.startswith("DESCARTADO: "):
                    descartado.append(line.removeprefix("DESCARTADO: "))
        return WindowSummary(
            content="El equipo habló de varias cosas y acordó seguir adelante.",
            record=SummaryRecord(requisitos=tuple(requisitos), descartado=tuple(descartado)),
            cause="ok",
        )


async def _seed_author(dsn: str) -> UUID:
    """A real ``users`` row: the CHECK ``ck_messages_author_kind_consistency``
    requires ``author_user_id`` on every ``user``-authored message."""
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            f"turnos-{user_id}@compression.test",
            "argon2-placeholder",
        )
    finally:
        await conn.close()
    return user_id


async def _add_turn(
    session_factory,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    user_id: UUID,
    user_text: str,
    replies: int = 5,
) -> None:
    """One chat turn: the user's message plus the team's `replies` messages.

    The team messages are ``system``-authored so the test needs no agent rows;
    what matters for turn alignment is only that they are NOT ``user``.
    """
    async with session_factory() as session:
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                author_kind=MessageAuthorKind.USER.value,
                author_user_id=user_id,
                content=user_text,
                mode="planning",
                attachments=[],
            )
        )
        for i in range(replies):
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    author_kind=MessageAuthorKind.SYSTEM.value,
                    content=f"respuesta {i} a «{user_text[:20]}»",
                    mode="planning",
                    attachments=[],
                )
            )
        await session.flush()
        await session.commit()


def test_a_requirement_from_message_one_survives_three_floors_literally(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """**El test que importa** (task_wf_06 b).

    Un requisito y un descarte enunciados en el primer mensaje deben llegar
    LITERALES al tercer piso de compresión. Con compresión jerárquica de prosa no
    llegarían: cada piso vuelve a pasar por un modelo un texto que ya era resumen,
    y a los tres pisos «debe funcionar sin conexión» se ha convertido en «se
    habló de varias cosas». El pliegue híbrido lo evita porque el registro
    estructurado se fusiona de forma determinista, sin modelo de por medio.
    """
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run():  # type: ignore[no-untyped-def]
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            author_id = await _seed_author(migrations_pg_dsn)
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            summariser = _ExtractingSummariser()
            floors = []
            turn = 0

            # Un resumen se inserta al FINAL del feed (su id v7 es el más nuevo),
            # así que solo entra en la ventana más antigua cuando llegan turnos
            # posteriores. Por eso los pisos se construyen intercalando
            # conversación y compresión, que es como ocurre en producción.
            for floor in range(3):
                for _ in range(2):
                    turn += 1
                    text = f"turno {turn}: sigamos"
                    if turn == 1:
                        text = (
                            f"Quiero un lector de PDFs.\nREQUISITO: {_REQUISITO}\n"
                            f"DESCARTADO: {_DESCARTADO}"
                        )
                    await _add_turn(
                        session_factory,
                        tenant_id,
                        conv_id,
                        user_id=author_id,
                        user_text=text,
                    )
                async with session_factory() as session:
                    summary = await compress_old_messages(
                        session,
                        conv_id,
                        summariser,
                        threshold_messages=10,
                        window_messages=15,
                        align_to_turns=True,
                    )
                    await session.commit()
                assert summary is not None, f"piso {floor + 1} no comprimió"
                floors.append(summary)

            async with session_factory() as session:
                ctx = await load_context_window(session, conv_id, max_messages=50)
            return floors, ctx, summariser

        finally:
            await engine.dispose()

    floors, ctx, summariser = asyncio.run(_run())
    from api_server.db.conversation_compression import record_of

    # Que sean tres PISOS y no tres resúmenes hermanos: cada uno pliega al
    # anterior. Sin esto el test pasaría igual si la jerarquía dejase de formarse.
    for lower, upper in pairwise(floors):
        covered = {UUID(str(x)) for x in upper.attachments[0]["message_ids"]}
        assert lower.id in covered, "el piso superior no plegó al inferior"

    top = floors[-1]
    record = record_of(top)
    assert record is not None
    # Literal, no parafraseado.
    assert _REQUISITO in record.requisitos
    assert _DESCARTADO in record.descartado
    # Y visible en `content`: `history_from_messages` solo pasa el content al
    # prompt, así que un registro que viviese solo en attachments no lo vería
    # el modelo y la garantía sería falsa.
    assert _REQUISITO in top.content
    assert _DESCARTADO in top.content

    # La prueba de que fue el pliegue determinista y no el «modelo»: a partir del
    # segundo piso, el mensaje original ya no entra en la ventana cruda.
    assert any(_REQUISITO in text for text in summariser.windows[0])
    for window in summariser.windows[1:]:
        assert all(_REQUISITO not in text for text in window)

    # El contexto que leería el equipo sigue conteniendo el requisito.
    assert any(_REQUISITO in m.content for m in ctx)


def test_no_summary_ever_covers_half_a_turn(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """La ventana se recorta a la última frontera de turno (task_wf_06 c).

    Con `window_messages=10` y turnos de 6 mensajes, una ventana ciega plegaría el
    turno 1 entero y cuatro mensajes del turno 2: el resumen contaría media
    discusión — el framing del PM y cuatro especialistas sin la síntesis — como si
    fuera la conclusión del equipo.
    """
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run():  # type: ignore[no-untyped-def]
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            author_id = await _seed_author(migrations_pg_dsn)
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            for i in range(1, 6):
                await _add_turn(
                    session_factory,
                    tenant_id,
                    conv_id,
                    user_id=author_id,
                    user_text=f"turno {i}",
                )
            async with session_factory() as session:
                summary = await compress_old_messages(
                    session,
                    conv_id,
                    _ExtractingSummariser(),
                    threshold_messages=10,
                    window_messages=10,
                    align_to_turns=True,
                )
                await session.commit()
            assert summary is not None
            covered = {UUID(str(x)) for x in summary.attachments[0]["message_ids"]}
            async with session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(Message)
                            .where(Message.conversation_id == conv_id)
                            .order_by(Message.id)
                        )
                    )
                    .scalars()
                    .all()
                )
            return covered, list(rows)
        finally:
            await engine.dispose()

    covered, rows = asyncio.run(_run())
    # Un turno son 6 mensajes: se pliega exactamente uno, no 10.
    assert len(covered) == 6
    # Y el primer mensaje NO cubierto es el arranque de un turno (`user`), no un
    # trozo suelto de la conversación anterior.
    first_uncovered = next(m for m in rows if m.id not in covered and not m.is_summary)
    assert first_uncovered.author_kind == MessageAuthorKind.USER.value


def test_a_failed_summariser_leaves_the_conversation_uncompressed(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """Sin resumen no se comprime: perder diez mensajes de la vista de contexto a
    cambio de nada sería peor que no comprimir."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    class _BrokenSummariser:
        async def summarise_window(self, messages):  # type: ignore[no-untyped-def]
            from api_server.db.conversation_compression import WindowSummary

            return WindowSummary(content="", cause="llm_error")

    async def _run():  # type: ignore[no-untyped-def]
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            await _add_user_messages(session_factory, tenant_id, conv_id, count=20)
            async with session_factory() as session:
                summary = await compress_old_messages(
                    session, conv_id, _BrokenSummariser(), threshold_messages=10
                )
                await session.commit()
            async with session_factory() as session:
                return summary, await load_context_window(session, conv_id, max_messages=50)
        finally:
            await engine.dispose()

    summary, ctx = asyncio.run(_run())
    assert summary is None
    assert len(ctx) == 20  # nada se perdió


def test_the_token_ceiling_trims_the_context_window(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """Segunda guarda (task_wf_06 e): un contador no ve que 50 filas larguísimas
    desbordan igual que 500 cortas."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run():  # type: ignore[no-untyped-def]
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            async with session_factory() as session:
                for i in range(10):
                    session.add(
                        Message(
                            tenant_id=tenant_id,
                            conversation_id=conv_id,
                            author_kind=MessageAuthorKind.SYSTEM.value,
                            content=f"{i} " + "x" * 4000,  # ~1000 tokens cada uno
                            mode="planning",
                            attachments=[],
                        )
                    )
                await session.commit()
            async with session_factory() as session:
                capped = await load_context_window(
                    session, conv_id, max_messages=50, max_tokens=2500
                )
                uncapped = await load_context_window(session, conv_id, max_messages=50)
            return capped, uncapped
        finally:
            await engine.dispose()

    capped, uncapped = asyncio.run(_run())
    assert len(uncapped) == 10
    assert len(capped) == 2  # ~1000 tokens por fila
    # Se conserva la COLA: lo último dicho es lo que el equipo tiene que contestar.
    assert capped[-1].content.startswith("9 ")


def test_summary_row_persisted_under_conversation_tenant(
    schema_ready, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """The synthesised summary inherits the conversation's tenant_id —
    a leak here would bypass RLS for everyone reading the chat."""
    tenant_id, project_id = asyncio.run(_seed_tenant_project(migrations_pg_dsn))

    async def _run() -> UUID:
        engine = _engine(admin_database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            conv_id = await _create_conversation(session_factory, tenant_id, project_id)
            await _add_user_messages(session_factory, tenant_id, conv_id, count=12)
            summariser = ScriptedSummariser(summaries=["S"])
            async with session_factory() as session:
                summary = await compress_old_messages(
                    session,
                    conv_id,
                    summariser,
                    threshold_messages=10,
                    window_messages=10,
                )
                await session.commit()
                assert summary is not None
                summary_id = summary.id

            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT tenant_id FROM messages WHERE id = :id").bindparams(id=summary_id)
                )
                return UUID(str(result.scalar_one()))
        finally:
            await engine.dispose()

    actual = asyncio.run(_run())
    assert actual == tenant_id
