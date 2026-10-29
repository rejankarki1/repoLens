"""Shared helpers for the search tests: a realistic corpus, a deterministic fake embedder, snapshot setup."""

import hashlib
import re

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.models.entities import EMBEDDING_DIMENSIONS, Repository
from apps.api.services.archive_reader import ArchiveReadResult, Category
from apps.api.services.archive_reader import SourceFile as ArchiveFile
from apps.api.services.code_indexing import index_entries
from apps.api.services.code_search import split_camel
from apps.api.services.embeddings import DOCUMENT_TASK, QUERY_TASK, EmbeddingProviderError, model_key
from apps.api.services.source_file_persistence import persist_source_snapshot

SHA_A, SHA_B, SHA_OLD = "a" * 40, "b" * 40, "9" * 40

AUTH_PY = '''import hashlib


def hash_password(password: str, salt: str) -> str:
    """Hash a password with a salt using sha256."""
    return hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, salt: str, expected: str) -> bool:
    """Check a password against its stored hash."""
    return hash_password(password, salt) == expected


class SessionStore:
    """Keeps login sessions in memory for signed in users."""

    def create(self, user_id: int) -> str:
        return f"session-{user_id}"
'''

ENV_TS = '''import fs from "fs";

export function loadEnvLocal(file: string): void {
  // Read a .env.local file and copy every variable into process.env
  const text = fs.readFileSync(file, "utf8");
  for (const line of text.split("\\n")) {
    const [key, value] = line.split("=");
    process.env[key] = value;
  }
}

export const API_KEY_NAME = "OPENAI_API_KEY";
'''

BUTTON_TSX = '''import { Pressable, Text } from "react-native";

interface Props {
  label: string;
  onPress: () => void;
}

export function AppButton({ label, onPress }: Props) {
  return (
    <Pressable onPress={onPress}>
      <Text>{label}</Text>
    </Pressable>
  );
}
'''

README = "# Demo\n\nThis project stores login sessions and hashes passwords.\n"

CORPUS = {
    "backend/auth.py": (AUTH_PY, "python"),
    "scripts/env.ts": (ENV_TS, "typescript"),
    "src/components/ui/AppButton.tsx": (BUTTON_TSX, "typescript"),
    "README.md": (README, "markdown"),
}

_WORD = re.compile(r"[a-z0-9]+")


def fake_vector(text: str) -> list[float]:
    """A deterministic bag-of-words vector: texts that share words are close in cosine distance."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for word in _WORD.findall(split_camel(text).lower().replace("_", " ")):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS] += 1.0
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return [x / norm for x in vector]


class FakeEmbedder:
    """Stands in for GeminiEmbedder in tests: no network, counts every text it is asked to embed."""

    def __init__(self, *, fail_on_call: int | None = None, fail_retryable: bool = True, fail_queries: bool = False) -> None:
        self.key = model_key(get_settings())
        self.batches: list[list[str]] = []
        self.tasks: list[str] = []  # the task type of every embed() call, in order
        self.fail_on_call = fail_on_call  # 1-based index of the embed() call that should fail
        self.fail_retryable = fail_retryable
        self.fail_queries = fail_queries
        self.query_calls = 0
        self.query_tasks: list[str] = []

    @property
    def texts_embedded(self) -> int:
        return sum(len(b) for b in self.batches)

    async def embed(
        self, texts: list[str], *, task: str = DOCUMENT_TASK, attempts: int = 3, timeout: float | None = None
    ) -> list[list[float]]:
        if self.fail_on_call is not None and len(self.batches) + 1 == self.fail_on_call:
            raise EmbeddingProviderError("provider down", retryable=self.fail_retryable)
        self.batches.append(list(texts))
        self.tasks.append(task)
        return [fake_vector(t) for t in texts]

    async def embed_query(self, query: str) -> list[float]:
        self.query_calls += 1
        self.query_tasks.append(QUERY_TASK)
        if self.fail_queries:
            raise EmbeddingProviderError("provider down", retryable=True)
        return fake_vector(query)


def snapshot(sha: str, files: dict[str, tuple[str, str]], *, truncated: bool = False) -> ArchiveReadResult:
    entries = tuple(
        ArchiveFile(
            path=path, language=language, category=Category.SOURCE, content=content,
            sha256=hashlib.sha256(content.encode()).hexdigest(), size_bytes=len(content.encode()),
            line_count=content.count("\n"),
        )
        for path, (content, language) in files.items()
    )
    return ArchiveReadResult(sha, entries, (), {}, truncated, None, 0)


_ids = iter(range(10_000_000, 11_000_000))


async def new_repository(db: AsyncSession, name: str = "octo/demo") -> int:
    repo = Repository(github_repo_id=next(_ids), full_name=name, default_branch="main", ingestion_status="completed")
    db.add(repo)
    await db.commit()
    return repo.id


async def ingest(
    db: AsyncSession, repo_id: int, files: dict[str, tuple[str, str]] | None = None, sha: str = SHA_A, chunk_max_bytes: int = 220
) -> ArchiveReadResult:
    """Persist a snapshot (files + chunks + keyword index) exactly as the worker does."""
    result = snapshot(sha, files or CORPUS)
    await persist_source_snapshot(db, repo_id, result, index_entries(result.entries, chunk_max_bytes=chunk_max_bytes).chunks_by_path)
    return result
