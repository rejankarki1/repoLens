"""Source-first component planning (`select_context`'s `candidate_components`): pure logic, no database,
no AI provider. Each fixture here deliberately has several files in ONE directory with DIFFERENT
responsibilities — controllers, pages, library modules, pipeline stages — the exact shape that collapsed
into one generic "Controllers"/"UI Pages" bucket in the real Wanderlust regeneration this round targets.
"""

import json

import pytest

from apps.api.config import Settings
from apps.api.schemas.architecture import GraphEdge, GraphNode
from apps.api.schemas.architecture_diagram import DIAGRAM_GENERATOR_VERSION, RawArchitectureDiagram
from apps.api.services.architecture.build import SnapshotFile, analyze_snapshot
from apps.api.services.architecture_diagram.context import ExcerptCandidate, select_context
from apps.api.services.architecture_diagram.prompt import SYSTEM_PROMPT
from apps.api.services.architecture_diagram.validate import validate_diagram

SHA = "f" * 40


def Settings_(**values) -> Settings:  # noqa: N802
    return Settings(_env_file=None, **values)


def _analyze(files: dict[str, str]):
    snapshot = [SnapshotFile(path, "source", len(text.encode()), text) for path, text in sorted(files.items())]
    return analyze_snapshot(SHA, snapshot)


def _context(files: dict[str, str], settings: Settings | None = None):
    result = _analyze(files)
    graph = result.graph
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    file_nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
    file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
    line_counts = {path: text.count("\n") for path, text in files.items()}
    return select_context(
        graph=graph, repo_full_name="octo/demo", file_nodes=file_nodes, module_of=module_of,
        line_counts=line_counts, docs=(), manifests=(), excerpt_candidates=(), file_edges=file_edges,
        settings=settings or Settings_(),
    )


# --- fixtures: three archetypes, each with >=3 same-directory siblings of different responsibilities -------

WANDERLUST_LIKE = {
    "package.json": '{"dependencies": {"express": "4.19.0", "react": "18.0.0"}}',
    # bookings.js genuinely requires listings.js (real coupling); the rest are independent siblings.
    "backend/controllers/listings.js": "const Listing = require('../models/listing')\n\nfunction index() { return Listing.find() }\n\nmodule.exports = { index }\n",
    "backend/controllers/bookings.js": (
        "const Booking = require('../models/booking')\nconst { index } = require('./listings')\n\n"
        "function create() { index(); return Booking.create() }\n\nmodule.exports = { create }\n"
    ),
    "backend/controllers/users.js": "const User = require('../models/user')\n\nfunction show() { return User.findOne() }\n\nmodule.exports = { show }\n",
    "backend/controllers/reviews.js": "const Review = require('../models/review')\n\nfunction create() { return Review.create() }\n\nmodule.exports = { create }\n",
    "backend/models/listing.js": "module.exports = {}\n",
    "backend/models/booking.js": "module.exports = {}\n",
    "backend/models/user.js": "module.exports = {}\n",
    "backend/models/review.js": "module.exports = {}\n",
    "backend/tests/listings.test.js": "require('../controllers/listings')\n",
    "backend/tests/bookings.test.js": "require('../controllers/bookings')\n",
    "backend/tests/users.test.js": "require('../controllers/users')\n",
    "client/src/pages/ListingsPage.jsx": "export function ListingsPage() { return null }\n",
    "client/src/pages/BookingPage.jsx": "export function BookingPage() { return null }\n",
    "client/src/pages/LoginPage.jsx": "export function LoginPage() { return null }\n",
    "client/src/pages/ReviewPage.jsx": "export function ReviewPage() { return null }\n",
}

PYTHON_LIBRARY = {
    "pyproject.toml": '[project]\nname = "pkg"\n',
    # serializer.py genuinely imports signer.py; timed.py and url_safe.py are independent siblings.
    "pkg/signer.py": "class Signer:\n    pass\n",
    "pkg/serializer.py": "from pkg.signer import Signer\n\n\nclass Serializer:\n    pass\n",
    "pkg/timed.py": "class TimedSerializer:\n    pass\n",
    "pkg/url_safe.py": "class URLSafeSerializer:\n    pass\n",
}

AI_ML_PIPELINE = {
    "pyproject.toml": '[project]\nname = "pipeline"\n',
    # embed.py genuinely imports ingest.py; retrieve.py and rerank.py are independent siblings.
    "pipeline/ingest.py": "def load():\n    pass\n",
    "pipeline/embed.py": "from pipeline.ingest import load\n\n\ndef embed():\n    load()\n",
    "pipeline/retrieve.py": "def retrieve():\n    pass\n",
    "pipeline/rerank.py": "def rerank():\n    pass\n",
}

ARCHETYPES = {
    "wanderlust_like": (WANDERLUST_LIKE, "backend/controllers/bookings.js", "backend/controllers/listings.js"),
    "python_library": (PYTHON_LIBRARY, "pkg/serializer.py", "pkg/signer.py"),
    "ai_ml_pipeline": (AI_ML_PIPELINE, "pipeline/embed.py", "pipeline/ingest.py"),
}


# --- each sibling becomes its own candidate, not one shared bucket -------------------------------------------


@pytest.mark.parametrize("name", ARCHETYPES)
def test_every_sibling_in_the_bucket_becomes_its_own_candidate(name: str) -> None:
    files, coupled_source, coupled_target = ARCHETYPES[name]
    ctx = _context(files)
    candidate_paths = {c.path for c in ctx.candidate_components}
    assert coupled_source in candidate_paths and coupled_target in candidate_paths


def test_wanderlust_like_controllers_and_pages_are_all_individually_candidates() -> None:
    ctx = _context(WANDERLUST_LIKE)
    candidate_paths = {c.path for c in ctx.candidate_components}
    for controller in ("listings", "bookings", "users", "reviews"):
        assert f"backend/controllers/{controller}.js" in candidate_paths
    for page in ("ListingsPage", "BookingPage", "LoginPage", "ReviewPage"):
        assert f"client/src/pages/{page}.jsx" in candidate_paths


@pytest.mark.parametrize("name", ARCHETYPES)
def test_real_coupling_between_siblings_is_captured(name: str) -> None:
    """The one real import between two siblings must show up as a captured signal — the deterministic
    basis for "merge these two" — while unrelated siblings show none between each other."""
    files, coupled_source, coupled_target = ARCHETYPES[name]
    ctx = _context(files)
    by_path = {c.path: c for c in ctx.candidate_components}
    assert coupled_target in by_path[coupled_source].imports
    assert coupled_source in by_path[coupled_target].imported_by


def test_independent_siblings_show_no_coupling_to_each_other() -> None:
    """users.js and reviews.js share a directory with listings.js/bookings.js but import nothing from any
    other controller — the signal that they are independent, unrelated features. Each does genuinely
    import its own model (also a candidate, from the separate models/ bucket) — that is real, just not
    controller-to-controller coupling."""
    ctx = _context(WANDERLUST_LIKE)
    by_path = {c.path: c for c in ctx.candidate_components}
    users = by_path["backend/controllers/users.js"]
    reviews = by_path["backend/controllers/reviews.js"]
    other_controllers = {"backend/controllers/listings.js", "backend/controllers/bookings.js"}
    assert not (set(users.imports) & other_controllers) and not (set(users.imported_by) & other_controllers)
    assert not (set(reviews.imports) & other_controllers) and not (set(reviews.imported_by) & other_controllers)


def test_pages_have_no_false_coupling_to_each_other() -> None:
    """None of the page components import each other; none should appear in any other page's candidate
    imports/imported_by, unlike the controllers' one genuine coupling."""
    ctx = _context(WANDERLUST_LIKE)
    pages = {c.path for c in ctx.candidate_components if c.path.startswith("client/src/pages/")}
    for c in ctx.candidate_components:
        if c.path in pages:
            assert not (set(c.imports) | set(c.imported_by)) & pages


# --- test files are not treated as feature candidates --------------------------------------------------------


def test_test_role_files_are_excluded_from_candidates_even_as_a_large_sibling_group() -> None:
    ctx = _context(WANDERLUST_LIKE)
    candidate_paths = {c.path for c in ctx.candidate_components}
    assert not any(p.startswith("backend/tests/") for p in candidate_paths)
    # still visible via the module's own file listing — just not promoted as a feature candidate
    tests_module = next(m for m in ctx.modules if m.id == "module:backend/tests")
    assert any(f.path.startswith("backend/tests/") for f in tests_module.files)


# --- small buckets are not promoted (nothing new to distinguish) ---------------------------------------------


def test_bucket_below_the_sibling_minimum_is_not_promoted() -> None:
    files = dict(WANDERLUST_LIKE)
    del files["backend/controllers/reviews.js"]  # down to 3; drop one more to go below the default minimum (3)
    del files["backend/controllers/users.js"]
    ctx = _context(files)
    candidate_paths = {c.path for c in ctx.candidate_components}
    assert "backend/controllers/listings.js" not in candidate_paths
    assert "backend/controllers/bookings.js" not in candidate_paths


def test_sibling_minimum_is_configurable() -> None:
    settings = Settings_(architecture_diagram_candidate_min_siblings=2)
    files = dict(WANDERLUST_LIKE)
    del files["backend/controllers/reviews.js"]
    del files["backend/controllers/users.js"]
    ctx = _context(files, settings=settings)
    candidate_paths = {c.path for c in ctx.candidate_components}
    assert "backend/controllers/listings.js" in candidate_paths and "backend/controllers/bookings.js" in candidate_paths


# --- caps ------------------------------------------------------------------------------------------------------


def test_candidates_per_bucket_is_capped() -> None:
    """Mirrors the existing, unwarned per-module `files` cap (`architecture_diagram_max_files_per_module`):
    a per-bucket cap silently truncates ranking, the same established pattern — only the overall count cap
    (below) warns, since that is the one that can make a whole directory's candidates vanish entirely."""
    settings = Settings_(architecture_diagram_max_candidates_per_bucket=1)
    ctx = _context(WANDERLUST_LIKE, settings=settings)
    by_module_subdir: dict[str, int] = {}
    for c in ctx.candidate_components:
        key = c.path.rsplit("/", 1)[0]
        by_module_subdir[key] = by_module_subdir.get(key, 0) + 1
    assert by_module_subdir and all(count <= 1 for count in by_module_subdir.values())


def test_total_candidate_cap_is_enforced_and_warns() -> None:
    settings = Settings_(architecture_diagram_max_candidate_components=2)
    ctx = _context(WANDERLUST_LIKE, settings=settings)
    assert len(ctx.candidate_components) == 2
    assert any("candidate component" in w for w in ctx.warnings)


# --- budget: candidates are cheap, so they give way after file_references but before excerpts -----------------


def test_overall_size_budget_drops_candidates_before_excerpts_but_after_file_references() -> None:
    big_excerpt = "x" * 2_000
    candidates = [ExcerptCandidate("backend/controllers/listings.js", "module:backend", "index", "function", big_excerpt)]
    result = _analyze(WANDERLUST_LIKE)
    graph = result.graph
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    file_nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
    file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
    line_counts = {path: text.count("\n") for path, text in WANDERLUST_LIKE.items()}

    def build(max_chars: int):
        return select_context(
            graph=graph, repo_full_name="octo/demo", file_nodes=file_nodes, module_of=module_of,
            line_counts=line_counts, docs=(), manifests=(), excerpt_candidates=candidates, file_edges=file_edges,
            settings=Settings_(architecture_diagram_context_max_chars=max_chars, architecture_diagram_excerpt_max_chars_per_file=2_000),
        )

    unbounded = build(10_000_000)
    assert unbounded.candidate_components and unbounded.file_references and unbounded.excerpts

    tight = build(len(json.dumps(unbounded.to_json_dict(), sort_keys=True, default=str)) - 50)
    # something had to go; file_references or candidates shrank, but the excerpt (the expensive part) did not
    assert len(tight.file_references) + len(tight.candidate_components) < len(unbounded.file_references) + len(unbounded.candidate_components)
    assert tight.excerpts == unbounded.excerpts


# --- prompt mentions the new section ----------------------------------------------------------------------------


def test_prompt_explains_candidate_components_and_the_anti_clustering_rule() -> None:
    assert "candidate_components" in SYSTEM_PROMPT
    assert "DIFFERENT feature" in SYSTEM_PROMPT or "different feature" in SYSTEM_PROMPT.lower()


# --- end to end with a mocked model response: the shape this round aims for --------------------------------------


def test_a_model_response_honoring_candidate_hints_validates_with_real_evidence() -> None:
    """If the model does what rule 2 asks — one component per candidate, merged only where `imports` shows
    real coupling — the existing (unmodified) validation pipeline accepts it cleanly: real paths, and the
    one genuine import (bookings -> listings) verified with real evidence, exactly like last round's fix."""
    result = _analyze(WANDERLUST_LIKE)
    graph = result.graph
    known_paths = {n["path"] for n in result.detail["nodes"]}
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])

    raw = {
        "groups": [{"id": "g1", "label": "Backend", "description": "x"}],
        "components": [
            {"id": "c_listings", "group_id": "g1", "label": "Listings", "description": "x", "kind": "service", "source_paths": ["backend/controllers/listings.js"]},
            {"id": "c_bookings", "group_id": "g1", "label": "Bookings", "description": "x", "kind": "service", "source_paths": ["backend/controllers/bookings.js"]},
            {"id": "c_users", "group_id": "g1", "label": "Users", "description": "x", "kind": "service", "source_paths": ["backend/controllers/users.js"]},
            {"id": "c_reviews", "group_id": "g1", "label": "Reviews", "description": "x", "kind": "service", "source_paths": ["backend/controllers/reviews.js"]},
        ],
        "relationships": [
            {"id": "r1", "source": "c_bookings", "target": "c_listings", "label": "checks availability via", "kind": "imports"},
        ],
    }
    diagram = validate_diagram(
        RawArchitectureDiagram.model_validate(raw), graph=graph, known_paths=known_paths, module_of=module_of,
        file_edges=file_edges, commit_sha=SHA, settings=Settings_(),
    )
    assert {c.id for c in diagram.components} == {"c_listings", "c_bookings", "c_users", "c_reviews"}
    assert all(c.evidence_basis == "verified_paths" for c in diagram.components)
    rel = diagram.relationships[0]
    assert rel.confidence == "verified"
    assert rel.evidence and rel.evidence[0].path == "backend/controllers/bookings.js"
    assert diagram.generator_version == DIAGRAM_GENERATOR_VERSION  # output schema/version untouched this round


# --- relationship-coverage diagnostic: warn-only, never invents or rejects anything --------------------------


def _coupled_components(coupled_source: str, coupled_target: str) -> list[dict]:
    """Two components, one per side of the archetype's one real coupled pair — minimal, so each test's
    relationships list (or lack of one) is unambiguous."""
    return [
        {"id": "c_source", "label": "Source", "description": "x", "kind": "service", "source_paths": [coupled_source]},
        {"id": "c_target", "label": "Target", "description": "x", "kind": "service", "source_paths": [coupled_target]},
    ]


def _seal_with_candidates(files: dict[str, str], components: list[dict], relationships: list[dict]):
    result = _analyze(files)
    graph = result.graph
    known_paths = {n["path"] for n in result.detail["nodes"]}
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
    ctx = _context(files)
    raw = {"groups": [], "components": components, "relationships": relationships}
    return validate_diagram(
        RawArchitectureDiagram.model_validate(raw), graph=graph, known_paths=known_paths, module_of=module_of,
        file_edges=file_edges, candidate_components=ctx.candidate_components, commit_sha=SHA, settings=Settings_(),
    )


@pytest.mark.parametrize("name", ARCHETYPES)
def test_coverage_warning_fires_when_the_known_real_connection_is_omitted(name: str) -> None:
    files, coupled_source, coupled_target = ARCHETYPES[name]
    diagram = _seal_with_candidates(files, _coupled_components(coupled_source, coupled_target), [])
    assert any("real import connection" in w for w in diagram.warnings), f"{name}: expected a coverage warning"
    assert diagram.relationships == []  # the diagnostic never invents one


@pytest.mark.parametrize("name", ARCHETYPES)
def test_coverage_warning_does_not_fire_when_the_known_connection_is_covered(name: str) -> None:
    files, coupled_source, coupled_target = ARCHETYPES[name]
    relationships = [{"id": "r1", "source": "c_source", "target": "c_target", "label": "uses", "kind": "imports"}]
    diagram = _seal_with_candidates(files, _coupled_components(coupled_source, coupled_target), relationships)
    assert not any("real import connection" in w for w in diagram.warnings)


def test_coverage_warning_counts_each_missing_edge_once_not_twice() -> None:
    """`imports` and `imported_by` are inverses of the same underlying edge; the diagnostic must iterate
    only one of them, or every real connection would be counted twice."""
    diagram = _seal_with_candidates(WANDERLUST_LIKE, _coupled_components("backend/controllers/bookings.js", "backend/controllers/listings.js"), [])
    (warning,) = [w for w in diagram.warnings if "real import connection" in w]
    assert warning.startswith("1 "), warning


def test_coverage_diagnostic_ignores_candidates_merged_into_the_same_component() -> None:
    """Putting both sides of the real coupling in one component is a valid resolution (the model chose to
    merge), not a coverage gap — it must not be flagged."""
    merged = [{"id": "c_merged", "label": "Merged", "description": "x", "kind": "service", "source_paths": ["backend/controllers/bookings.js", "backend/controllers/listings.js"]}]
    diagram = _seal_with_candidates(WANDERLUST_LIKE, merged, [])
    assert not any("real import connection" in w for w in diagram.warnings)


def test_coverage_diagnostic_ignores_a_candidate_the_model_never_placed_anywhere() -> None:
    """Nothing to compare against: the diagnostic cannot fault the model for a real-connection target it
    never used in any component's `source_paths` at all."""
    only_source = [{"id": "c_source", "label": "Source", "description": "x", "kind": "service", "source_paths": ["backend/controllers/bookings.js"]}]
    diagram = _seal_with_candidates(WANDERLUST_LIKE, only_source, [])
    assert not any("real import connection" in w for w in diagram.warnings)


def test_coverage_diagnostic_is_a_no_op_when_candidate_components_is_omitted() -> None:
    """The default `()` must truly disable the feature, not merely happen not to trigger — matching every
    pre-existing caller of `validate_diagram` that never passes `candidate_components`."""
    result = _analyze(WANDERLUST_LIKE)
    graph = result.graph
    known_paths = {n["path"] for n in result.detail["nodes"]}
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    raw = {"groups": [], "components": _coupled_components("backend/controllers/bookings.js", "backend/controllers/listings.js"), "relationships": []}
    diagram = validate_diagram(
        RawArchitectureDiagram.model_validate(raw), graph=graph, known_paths=known_paths, module_of=module_of,
        commit_sha=SHA, settings=Settings_(),
    )
    assert not any("real import connection" in w for w in diagram.warnings)
