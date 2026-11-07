"""Deterministic context selection (`services/architecture_diagram/context.py::select_context`): pure
logic, no database, no AI provider. Uses the same realistic fixture repository as `test_architecture_build.py`."""

import json

from apps.api.config import Settings
from apps.api.schemas.architecture import GraphEdge, GraphNode
from apps.api.services.architecture_diagram.context import (
    ContextDoc,
    ExcerptCandidate,
    select_context,
)
from tests.architecture_support import REALISTIC, SHA, analyze


def Settings_(**values) -> Settings:  # noqa: N802
    return Settings(_env_file=None, **values)


def _build(files=REALISTIC, *, docs=(), manifests=(), excerpts=(), settings=None, with_file_edges=True):
    result = analyze(files)
    graph = result.graph
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
    file_nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
    file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"]) if with_file_edges else ()
    line_counts = {path: text.count("\n") for path, text in files.items()}
    return select_context(
        graph=graph, repo_full_name="octo/demo", file_nodes=file_nodes, module_of=module_of,
        line_counts=line_counts, docs=docs, manifests=manifests, excerpt_candidates=excerpts,
        file_edges=file_edges, settings=settings or Settings_(),
    )


def test_selection_is_deterministic() -> None:
    a = _build()
    b = _build()
    assert json.dumps(a.to_json_dict(), sort_keys=True) == json.dumps(b.to_json_dict(), sort_keys=True)


def test_modules_and_edges_come_from_the_real_graph() -> None:
    ctx = _build()
    module_ids = {m.id for m in ctx.modules}
    assert "module:apps/web" in module_ids and "module:tests" in module_ids
    # tests/test_users.py imports apps.api.routes.users: a real cross-module edge must survive selection.
    assert any(e.source == "module:tests" and e.target == "module:apps" for e in ctx.module_edges)
    # every edge's endpoints are modules that were actually kept in this context (no dangling references)
    assert all(e.source in module_ids and e.target in module_ids for e in ctx.module_edges)


def test_entry_points_are_attributed_to_their_real_module() -> None:
    ctx = _build()
    by_path = {p.path: p for p in ctx.entry_points}
    assert by_path["apps/api/main.py"].kind == "entrypoint"
    assert by_path["apps/api/main.py"].module_id == "module:apps"
    assert by_path["supabase/functions/hello/index.ts"].kind == "edge_function"
    assert by_path["supabase/functions/hello/index.ts"].module_id == "module:supabase/functions/hello"


def test_readme_and_manifests_are_included_and_truncated() -> None:
    settings = Settings_(architecture_diagram_readme_max_chars=10, architecture_diagram_manifest_max_chars=10)
    ctx = _build(
        docs=[ContextDoc("README.md", "# A long readme that goes on and on")],
        manifests=[ContextDoc("pyproject.toml", '[project]\nname = "demo-with-a-long-name"\n')],
        settings=settings,
    )
    assert ctx.readme_excerpt is not None and len(ctx.readme_excerpt) <= 10
    assert ctx.readme_excerpt.endswith("…")
    assert len(ctx.manifests) == 1 and ctx.manifests[0].content.endswith("…")


def test_excerpts_are_grouped_per_file_and_capped_per_file_budget() -> None:
    settings = Settings_(architecture_diagram_excerpt_max_chars_per_file=20)
    candidates = [
        ExcerptCandidate("apps/api/routes/users.py", "module:apps", "list_users", "function", "async def list_users(): return accounts.all_users()"),
        ExcerptCandidate("apps/api/routes/users.py", "module:apps", "router", "code", "router = APIRouter()"),
    ]
    ctx = _build(excerpts=candidates, settings=settings)
    assert len(ctx.excerpts) == 1
    assert len(ctx.excerpts[0].content) <= 20 + len("\n…\n")  # one join separator may still fit inside the cap check


def test_module_cap_warns_and_truncates() -> None:
    settings = Settings_(architecture_diagram_max_modules=2)
    ctx = _build(settings=settings)
    assert len(ctx.modules) == 2
    assert any("module(s) omitted" in w for w in ctx.warnings)


def test_entry_point_cap_warns_and_truncates() -> None:
    settings = Settings_(architecture_diagram_max_entry_points=1)
    ctx = _build(settings=settings)
    assert len(ctx.entry_points) == 1
    assert any("entry point(s) omitted" in w for w in ctx.warnings)


def test_every_module_carries_its_own_ranked_file_listing() -> None:
    """The fix for large-module blindness: a module's `files` lists real paths the model can ground a
    component in even when no code excerpt was pulled for them, ranked the same way excerpt selection
    ranks significance (role tier, then naming, then substance)."""
    ctx = _build()
    apps_module = next(m for m in ctx.modules if m.id == "module:apps")
    paths = [f.path for f in apps_module.files]
    assert "apps/api/routes/users.py" in paths  # a route: real, meaningful, must be listed
    assert "apps/api/jobs/email.py" in paths  # a job: likewise
    assert len(apps_module.files) <= len(paths) == len(set(paths))  # no duplicates
    # role-tagged files (route/model/job, tier 0) rank ahead of plain, unclassified source (tier 2)
    assert paths.index("apps/api/jobs/email.py") < paths.index("apps/api/services/accounts.py")


def test_module_file_listing_is_capped_per_module() -> None:
    settings = Settings_(architecture_diagram_max_files_per_module=1)
    ctx = _build(settings=settings)
    assert all(len(m.files) <= 1 for m in ctx.modules)
    apps_module = next(m for m in ctx.modules if m.id == "module:apps")
    assert apps_module.file_count > 1  # the module itself still reports its true size
    assert len(apps_module.files) == 1  # only the listing is capped


def test_overall_size_budget_is_enforced_by_dropping_excerpts_first() -> None:
    big_excerpt = "x" * 5_000
    candidates = [ExcerptCandidate("apps/api/routes/users.py", "module:apps", "list_users", "function", big_excerpt)]
    settings = Settings_(architecture_diagram_context_max_chars=500, architecture_diagram_excerpt_max_chars_per_file=5_000)
    ctx = _build(excerpts=candidates, settings=settings)
    assert ctx.excerpts == ()
    assert any("budget" in w for w in ctx.warnings)
    assert len(json.dumps(ctx.to_json_dict())) <= 500 or not (ctx.excerpts or ctx.manifests or ctx.entry_points)


# --- file_references: real file-to-file edges among already-shown files -----------------------------------------


def test_file_references_show_a_same_module_connection_the_module_graph_cannot() -> None:
    """`apps/api/routes/users.py` imports `apps/api/services/accounts.py`; both live in `module:apps`, so
    there is no module-level edge for this (a module never depends on itself) — `file_references` is the
    only place this real connection can reach the model at all."""
    ctx = _build()
    refs = {r.source: r.targets for r in ctx.file_references}
    assert "apps/api/services/accounts.py" in refs.get("apps/api/routes/users.py", ())


def test_file_references_are_restricted_to_files_already_shown() -> None:
    """A reference to a file the model was never told exists (trimmed out of its module's listing) would
    be unverifiable noise, not evidence, so it must not appear."""
    settings = Settings_(architecture_diagram_max_files_per_module=1)
    ctx = _build(settings=settings)
    shown = {f.path for m in ctx.modules for f in m.files}
    for ref in ctx.file_references:
        assert ref.source in shown
        for target in ref.targets:
            assert target in shown


def test_file_references_omitted_when_no_file_edges_are_passed() -> None:
    ctx = _build(with_file_edges=False)
    assert ctx.file_references == ()


def test_file_reference_source_cap_warns_and_truncates() -> None:
    settings = Settings_(architecture_diagram_max_file_reference_sources=1)
    ctx = _build(settings=settings)
    assert len(ctx.file_references) <= 1
    assert any("file reference" in w for w in ctx.warnings)


def test_file_reference_targets_are_capped_per_source() -> None:
    settings = Settings_(architecture_diagram_max_targets_per_file_reference=0)
    ctx = _build(settings=settings)
    assert all(r.targets == () for r in ctx.file_references)


def test_overall_size_budget_drops_file_references_before_excerpts() -> None:
    """File references are the newest, most experimental addition, so they give way first under budget
    pressure — every prior excerpt/manifest/entry-point trimming test keeps its exact old behavior."""
    big_excerpt = "x" * 2_000
    candidates = [ExcerptCandidate("apps/api/routes/users.py", "module:apps", "list_users", "function", big_excerpt)]
    unbounded = _build(excerpts=candidates, settings=Settings_(architecture_diagram_excerpt_max_chars_per_file=2_000))
    settings = Settings_(architecture_diagram_context_max_chars=8_400, architecture_diagram_excerpt_max_chars_per_file=2_000)
    ctx = _build(excerpts=candidates, settings=settings)
    assert len(ctx.file_references) < len(unbounded.file_references)  # some were dropped for the budget
    assert ctx.excerpts != ()  # the budget pressure was relieved by dropping references first, not the excerpt
    assert any("file reference" in w for w in ctx.warnings)


def test_no_hardcoded_project_shape_assumptions() -> None:
    """A single-package Python library with no web/db/queue signals at all must still produce a sane,
    non-empty context through the exact same code path as the web+api+db fixture."""
    library = {
        "pyproject.toml": '[project]\nname = "mathkit"\n',
        "mathkit/__init__.py": "from mathkit.core import add\n",
        "mathkit/core.py": "def add(a, b):\n    return a + b\n",
        "README.md": "# mathkit\n\nA tiny arithmetic library.\n",
    }
    ctx = _build(library, docs=[ContextDoc("README.md", library["README.md"])])
    assert ctx.modules  # some module structure was found
    assert ctx.commit_sha == SHA
    assert ctx.readme_excerpt == "# mathkit\n\nA tiny arithmetic library."
