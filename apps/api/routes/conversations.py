"""Repository-scoped conversations with grounded (RAG) answers."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.core.database import get_db, session_factory
from apps.api.models.entities import Conversation, Message, Repository
from apps.api.schemas.conversation import (
    AnswerResponse,
    ClearedResponse,
    ConversationCreate,
    ConversationDetailResponse,
    ConversationRename,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
)
from apps.api.services import rag
from apps.api.services.code_search import NotIndexedError, RepositoryNotFoundError
from apps.api.services.embeddings import GeminiEmbedder, get_embedder
from apps.api.services.generation import GenerationError, Generator, get_generator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["conversations"])

_ERROR_STATUS = {"rate_limited": 429, "auth": 503, "unavailable": 503, "timeout": 504}


def get_sessions():
    """The session factory used to save streamed answers (overridable in tests)."""
    return session_factory


def _generation_http_error(exc: GenerationError) -> HTTPException:
    headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 502), detail=exc.message, headers=headers)


def _require_generator(generator: Generator | None) -> Generator:
    if generator is None:
        raise HTTPException(status_code=503, detail="Answer generation is not configured (set GENERATION_PROVIDER and that provider's API key)")
    return generator


def _question(body: MessageCreate) -> str:
    question = body.content.strip()
    if not question:
        raise HTTPException(status_code=422, detail="The question must not be empty")
    limit = get_settings().rag_question_max_chars
    if len(question) > limit:
        raise HTTPException(status_code=422, detail=f"The question is too long (maximum {limit} characters)")
    return question


async def _conversation(db: AsyncSession, repository_id: int, conversation_id: int) -> Conversation:
    """The conversation, if it is this repository's and belongs to the current user.

    Ownership today: every conversation belongs to the one local user (`rag.DEFAULT_USER_EMAIL`), and every route
    below filters by that user as well as by repository. Phase 8 (authentication) replaces `find_default_user_id`
    with the signed-in user's id; nothing else in these routes has to change. See docs/rag.md, "Ownership".
    """
    user_id = await rag.find_default_user_id(db)
    if user_id is None:  # nobody has created a conversation yet
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found in repository {repository_id}")
    try:
        return await rag.get_conversation(db, repository_id, conversation_id, user_id)
    except rag.ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _require_repository(db: AsyncSession, repository_id: int) -> None:
    if await db.get(Repository, repository_id) is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")


async def _prepare(db, conversation, question, embedder) -> rag.PreparedQuestion:
    try:
        return await rag.prepare_question(db, conversation, question, embedder=embedder)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotIndexedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/repositories/{repository_id}/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    repository_id: int, body: ConversationCreate | None = None, db: AsyncSession = Depends(get_db)
) -> Conversation:
    await _require_repository(db, repository_id)
    title = body.title.strip() if body and body.title else None
    conversation = Conversation(user_id=await rag.default_user_id(db), repository_id=repository_id, title=title or None)
    db.add(conversation)
    await db.commit()
    return conversation


@router.get("/repositories/{repository_id}/conversations", response_model=list[ConversationResponse])
async def list_conversations(repository_id: int, db: AsyncSession = Depends(get_db)) -> list[ConversationResponse]:
    """The current user's conversations about this repository, most recently active first.

    Conversations without any message are left out: one is created when the first question is sent, and it stays
    empty if that question fails, which would otherwise pile up as identical "Untitled chat" rows. A conversation
    saved without a title (older data) is titled from its first question here, without being modified.
    """
    await _require_repository(db, repository_id)
    user_id = await rag.find_default_user_id(db)
    if user_id is None:
        return []
    stats = (
        select(
            Message.conversation_id,
            func.count().label("n"),
            func.max(Message.created_at).label("last_at"),
            func.max(Message.id).label("last_id"),  # orders by activity even when timestamps tie
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    first_question = (
        select(Message.content)
        .where(Message.conversation_id == Conversation.id, Message.role == "user")
        .order_by(Message.id)
        .limit(1)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(Conversation, stats.c.n, stats.c.last_at, first_question)
        .join(stats, stats.c.conversation_id == Conversation.id)
        .where(Conversation.repository_id == repository_id, Conversation.user_id == user_id)
        .order_by(stats.c.last_id.desc())
    )
    return [
        ConversationResponse(
            id=c.id, repository_id=c.repository_id, title=c.title or (rag.make_title(first) if first else None),
            created_at=c.created_at, updated_at=last_at, message_count=n,
        )
        for c, n, last_at, first in rows.all()
    ]


@router.delete("/repositories/{repository_id}/conversations", response_model=ClearedResponse)
async def clear_conversations(repository_id: int, db: AsyncSession = Depends(get_db)) -> ClearedResponse:
    """Delete the current user's whole chat history for this repository (its messages go with it)."""
    await _require_repository(db, repository_id)
    user_id = await rag.find_default_user_id(db)
    if user_id is None:
        return ClearedResponse(deleted=0)
    result = await db.execute(
        delete(Conversation).where(Conversation.repository_id == repository_id, Conversation.user_id == user_id)
    )
    await db.commit()
    return ClearedResponse(deleted=result.rowcount or 0)


@router.get("/repositories/{repository_id}/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(repository_id: int, conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await _conversation(db, repository_id, conversation_id)
    messages = list(
        (await db.execute(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id))).scalars()
    )
    first_question = next((m.content for m in messages if m.role == "user"), None)
    return ConversationDetailResponse(
        id=conversation.id, repository_id=conversation.repository_id,
        title=conversation.title or (rag.make_title(first_question) if first_question else None),
        created_at=conversation.created_at, messages=[MessageResponse(**rag.message_payload(m)) for m in messages],
    )


@router.patch("/repositories/{repository_id}/conversations/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    repository_id: int, conversation_id: int, body: ConversationRename, db: AsyncSession = Depends(get_db)
) -> Conversation:
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(status_code=422, detail="The title must not be empty")
    conversation = await _conversation(db, repository_id, conversation_id)
    conversation.title = title
    await db.commit()
    return conversation


@router.delete("/repositories/{repository_id}/conversations/{conversation_id}", status_code=204)
async def delete_conversation(repository_id: int, conversation_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    conversation = await _conversation(db, repository_id, conversation_id)
    await db.execute(delete(Conversation).where(Conversation.id == conversation.id))  # messages cascade in the database
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/repositories/{repository_id}/conversations/{conversation_id}/messages", response_model=AnswerResponse, status_code=201
)
async def send_message(
    repository_id: int,
    conversation_id: int,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    generator: Generator | None = Depends(get_generator),
    embedder: GeminiEmbedder | None = Depends(get_embedder),
) -> AnswerResponse:
    """Ask a question and get the whole grounded answer at once (also the easiest path to test)."""
    question = _question(body)
    generator = _require_generator(generator)
    conversation = await _conversation(db, repository_id, conversation_id)
    prepared = await _prepare(db, conversation, question, embedder)  # 404/409 surface before any model call
    try:
        exchange = await rag.answer_question(
            db, conversation, question, generator=generator, embedder=embedder, prepared=prepared
        )
    except GenerationError as exc:
        logger.warning("Answer generation failed (%s): %s", exc.code, exc.message)
        raise _generation_http_error(exc) from exc
    except IntegrityError as exc:  # the conversation was deleted while the answer was being written
        await db.rollback()
        raise HTTPException(status_code=404, detail="This conversation was deleted before the answer could be saved") from exc
    return AnswerResponse(**rag.exchange_payload(exchange))


@router.post("/repositories/{repository_id}/conversations/{conversation_id}/messages/stream")
async def stream_message(
    repository_id: int,
    conversation_id: int,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    generator: Generator | None = Depends(get_generator),
    embedder: GeminiEmbedder | None = Depends(get_embedder),
    sessions=Depends(get_sessions),
) -> StreamingResponse:
    """Server-sent events: `retrieval` (sources), `token`s, then `done` (saved messages and citations) or `error`."""
    question = _question(body)
    generator = _require_generator(generator)
    conversation = await _conversation(db, repository_id, conversation_id)
    prepared = await _prepare(db, conversation, question, embedder)
    return StreamingResponse(
        rag.stream_events(prepared, generator=generator, sessions=sessions),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
