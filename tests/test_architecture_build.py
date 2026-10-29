"""Graph construction on whole repositories: nodes, edges, evidence, roles, modules, cycles and edge cases."""

import pytest

from apps.api.services.architecture.build import ANALYZER_VERSION, group_modules, strongly_connected
from tests.architecture_support import REALISTIC, SHA, analyze, file_edges, file_nodes


@pytest.fixture(scope="module")
def realistic():
    return analyze(REALISTIC)


# --- the realistic repository -------------------------------------------------------------------------------------


def test_file_edges_are_exactly_the_static_imports(realistic) -> None:
    edges = {k: (v["resolution"], v["confidence"]) for k, v in file_edges(realistic).items()}
    assert edges == {
        ("apps/api/main.py", "apps/api/routes/users.py", "imports"): ("submodule", "verified"),
        ("apps/api/routes/users.py", "apps/api/schemas.py", "imports"): ("absolute", "verified"),
        ("apps/api/routes/users.py", "apps/api/services/accounts.py", "imports"): ("submodule", "verified"),
        ("apps/api/services/accounts.py", "apps/api/jobs/email.py", "imports"): ("absolute", "verified"),
        ("apps/api/services/accounts.py", "apps/api/models.py", "imports"): ("absolute", "verified"),
        ("apps/api/services/accounts.py", "apps/api/schemas.py", "type_only"): ("absolute", "verified"),
        ("apps/api/jobs/email.py", "apps/api/services/accounts.py", "lazy_import"): ("submodule", "verified"),
        ("tests/test_users.py", "apps/api/routes/users.py", "imports"): ("absolute", "verified"),
        ("apps/web/app/page.tsx", "apps/web/components/header.tsx", "imports"): ("alias", "verified"),
        ("apps/web/app/page.tsx", "apps/web/lib/api.ts", "imports"): ("alias", "verified"),
        ("apps/web/components/header.tsx", "apps/web/lib/api.ts", "imports"): ("relative", "verified"),
        ("apps/web/components/header.tsx", "apps/web/types/index.ts", "type_only"): ("alias", "verified"),
        ("apps/web/lib/api.ts", "apps/web/types/index.ts", "imports"): ("alias", "verified"),
        ("apps/web/lib/api.ts", "apps/web/lib/http.ts", "reexports"): ("relative", "verified"),
        ("supabase/functions/hello/index.ts", "supabase/functions/_shared/cors.ts", "imports"): ("relative", "verified"),
    }


def test_every_edge_has_evidence_that_matches_the_source(realistic) -> None:
    for (source, target, relationship), edge in file_edges(realistic).items():
        assert edge["evidence"], (source, target)
        for ev in edge["evidence"]:
            lines = REALISTIC[ev["path"]].split("\n")
            assert ev["path"] == source and 1 <= ev["start_line"] <= ev["end_line"] <= len(lines)
            statement = " ".join(lines[ev["start_line"] - 1: ev["end_line"]])
            assert "import" in statement or "export" in statement or "require" in statement
        assert edge["basis"] == "static" and edge["count"] >= len(edge["evidence"]) >= 1


def test_graph_is_internally_consistent(realistic) -> None:
    graph = realistic.graph
    node_ids = {n.id for n in graph.nodes}
    assert len(node_ids) == len(graph.nodes)
    file_ids = {n["id"] for n in realistic.detail["nodes"]}
    assert all(e["source"] in file_ids and e["target"] in file_ids for e in realistic.detail["edges"])
    assert all(e.source in node_ids and e.target in node_ids and e.source != e.target for e in graph.edges)
    assert all(e.basis == "aggregated" and e.relationship == "depends_on" and e.evidence for e in graph.edges)
    assert sum(n.file_count for n in graph.nodes) == graph.stats.code_files == len(file_ids)
    assert {f for n in graph.nodes for f in n.files} == {p for p in REALISTIC if p.endswith((".py", ".ts", ".tsx"))}
    assert graph.commit_sha == SHA and graph.analyzer_version == ANALYZER_VERSION


def test_roles_come_with_the_evidence_that_justifies_them(realistic) -> None:
    nodes = file_nodes(realistic)
    expectations = {
        "apps/api/routes/users.py": "route", "apps/api/models.py": "model", "apps/api/jobs/email.py": "job",
        "database/migrations/versions/0001_init.py": "migration", "tests/test_users.py": "test", "apps/web/app/page.tsx": "page",
        "apps/web/components/header.tsx": "component", "supabase/functions/hello/index.ts": "edge_function", "apps/api/main.py": "entrypoint",
    }
    for path, role in expectations.items():
        assert nodes[path]["kind"] == role, (path, nodes[path]["roles"])
        assert nodes[path]["evidence"], path
    route_evidence = nodes["apps/api/routes/users.py"]["evidence"]
    assert any(e["kind"] == "import" and "fastapi" in e["detail"] for e in route_evidence)
    assert any(e["kind"] == "code" and "@router.get" in e["detail"] and e["start_line"] == 9 for e in route_evidence)
    page_evidence = nodes["apps/web/app/page.tsx"]["evidence"]
    assert any(e["kind"] == "manifest" and e["path"] == "apps/web/package.json" for e in page_evidence)


def test_directory_names_alone_never_create_a_role(realistic) -> None:
    assert file_nodes(realistic)["lib/routes/helpers.py"]["kind"] == "source"
    lookalike = analyze({"routes/a.py": "def f(): pass\n", "models/b.py": "class M: pass\n", "jobs/c.py": "def g(): pass\n", "tests_data/d.py": "x = 1\n"})
    assert lookalike.graph.areas == []
    assert {n["kind"] for n in lookalike.detail["nodes"]} == {"source"}


def test_framework_import_without_a_matching_construct_is_not_a_route() -> None:
    result = analyze({"app.py": "from fastapi import FastAPI\n\nx = 1\n", "web.ts": "import express from 'express'\nexport const x = 1\n"})
    assert {n["kind"] for n in result.detail["nodes"]} == {"source"}


def test_next_pages_need_the_manifest_not_just_the_path() -> None:
    files = {"app/page.tsx": "export default function P() { return null }\n", "package.json": '{"dependencies": {"react": "19"}}'}
    assert file_nodes(analyze(files))["app/page.tsx"]["kind"] == "source"
    files["package.json"] = '{"dependencies": {"next": "16"}}'
    assert file_nodes(analyze(files))["app/page.tsx"]["kind"] == "page"


def test_areas_and_their_evidence(realistic) -> None:
    areas = {a.kind: a for a in realistic.graph.areas}
    assert set(areas) == {"api", "database", "workers", "frontend", "serverless", "tests"}
    assert areas["api"].confidence == "high" and "apps/api/routes/users.py" in areas["api"].files
    assert areas["frontend"].confidence == "high" and areas["frontend"].file_count == 3  # layout, page and the Header component
    assert set(areas["database"].files) == {"apps/api/models.py", "database/migrations/versions/0001_init.py"}
    assert any(e.path == "supabase/migrations/20240101_init.sql" and e.kind == "code" for e in areas["database"].evidence)
    for area in areas.values():
        assert area.evidence and all(e.path in REALISTIC and e.start_line >= 1 for e in area.evidence)


def test_technologies_always_have_declared_or_imported_evidence(realistic) -> None:
    techs = {t.name: t for t in realistic.graph.technologies}
    assert {"FastAPI", "SQLAlchemy", "Alembic", "RQ", "Next.js", "React", "pytest", "Supabase"} <= set(techs)
    fastapi = techs["FastAPI"].evidence[0]
    assert (fastapi.kind, fastapi.path, fastapi.start_line) == ("manifest", "pyproject.toml", 4)
    assert techs["Next.js"].evidence[0].path == "apps/web/package.json"
    assert any(e.kind == "import" for e in techs["Supabase"].evidence)  # imported via npm:, never declared
    assert all(t.evidence for t in techs.values())


def test_external_packages_are_listed_but_are_not_nodes(realistic) -> None:
    externals = {(d.ecosystem, d.name): d for d in realistic.graph.external_dependencies}
    assert ("python", "fastapi") in externals and ("npm", "react") in externals and ("url", "deno.land/std") in externals
    assert ("npm", "@supabase/supabase-js") in externals and ("python_stdlib", "typing") in externals
    assert externals[("python", "fastapi")].importer_count == 2 and externals[("python", "fastapi")].evidence
    assert not any("fastapi" in n["id"] for n in realistic.detail["nodes"])


def test_non_code_imports_are_counted_and_create_no_edge(realistic) -> None:
    assert realistic.graph.stats.non_code_imports == 1  # './globals.css'
    assert not any(e["target"].endswith(".css") for e in realistic.detail["edges"])


# --- cycles -----------------------------------------------------------------------------------------------------------


def test_python_cycle_through_a_lazy_import_is_not_a_load_time_cycle(realistic) -> None:
    # accounts -> email is eager, email -> accounts is imported inside a function (lazy)
    assert realistic.graph.cycles == []


def test_eager_python_cycle_is_reported_with_its_edges() -> None:
    files = {"p/a.py": "from p import b\n", "p/b.py": "from p import c\n", "p/c.py": "from p import a\n", "p/d.py": "from p import a\n", "p/__init__.py": ""}
    result = analyze(files)
    [cycle] = [c for c in result.graph.cycles if c.level == "file"]
    assert cycle.nodes == ["file:p/a.py", "file:p/b.py", "file:p/c.py"] and cycle.size == 3 and not cycle.truncated
    assert len(cycle.edge_ids) == 3
    assert all(e["cycle_id"] == cycle.id for k, e in file_edges(result).items() if k[0] != "p/d.py")
    assert file_edges(result)[("p/d.py", "p/a.py", "imports")]["cycle_id"] is None  # d imports into the cycle but is not on it
    assert {n["path"] for n in result.detail["nodes"] if n["cycle_id"] == cycle.id} == {"p/a.py", "p/b.py", "p/c.py"}


def test_typescript_cycle_and_type_only_exception() -> None:
    runtime = analyze({"a.ts": "import { b } from './b'\nexport const a = 1\n", "b.ts": "import { a } from './a'\nexport const b = 1\n"})
    assert [c.nodes for c in runtime.graph.cycles] == [["file:a.ts", "file:b.ts"]]
    typed = analyze({"a.ts": "import { b } from './b'\nexport const a = 1\n", "b.ts": "import type { a } from './a'\nexport const b = 1\n"})
    assert typed.graph.cycles == [] and ("b.ts", "a.ts", "type_only") in file_edges(typed)


def test_module_level_cycle_between_directories() -> None:
    files = {f"{d}/{n}.ts": body for d, n, body in [
        ("x", "one", "import { two } from '../y/two'\nexport const one = 1\n"), ("y", "two", "import { three } from '../x/three'\nexport const two = 1\n"),
        ("x", "three", "export const three = 1\n"),
    ]}
    # x/one -> y/two and y/two -> x/three: the modules depend on each other, but no file imports itself back
    result = analyze(files)
    assert [c.level for c in result.graph.cycles] == ["module"] and result.graph.cycles[0].nodes == ["module:x", "module:y"]


def test_strongly_connected_is_iterative_and_handles_deep_chains() -> None:
    chain = {str(i): {str(i + 1)} for i in range(20_000)}
    chain["20000"] = {"0"}
    assert [len(c) for c in strongly_connected(chain)] == [20_001]
    assert strongly_connected({"a": {"b"}, "b": set()}) == []


# --- missing dependencies and aliases -------------------------------------------------------------------------------------


def test_missing_modules_are_reported_with_their_statement() -> None:
    result = analyze({
        "pkg/__init__.py": "", "pkg/a.py": "from pkg.missing import x\nfrom .gone import y\n",
        "web/main.ts": "import a from './nope'\nimport b from '@/lib/x'\nimport c from './real'\n", "web/real.ts": "export {}\n",
    })
    got = {(u["file"], u["specifier"], u["reason"], u["evidence"]["start_line"]) for u in result.detail["unresolved"]}
    assert got == {
        ("pkg/a.py", "pkg.missing", "not_found", 1), ("pkg/a.py", ".gone", "not_found", 2),
        ("web/main.ts", "./nope", "not_found", 1), ("web/main.ts", "@/lib/x", "alias_not_configured", 2),
    }
    assert result.graph.stats.unresolved_imports == 4 and file_nodes(result)["pkg/a.py"]["metrics"]["unresolved_imports"] == 2
    assert set(file_edges(result)) == {("web/main.ts", "web/real.ts", "imports")}


def test_computed_requires_are_never_turned_into_edges() -> None:
    result = analyze({"a.js": "const m = require(process.env.X)\nconst n = require('./b')\n", "b.js": "module.exports = 1\n"})
    assert set(file_edges(result)) == {("a.js", "b.js", "requires")}
    assert any("computed argument" in w for w in result.graph.warnings)


# --- empty, unsupported, limits --------------------------------------------------------------------------------------------


def test_empty_repository_and_unsupported_only_repository_are_graceful() -> None:
    for files in ({}, {"README.md": "# hi\n", "data.json": "{}", "main.go": "package main\n"}):
        result = analyze(files)
        assert result.graph.nodes == [] and result.graph.edges == [] and result.graph.stats.code_files == 0
        assert any("No supported source files" in w for w in result.graph.warnings)


def test_file_limit_truncates_deterministically_with_a_warning() -> None:
    files = {f"m/f{i:02}.py": "import os\n" for i in range(10)}
    result = analyze(files, max_files=4)
    assert result.graph.stats.truncated and result.graph.stats.code_files == 4 and result.graph.stats.skipped_files == 6
    assert sorted(n["path"] for n in result.detail["nodes"]) == [f"m/f0{i}.py" for i in range(4)]
    assert any("limited to 4" in w for w in result.graph.warnings)


def test_files_that_cannot_be_parsed_still_become_nodes_and_are_reported() -> None:
    result = analyze({"big.py": "import os\n" + "x = 1\n" * 200_000, "ok.py": "import os\n"})
    assert set(file_nodes(result)) == {"big.py", "ok.py"} and result.graph.stats.skipped_files == 1
    assert any("could not be parsed" in w for w in result.graph.warnings)


def test_analysis_is_deterministic(realistic) -> None:
    again = analyze(REALISTIC)
    assert again.graph.model_dump() == realistic.graph.model_dump() and again.detail == realistic.detail


def test_syntax_errors_are_reported_without_failing() -> None:
    result = analyze({"bad.ts": "import a from './a'\nfunction (((\n", "a.ts": "export const a = 1\n"})
    assert ("bad.ts", "a.ts", "imports") in file_edges(result) and any("syntax errors" in w for w in result.graph.warnings)


# --- module grouping -------------------------------------------------------------------------------------------------------


def test_flat_package_stays_one_module_and_chains_collapse() -> None:
    paths = [f"src/pkg/m{i}.py" for i in range(8)] + ["tests/test_a.py", "docs/conf.py"]
    groups = group_modules(paths, set())
    assert {groups[p] for p in paths[:8]} == {"src/pkg"} and groups["tests/test_a.py"] == "tests" and groups["docs/conf.py"] == "docs"


def test_large_directories_are_split_by_subdirectory_and_root_files_stay() -> None:
    paths = [f"app/{sub}/f{i}.py" for sub in ("a", "b", "c") for i in range(20)] + ["app/main.py", "setup.py"]
    groups = group_modules(paths, set())
    assert {groups[p] for p in paths if "/a/" in p} == {"app/a"} and groups["app/main.py"] == "app" and groups["setup.py"] == ""


def test_forced_roots_are_their_own_modules() -> None:
    paths = ["packages/ui/src/a.ts", "packages/ui/src/b.ts", "packages/core/x.ts", "tools/t.ts"]
    groups = group_modules(paths, {"packages/ui", "packages/core"})
    assert groups["packages/ui/src/a.ts"] == "packages/ui" and groups["packages/core/x.ts"] == "packages/core"


def test_grouping_is_a_function_of_the_file_list_only() -> None:
    paths = [f"src/{d}/f{i}.ts" for d in "abcdefgh" for i in range(10)]
    assert group_modules(paths, set()) == group_modules(list(reversed(paths)), set())


def test_large_cycles_are_listed_with_a_cap_but_every_member_keeps_its_cycle_id() -> None:
    count = 120
    files = {f"ring/f{i:03}.ts": f"import {{ x }} from './f{(i + 1) % count:03}'\nexport const x = 1\n" for i in range(count)}
    result = analyze(files)
    [cycle] = [c for c in result.graph.cycles if c.level == "file"]
    assert cycle.size == count and len(cycle.nodes) == 50 and cycle.truncated and len(cycle.edge_ids) <= 100
    assert all(n["cycle_id"] == cycle.id for n in result.detail["nodes"]) and all(e["cycle_id"] == cycle.id for e in result.detail["edges"])
    assert result.graph.stats.cycles == 1


def test_tsconfig_aliases_are_followed_through_an_extended_base_config() -> None:
    result = analyze({
        "configs/base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["shared/*"]}}}',
        "apps/web/tsconfig.json": '{"extends": "../../configs/base.json"}',
        "apps/web/src/main.ts": "import { x } from '@lib/util'\n", "shared/util.ts": "export const x = 1\n",
    })
    # `baseUrl` "." is relative to configs/, so "@lib/*" -> configs/shared/*: not where shared/util.ts is. No edge is invented.
    assert set(file_edges(result)) == set() and result.graph.stats.unresolved_imports == 1
    fixed = analyze({
        "configs/base.json": '{"compilerOptions": {"baseUrl": "..", "paths": {"@lib/*": ["shared/*"]}}}',
        "apps/web/tsconfig.json": '{"extends": "../../configs/base.json"}',
        "apps/web/src/main.ts": "import { x } from '@lib/util'\n", "shared/util.ts": "export const x = 1\n",
    })
    assert set(file_edges(fixed)) == {("apps/web/src/main.ts", "shared/util.ts", "imports")}
    assert file_edges(fixed)[("apps/web/src/main.ts", "shared/util.ts", "imports")]["resolution"] == "alias"
