"""Orchestration (`services/architecture_diagram/generate.py`) against a fake `Generator`: no network, no
real Groq/Gemini call is possible here. `FakeGenerator` only implements the `Generator` protocol's shape."""

import json

import pytest

from apps.api.config import Settings
from apps.api.schemas.architecture import GraphEdge, GraphNode
from apps.api.services.architecture_diagram.context import LoadedArchitecture, select_context
from apps.api.services.architecture_diagram.generate import build_diagram_generator, generate_architecture_diagram
from apps.api.services.architecture_diagram.validate import DiagramGenerationError
from apps.api.services.generation import GenerationResult
from tests.architecture_support import REALISTIC, SHA, analyze

pytestmark = pytest.mark.anyio


def Settings_(**values) -> Settings:  # noqa: N802
    return Settings(_env_file=None, **values)


class FakeGenerator:
    """Stands in for `GeminiGenerator`/`GroqGenerator`: records the call, returns a canned result, makes
    no HTTP request of any kind."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, list]] = []

    async def generate(self, system, contents, *, attempts: int = 2) -> GenerationResult:
        self.calls.append((system, contents))
        return GenerationResult(self.text)

    def stream(self, system, contents):  # pragma: no cover - diagram generation never streams
        raise NotImplementedError


def _loaded() -> LoadedArchitecture:
    result = analyze(REALISTIC)
    graph = result.graph
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    file_nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
    file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
    known_paths = frozenset(n["path"] for n in result.detail["nodes"])
    line_counts = {path: text.count("\n") for path, text in REALISTIC.items()}
    context = select_context(
        graph=graph, repo_full_name="octo/demo", file_nodes=file_nodes, module_of=module_of,
        line_counts=line_counts, docs=(), manifests=(), excerpt_candidates=(), file_edges=file_edges, settings=Settings_(),
    )
    return LoadedArchitecture(
        context=context, graph=graph, known_paths=known_paths, module_of=module_of, file_edges=file_edges,
        repo_full_name="octo/demo", commit_sha=SHA,
    )


VALID_RESPONSE = json.dumps({
    "groups": [{"id": "g1", "label": "Backend", "description": "Serves and stores user data"}],
    "components": [
        {"id": "c1", "group_id": "g1", "label": "Users API", "description": "Serves the users endpoint over HTTP", "kind": "api", "source_paths": ["apps/api/routes/users.py"]},
        {"id": "c2", "group_id": "g1", "label": "Test Suite", "description": "Exercises the users endpoint", "kind": "other", "source_paths": ["tests/test_users.py"]},
    ],
    "relationships": [{"id": "r1", "source": "c2", "target": "c1", "label": "imports", "kind": "imports"}],
})


def test_diagram_generator_follows_ask_ai_s_provider_by_default() -> None:
    gen = build_diagram_generator(Settings_(generation_provider="groq", groq_api_key="q-key", gemini_api_key="g-key"))
    assert gen.provider == "groq"


def test_diagram_generator_override_uses_gemini_even_when_ask_ai_uses_groq() -> None:
    """The scenario this setting exists for: Ask AI stays on Groq, the diagram call alone uses Gemini
    (its prompt is much larger and would exceed a smaller Groq model's per-request limit)."""
    gen = build_diagram_generator(Settings_(
        generation_provider="groq", architecture_diagram_generation_provider="gemini",
        groq_api_key="q-key", gemini_api_key="g-key",
    ))
    assert gen.provider == "gemini"


def test_diagram_generator_override_uses_groq_even_when_ask_ai_uses_gemini() -> None:
    gen = build_diagram_generator(Settings_(
        generation_provider="gemini", architecture_diagram_generation_provider="groq",
        groq_api_key="q-key", gemini_api_key="g-key",
    ))
    assert gen.provider == "groq"


def test_diagram_generator_is_tuned_for_a_bigger_output_regardless_of_provider() -> None:
    settings = Settings_(gemini_api_key="g-key", architecture_diagram_max_output_tokens=1234, architecture_diagram_temperature=0.05)
    gen = build_diagram_generator(settings)
    assert gen._max_tokens == 1234
    assert gen._temperature == 0.05


async def test_generates_and_validates_a_diagram_from_a_fake_provider() -> None:
    generator = FakeGenerator(VALID_RESPONSE)
    diagram = await generate_architecture_diagram(_loaded(), generator=generator, settings=Settings_())
    assert len(generator.calls) == 1
    assert diagram.repository_commit_sha == SHA
    assert {c.id for c in diagram.components} == {"c1", "c2"}
    assert diagram.relationships[0].confidence == "verified"  # real import edge, kind="imports"


async def test_system_prompt_tells_the_model_it_does_not_decide_verification() -> None:
    generator = FakeGenerator(VALID_RESPONSE)
    await generate_architecture_diagram(_loaded(), generator=generator, settings=Settings_())
    system, contents = generator.calls[0]
    assert "you do not decide what is verified" in system
    assert contents and contents[0]["role"] == "user" and len(contents) == 1  # one-shot: no conversation history


def test_diagram_modules_do_not_import_rag_py() -> None:
    """Diagram generation must stay separate from Ask AI: none of its modules may import `services/rag.py`
    (its Conversation/Message persistence, citation model, or system prompt)."""
    import ast
    import inspect

    import apps.api.services.architecture_diagram.context as context_module
    import apps.api.services.architecture_diagram.generate as generate_module
    import apps.api.services.architecture_diagram.prompt as prompt_module
    import apps.api.services.architecture_diagram.validate as validate_module

    for module in (context_module, generate_module, prompt_module, validate_module):
        tree = ast.parse(inspect.getsource(module))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        } | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert not any(name and "services.rag" in name for name in imported), f"{module.__name__} imports rag.py"


async def test_malformed_model_output_raises_diagramgenerationerror_and_nothing_is_returned() -> None:
    generator = FakeGenerator("I'm not going to produce JSON today.")
    with pytest.raises(DiagramGenerationError):
        await generate_architecture_diagram(_loaded(), generator=generator, settings=Settings_())


async def test_fabricated_path_from_the_model_does_not_survive_generation() -> None:
    response = json.dumps({
        "groups": [], "relationships": [],
        "components": [{"id": "c1", "label": "Imaginary", "description": "x", "kind": "service", "source_paths": ["totally/made/up.py"]}],
    })
    diagram = await generate_architecture_diagram(_loaded(), generator=FakeGenerator(response), settings=Settings_())
    assert diagram.components[0].source_paths == []
    assert diagram.components[0].evidence_basis == "inferred"
