# Architecture Diagram V2 (generation/validation layer)

AI-identified functional components on top of the deterministic import graph (`docs/architecture.md`). This
document covers **Phase 2** only: context selection, prompt construction, and validation. There is no
database table, RQ job, API route, Mermaid compiler or UI yet — see "Not built yet" below.

## Why a second layer on top of the deterministic graph

The deterministic graph (`architecture_analyses`) answers "what does this file import". It cannot answer
"what does this system do": a `services/` directory full of unrelated helpers is not a "Services" component,
and a module that imports another is not necessarily calling it over HTTP. Architecture Diagram V2 asks an
LLM to propose functional components and relationships in plain language, then **validates every claim
against real data** before anything is trusted.

## Modules

| File | Role |
| --- | --- |
| `apps/api/schemas/architecture_diagram.py` | `Raw*` (what the model may say) vs. sealed (`Component`, `Relationship`, `ArchitectureDiagram`) types. The model is never given a field to assert `confidence` or `evidence_basis` — only the backend can set those. |
| `apps/api/services/architecture_diagram/context.py` | `select_context` (pure, deterministic, bounded) and `load_architecture_context` (the async database glue: reads the existing `architecture_analyses` row and targeted `source_files`/`code_chunks` rows — never re-parses or re-analyzes). |
| `apps/api/services/architecture_diagram/prompt.py` | The system prompt and the one-shot JSON payload. Deliberately independent of `services/rag.py`: no conversation history, no citation model. |
| `apps/api/services/architecture_diagram/validate.py` | `parse_raw_diagram` (strict JSON parse) and `validate_diagram` (seals a `RawArchitectureDiagram` into an `ArchitectureDiagram` by filtering paths against the real snapshot and recomputing every relationship's confidence from the deterministic graph). |
| `apps/api/services/architecture_diagram/generate.py` | Orchestration: `generate_architecture_diagram(loaded, generator=...)`. Takes any `services.generation.Generator` (Gemini or Groq); does not choose a provider itself. |

## The one rule that matters most

`confidence="verified"` is reachable **only** through `kind="imports"`, and only when a real import edge
connects the two components in the stated direction (`schemas.architecture_diagram.IMPORT_VERIFIABLE_KINDS`).
That edge may be a **file-level** one between the components' exact `source_paths` (checked first — the
only way to verify two components whose code shares one module, since a module has no dependency edge to
itself) or, failing that, a **module-level** `depends_on` edge between their containing modules. Both come
from the same stored, Tree-sitter-derived deterministic analysis (`architecture_analyses.detail["edges"]`
for the file level, `Graph.edges` for the module level) — never from the model. A relationship proposed as
`sync_call`, `async_call`, `queue`, `db_read` or `db_write` is **always** `"inferred"`, however strong the
import relationship between the same two files or modules happens to be: a static import proves a
compile-time dependency, never that an HTTP call, a queue hop, or a database read/write happens at runtime.
See `test_architecture_diagram_validate.py::test_non_import_kinds_are_never_verified_even_with_a_real_import_edge_underneath`
and `test_architecture_diagram_evaluation.py` (the file-level case, across four repository archetypes).

Every other claim gets the same treatment: a `source_paths` entry not found in the current snapshot is
dropped (not trusted), and evidence attached to a "verified" relationship is always copied from the
deterministic graph's own evidence, never from the model's text.

## Context selection

`select_context` builds, in priority order, from data that already exists: capped module/edge lists from
the stored `Graph`, detected areas/technologies/external dependencies, entry-point files (`entrypoint`,
`route`, `page`, `edge_function`, `job`, `model`), a root `README` excerpt, a few dependency manifests,
named-symbol excerpts of up to `architecture_diagram_max_excerpts_per_module` representative files per
module (from already-stored `code_chunks`, not a new parse), and `file_references` — real file-to-file
import edges among files already shown elsewhere in the context (from `architecture_analyses.detail`,
Tree-sitter-derived, not a new parse), restricted to both endpoints being already-shown files so nothing
unverifiable is implied. This is the only place a connection between two files inside the *same* module can
reach the model at all, and it is what `validate.py` checks first when deciding whether an `imports`
relationship is verified. Every list is capped by a `Settings.architecture_diagram_*` limit; if the
rendered size still exceeds `architecture_diagram_context_max_chars`, `file_references` is dropped first
(the newest, most experimental section), then excerpts, then manifests, then entry points, each drop
recorded in `warnings`. Given the same inputs, the output is byte-identical.

### Which files become "representative excerpts" (Phase 2.5)

`select_representative_files` picks which files' code the model actually sees. It deliberately does **not**
rank by import degree (fan-in/fan-out): a heavily-imported utility is not architecturally significant just
because many files import it, and a feature file in GitDiagram's ClassLens example (`auth.ts`,
`purchases.ts`, `captureSession.ts`) is often imported by nobody while being the actual architecture. The
ranking is, in order: (1) a real detected role (`entrypoint`/`route`/`page`/`job`/`model`/`edge_function`
beat plain `source`); (2) a generic/boilerplate filename (`utils`, `helpers`, `index`, `config`, ...) is
pushed behind a domain-specific one; (3) line count (already stored at ingestion — a free proxy for "how
much is actually implemented here"); (4) path, for determinism. Selection is breadth-first across modules
(every module gets one file before any module gets a second), up to
`architecture_diagram_max_excerpts_per_module` deep and `architecture_diagram_max_excerpt_files` total. See
`tests/test_architecture_diagram_selection.py` for the head-to-head cases this is tested against.

## Not built yet (blockers before the next phase)

* **No table, job, or route.** `load_architecture_context`/`generate_architecture_diagram` are called
  directly today (see the test suite); nothing persists a diagram or exposes it over HTTP.
* **No Mermaid compiler.** `ArchitectureDiagram` is the provider-independent representation a later phase
  compiles to a flowchart (subgraphs per `Group`, labeled arrows per `Relationship`, click-links to real
  source paths).
* **No JSON-mode/structured-output wiring.** Generation relies on prompt instructions plus robust parsing
  (`parse_raw_diagram` tolerates a markdown fence or surrounding commentary); it does not use a provider's
  native JSON-schema mode. Worth revisiting if malformed output turns out to be common in practice.
* **No quota/usage limits wired to a real endpoint** (the `architecture_diagram_*` settings bound context
  and output size, not request rate; that needs its own hourly limit once there is a route to protect).
* **Auto-trigger question still open** (noted in the Phase 1 report): recommend diagram generation stays an
  explicit user action, never auto-queued on ingestion like the deterministic analysis is.
