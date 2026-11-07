"""Representative-file selection (`services/architecture_diagram/context.py::select_representative_files`):
the Phase 2.5 fix. Pure logic, no database, no AI provider, built on the real deterministic graph from
`tests/architecture_support.py::analyze` so role detection and import resolution are the genuine thing, not
a stand-in.

The scenario this file exists to cover: a heavily-imported, genuinely-named utility must NOT beat a
barely-imported but substantial, domain-named feature file for a module's representative-excerpt slot —
GitDiagram's ClassLens example (`auth.ts`, `purchases.ts`, `captureSession.ts`) is exactly this shape.
"""

from apps.api.config import Settings
from apps.api.schemas.architecture import GraphNode
from apps.api.services.architecture_diagram.context import _significance_key, select_representative_files
from tests.architecture_support import analyze


def Settings_(**values) -> Settings:  # noqa: N802
    return Settings(_env_file=None, **values)


def _rank(files: dict[str, str], **settings_overrides) -> tuple[list[str], dict]:
    """Run the real deterministic analyzer, then `select_representative_files`. Returns (picked paths, detail)."""
    result = analyze(files)
    graph = result.graph
    detail = result.detail
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in detail["module_of"].items()}
    file_nodes = [GraphNode.model_validate(n) for n in detail["nodes"]]
    line_counts = {path: text.count("\n") for path, text in files.items()}
    picked = select_representative_files(graph, file_nodes, module_of, line_counts, Settings_(**settings_overrides))
    return picked, detail


# --- the central case: a hub utility vs. a leaf feature file ---------------------------------------------

UTIL = "export function cn(...xs) {\n  return xs.filter(Boolean).join(' ')\n}\n"
PURCHASES = (
    "import { cn } from \"./utils\"\n\n"
    "export function createOrder(id) {\n  return cn('order', id)\n}\n\n"
    "export function refundOrder(id) {\n  return cn('refund', id)\n}\n\n"
    "export function listOrders(userId) {\n  return cn('list', userId)\n}\n\n"
    "export function applyDiscount(id, pct) {\n  return cn('discount', id, pct)\n}\n"
)
HUB_FIXTURE = {
    "app/utils.ts": UTIL,
    "app/purchases.ts": PURCHASES,
    "consumers/a.ts": "import { cn } from \"../app/utils\"\nexport const a = cn('a')\n",
    "consumers/b.ts": "import { cn } from \"../app/utils\"\nexport const b = cn('b')\n",
    "consumers/c.ts": "import { cn } from \"../app/utils\"\nexport const c = cn('c')\n",
}


def test_feature_file_beats_a_heavily_imported_generic_utility() -> None:
    """`utils.ts` is imported by 4 files (`purchases.ts` + 3 consumers); `purchases.ts` is imported by
    nobody. Both are plain, unclassified "source" (no framework role matched), sharing one module. Picking
    by import degree would choose `utils.ts`; picking architectural significance must choose `purchases.ts`."""
    picked, detail = _rank(HUB_FIXTURE, architecture_diagram_max_excerpts_per_bucket=1, architecture_diagram_max_excerpt_files=10)
    nodes_by_path = {n["path"]: n for n in detail["nodes"]}
    assert nodes_by_path["app/utils.ts"]["metrics"]["incoming"] == 4
    assert nodes_by_path["app/purchases.ts"]["metrics"]["incoming"] == 0
    assert "app/purchases.ts" in picked
    assert "app/utils.ts" not in picked


def test_substance_alone_breaks_a_tie_when_naming_is_equally_generic() -> None:
    """Isolate the line-count signal from the naming signal: neither file has a generic name, so only
    "how much is actually implemented" (line count) should decide, not fan-in."""
    fixture = {
        "app/shortfeature.ts": "import { cn } from \"./utils\"\nexport function run() { return cn('x') }\n",
        "app/bigfeature.ts": (
            "export function stepOne() { return 1 }\n\n"
            "export function stepTwo() { return 2 }\n\n"
            "export function stepThree() { return 3 }\n\n"
            "export function stepFour() { return 4 }\n"
        ),
        "app/utils.ts": UTIL,
        "consumers/a.ts": "import { cn } from \"../app/utils\"\nimport { run } from \"../app/shortfeature\"\nexport const a = run()\n",
    }
    picked, detail = _rank(fixture, architecture_diagram_max_excerpts_per_bucket=1, architecture_diagram_max_excerpt_files=10)
    nodes_by_path = {n["path"]: n for n in detail["nodes"]}
    assert nodes_by_path["app/shortfeature.ts"]["metrics"]["incoming"] == 1  # imported
    assert nodes_by_path["app/bigfeature.ts"]["metrics"]["incoming"] == 0  # imported by nobody
    assert "app/bigfeature.ts" in picked  # wins purely on substance


# --- role tier still outranks a generic-tier file, however big -------------------------------------------


def test_a_real_entry_point_role_still_outranks_plain_source_regardless_of_size() -> None:
    """A role actually detected by `services/architecture/signals.py` (here: a FastAPI route) is a stronger
    signal than anything line-count-based, so it must win round one even against a much longer plain file."""
    long_source = "\n\n".join(f"def helper_{i}():\n    return {i}\n" for i in range(40))
    fixture = {
        "pyproject.toml": '[project]\nname = "demo"\ndependencies = ["fastapi"]\n',
        "svc/routes.py": "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get(\"/x\")\ndef x():\n    return 1\n",
        "svc/helpers.py": long_source,
    }
    picked, detail = _rank(fixture, architecture_diagram_max_excerpts_per_bucket=1, architecture_diagram_max_excerpt_files=10)
    nodes_by_path = {n["path"]: n for n in detail["nodes"]}
    assert nodes_by_path["svc/routes.py"]["kind"] == "route"
    assert nodes_by_path["svc/helpers.py"]["kind"] == "source"
    assert "svc/routes.py" in picked
    assert "svc/helpers.py" not in picked


# --- breadth first across modules, then depth -------------------------------------------------------------


def test_every_module_gets_one_file_before_any_module_gets_a_second() -> None:
    fixture = {
        "small/only.py": "def f():\n    return 1\n",
        "big/one.py": "def a():\n    return 1\n",
        "big/two.py": "def b():\n    return 2\n",
        "big/three.py": "def c():\n    return 3\n",
    }
    # cap tight enough that depth-first would starve the small module entirely
    picked, _ = _rank(fixture, architecture_diagram_max_excerpts_per_bucket=2, architecture_diagram_max_excerpt_files=2)
    assert "small/only.py" in picked  # the one-file module was not starved by the bigger module


def test_per_module_depth_cap_is_respected() -> None:
    fixture = {f"big/f{i}.py": f"def f{i}():\n    return {i}\n" for i in range(5)}
    picked, _ = _rank(fixture, architecture_diagram_max_excerpts_per_bucket=2, architecture_diagram_max_excerpt_files=10)
    assert len(picked) == 2  # only one module, capped at 2 files deep


# --- the Phase 4 fix: a large unsplit module's own sub-areas each get a turn --------------------------------

# A handful of single-file top-level directories push the deterministic module count to 6 before "backend"'s
# turn in the splitting loop comes up, so — exactly like the real Wanderlust repository — "backend" stays
# ONE module despite having three clearly distinct internal areas (controllers/, models/, routes/). The
# controller files are made much longer than the model/route files: under the old flat per-module ranking
# (rank every file in the module together, take the top N) they would have won every excerpt slot on their
# own, and models/routes would never be shown to the AI at all.
_PAD = {f"{letter}/one.py": "x = 1\n" for letter in "abcde"}
BACKEND_FIXTURE = {
    **_PAD,
    "backend/controllers/bookings.js": "\n".join(f"export function f{i}() {{ return {i} }}" for i in range(20)),
    "backend/controllers/listings.js": "\n".join(f"export function g{i}() {{ return {i} }}" for i in range(20)),
    "backend/models/booking.js": "export class Booking {}\n",
    "backend/models/listing.js": "export class Listing {}\n",
    "backend/routes/index.js": "export const router = {}\n",
}


def test_a_large_unsplit_modules_distinct_subdirectories_each_get_a_representative_file() -> None:
    picked, detail = _rank(BACKEND_FIXTURE, architecture_diagram_max_excerpts_per_bucket=1, architecture_diagram_max_excerpt_files=10)
    module_of = {p[5:] if p.startswith("file:") else p: m for p, m in detail["module_of"].items()}
    # sanity check the fixture actually produced one unsplit "backend" module (the scenario this test needs)
    assert len({m for p, m in module_of.items() if p.startswith("backend/")}) == 1

    subdirs_represented = {path.split("/")[1] for path in picked if path.startswith("backend/")}
    assert subdirs_represented == {"controllers", "models", "routes"}
    # the two controller files must NOT have been allowed to fill every slot between them
    assert not {"backend/controllers/bookings.js", "backend/controllers/listings.js"}.issubset(picked)


def test_old_flat_per_module_ranking_would_have_starved_models_and_routes() -> None:
    """Documents the bug this fix addresses: ranking the whole module's files together (ignoring
    subdirectory) picks the two longest files — both controllers — and the shorter, equally real
    models/routes files never surface at all."""
    _, detail = _rank(BACKEND_FIXTURE)
    line_counts = {path: text.count("\n") for path, text in BACKEND_FIXTURE.items()}
    nodes = [GraphNode.model_validate(n) for n in detail["nodes"] if n["path"].startswith("backend/")]
    flat_top_2 = sorted(nodes, key=lambda f: _significance_key(f, line_counts.get(f.path, 0)))[:2]
    assert {f.path for f in flat_top_2} == {"backend/controllers/bookings.js", "backend/controllers/listings.js"}


# --- no hardcoded project-shape assumptions -----------------------------------------------------------------


def test_selection_logic_is_identical_for_a_plain_python_library() -> None:
    """The same ranking (role tier, then naming, then substance) must apply without any web/JS-specific
    branch: a pure Python library with no detected role at all should still prefer the substantial,
    domain-named file over a trivial, generically-named one."""
    fixture = {
        "pyproject.toml": '[project]\nname = "mathkit"\n',
        "mathkit/utils.py": "def helper():\n    return 1\n",
        "mathkit/statistics.py": (
            "def mean(xs):\n    return sum(xs) / len(xs)\n\n\n"
            "def median(xs):\n    return sorted(xs)[len(xs) // 2]\n\n\n"
            "def variance(xs):\n    m = mean(xs)\n    return sum((x - m) ** 2 for x in xs) / len(xs)\n"
        ),
        "mathkit/consumer_a.py": "from mathkit.utils import helper\nhelper()\n",
        "mathkit/consumer_b.py": "from mathkit.utils import helper\nhelper()\n",
    }
    picked, detail = _rank(fixture, architecture_diagram_max_excerpts_per_bucket=1, architecture_diagram_max_excerpt_files=10)
    nodes_by_path = {n["path"]: n for n in detail["nodes"]}
    assert nodes_by_path["mathkit/utils.py"]["metrics"]["incoming"] == 2
    assert nodes_by_path["mathkit/statistics.py"]["metrics"]["incoming"] == 0
    assert "mathkit/statistics.py" in picked
    assert "mathkit/utils.py" not in picked
