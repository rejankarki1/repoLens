"""Orchestrates Architecture Diagram V2 generation: context in, sealed diagram out.

No new provider code: this calls the existing `Generator` protocol (`services/generation.py`,
`GeminiGenerator`/`GroqGenerator`) exactly as `services/rag.py` does, just with a different system prompt
and a one-shot (no history) message. `build_diagram_generator` picks the provider
`ARCHITECTURE_DIAGRAM_GENERATION_PROVIDER` names, or `GENERATION_PROVIDER` (Ask AI's own setting) when that
is unset, with a larger output-token budget for this one call — a diagram is bigger than a chat answer, and
can need a provider with more context room than Ask AI's chosen one offers. `generate_architecture_diagram`
itself still takes an already-built `Generator`, so tests can inject a fake one without touching provider
selection.
"""

from __future__ import annotations

from apps.api.config import Settings, get_settings
from apps.api.schemas.architecture_diagram import ArchitectureDiagram
from apps.api.services.architecture_diagram.context import LoadedArchitecture
from apps.api.services.architecture_diagram.prompt import SYSTEM_PROMPT, build_contents
from apps.api.services.architecture_diagram.validate import parse_raw_diagram, validate_diagram
from apps.api.services.generation import Generator


def build_diagram_generator(settings: Settings | None = None) -> Generator:
    """The provider `ARCHITECTURE_DIAGRAM_GENERATION_PROVIDER` selects, falling back to the same
    `GENERATION_PROVIDER` Ask AI uses when unset, tuned with a larger output-token budget and lower
    temperature for this one call. Same classes, same models, same API keys as chat — not a new provider
    path, and Ask AI's own provider selection is untouched either way. Raises `GenerationError
    ("not_configured", ...)` if the selected provider has no key, same as `services.generation.get_generator()`
    does (there, by returning `None` instead; here, by raising, since a diagram request has nothing useful
    to do without one)."""
    settings = settings or get_settings()
    tuned = settings.model_copy(update={
        "gemini_generation_max_output_tokens": settings.architecture_diagram_max_output_tokens,
        "gemini_generation_temperature": settings.architecture_diagram_temperature,
    })
    provider = settings.architecture_diagram_generation_provider or settings.generation_provider
    if provider == "groq":
        from apps.api.services.groq_generation import GroqGenerator

        return GroqGenerator(tuned)
    from apps.api.services.generation import GeminiGenerator

    return GeminiGenerator(tuned)


async def generate_architecture_diagram(
    loaded: LoadedArchitecture,
    *,
    generator: Generator,
    settings: Settings | None = None,
) -> ArchitectureDiagram:
    """Generate and validate one diagram for an already-loaded `LoadedArchitecture` (see `context.py`).

    Raises `services.generation.GenerationError` for a provider/transport failure, or
    `services.architecture_diagram.validate.DiagramGenerationError` if the model's response could not be
    parsed into the expected shape. Either way, nothing is returned on failure: there is no partial
    diagram, matching how `services/rag.py` never saves a partial answer.
    """
    settings = settings or get_settings()
    result = await generator.generate(SYSTEM_PROMPT, build_contents(loaded.context))
    raw = parse_raw_diagram(result.text)
    return validate_diagram(
        raw,
        graph=loaded.graph,
        known_paths=loaded.known_paths,
        module_of=loaded.module_of,
        file_edges=loaded.file_edges,
        candidate_components=loaded.context.candidate_components,
        infrastructure=loaded.context.infrastructure,
        commit_sha=loaded.commit_sha,
        settings=settings,
    )
