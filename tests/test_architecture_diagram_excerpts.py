"""Call-site excerpts (`services/architecture_diagram/excerpt.py`) over five repository archetypes: web, mobile,
Python simulation/data, AI/ML and a small library. Pure logic against hand-built fixtures; no database, no AI
provider. Import line ranges come from the real Tree-sitter analyzer's file-edge evidence, as in production."""

import re

import pytest

from apps.api.config import Settings
from apps.api.schemas.architecture import GraphEdge, GraphNode
from apps.api.services.architecture.build import SnapshotFile, analyze_snapshot
from apps.api.services.architecture_diagram.context import ExcerptCandidate, select_context
from apps.api.services.architecture_diagram.excerpt import call_site_excerpt
from apps.api.services.architecture_diagram.prompt import SYSTEM_PROMPT

SHA = "a" * 40
PAD = "\n".join(f"// unrelated filler line {i}" for i in range(60))


def _analyze(files):
    return analyze_snapshot(SHA, [SnapshotFile(p, "source", len(t.encode()), t) for p, t in sorted(files.items())])


def _ranges(result, path):
    out = []
    for e in result.detail["edges"]:
        if e["source"] == f"file:{path}":
            out.extend((ev["start_line"], ev["end_line"]) for ev in e["evidence"])
    return sorted(set(out))


WEB = {
    "backend/models/listing.js": "module.exports = {}\n",
    "backend/controllers/listings.js": (
        "const Listing = require('../models/listing')\n" + PAD + "\n\n"
        "async function index(req, res) {\n  const rows = await Listing.find({})\n  res.json(rows)\n}\n\n" + PAD + "\n"
        "module.exports = { index }\n"
    ),
}
MOBILE = {
    "src/services/storage.ts": "export function uploadPhoto(p: string) {}\n",
    "src/features/capture.ts": (
        "import { uploadPhoto } from '../services/storage'\n" + PAD + "\n\n"
        "export async function captureSession(photo: string) {\n  const id = Date.now()\n  await uploadPhoto(photo)\n  return id\n}\n\n" + PAD + "\n"
    ),
}
SIMULATION = {
    "src/cleaning/cleaning.py": "def clean(df):\n    return df\n",
    "src/data_generation/merge_data.py": (
        "import pandas as pd\nfrom src.cleaning.cleaning import clean\n" + "\n".join(f"# filler {i}" for i in range(80)) + "\n\n"
        "def main():\n    df = pd.read_csv('suppliers.csv')\n    out = clean(df)\n    out.to_csv('merged.csv')\n\n\n"
        "if __name__ == '__main__':\n    main()\n"
    ),
}
ML = {
    "ml/features.py": "def build(x):\n    return x\n",
    "ml/train.py": (
        "from ml.features import build\n" + "\n".join(f"# filler {i}" for i in range(80)) + "\n\n"
        "def train(data):\n    x = build(data)\n    model = fit(x)\n    return model\n\n\n"
        "def fit(x):\n    return x\n" + "\n".join(f"# tail {i}" for i in range(60)) + "\n"
    ),
}
LIBRARY = {
    "pkg/signer.py": "class Signer:\n    pass\n",
    "pkg/serializer.py": (
        "from pkg.signer import Signer\n" + "\n".join(f"# filler {i}" for i in range(80)) + "\n\n"
        "class Serializer:\n    def dumps(self, obj):\n        return Signer().sign(obj)\n\n" + "\n".join(f"# tail {i}" for i in range(60)) + "\n"
    ),
}
CASES = {
    "web": (WEB, "backend/controllers/listings.js", "Listing.find", "async function index", "require('../models/listing')"),
    "mobile": (MOBILE, "src/features/capture.ts", "uploadPhoto(photo)", "export async function captureSession", "import { uploadPhoto }"),
    "simulation": (SIMULATION, "src/data_generation/merge_data.py", "clean(df)", "def main", "from src.cleaning.cleaning import clean"),
    "ml": (ML, "ml/train.py", "build(data)", "def train", "from ml.features import build"),
    "library": (LIBRARY, "pkg/serializer.py", "Signer().sign", "class Serializer", "from pkg.signer import Signer"),
}


def _excerpt(files, path, budget=700):
    return call_site_excerpt(files[path], budget, import_line_ranges=_ranges(_analyze(files), path))


@pytest.mark.parametrize("name", CASES)
def test_excerpt_keeps_the_call_its_enclosing_definition_and_the_binding_import(name: str) -> None:
    files, path, call, definition, binding = CASES[name]
    text = _excerpt(files, path)
    assert call in text
    assert definition in text, "the call must be shown inside the function/class that makes it"
    assert binding in text, "the import that binds the called name must be preserved"


@pytest.mark.parametrize("name", CASES)
def test_every_excerpt_line_is_verbatim_and_correctly_numbered(name: str) -> None:
    files, path, *_ = CASES[name]
    source = files[path].split("\n")
    text = _excerpt(files, path)
    shown = 0
    for line in text.split("\n"):
        if line.startswith("⋯"):
            continue
        number, _, content = line.partition("| ")
        assert source[int(number) - 1] == content, f"{path}:{number} was altered"
        shown += 1
    assert shown > 0


@pytest.mark.parametrize("name", CASES)
def test_excerpt_is_bounded_deterministic_and_marks_omissions(name: str) -> None:
    files, path, *_ = CASES[name]
    first, second = _excerpt(files, path, 500), _excerpt(files, path, 500)
    assert first == second
    assert len(first) <= 500
    assert "omitted]" in first


def test_a_file_that_fits_the_budget_is_returned_whole() -> None:
    assert call_site_excerpt("a = 1\nb = 2\n", 600) == "1| a = 1\n2| b = 2\n3| "


def test_unrelated_filler_is_not_preferred_over_the_call_site() -> None:
    text = _excerpt(WEB, "backend/controllers/listings.js", 400)
    assert "unrelated filler" not in text.replace("// unrelated filler line 0", "") or "Listing.find" in text
    assert "Listing.find" in text


def test_without_import_evidence_it_still_returns_bounded_real_lines() -> None:
    text = call_site_excerpt(SIMULATION["src/data_generation/merge_data.py"], 400)
    assert len(text) <= 400 and "def main" in text
    assert "relationship" not in text.lower()


def test_excerpt_text_carries_no_inferred_labels() -> None:
    text = _excerpt(*[CASES["web"][0], CASES["web"][1]])
    assert not re.search(r"\b(?:calls|http request|database)\b", text.replace(CASES["web"][0][CASES["web"][1]], ""), re.I)


def test_select_context_uses_call_site_excerpts_when_source_is_available() -> None:
    files, path, call, *_ = CASES["web"]
    result = _analyze(files)
    module_of = {p[5:]: m for p, m in result.detail["module_of"].items()}
    nodes = [GraphNode.model_validate(n) for n in result.detail["nodes"]]
    edges = tuple(GraphEdge.model_validate(e) for e in result.detail["edges"])
    chunk = ExcerptCandidate(path, module_of[path], "head", "function", "const Listing = require('../models/listing')")
    kwargs = dict(
        graph=result.graph, repo_full_name="o/r", file_nodes=nodes, module_of=module_of,
        line_counts={p: t.count("\n") for p, t in files.items()}, docs=(), manifests=(),
        excerpt_candidates=[chunk], file_edges=edges, settings=Settings(_env_file=None),
    )
    with_source = select_context(**kwargs, source_texts={path: files[path]})
    without = select_context(**kwargs)
    assert call in with_source.excerpts[0].content and with_source.excerpts[0].symbol is None
    assert call not in without.excerpts[0].content  # fallback path (stored head chunks) is unchanged


def test_prompt_asks_for_actor_entry_flow_state_and_forbids_invented_pipelines() -> None:
    for phrase in ("principal actor", "entry points", "main workflow", "outputs", "not a pipeline", "never to make the graph"):
        assert phrase in SYSTEM_PROMPT
