# Milestone 1: end-to-end verification

What was exercised, how, and what was not. Each behaviour is classified as:

- **Real**: run against real components: PostgreSQL/pgvector, Redis/RQ worker, the real API, and a real Chrome driven with real mouse, wheel, touch-size and keyboard input. "Real AI" means a live Groq or Gemini call.
- **Fixture**: run against a controlled stand-in (a fake provider server, crafted or modified database rows, a synthetic repository). These show the application handles the situation, not that any real provider behaves that way.
- **Not verified**: not run in this milestone.

All of it ran in an isolated copy of the development database, on a private job queue and private ports. No development data was changed.

## Live provider calls

| Provider | Calls | Purpose |
| --- | --- | --- |
| Groq `openai/gpt-oss-120b` (answers) | 6 | On a fully embedded repository: a question, its follow-up, and an off-topic question. On a keyword-only repository: a conceptual question, an exact-name question, and a no-match question. All streamed and completed. |
| Gemini `gemini-embedding-001` (query embeddings) | 5 | 3 hybrid questions on a fully embedded repository, plus 1 hybrid and 1 semantic-only search request. |

Every other provider scenario used a local fake server with dummy keys; the real keys were never sent to it.

## AI and retrieval

| Behaviour | Class | Result |
| --- | --- | --- |
| Hybrid search retrieves relevant code | Real | Matched by both keyword and semantic retrievers and fused; top hits were the quiz usage migration and the quiz handler for a question about free quiz limits. |
| Groq generation, configured model | Real | Answers in 1.5 to 2.6 s. |
| Streaming displays tokens progressively | Real | The visible answer grew in 3 to 9 steps over the response, not at the end. Also confirmed with a slow fake stream (partial text visible while streaming). |
| Citations match retrieved code, files and lines | Real | Every cited range exists in the stored file, its byte range slices to the same text, and the claim tokens (`functions.invoke('generate-quiz'`, `QUIZ_LIMIT_REACHED`, `SignatureExpired`, `max_age`) appear inside the cited lines. |
| Follow-up retrieval uses context | Real | "What if the quota runs out?" mentions no quiz, yet retrieved the README lines containing `QUIZ_LIMIT_REACHED`. |
| Keyword-only fallback (no embeddings) | Real | Exact-name question answered with a correct citation. A conceptual question ("how does it decide a signature has expired") retrieved the wrong chunks, and the model said so instead of inventing a source (see limitations). |
| Embedding provider outage falls back to keyword | Fixture | Fake endpoint returns 503; the answer still arrives with the warning "Semantic search is temporarily unavailable". |
| Partial semantic index | Fixture | Half of one repository's embeddings were removed; status reports `partial` and the UI says "436 of 869 chunks… keyword matching for the rest". |
| Missing semantic index | Real | itsdangerous has none; the banner says so and answers work. |
| No-evidence and off-topic questions invent no citations | Real | Two questions (no matching terms; Kubernetes operator in a mobile app) returned an explicit "not in the excerpts" answer with zero citations. |
| History limit | Fixture + backend tests | Eight questions in one conversation: from the fourth request on, exactly 6 prior messages were sent (`RAG_HISTORY_MESSAGES`), while all 16 messages stayed stored. |

## Conversations, citations and Code Explorer (real browser input)

| Behaviour | Class |
| --- | --- |
| Create, ask, follow up, reload restores, open previous, New chat | Real |
| Rename (saved, survives reload, Escape cancels), delete the active conversation, open a deleted conversation by URL | Real |
| Clear history removes only that repository's conversations (6 removed, the other repository's 4 untouched); another repository's conversation by URL is "not found" | Real |
| Citation click opens the right file with exactly the cited lines highlighted (desktop split, 528 px) | Real |
| Ctrl-click keeps normal navigation (no preview); Escape closes the preview | Real |
| Open in Code Explorer, Back returns to the same conversation, Forward returns to the file | Real |
| Mobile (390 px) citation drawer, Escape closes, no horizontal overflow | Real |
| Citation to a missing file, a range past the end, a range partly past the end, and another commit: each shows a specific note and no wrong highlight | Fixture (crafted citations) |
| Code Explorer tree, Dependencies panel, following an import, evidence line link, Show in architecture, Back | Real |
| Phone file tree dialog | Real |

## Architecture Explorer

| Behaviour | Class |
| --- | --- |
| Graph edges correspond to the code: all 183 evidence records checked in two repositories name an import statement that is on the stated line of the stored source | Real |
| Independent check: itsdangerous file edges recomputed with Python's `ast` agree (no edge missing; 4 extra test-to-test edges are real `from test_itsdangerous.… import` statements) | Real |
| Module graph, drill-down, find, toggles, file selection, imports / imported-by counts equal the API (30 files, 18 internal imports; 4 imports and 7 importers for one file) | Real |
| Wheel zoom, background drag pan, fit view, zoom buttons, edge click, node click, node double-click, keyboard select | Real (real input events) |
| 500-file, 20-module synthetic repository (2,561 file edges, 361 module edges, long names): analysis, render in under a second, long names clipped with tooltip, 390 px and desktop without overflow | Real analyzer, synthetic data |
| Stale analysis is re-requested once per commit; the answer preview warns that its commit differs | Fixture (commit changed in the test database) |

## Errors and recovery

| Behaviour | Class | Result |
| --- | --- | --- |
| Rate limit (429 with Retry-After), server error, rejected key, truncated answer | Fixture | Each shows a specific message, "This answer was not saved", and Try again. Retry after recovery succeeds. |
| Provider connection cut mid-answer | Fixture | Error and nothing saved. |
| Stop generating | Fixture | "Stopped. This answer was not saved." |
| API process killed mid-stream | Real | "The connection was interrupted…"; after restarting the API, Try again works. |
| Browser offline while sending | Real | Now says the API cannot be reached (it said "Something went wrong" before this milestone); Try again works once online. |
| Redis worker not running | Real | Repository shows Queued with "Waiting for a background worker"; the job completed when the worker started. |
| Redis unreachable | Real | `/health/ready` returns 503; Re-analyze says "Could not queue the ingestion; please try again" and the previous snapshot stays browsable. |
| Invalid repository URLs (5 forms), repository that does not exist, duplicate import | Real | Specific messages; the duplicate opens the existing workspace. |
| Real import of a small repository through the worker | Real | Ingested and shown on the overview. |
| Real ingestion failure (a repository far over the archive limit) | Real | "Analysis failed: Repository archive exceeds the maximum download size", with a Re-analyze hint on every tab. |
| Offline Re-analyze | Real | "Could not reach the RepoLens API". |

## Mobile and accessibility

Automated audit of 7 pages at 390, 768, 1024 and 1440 px: no horizontal overflow, no unnamed buttons or links, no unlabelled form fields, no clipped content. Real Tab traversal of the Ask AI page (32 stops): logical order, and no stop lacked an outline or focus ring in a computed-style check. Import form: reachable and submittable by keyboard, error tied to the field with `aria-describedby`. Mobile history sheet and menu: dialog opens, focus moves inside, Escape closes and focus returns to the trigger; all four workspace tabs are visible at 390 px.

Not covered: screen-reader output, Safari and Firefox, physical touch devices (touch was emulated), contrast measurement.

## Defects found and fixed

1. **Graph nodes ignored real mouse clicks and double-clicks.** React Flow gives nodes `pointer-events: none` when they are neither selectable nor draggable, and the card inherited it. Selecting a module, opening its files, and selecting a file worked only from the keyboard. Earlier checks used synthetic events, which bypass this. Fixed by letting the card receive pointer events.
2. **Sending while the API was unreachable showed "Something went wrong while answering."** The real message was thrown while creating the conversation and then discarded. It is now shown.
3. **Phone tap target.** The Code Explorer's icon-only GitHub link was 14 px square and had only a tooltip as its name. It now has a 34 px target and an accessible label.

## Known limitations found

- A dense, fully cyclic dependency graph is laid out as one long chain, which is tiny at "fit to view". Zoom, find and the details panel still work. The real repositories tested have no cycles.
- Keyword-only mode matches identifiers, not concepts. The model answers honestly that the excerpts lack the code.
- When the provider stalls silently, the interface waits for the 60 second read timeout before reporting it. Stop is available throughout.
- A connection error from the provider names the HTTP library's exception ("RemoteProtocolError").
- Switching conversations replaces the history entry, so the browser Back button leaves the Ask AI tab instead of stepping through conversations. Back from the Code Explorer returns to the open conversation.
- The newly imported repository's embedding job was not run end to end here (the small repository imported has no supported code, so it produced no chunks). Embedding generation was verified in earlier milestones.

## Not verified

Real provider rate limiting or outages (only simulated), private repositories, GitHub API rate-limit exhaustion, Safari and Firefox, screen readers, physical touch hardware, anything about authentication or deployment.
