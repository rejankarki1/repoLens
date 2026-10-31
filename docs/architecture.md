# Architecture analysis

Builds an evidence-backed dependency graph of an ingested repository: which modules and files import which, what
kind of code each file appears to be, and which technologies it uses. It is deterministic static analysis of the
stored source files. It calls no AI provider and needs no API key.

Supported languages: Python, JavaScript, TypeScript and TSX. Other files are not graph nodes, but manifests, config and
SQL migrations are read as evidence.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/repositories/{id}/architecture` | Queue an analysis of the current ingested commit. `?force=true` re-analyzes a current result. |
| GET | `/repositories/{id}/architecture/status` | Cheap status: latest attempt, freshness of the stored result. Poll this. |
| GET | `/repositories/{id}/architecture` | High-level view: modules, module-to-module edges, areas, technologies, externals, cycles. |
| GET | `/repositories/{id}/architecture/nodes?id=<node id>` | Detail for one module (`module:<dir>`) or file (`file:<path>`). |

```bash
curl -X POST localhost:8765/repositories/3/architecture            # 202 {"analysis": {...}, "enqueued": true}
curl localhost:8765/repositories/3/architecture/status             # {"status": "completed", "freshness": "current", ...}
curl localhost:8765/repositories/3/architecture                    # {"analysis": {...}, "graph": {...}}
curl "localhost:8765/repositories/3/architecture/nodes?id=module:src/services"
curl "localhost:8765/repositories/3/architecture/nodes?id=file:src/lib/api.ts"
```

Status codes: `POST` 202 (queued, or already queued/running) or 200 (the stored result is already current), 404 unknown
repository, 409 not ingested, 503 the job could not be queued. `GET` graph and node endpoints: 404 until an analysis
has completed, and for an unknown node id.

Ingestion queues an analysis automatically when it completes, so a freshly imported repository normally has one
without a request.

## Status and freshness

`analysis.status` describes the latest *attempt*: `none | queued | running | completed | failed`.
`analysis.freshness` describes the *stored result*: `none | current | stale`.

* A result describes exactly one commit (`analyzed_commit_sha`). If the repository has been re-ingested at another
  commit, or the analyzer has changed (`analyzer_version`), the result is `stale`. It is still returned, flagged, and
  never mixed with the new commit's files.
* A failed or lost attempt records its error but **keeps the last completed result**. Results are only replaced by a
  successful analysis.
* A queued or running attempt that has not been touched for `ingestion_job_timeout_seconds + 60` is reported as
  failed ("stopped unexpectedly") and can simply be requested again.
* A job that finishes after a newer request took over the row discards its result.

## Graph model

The high-level `graph` contains **module nodes** and **aggregated module edges**. The file-level graph is behind the
`nodes` endpoint.

**Node** (`GraphNode`): `id` (`module:<dir>` / `file:<path>`, stable), `label`, `type` (`module` | `file`), `kind`,
`path`, `language`, `files` (a module's member files, capped at 500; a file's own path), `file_count`, `roles`,
`area_ids`, `evidence`, `metrics` (`incoming`, `outgoing`, `external_packages`, `unresolved_imports`), `cycle_id`.

* File `kind`: `source`, or the strongest detected role: `route`, `page`, `edge_function`, `model`, `migration`,
  `job`, `component`, `entrypoint`, `test`.
* Module `kind`: `directory`, or the dominant area (`api`, `frontend`, `database`, `workers`, `tests`) when at least
  half of its files belong to it, or `serverless` for a Supabase function directory.

**Edge** (`GraphEdge`): `source`, `target`, `level`, `relationship`, `basis`, `resolution`, `confidence`, `count`,
`breakdown`, `evidence`, `cycle_id`.

* File edges are `basis: "static"`: one edge per (source file, target file, relationship). `relationship` is
  `imports`, `reexports`, `requires`, `lazy_import` (JS `import()`, Python import inside a function) or `type_only`
  (`import type`, `if TYPE_CHECKING:`).
* Module edges are `basis: "aggregated"` with `relationship: "depends_on"`: the file edges crossing two modules,
  `count` = number of file edges, `breakdown` = statements per relationship, `evidence` = a sample of the
  underlying import statements.
* `resolution` says how the target was found: `relative`, `absolute`, `submodule`, `ancestor_dir`, `alias`,
  `base_url`, `workspace`. `confidence` is `verified` (a unique existing file under the language's resolution rules)
  or `heuristic` (a guess among those rules, for example a Python import resolved against an ancestor directory, or
  `from pkg import name` where `pkg/__init__.py` mentions `name`). Module edges are `mixed` if any underlying edge is
  heuristic.

**Evidence** (`Evidence`): `path`, `start_line`, `end_line` (1-based, inclusive), `kind` (`import`, `reexport`,
`require`, `lazy_import`, `manifest`, `code`, `path`) and `detail` (the statement or matched line). Every edge,
every role and every area carries it. An edge has at most 3 evidence entries (`count` tells how many statements
there are).

**Area** (`Area`): `api`, `frontend`, `database`, `workers`, `serverless`, `tests`, each with `confidence`
(`high`: a framework import plus a matching construct; `medium`: a manifest declaring the framework plus a path
convention; `low`: a path convention only), the files, the modules they live in and the evidence.

**Technology** (`Technology`): present only with evidence, either a dependency declared in `package.json`,
`requirements*.txt` or `pyproject.toml` (with its line) or imports found in code.

Also in the overview: `external_dependencies` (packages, standard-library and URL imports; **not** nodes),
`cycles`, `stats` and `warnings`. Per-file externals and `unresolved_imports` (missing files, with the statement) are
in the node detail.

### What is verified and what is inferred

Relationships are never inferred. Every file edge comes from an import statement in the source and points at a file
that exists in the same snapshot. Module edges are sums of those. **Classifications** (a file's `kind`, areas,
technologies) are inferred, and say so by carrying the evidence they rest on. A directory called `routes/` or `api/`
earns no role without code that shows it.

## How extraction works

1. **Imports** are read with tree-sitter queries (same grammars and guards as `code_parser`): `import`,
   `export ... from`, `require("x")`, `import("x")`, and Python `import` / `from ... import`. A `require(name)` or
   `import(name)` with a computed argument is counted in a warning, never guessed.
2. **Resolution** (`services/architecture/resolve.py`) maps each specifier to a file in the snapshot, an external
   package, an existing non-code file (`css`, `json`...: no edge, counted), or *unresolved*.
   * JavaScript/TypeScript: relative paths, extension swapping (`./x.js` for `x.ts`), `index` files,
     `tsconfig.json` / `jsconfig.json` `paths` and `baseUrl` (nearest ancestor, following relative `extends`),
     workspace packages by `package.json` name. `@/...` with no configured alias is `alias_not_configured`, not an
     external package.
   * Python: relative imports, absolute imports from the repository root and from detected package roots
     (`src/pkg/__init__.py` makes `src` a root), `from pkg import submodule`, and a heuristic ancestor-directory
     fallback (never for standard-library names).
3. **Roles** (`signals.py`) require a framework import plus a matching construct, or a manifest plus a path
   convention (see above).
4. **Modules** are directories, chosen by a deterministic rule: files start in their top-level directory or in a
   package/function directory (a `package.json` / `pyproject.toml` directory, a `supabase/functions/<name>`), and
   a group is split by sub-directory while it has more than 40 files, or while there are fewer than 6 groups and it
   has at least 6 files.
5. **Cycles** are strongly connected components of the *eager* graph (imports, re-exports, require). Lazy and
   type-only imports cannot form a load-time cycle. The overview lists at most 50 nodes per cycle and 100 cycles; every
   member node and edge carries `cycle_id`.

## Using the graph in the web app

The Architecture tab reads these endpoints only; nothing is analysed in the browser.

- **Dense graphs.** Type-only imports can be switched off, and the files of a drilled-down module can show the other modules they import as collapsed stubs ("Other modules"). A repository with more than 40 dependency arrows starts with the optional layers hidden and says how many arrows are hidden. "Find a module" / "Find a file" highlights matches and Enter selects the first.
- **Links in.** `/repos/{id}/graph?module=<module id>&file=<path>` opens a module and selects one file. The Code Explorer's "Dependencies" panel (files the open file imports and files that import it, each linked to the exact import line) and its "Show in architecture" link use it; the panel appears only when an analysis exists and covers the file.
- **Overview.** The repository overview summarises the stored analysis (modules, dependencies, packages, cycles, unresolved imports, detected technologies) and links to the graph.

## Processing

The analysis runs as an RQ job on the existing ingestion queue and worker (`analyze_architecture_job`), with the
same timeout and no retries. The only inputs are the repository's stored files for exactly its current ingested
commit, read in one pass; if those rows change while reading (a re-ingestion swapped the snapshot) the attempt fails
rather than mixing commits. A 5,000-file repository takes about a second. At most `ARCHITECTURE_MAX_FILES` (default
5,000) code files are analyzed; the rest are skipped with a warning and counted in `stats`.

## Storage and migration

Table `architecture_analyses` (migration `b84f2c6d1e07`), one row per repository: attempt status and error, the
target commit, and the last completed result (`result_commit_sha`, `analyzer_version`, `analyzed_at`, `summary` JSONB
for the high-level view, `detail` JSONB for the file-level graph). Apply it with `alembic upgrade head`.

## Limits

* Static imports only: no runtime or dynamic relationships, no framework magic (decorator registration, dependency
  injection, string-based plugin loading), no symbol-level resolution.
* One `tsconfig.json` / `jsconfig.json` per directory tree is used (project references and `tsconfig.*.json`
  siblings are not selected); package.json `imports` / `exports` maps are used only for workspace entry points.
* Vue/Svelte single-file components and other languages are not parsed.
