"""Generate one Architecture Diagram V2 result for a repository already ingested in the LOCAL dev database,
and save it as a static JSON fixture for the web app to render without a backend route (Phase 3 Checkpoint A).

This is the ONLY place in the codebase that may call a paid Groq/Gemini provider for a diagram. It is a
manual, standalone tool: nothing else imports it, nothing runs it automatically (not dev-start.sh, not CI,
not a hook). Run it yourself, on purpose, when you want a fresh fixture:

    apps/api/venv/bin/python scripts/generate_architecture_diagram_fixture.py --repository-id 7

Reuses the existing Phase 2/2.5 context selector, validator and the existing Groq/Gemini client classes
(`services/generation.py`, `services/groq_generation.py`) exactly as configured in `.env`
(`GENERATION_PROVIDER`, model, API key) — it does not add a new provider path, just a larger output-token
budget for this one call (a diagram is bigger than a chat answer). Reads the real dev database; writes
nothing to it. The only side effect is the fixture file on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.config import get_settings  # noqa: E402
from apps.api.services.architecture_diagram.context import load_architecture_context  # noqa: E402
from apps.api.services.architecture_diagram.generate import build_diagram_generator  # noqa: E402
from apps.api.services.architecture_diagram.prompt import SYSTEM_PROMPT, build_contents  # noqa: E402
from apps.api.services.architecture_diagram.validate import (  # noqa: E402
    DiagramGenerationError,
    parse_raw_diagram,
    validate_diagram,
)
from apps.api.services.generation import GenerationError  # noqa: E402

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "apps" / "web" / "public" / "dev-fixtures"


def _slug(full_name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", full_name.lower()).strip("-")


async def _run(repository_id: int, out_path: Path | None) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            loaded = await load_architecture_context(db, repository_id, settings=settings)
    finally:
        await engine.dispose()

    print(f"Loaded context for {loaded.repo_full_name} @ {loaded.commit_sha[:12]}")
    print(f"  modules: {len(loaded.context.modules)}  module edges: {len(loaded.context.module_edges)}  "
          f"entry points: {len(loaded.context.entry_points)}  excerpts: {len(loaded.context.excerpts)}")
    payload_size = len(json.dumps(loaded.context.to_json_dict(), default=str))
    print(f"  context payload size: {payload_size} chars "
          f"(budget {settings.architecture_diagram_context_max_chars}) warnings={list(loaded.context.warnings)}")

    generator = build_diagram_generator(settings)
    print(f"Calling provider={generator.provider} model={generator.model} (ONE request) ...")
    try:
        result = await generator.generate(SYSTEM_PROMPT, build_contents(loaded.context))
    except GenerationError as exc:
        print(f"Generation failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1

    # Save the raw response BEFORE attempting to parse it: a parsing/validation bug must never cost a
    # second live request. The default out_path's sibling `.raw.txt` is where to look if parsing fails.
    raw_out_path = (out_path or (DEFAULT_OUT_DIR / f"{_slug(loaded.repo_full_name)}-architecture.json")).with_suffix(".raw.txt")
    raw_out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_out_path.write_text(result.text)
    print(f"Saved raw provider response to {raw_out_path} ({len(result.text)} chars)")

    try:
        diagram = validate_diagram(
            parse_raw_diagram(result.text), graph=loaded.graph, known_paths=loaded.known_paths,
            module_of=loaded.module_of, file_edges=loaded.file_edges,
            candidate_components=loaded.context.candidate_components,
            infrastructure=loaded.context.infrastructure,
            commit_sha=loaded.commit_sha, settings=settings,
        )
    except DiagramGenerationError as exc:
        print(f"\nParsing/validation failed ({exc.code}): {exc.message}", file=sys.stderr)
        print(f"The raw response is safe at {raw_out_path}; fix the bug and re-run parse/validate offline, "
              "no new request needed.", file=sys.stderr)
        return 1

    print(f"\nGenerated diagram: {len(diagram.groups)} groups, {len(diagram.components)} components, "
          f"{len(diagram.relationships)} relationships")
    for group in diagram.groups:
        members = [c.id for c in diagram.components if c.group_id == group.id]
        print(f"  [{group.id}] {group.label}: {members}")
    print()
    for c in diagram.components:
        print(f"  component {c.id!r} ({c.kind}, {c.evidence_basis}): {c.label} -> {c.source_paths}")
    print()
    for r in diagram.relationships:
        print(f"  {r.source} --{r.label}[{r.kind}/{r.confidence}]--> {r.target}")
    if diagram.warnings:
        print("\nWarnings:")
        for w in diagram.warnings:
            print(f"  - {w}")

    out_path = out_path or (DEFAULT_OUT_DIR / f"{_slug(loaded.repo_full_name)}-architecture.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(diagram.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    print(f"\nSaved fixture to {out_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository-id", type=int, required=True)
    parser.add_argument("--out", type=Path, default=None, help="Fixture output path (default: apps/web/public/dev-fixtures/<repo-slug>-architecture.json)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.repository_id, args.out)))


if __name__ == "__main__":
    main()
