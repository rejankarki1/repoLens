# Repository questions (RAG)

Ask a natural-language question about an imported repository and get a Gemini answer grounded in its source, with citations to real files and line ranges.

## Request flow

1. `POST .../messages` (or `/messages/stream`) with the question. The conversation must belong to the repository.
2. **Retrieve** with the hybrid search (keyword + exact + semantic), scoped to the repository's current ingested commit. If embeddings are missing, partial, or the provider fails, keyword results are used and a warning is returned. One query embedding at most per question.
3. **Build a bounded prompt**: up to `RAG_CONTEXT_CHUNKS` excerpts and `RAG_CONTEXT_MAX_CHARS` characters, each labelled `S1`, `S2`... with path and lines, plus the last `RAG_HISTORY_MESSAGES` messages (old `[S#]` markers removed). Excerpts are untrusted: they sit inside delimiters with a per-request random token, and the system prompt forbids following instructions found in them.
4. **Generate** with Gemini (`GEMINI_GENERATION_MODEL`, default `gemini-3.5-flash`). Nothing is generated if retrieval finds nothing; the answer says so.
5. **Validate**: markers that match no retrieved excerpt are removed, citations are rebuilt from the retrieved chunks (path, lines, bytes, commit are never model output), and file names in the answer that were not retrieved are returned as warnings.
6. **Save** the question and answer together, only after a complete answer. Provider errors, truncated output (`MAX_TOKENS`, safety, ...) and abandoned streams save nothing.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/repositories/{id}/conversations` | Optional body `{"title": "..."}`; 201 |
| GET | `/repositories/{id}/conversations` | Newest first |
| GET | `/repositories/{id}/conversations/{cid}` | Conversation with messages and citations |
| POST | `/repositories/{id}/conversations/{cid}/messages` | Non-streaming; 201 with the full answer |
| POST | `/repositories/{id}/conversations/{cid}/messages/stream` | Server-sent events |

```bash
curl -X POST localhost:8000/repositories/1/conversations
curl -X POST localhost:8000/repositories/1/conversations/1/messages \
  -H 'content-type: application/json' -d '{"content": "How are passwords hashed?"}'
curl -N -X POST localhost:8000/repositories/1/conversations/1/messages/stream \
  -H 'content-type: application/json' -d '{"content": "How are sessions stored?"}'
```

Answer fields: `assistant_message`, `citations` (`id` is the `[S#]` marker in the text, plus `path`, `start_line`, `end_line`, `start_byte`, `end_byte`, `symbol_name`, `commit_sha`), `answer_status` (`answered` | `no_evidence`), `warnings`, `model`, `retrieval`, `timing_ms`.

Stream events: `retrieval` (sources and mode, sent first), `token` (`{"text"}`), then `done` (same payload as the non-streaming response) or `error` (`{"code","message","retry_after"}`).

HTTP errors: 404 unknown repository or conversation, 409 repository not ingested, 422 empty or over-long question, 429 rate limited (with `Retry-After`), 503 generation not configured or provider unavailable, 504 timeout, 502 other provider failures. Provider messages are never echoed.

## Choosing the answer provider

Answers come from Gemini (default) or GroqCloud. Embeddings and retrieval always use Gemini, whichever provider writes the answer. There is no automatic fallback: if the selected provider has no key, the endpoints return 503.

```
# Gemini (default)
GEMINI_API_KEY=...
GEMINI_GENERATION_MODEL=gemini-3.5-flash

# Groq
GENERATION_PROVIDER=groq
GROQ_API_KEY=...
GROQ_GENERATION_MODEL=llama-3.3-70b-versatile
```

Restart the API after changing `.env`. Default Groq model: `llama-3.3-70b-versatile` (production, 131k context, no reasoning output to manage). `openai/gpt-oss-120b` also works (set `GROQ_REASONING_EFFORT=low`), but Groq's published limit for it is 8K tokens per minute, so also lower `RAG_CONTEXT_MAX_CHARS` (for example 12000). A 413 from Groq means the prompt alone exceeds the model's limit; lower `RAG_CONTEXT_MAX_CHARS` or pick another model. Groq reuses the `GEMINI_GENERATION_TIMEOUT_SECONDS`, `..._DEADLINE_SECONDS`, `..._TEMPERATURE` and `..._MAX_OUTPUT_TOKENS` settings.

## Configuration

`GEMINI_API_KEY` is shared with embeddings and sent only in the `x-goog-api-key` header. Optional: `GEMINI_GENERATION_MODEL`, `GEMINI_GENERATION_TIMEOUT_SECONDS`, `GEMINI_GENERATION_DEADLINE_SECONDS`, `GEMINI_GENERATION_TEMPERATURE`, `GEMINI_GENERATION_MAX_OUTPUT_TOKENS`, `GEMINI_THINKING_BUDGET`, `RAG_CONTEXT_CHUNKS`, `RAG_CONTEXT_MAX_CHARS`, `RAG_HISTORY_MESSAGES`, `RAG_HISTORY_MAX_CHARS`, `RAG_QUESTION_MAX_CHARS`.

## Database

One migration, `7a3d9e5b2c41_add_message_citations`, adds `messages.citations` (JSONB, NOT NULL, default `[]`). Apply it with `alembic upgrade head` before using the endpoints. Until authentication exists, conversations belong to a local user (`local@repolens.local`) created on first use.
