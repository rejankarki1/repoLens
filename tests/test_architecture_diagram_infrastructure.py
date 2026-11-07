"""Infrastructure detection and its warn-only coverage check (`infrastructure.py`, `validate.py`). Mocked
fixtures only: no database, no AI provider. Detection uses the real Tree-sitter analyzer's technologies and
externals, as in production; the check never adds a component or relationship."""

from apps.api.config import Settings
from apps.api.schemas.architecture import FileExternal
from apps.api.schemas.architecture_diagram import RawArchitectureDiagram
from apps.api.services.architecture.build import SnapshotFile, analyze_snapshot
from apps.api.services.architecture_diagram.infrastructure import detect_infrastructure
from apps.api.services.architecture_diagram.validate import validate_diagram

SHA = "b" * 40


def _analyze(files):
    return analyze_snapshot(SHA, [SnapshotFile(p, "source", len(t.encode()), t) for p, t in sorted(files.items())])


def _infra(files):
    r = _analyze(files)
    externals = [FileExternal.model_validate(x) for x in r.detail["externals"]]
    texts = {p: t for p, t in files.items() if p.endswith((".js", ".ts", ".py"))}
    return r, detect_infrastructure(r.graph.technologies, r.graph.external_dependencies, externals, texts)


def _names(items, strength=None):
    return {i.name for i in items if strength is None or i.strength == strength}


MONGO = {
    "package.json": '{"dependencies": {"express": "4.19.0", "mongoose": "8.0.0", "cloudinary": "1.0.0", "mapbox-gl": "3.0.0"}}',
    "backend/config/db.js": "const mongoose = require('mongoose')\nmodule.exports = () => mongoose.connect(process.env.URL)\n",
    "backend/models/listing.js": "const mongoose = require('mongoose')\nmodule.exports = mongoose.model('L', new mongoose.Schema({}))\n",
    "backend/cloud.js": "const cloudinary = require('cloudinary')\ncloudinary.v2.config({})\nmodule.exports = cloudinary\n",
    "client/map.js": "import mapboxgl from 'mapbox-gl'\nexport const map = (el) => new mapboxgl.Map({ container: el })\n",
    "backend/server.js": "const express = require('express')\nrequire('./config/db')()\nexpress().listen(3000)\n",
}
SUPABASE = {
    "package.json": '{"dependencies": {"@supabase/supabase-js": "2.0.0"}}',
    "supabase/functions/process/index.ts": (
        "import { createClient } from 'npm:@supabase/supabase-js@2'\nconst s = createClient('u', 'k')\n"
        "const f = await s.storage.from('captures').download('a')\nconst r = await s.from('jobs').select('*')\n"
        "await fetch('https://generativelanguage.googleapis.com/v1beta/models/x:generateContent')\n"
    ),
}
LIBRARY = {  # no database, storage, AI or queue at all
    "pyproject.toml": '[project]\nname = "kit"\ndependencies = ["numpy"]\n',
    "kit/core.py": "import numpy as np\n\ndef add(a, b):\n    return np.add(a, b)\n",
}
SCRIPTS = {  # independent scripts: nothing connects them, and nothing may be invented
    "pyproject.toml": '[project]\nname = "s"\ndependencies = ["pandas"]\n',
    "a.py": "import pandas as pd\nprint(pd.__name__)\n",
    "b.py": "import pandas as pd\nprint(pd.__version__)\n",
}


def test_mongodb_is_runtime_usage_and_cloudinary_too() -> None:
    _, items = _infra(MONGO)
    assert {"MongoDB", "Cloudinary"} <= _names(items, "runtime_usage")
    mongo = next(i for i in items if i.name == "MongoDB")
    assert mongo.evidence[0].path == "backend/config/db.js" and "mongoose.connect" in mongo.evidence[0].text


def test_supabase_database_storage_and_ai_provider_are_found_from_sdk_calls() -> None:
    _, items = _infra(SUPABASE)
    assert {"Supabase Database", "Supabase Storage", "Google Gemini"} <= _names(items, "runtime_usage")


def test_a_package_known_only_by_manifest_or_import_is_weak_evidence() -> None:
    _, items = _infra(MONGO)
    mapbox = [i for i in items if i.category == "external service"]
    assert mapbox and all(i.strength in {"declared", "imported"} for i in mapbox)


def test_a_repository_without_infrastructure_reports_none() -> None:
    _, items = _infra(LIBRARY)
    assert items == ()
    r = _analyze(LIBRARY)
    diagram = validate_diagram(
        RawArchitectureDiagram.model_validate({"components": [{"id": "c", "label": "Core", "kind": "library", "source_paths": ["kit/core.py"]}]}),
        graph=r.graph, known_paths={n["path"] for n in r.detail["nodes"]}, module_of={}, commit_sha=SHA,
        infrastructure=items, settings=Settings(_env_file=None),
    )
    assert not any("infrastructure" in w for w in diagram.warnings)


def test_independent_scripts_stay_unconnected_and_unwarned() -> None:
    r, items = _infra(SCRIPTS)
    comps = [{"id": "a", "label": "Script A", "kind": "cli", "source_paths": ["a.py"]}, {"id": "b", "label": "Script B", "kind": "cli", "source_paths": ["b.py"]}]
    diagram = validate_diagram(
        RawArchitectureDiagram.model_validate({"components": comps}), graph=r.graph,
        known_paths={n["path"] for n in r.detail["nodes"]}, module_of={}, commit_sha=SHA, infrastructure=items,
        settings=Settings(_env_file=None),
    )
    assert diagram.relationships == [] and not any("infrastructure" in w for w in diagram.warnings)


def _seal(files, comps, items):
    r = _analyze(files)
    return validate_diagram(
        RawArchitectureDiagram.model_validate({"components": comps}), graph=r.graph,
        known_paths={n["path"] for n in r.detail["nodes"]}, module_of={}, commit_sha=SHA, infrastructure=items,
        settings=Settings(_env_file=None),
    )


def test_missing_strong_infrastructure_warns_without_inventing_anything() -> None:
    _, items = _infra(MONGO)
    diagram = _seal(MONGO, [{"id": "c", "label": "Server", "kind": "service", "source_paths": ["backend/server.js"]}], items)
    strong = [w for w in diagram.warnings if "runtime-usage evidence" in w]
    assert strong and "MongoDB" in strong[0] and "Cloudinary" in strong[0]
    assert any("weaker evidence" in w for w in diagram.warnings)  # weak hints are reported separately
    assert [c.id for c in diagram.components] == ["c"] and diagram.relationships == []


def test_represented_infrastructure_is_not_flagged() -> None:
    _, items = _infra(MONGO)
    comps = [
        {"id": "m", "label": "MongoDB", "description": "Document database", "kind": "database"},
        {"id": "c", "label": "Cloudinary", "description": "Image storage", "kind": "external_service"},
    ]
    diagram = _seal(MONGO, comps, items)
    assert not any("runtime-usage evidence" in w for w in diagram.warnings)


def test_a_file_that_merely_imports_the_sdk_does_not_count_as_representing_it() -> None:
    _, items = _infra(MONGO)
    comps = [{"id": "d", "label": "Listing model", "kind": "store", "source_paths": ["backend/config/db.js", "backend/cloud.js"]}]
    assert any("runtime-usage evidence" in w for w in _seal(MONGO, comps, items).warnings)


def test_check_is_a_no_op_without_infrastructure() -> None:
    diagram = _seal(MONGO, [{"id": "c", "label": "Server", "kind": "service"}], ())
    assert not any("infrastructure" in w for w in diagram.warnings)
