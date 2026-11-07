"""Parsing and sealing the model's JSON (`services/architecture_diagram/validate.py`): pure logic, no
database, no AI provider. Uses the realistic fixture repository's real deterministic graph so "verified"
checks run against a real import edge, not a hand-rolled stand-in."""

import pytest

from apps.api.config import Settings
from apps.api.schemas.architecture_diagram import RawArchitectureDiagram
from apps.api.services.architecture_diagram.validate import (
    DiagramGenerationError,
    parse_raw_diagram,
    validate_diagram,
)
from tests.architecture_support import REALISTIC, SHA, analyze


def Settings_(**values) -> Settings:  # noqa: N802
    return Settings(_env_file=None, **values)


@pytest.fixture
def graph():
    return analyze(REALISTIC).graph


@pytest.fixture
def known_paths():
    return {n["path"] for n in analyze(REALISTIC).detail["nodes"]}


@pytest.fixture
def module_of():
    detail = analyze(REALISTIC).detail
    return {p[5:] if p.startswith("file:") else p: m for p, m in detail["module_of"].items()}


@pytest.fixture
def file_edges():
    from apps.api.schemas.architecture import GraphEdge

    return tuple(GraphEdge.model_validate(e) for e in analyze(REALISTIC).detail["edges"])


def seal(raw: dict, graph, known_paths, module_of, file_edges=(), **settings_overrides):
    return validate_diagram(
        RawArchitectureDiagram.model_validate(raw), graph=graph, known_paths=known_paths, module_of=module_of,
        file_edges=file_edges, commit_sha=SHA, settings=Settings_(**settings_overrides),
    )


# --- parse_raw_diagram -----------------------------------------------------------------------------------


def test_parses_a_bare_json_object() -> None:
    raw = parse_raw_diagram('{"groups": [], "components": [], "relationships": []}')
    assert raw.groups == [] and raw.components == [] and raw.relationships == []


def test_strips_a_markdown_code_fence() -> None:
    text = '```json\n{"groups": [], "components": [], "relationships": []}\n```'
    assert parse_raw_diagram(text).components == []


def test_extracts_the_json_object_even_with_surrounding_commentary() -> None:
    text = 'Sure, here you go:\n{"groups": [], "components": [], "relationships": []}\nHope that helps!'
    assert parse_raw_diagram(text).components == []


def test_rejects_text_with_no_json_object() -> None:
    with pytest.raises(DiagramGenerationError) as exc:
        parse_raw_diagram("I cannot help with that.")
    assert exc.value.code == "invalid_json"


def test_rejects_malformed_json() -> None:
    with pytest.raises(DiagramGenerationError) as exc:
        parse_raw_diagram('{"groups": [}')
    assert exc.value.code == "invalid_json"


def test_rejects_a_shape_that_does_not_match_the_schema() -> None:
    with pytest.raises(DiagramGenerationError) as exc:
        parse_raw_diagram('{"components": "not a list"}')
    assert exc.value.code == "invalid_shape"


def test_unknown_top_level_keys_are_ignored_not_rejected() -> None:
    raw = parse_raw_diagram('{"groups": [], "components": [], "relationships": [], "confidence_notes": "trust me"}')
    assert raw.components == []


# --- component path validation ----------------------------------------------------------------------------


def test_a_real_path_is_kept_and_marked_verified(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [{"id": "c1", "label": "Users API", "description": "x", "kind": "api", "source_paths": ["apps/api/routes/users.py"]}]},
        graph, known_paths, module_of,
    )
    assert diagram.components[0].source_paths == ["apps/api/routes/users.py"]
    assert diagram.components[0].evidence_basis == "verified_paths"


def test_a_fabricated_path_is_dropped_and_warned_about(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [{"id": "c1", "label": "Ghost", "description": "x", "kind": "service", "source_paths": ["this/does/not/exist.py"]}]},
        graph, known_paths, module_of,
    )
    assert diagram.components[0].source_paths == []
    assert diagram.components[0].evidence_basis == "inferred"
    assert any("do not exist" in w for w in diagram.warnings)


def test_a_module_directory_path_is_accepted(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [{"id": "c1", "label": "Web app", "description": "The Next.js frontend", "kind": "ui", "source_paths": ["apps/web"]}]},
        graph, known_paths, module_of,
    )
    assert diagram.components[0].evidence_basis == "verified_paths"


def test_component_with_no_paths_at_all_is_inferred_but_kept(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [{"id": "c1", "label": "Deployment Topology", "description": "A conceptual grouping with no single file", "kind": "other", "source_paths": []}]},
        graph, known_paths, module_of,
    )
    assert len(diagram.components) == 1
    assert diagram.components[0].evidence_basis == "inferred"


def test_bare_directory_rename_heuristic_warns_without_rejecting(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [{"id": "c1", "label": "routes", "description": "", "kind": "api", "source_paths": ["apps/api/routes/users.py"]}]},
        graph, known_paths, module_of,
    )
    assert len(diagram.components) == 1  # not rejected, just flagged
    assert any("bare directory rename" in w for w in diagram.warnings)


# --- structural hygiene: ids, groups, caps --------------------------------------------------------------------


def test_duplicate_component_ids_keep_only_the_first(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [
            {"id": "c1", "label": "First", "description": "x", "kind": "service", "source_paths": []},
            {"id": "c1", "label": "Second", "description": "x", "kind": "service", "source_paths": []},
        ]},
        graph, known_paths, module_of,
    )
    assert [c.label for c in diagram.components] == ["First"]
    assert any("duplicate component id" in w for w in diagram.warnings)


def test_component_cap_is_enforced_regardless_of_model_output(graph, known_paths, module_of) -> None:
    components = [{"id": f"c{i}", "label": f"Component {i}", "description": "x", "kind": "service", "source_paths": []} for i in range(10)]
    diagram = seal({"components": components}, graph, known_paths, module_of, architecture_diagram_max_components=3)
    assert len(diagram.components) == 3
    assert any("more than 3 components" in w for w in diagram.warnings)


def test_unknown_group_reference_is_dropped_not_fatal(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": [{"id": "c1", "group_id": "ghost-group", "label": "X", "description": "x", "kind": "service", "source_paths": []}]},
        graph, known_paths, module_of,
    )
    assert diagram.components[0].group_id is None
    assert any("unknown group" in w for w in diagram.warnings)


def test_source_paths_are_capped_per_component(graph, known_paths, module_of) -> None:
    paths = ["apps/api/routes/users.py", "apps/api/jobs/email.py", "apps/api/models.py", "apps/api/schemas.py"]
    diagram = seal(
        {"components": [{"id": "c1", "label": "Everything", "description": "x", "kind": "service", "source_paths": paths}]},
        graph, known_paths, module_of, architecture_diagram_max_source_paths_per_component=2,
    )
    assert len(diagram.components[0].source_paths) == 2


# --- relationship structural hygiene -------------------------------------------------------------------------


def _two_components():
    return [
        {"id": "c1", "label": "Users API", "description": "x", "kind": "api", "source_paths": ["apps/api/routes/users.py"]},
        {"id": "c2", "label": "Test Suite", "description": "x", "kind": "other", "source_paths": ["tests/test_users.py"]},
    ]


def test_relationship_to_an_unknown_component_is_dropped(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": _two_components(), "relationships": [{"id": "r1", "source": "c1", "target": "ghost", "label": "x", "kind": "other"}]},
        graph, known_paths, module_of,
    )
    assert diagram.relationships == []
    assert any("unknown component" in w for w in diagram.warnings)


def test_self_loop_relationship_is_dropped(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": _two_components(), "relationships": [{"id": "r1", "source": "c1", "target": "c1", "label": "x", "kind": "other"}]},
        graph, known_paths, module_of,
    )
    assert diagram.relationships == []


def test_relationship_cap_is_enforced(graph, known_paths, module_of) -> None:
    rels = [{"id": f"r{i}", "source": "c1", "target": "c2", "label": "x", "kind": "other"} for i in range(5)]
    diagram = seal(
        {"components": _two_components(), "relationships": rels}, graph, known_paths, module_of,
        architecture_diagram_max_relationships=2,
    )
    assert len(diagram.relationships) == 2


# --- the core safety property: verified vs. inferred ----------------------------------------------------------


def test_import_relationship_backed_by_a_real_edge_is_verified_with_real_evidence(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": _two_components(), "relationships": [{"id": "r1", "source": "c2", "target": "c1", "label": "imports", "kind": "imports"}]},
        graph, known_paths, module_of,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "verified"
    assert rel.evidence and rel.evidence[0].path == "tests/test_users.py"
    assert "apps.api.routes.users" in rel.evidence[0].detail


def test_import_relationship_in_the_wrong_direction_is_not_verified(graph, known_paths, module_of) -> None:
    """tests/ imports apps/, not the other way around; claiming the reverse direction must not be verified."""
    diagram = seal(
        {"components": _two_components(), "relationships": [{"id": "r1", "source": "c1", "target": "c2", "label": "imports", "kind": "imports"}]},
        graph, known_paths, module_of,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "inferred"
    assert rel.evidence == []


@pytest.mark.parametrize("kind", ["sync_call", "async_call", "queue", "db_read", "db_write", "renders", "other"])
def test_non_import_kinds_are_never_verified_even_with_a_real_import_edge_underneath(graph, known_paths, module_of, kind) -> None:
    """The requirement this test exists to enforce: an import edge must never be read as proof of an HTTP
    call, a queue hop, or a database read/write. Only `kind="imports"` can ever become "verified"."""
    diagram = seal(
        {"components": _two_components(), "relationships": [{"id": "r1", "source": "c2", "target": "c1", "label": "does something", "kind": kind}]},
        graph, known_paths, module_of,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "inferred"
    assert rel.evidence == []


def test_import_relationship_between_components_with_no_verified_paths_is_inferred(graph, known_paths, module_of) -> None:
    diagram = seal(
        {
            "components": [
                {"id": "c1", "label": "A", "description": "x", "kind": "service", "source_paths": ["nonexistent/a.py"]},
                {"id": "c2", "label": "B", "description": "x", "kind": "service", "source_paths": ["nonexistent/b.py"]},
            ],
            "relationships": [{"id": "r1", "source": "c1", "target": "c2", "label": "imports", "kind": "imports"}],
        },
        graph, known_paths, module_of,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "inferred" and rel.evidence == []


# --- file-level verification: the fix for same-module relationships -------------------------------------------


def _same_module_components():
    """`apps/api/routes/users.py` and `apps/api/services/accounts.py` are a real `imports` edge, but both
    land in the same module (`module:apps`, see `tests/architecture_support.py`'s REALISTIC fixture) — a
    module never has a dependency edge to itself, so only a file-level edge can verify this relationship."""
    return [
        {"id": "c1", "label": "Users API", "description": "x", "kind": "api", "source_paths": ["apps/api/routes/users.py"]},
        {"id": "c2", "label": "Account Services", "description": "x", "kind": "service", "source_paths": ["apps/api/services/accounts.py"]},
    ]


def test_same_module_relationship_is_verified_via_the_file_level_edge(graph, known_paths, module_of, file_edges) -> None:
    diagram = seal(
        {
            "components": _same_module_components(),
            "relationships": [{"id": "r1", "source": "c1", "target": "c2", "label": "calls", "kind": "imports"}],
        },
        graph, known_paths, module_of, file_edges,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "verified"
    assert rel.evidence and rel.evidence[0].path == "apps/api/routes/users.py"
    assert "accounts" in rel.evidence[0].detail


def test_same_module_relationship_without_file_edges_stays_inferred(graph, known_paths, module_of) -> None:
    """Without `file_edges` (the default), this is exactly the gap the fix closes: the module-level graph
    has no self-edge for `module:apps`, so the real relationship above could never be verified."""
    diagram = seal(
        {
            "components": _same_module_components(),
            "relationships": [{"id": "r1", "source": "c1", "target": "c2", "label": "calls", "kind": "imports"}],
        },
        graph, known_paths, module_of,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "inferred" and rel.evidence == []


def test_same_module_relationship_in_the_wrong_direction_is_not_verified(graph, known_paths, module_of, file_edges) -> None:
    """accounts.py does not import users.py; claiming the reverse direction must not be verified even
    though both files share a module and a (different) file edge exists between them."""
    diagram = seal(
        {
            "components": _same_module_components(),
            "relationships": [{"id": "r1", "source": "c2", "target": "c1", "label": "calls", "kind": "imports"}],
        },
        graph, known_paths, module_of, file_edges,
    )
    rel = diagram.relationships[0]
    assert rel.confidence == "inferred" and rel.evidence == []


def test_evidence_is_capped_and_copied_from_the_graph_not_the_model(graph, known_paths, module_of) -> None:
    diagram = seal(
        {"components": _two_components(), "relationships": [{"id": "r1", "source": "c2", "target": "c1", "label": "totally different claim", "kind": "imports"}]},
        graph, known_paths, module_of, architecture_diagram_max_evidence_per_relationship=1,
    )
    rel = diagram.relationships[0]
    assert len(rel.evidence) <= 1
    # the model's own label is kept (it is not a verification claim), but evidence text comes from the graph
    assert rel.label == "totally different claim"
    assert all("totally different claim" not in e.detail for e in rel.evidence)


def test_output_is_a_sealed_architecturediagram_with_commit_and_generator_version(graph, known_paths, module_of) -> None:
    diagram = seal({"components": [], "relationships": []}, graph, known_paths, module_of)
    assert diagram.repository_commit_sha == SHA
    assert diagram.generator_version == 1
