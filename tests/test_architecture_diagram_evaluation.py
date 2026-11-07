"""Evaluation: old (module-edges only) vs new (module + file-level edges) relationship verification,
across four structurally different repository archetypes — not just Wanderlust/ClassLens. Pure logic
against hand-built fixtures; no database, no AI provider, no live model call.

Each fixture is deliberately small (a handful of files in ONE module) because that is exactly the case the
module-level graph structurally cannot verify: a module has no dependency edge to itself, so two
components whose code shares a directory could only ever be "inferred" before this change, however real
the import between them actually is. This file proves the fix closes that gap identically across a web
frontend+API split, a mobile app, a Python library, and an AI/ML pipeline — the quality problem this round
of work targets ("generic Frontend/Backend/Database" diagrams) is sharpest in exactly this shape of
repository, where rich, real, same-module relationships would otherwise all collapse to "inferred".
"""

from apps.api.config import Settings
from apps.api.schemas.architecture import GraphEdge, GraphNode
from apps.api.schemas.architecture_diagram import RawArchitectureDiagram
from apps.api.services.architecture.build import SnapshotFile, analyze_snapshot
from apps.api.services.architecture_diagram.context import select_context
from apps.api.services.architecture_diagram.validate import validate_diagram

SHA = "e" * 40


def Settings_(**values) -> Settings:  # noqa: N802
    return Settings(_env_file=None, **values)


def _analyze(files: dict[str, str]):
    snapshot = [SnapshotFile(path, "source", len(text.encode()), text) for path, text in sorted(files.items())]
    return analyze_snapshot(SHA, snapshot)


# --- the four archetypes: each a real import between two files that share one module ------------------------

WEB = {
    "package.json": '{"dependencies": {"react": "18.0.0", "express": "4.19.0"}}',
    "src/api/routes.ts": "import { getUsers } from './controller'\n\nexport const route = getUsers\n",
    "src/api/controller.ts": "export function getUsers() { return [] }\n",
    "src/ui/App.tsx": "import { Header } from './Header'\n\nexport function App() { return Header }\n",
    "src/ui/Header.tsx": "export function Header() { return null }\n",
}
WEB_RELATIONSHIP = ("src/api/routes.ts", "src/api/controller.ts")

MOBILE = {
    "package.json": '{"dependencies": {"react-native": "0.74.0", "expo": "51.0.0"}}',
    "src/features/capture/captureSession.ts": (
        "import { uploadPhoto } from '../../services/storage'\n\nexport function capture() { uploadPhoto() }\n"
    ),
    "src/services/storage.ts": "export function uploadPhoto() {}\n",
    "src/screens/HomeScreen.tsx": (
        "import { capture } from '../features/capture/captureSession'\n\nexport function HomeScreen() { capture() }\n"
    ),
}
MOBILE_RELATIONSHIP = ("src/features/capture/captureSession.ts", "src/services/storage.ts")

PYTHON_LIBRARY = {
    "pyproject.toml": '[project]\nname = "pkg"\n',
    "pkg/__init__.py": "from pkg.signer import Signer\n",
    "pkg/signer.py": "class Signer:\n    pass\n",
    "pkg/serializer.py": "from pkg.signer import Signer\n\n\nclass Serializer:\n    pass\n",
}
PYTHON_LIBRARY_RELATIONSHIP = ("pkg/serializer.py", "pkg/signer.py")

AI_ML_PIPELINE = {
    "pyproject.toml": '[project]\nname = "pipeline"\n',
    "pipeline/__init__.py": "",
    "pipeline/ingest.py": "def load():\n    pass\n",
    "pipeline/embed.py": "from pipeline.ingest import load\n\n\ndef embed():\n    load()\n",
    "pipeline/retrieve.py": "from pipeline.embed import embed\n\n\ndef retrieve():\n    embed()\n",
}
AI_ML_PIPELINE_RELATIONSHIP = ("pipeline/retrieve.py", "pipeline/embed.py")

ARCHETYPES = {
    "web": (WEB, WEB_RELATIONSHIP),
    "mobile": (MOBILE, MOBILE_RELATIONSHIP),
    "python_library": (PYTHON_LIBRARY, PYTHON_LIBRARY_RELATIONSHIP),
    "ai_ml_pipeline": (AI_ML_PIPELINE, AI_ML_PIPELINE_RELATIONSHIP),
}


def _seal(raw: dict, graph, known_paths, module_of, file_edges=()):
    return validate_diagram(
        RawArchitectureDiagram.model_validate(raw), graph=graph, known_paths=known_paths, module_of=module_of,
        file_edges=file_edges, commit_sha=SHA, settings=Settings_(),
    )


def _diagram_for(source_path: str, target_path: str) -> dict:
    return {
        "components": [
            {"id": "c1", "label": "Source Component", "description": "x", "kind": "service", "source_paths": [source_path]},
            {"id": "c2", "label": "Target Component", "description": "x", "kind": "service", "source_paths": [target_path]},
        ],
        "relationships": [{"id": "r1", "source": "c1", "target": "c2", "label": "calls", "kind": "imports"}],
    }


def test_same_module_relationships_move_from_inferred_to_verified_on_every_archetype() -> None:
    """The headline comparison: for each archetype, the OLD behavior (module edges only) leaves a real,
    same-module relationship "inferred"; the NEW behavior (module + file edges) verifies it with real
    evidence pointing at the actual import line. Not just Wanderlust/ClassLens-shaped repositories."""
    for name, (files, (source_path, target_path)) in ARCHETYPES.items():
        result = _analyze(files)
        graph = result.graph
        known_paths = {n["path"] for n in result.detail["nodes"]}
        module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
        file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
        raw = _diagram_for(source_path, target_path)

        old = _seal(raw, graph, known_paths, module_of)  # old: no file_edges
        new = _seal(raw, graph, known_paths, module_of, file_edges=file_edges)  # new

        assert old.relationships[0].confidence == "inferred", f"{name}: old behavior should be inferred"
        assert old.relationships[0].evidence == [], f"{name}: old behavior should have no evidence"
        new_rel = new.relationships[0]
        assert new_rel.confidence == "verified", f"{name}: new behavior should verify a real same-module import"
        assert new_rel.evidence, f"{name}: verified relationship must carry real evidence"
        assert new_rel.evidence[0].path == source_path, f"{name}: evidence should point at the importing file"


def test_file_references_surface_the_same_connection_in_every_archetype_s_context() -> None:
    """The model-visible half of the fix: `select_context`'s `file_references` must show this same
    connection for every archetype, not only the ones this feature happened to be designed against."""
    for name, (files, (source_path, target_path)) in ARCHETYPES.items():
        result = _analyze(files)
        graph = result.graph
        module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
        file_nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
        file_edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
        line_counts = {path: text.count("\n") for path, text in files.items()}

        ctx = select_context(
            graph=graph, repo_full_name=f"octo/{name}", file_nodes=file_nodes, module_of=module_of,
            line_counts=line_counts, docs=(), manifests=(), excerpt_candidates=(), file_edges=file_edges,
            settings=Settings_(),
        )
        refs = {r.source: r.targets for r in ctx.file_references}
        assert target_path in refs.get(source_path, ()), f"{name}: file_references should show {source_path} -> {target_path}"


def test_relevant_file_coverage_every_real_file_is_grounded_in_some_module_s_listing() -> None:
    """Coverage check, independent of excerpt budget: every real source file in each archetype must be
    reachable as a `source_paths` candidate via some module's `files` listing, not just the 1-2 files that
    happened to get a code excerpt — this is what lets the model name a feature file it was never shown
    the body of, instead of collapsing it into an unnamed, generic bucket."""
    for name, (files, _relationship) in ARCHETYPES.items():
        result = _analyze(files)
        graph = result.graph
        module_of = {p[5:] if p.startswith("file:") else p: m for p, m in result.detail["module_of"].items()}
        file_nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
        line_counts = {path: text.count("\n") for path, text in files.items()}
        real_source_files = {path for path in files if path not in ("package.json", "pyproject.toml")}

        ctx = select_context(
            graph=graph, repo_full_name=f"octo/{name}", file_nodes=file_nodes, module_of=module_of,
            line_counts=line_counts, docs=(), manifests=(), excerpt_candidates=(), settings=Settings_(),
        )
        grounded = {f.path for m in ctx.modules for f in m.files}
        missing = real_source_files - grounded
        assert not missing, f"{name}: {missing} are not groundable in any module's file listing"
