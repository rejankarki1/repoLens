"""Import resolution: which file, package or failure an import statement names."""

import json

import pytest

from apps.api.services.architecture.extract import RawImport
from apps.api.services.architecture.resolve import Resolver, loads_jsonc

CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def resolver(files: dict[str, str]) -> Resolver:
    code = {p for p in files if p.endswith(CODE_SUFFIXES)}
    texts = {p: t for p, t in files.items() if p.endswith(("package.json", "tsconfig.json", "jsconfig.json", "__init__.py"))}
    return Resolver(files, code, texts)


def py(r: Resolver, importer: str, spec: str, names: tuple[str, ...] = ()):
    return r.resolve(importer, RawImport("python", "imports", spec, names, 1, 1, ""))


def js(r: Resolver, importer: str, spec: str):
    language = "tsx" if importer.endswith("x") else "typescript"
    return r.resolve(importer, RawImport(language, "imports", spec, (), 1, 1, ""))


def hit(resolution) -> list[tuple[str, str, str]]:
    return [(t.path, t.how, t.confidence) for t in resolution.targets]


PYTHON = {
    "src/pkg/__init__.py": "from .a import helper\n", "src/pkg/a.py": "", "src/pkg/b.py": "", "src/pkg/sub/__init__.py": "",
    "src/pkg/sub/c.py": "", "src/pkg/ns/d.py": "", "tests/test_a.py": "", "tests/helpers/util.py": "", "tests/helpers/test_x.py": "",
    "scripts/run.py": "", "scripts/tool.py": "", "apps/svc/main.py": "", "apps/svc/db.py": "",
}


def test_python_relative_imports() -> None:
    r = resolver(PYTHON)
    assert hit(py(r, "src/pkg/a.py", ".b")) == [("src/pkg/b.py", "relative", "verified")]
    assert hit(py(r, "src/pkg/a.py", ".", ("b",))) == [("src/pkg/b.py", "submodule", "verified")]
    # __init__ mentions "a" (it re-exports from .a), so `from .. import a` might be that attribute: __init__ stays and the
    # submodule edge is a guess, unlike "b", which only exists as a module
    assert hit(py(r, "src/pkg/sub/c.py", "..", ("a", "b"))) == [
        ("src/pkg/__init__.py", "relative", "verified"), ("src/pkg/a.py", "submodule", "heuristic"), ("src/pkg/b.py", "submodule", "verified"),
    ]
    assert hit(py(r, "src/pkg/sub/c.py", "..", ("b",))) == [("src/pkg/b.py", "submodule", "verified")]
    assert hit(py(r, "src/pkg/sub/c.py", "..a", ("helper",))) == [("src/pkg/a.py", "relative", "verified")]
    assert hit(py(r, "src/pkg/a.py", ".sub")) == [("src/pkg/sub/__init__.py", "relative", "verified")]


def test_python_relative_import_of_a_missing_module_or_beyond_the_root_is_unresolved() -> None:
    r = resolver(PYTHON)
    assert py(r, "src/pkg/a.py", ".missing", ("z",)).reason == "not_found"
    assert py(r, "src/pkg/a.py", "....x").reason == "relative_beyond_root"


def test_python_src_layout_resolves_through_the_package_root() -> None:
    r = resolver(PYTHON)
    assert hit(py(r, "tests/test_a.py", "pkg.a")) == [("src/pkg/a.py", "absolute", "verified")]
    assert hit(py(r, "tests/test_a.py", "pkg", ("a",)))[0] == ("src/pkg/__init__.py", "absolute", "verified")
    assert ("src/pkg/a.py", "submodule", "heuristic") in hit(py(r, "tests/test_a.py", "pkg", ("a",)))  # __init__ mentions the name


def test_python_from_import_of_a_submodule_is_verified_when_init_does_not_shadow_it() -> None:
    r = resolver(PYTHON)
    assert ("src/pkg/b.py", "submodule", "verified") in hit(py(r, "tests/test_a.py", "pkg", ("b",)))


def test_python_namespace_package_and_repo_root_imports() -> None:
    r = resolver(PYTHON)
    assert py(r, "scripts/run.py", "apps.svc").status == "namespace"
    assert hit(py(r, "scripts/run.py", "apps.svc.db")) == [("apps/svc/db.py", "absolute", "verified")]
    assert hit(py(r, "scripts/run.py", "apps.svc", ("db",))) == [("apps/svc/db.py", "submodule", "verified")]


def test_python_ancestor_directory_imports_are_heuristic_and_never_for_stdlib_names() -> None:
    r = resolver(PYTHON)
    assert hit(py(r, "scripts/run.py", "tool")) == [("scripts/tool.py", "ancestor_dir", "heuristic")]
    assert hit(py(r, "tests/helpers/test_x.py", "util")) == [("tests/helpers/util.py", "ancestor_dir", "heuristic")]
    shadowing = resolver({**PYTHON, "scripts/json.py": ""})
    assert py(shadowing, "scripts/run.py", "json").status == "external"  # the standard library wins over a sibling file


def test_python_external_stdlib_and_missing_local_submodule() -> None:
    r = resolver(PYTHON)
    assert (py(r, "tests/test_a.py", "os.path").status, py(r, "tests/test_a.py", "os.path").ecosystem) == ("external", "python_stdlib")
    third = py(r, "tests/test_a.py", "requests")
    assert (third.status, third.package, third.ecosystem) == ("external", "requests", "python")
    assert py(r, "tests/test_a.py", "pkg.nope").reason == "not_found"  # a local package, but that module is not there


def test_python_import_of_the_file_itself_is_ignored() -> None:
    assert py(resolver(PYTHON), "src/pkg/a.py", "pkg.a").status == "self"


WEB = {
    "app/package.json": json.dumps({"name": "web"}),
    "app/tsconfig.json": '{\n  // comment\n  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"], "@ui": ["./src/ui/index.ts"], "*": ["types/*"]},},\n}',
    "app/src/main.ts": "", "app/src/lib/util.ts": "", "app/src/ui/index.ts": "", "app/src/ui/Button.tsx": "", "app/src/dir/index.js": "",
    "app/src/legacy.js": "", "app/src/esm.ts": "", "app/src/styles.css": "", "app/src/data.json": "", "app/src/page.tsx": "",
    "app/components/Card.tsx": "",
}


@pytest.mark.parametrize(("spec", "expected"), [
    ("./lib/util", "app/src/lib/util.ts"), ("./lib/util.ts", "app/src/lib/util.ts"), ("./esm.js", "app/src/esm.ts"),
    ("./ui", "app/src/ui/index.ts"), ("./dir", "app/src/dir/index.js"), ("./legacy", "app/src/legacy.js"),
    ("../components/Card", "app/components/Card.tsx"), ("./ui/Button", "app/src/ui/Button.tsx"),
])
def test_js_relative_resolution(spec: str, expected: str) -> None:
    resolution = js(resolver(WEB), "app/src/main.ts", spec)
    assert (resolution.status, hit(resolution)) == ("internal", [(expected, "relative", "verified")])


def test_js_aliases_from_tsconfig_paths_and_base_url() -> None:
    r = resolver(WEB)
    assert hit(js(r, "app/src/main.ts", "@/lib/util")) == [("app/src/lib/util.ts", "alias", "verified")]
    assert hit(js(r, "app/src/main.ts", "@ui")) == [("app/src/ui/index.ts", "alias", "verified")]
    assert hit(js(r, "app/src/main.ts", "components/Card")) == [("app/components/Card.tsx", "base_url", "verified")]
    assert js(r, "app/src/main.ts", "@/nope").reason == "not_found"  # the alias is configured, the file is not there


def test_js_catch_all_alias_falls_back_to_external_packages() -> None:
    r = resolver(WEB)
    assert (js(r, "app/src/main.ts", "lodash/fp").status, js(r, "app/src/main.ts", "lodash/fp").package) == ("external", "lodash")


def test_js_alias_without_configuration_is_unresolved_not_external() -> None:
    r = resolver({"x/main.ts": "", "x/lib.ts": ""})
    assert js(r, "x/main.ts", "@/lib").reason == "alias_not_configured"
    assert js(r, "x/main.ts", "~/lib").reason == "alias_not_configured"
    assert js(r, "x/main.ts", "@scope/pkg").package == "@scope/pkg"


def test_js_missing_file_and_non_code_assets() -> None:
    r = resolver(WEB)
    assert js(r, "app/src/main.ts", "./missing").reason == "not_found"
    assert js(r, "app/src/main.ts", "./styles.css").status == "asset"
    assert js(r, "app/src/main.ts", "./data.json").status == "asset"
    assert js(r, "app/src/main.ts", "./styles.css?inline").status == "asset"


def test_js_externals_builtins_and_urls() -> None:
    r = resolver(WEB)
    cases = {
        "react": ("npm", "react"), "@tanstack/react-query/devtools": ("npm", "@tanstack/react-query"), "node:fs": ("node_builtin", "fs"),
        "fs/promises": ("node_builtin", "fs"), "npm:@supabase/supabase-js@2.39.0": ("npm", "@supabase/supabase-js"),
        "npm:zod@3": ("npm", "zod"), "https://deno.land/std@0.168.0/http/server.ts": ("url", "deno.land/std"),
    }
    for spec, (ecosystem, name) in cases.items():
        resolution = js(r, "app/src/main.ts", spec)
        assert (resolution.status, resolution.ecosystem, resolution.package) == ("external", ecosystem, name), spec


def test_js_workspace_packages_resolve_to_their_entry() -> None:
    files = {
        "packages/ui/package.json": json.dumps({"name": "@org/ui", "main": "src/main.ts"}), "packages/ui/src/main.ts": "", "packages/ui/src/button.ts": "",
        "packages/core/package.json": json.dumps({"name": "@org/core"}), "packages/core/src/index.ts": "",
        "apps/web/src/app.ts": "",
    }
    r = resolver(files)
    assert hit(js(r, "apps/web/src/app.ts", "@org/ui")) == [("packages/ui/src/main.ts", "workspace", "verified")]
    assert hit(js(r, "apps/web/src/app.ts", "@org/core")) == [("packages/core/src/index.ts", "workspace", "heuristic")]
    assert hit(js(r, "apps/web/src/app.ts", "@org/ui/button")) == [("packages/ui/src/button.ts", "workspace", "verified")]
    assert js(r, "apps/web/src/app.ts", "@org/ui/missing").reason == "not_found"


def test_tsconfig_extends_a_base_config() -> None:
    files = {
        "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["shared/lib/*"]}}}',
        "apps/a/tsconfig.json": '{"extends": "../../tsconfig.base", "compilerOptions": {"strict": true}}',
        "apps/a/src/x.ts": "", "shared/lib/thing.ts": "",
    }
    # tsconfig.base.json is not a nearest-config file name, but is reachable through extends
    r = Resolver(files, {p for p in files if p.endswith(".ts")}, {p: t for p, t in files.items() if p.endswith(".json")})
    assert hit(js(r, "apps/a/src/x.ts", "@lib/thing")) == [("shared/lib/thing.ts", "alias", "verified")]


def test_loads_jsonc_handles_comments_trailing_commas_and_strings() -> None:
    text = '{/* c */ "a": [1, 2,], // x\n "url": "http://not-a-comment", "b": {"c": 1,},}'
    assert loads_jsonc(text) == {"a": [1, 2], "url": "http://not-a-comment", "b": {"c": 1}}
    assert loads_jsonc("not json") == {} and loads_jsonc("[1]") == {}
