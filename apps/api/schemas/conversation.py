"""Conversation and RAG answer API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)  # the effective limit is `rag_question_max_chars`


class CitationResponse(BaseModel):
    id: str  # the [S#] marker used in the answer text
    chunk_id: int
    source_file_id: int
    path: str
    language: str | None = None
    kind: str
    symbol_name: str | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    commit_sha: str


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    citations: list[CitationResponse]
    created_at: datetime | None


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ConversationResponse(BaseModel):
    id: int
    repository_id: int | None
    title: str | None
    created_at: datetime
    updated_at: datetime | None = None  # time of the latest message; set by the list endpoint
    message_count: int = 0


class ClearedResponse(BaseModel):
    deleted: int


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse]


class RetrievalInfo(BaseModel):
    commit_sha: str | None
    mode: str
    sources_used: int


class AnswerResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
    citations: list[CitationResponse]
    answer_status: str  # answered | no_evidence
    warnings: list[str]
    model: str | None
    retrieval: RetrievalInfo
    timing_ms: dict[str, Any]
