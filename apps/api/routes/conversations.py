"""Repository-scoped conversations with grounded (RAG) answers."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.core.database import get_db, session_factory
from apps.api.models.entities import Conversation, Message, Repository
from apps.api.schemas.conversation import (
    AnswerResponse,
    ConversationCreate,
    ConversationDetailResponse,
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
    try:
        return await rag.get_conversation(db, repository_id, conversation_id)
    except rag.ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    if await db.get(Repository, repository_id) is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")
    conversation = Conversation(
        user_id=await rag.default_user_id(db), repository_id=repository_id, title=body.title if body else None
    )
    db.add(conversation)
    await db.commit()
    return conversation


@router.get("/repositories/{repository_id}/conversations", response_model=list[ConversationResponse])
async def list_conversations(repository_id: int, db: AsyncSession = Depends(get_db)) -> list[Conversation]:
    if await db.get(Repository, repository_id) is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")
    rows = await db.execute(
        select(Conversation).where(Conversation.repository_id == repository_id).order_by(Conversation.id.desc())
    )
    return list(rows.scalars())


@router.get("/repositories/{repository_id}/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(repository_id: int, conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await _conversation(db, repository_id, conversation_id)
    messages = (
        await db.execute(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id))
    ).scalars()
    return ConversationDetailResponse(
        id=conversation.id, repository_id=conversation.repository_id, title=conversation.title,
        created_at=conversation.created_at, messages=[MessageResponse(**rag.message_payload(m)) for m in messages],
    )


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
