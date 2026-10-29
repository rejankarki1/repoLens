"""Foundation tables for repository and conversation metadata."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.types import UserDefinedType
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    github_accounts: Mapped[list["GitHubAccount"]] = relationship(back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class GitHubAccount(Base):
    __tablename__ = "github_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(255))
    user: Mapped[User] = relationship(back_populates="github_accounts")
    repositories: Mapped[list["Repository"]] = relationship(back_populates="github_account")


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        CheckConstraint(
            "ingestion_status IN ('pending', 'queued', 'in_progress', 'completed', 'failed')",
            name="valid_ingestion_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    github_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_accounts.id", ondelete="SET NULL"), nullable=True
    )
    full_name: Mapped[str] = mapped_column(String(512))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_ingested_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ingestion_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    ingestion_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    ingestion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingestion_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_account: Mapped[GitHubAccount | None] = relationship(back_populates="repositories")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="repository")
    source_files: Mapped[list["SourceFile"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="conversations")
    repository: Mapped[Repository | None] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('system', 'user', 'assistant')", name="valid_message_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", "path", name="uq_source_files_repository_commit_path"),
        CheckConstraint("category IN ('source', 'test', 'docs', 'config', 'other')", name="valid_source_file_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    commit_sha: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    repository: Mapped[Repository] = relationship(back_populates="source_files")
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="source_file",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CodeChunk.chunk_index",
    )


class CodeChunk(Base):
    """One syntax-aware (or line-based fallback) chunk of a stored source file.

    The repository and commit are reached through `source_file`, so they are not repeated here. Chunks
    live and die with their file: replacing a repository's snapshot deletes its `source_files` rows,
    and the foreign key cascades to the chunks.
    """

    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint("source_file_id", "chunk_index", name="uq_code_chunks_source_file_chunk_index"),
        CheckConstraint("parser_mode IN ('ast', 'fallback')", name="valid_code_chunk_parser_mode"),
        CheckConstraint("chunk_index >= 0", name="valid_code_chunk_index"),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="valid_code_chunk_lines"),
        CheckConstraint("start_byte >= 0 AND end_byte > start_byte", name="valid_code_chunk_bytes"),
        Index("ix_code_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)  # 0-based position within the file
    language: Mapped[str] = mapped_column(String(64))  # parser language ("tsx" for .tsx), or the label of a text fallback
    kind: Mapped[str] = mapped_column(String(32))  # class, function, method, interface, imports, code, text, ...
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)  # primary symbol
    symbols: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )  # every symbol that starts in the chunk, in source order
    parser_mode: Mapped[str] = mapped_column(String(10))  # 'ast' or 'fallback'
    start_line: Mapped[int] = mapped_column(Integer)  # 1-based, inclusive
    end_line: Mapped[int] = mapped_column(Integer)  # 1-based, inclusive
    start_byte: Mapped[int] = mapped_column(Integer)  # UTF-8 offset into source_files.content, inclusive
    end_byte: Mapped[int] = mapped_column(Integer)  # exclusive
    starts_mid_line: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    ends_mid_line: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex of `content` (UTF-8), like source_files
    # Weighted full-text document: path and symbol names (A) over the code itself (C). Built in SQL at
    # insert time from normalized text (services/code_search.py); NULL only for rows written elsewhere.
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_file: Mapped[SourceFile] = relationship(back_populates="chunks")


EMBEDDING_DIMENSIONS = 1_536  # gemini-embedding-001 at 1,536 dimensions; a change needs a new migration and a re-embed


class Vector(UserDefinedType):
    """pgvector `vector(N)`, passed to asyncpg as text so no extra Python package is needed."""

    cache_ok = True
    render_bind_cast = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw: object) -> str:
        return f"VECTOR({self.dimensions})"

    def bind_processor(self, dialect: object):  # type: ignore[no-untyped-def]
        def process(value: list[float] | None) -> str | None:
            return None if value is None else "[" + ",".join(repr(float(x)) for x in value) + "]"

        return process


class CodeEmbedding(Base):
    """The embedding of one chunk text under one model, keyed by the text's hash.

    Content-addressed on purpose: re-ingesting an unchanged chunk (same commit or a new one, in any
    repository) reuses its vector instead of paying for it again, and a vector can never be attached
    to different text. Searches reach it only through `code_chunks.content_hash`, so they are always
    limited to a repository's current chunks, and rows for different models never mix (`model` is part
    of the key and of every lookup).
    """

    __tablename__ = "code_embeddings"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # = code_chunks.content_hash
    model: Mapped[str] = mapped_column(String(64), primary_key=True)  # "<model>/<dimensions>"
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

