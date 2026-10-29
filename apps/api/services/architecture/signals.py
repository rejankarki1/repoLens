"""Evidence-based classification: what a file appears to be, which areas exist, which technologies are used.

Nothing here is inferred from a directory name alone. A file gets a role only when the source shows it:
a framework import *and* a matching construct (`@router.get(...)`, `__tablename__ = ...`, `new Worker(...)`),
or a manifest that declares the framework together with its path convention (Next.js `app/page.tsx`).
Each role keeps the lines that justified it. Roles never create edges.

Technologies come from declared dependencies (package.json, requirements*.txt, pyproject.toml) and from
imports found in code, each with the line that says so.
"""

from __future__ import annotations

import json
import posixpath
import re
import tomllib
from dataclasses import dataclass

from apps.api.schemas.architecture import Evidence, Technology

# Roles in priority order: the first one a file has is its node `kind`.
ROLE_ORDER = ("route", "page", "edge_function", "model", "migration", "job", "component", "entrypoint", "test")
ROLE_AREA = {
    "route": "api", "page": "frontend", "component": "frontend", "edge_function": "serverless",
    "model": "database", "migration": "database", "job": "workers", "test": "tests",
}
AREA_LABEL = {
    "api": "API routes", "frontend": "Frontend", "database": "Database models and migrations",
    "workers": "Background jobs", "serverless": "Serverless functions", "tests": "Tests",
}
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True, slots=True)
class RoleHit:
    role: str
    confidence: str  # high: framework import plus a matching construct; medium: manifest plus convention; low: convention only
    evidence: tuple[Evidence, ...]


def _find(content: str, pattern: re.Pattern[str], limit: int = 2) -> list[tuple[int, str]]:
    """Up to `limit` matches as (1-based line number, trimmed line text)."""
    out: list[tuple[int, str]] = []
    for match in pattern.finditer(content):
        end = content.find("\n", match.start())
        out.append((content.count("\n", 0, match.start()) + 1, content[match.start(): len(content) if end == -1 else end].strip()[:160]))
        if len(out) >= limit:
            break
    return out


def _code(path: str, hits: list[tuple[int, str]], why: str) -> list[Evidence]:
    return [Evidence(path=path, start_line=line, end_line=line, kind="code", detail=f"{why}: {text}") for line, text in hits]


_PY_WEB = {"fastapi", "flask", "starlette", "quart", "sanic", "aiohttp", "litestar", "bottle"}
_PY_ROUTE = re.compile(r"^[ \t]*@\s*[\w.]+\.(?:get|post|put|delete|patch|head|options|websocket|route|api_route)\s*\(", re.M)
_PY_ROUTER = re.compile(r"^\w+\s*=\s*APIRouter\(", re.M)
_DJANGO_URLS = re.compile(r"^urlpatterns\s*=", re.M)
_PY_APP = re.compile(r"^\w+\s*=\s*(?:FastAPI|Flask|Quart|Sanic|Starlette)\(", re.M)
_PY_MAIN = re.compile(r"^if\s+__name__\s*==\s*[\"']__main__[\"']\s*:", re.M)
_SA_MODEL = re.compile(r"^(?:[ \t]*__tablename__\s*=|class\s+\w+\([^)\n]*\b(?:DeclarativeBase|SQLModel)\b[^)\n]*\)|class\s+\w+\([^)\n]*table\s*=\s*True)", re.M)
_DJANGO_MODEL = re.compile(r"^class\s+\w+\(\s*models\.Model\s*\)", re.M)
_ALEMBIC = re.compile(r"^down_revision\b|^\s*context\.run_migrations\(", re.M)
_DJANGO_MIGRATION = re.compile(r"^class\s+Migration\(\s*migrations\.Migration\s*\)", re.M)
_PY_JOB_LIBS = {"rq", "celery", "dramatiq", "arq", "huey", "apscheduler", "taskiq"}
_PY_JOB = re.compile(r"\.enqueue\(|^[ \t]*@(?:\w+\.)?(?:task|shared_task|actor|job|periodic_task)\b|\bQueue\(|\bWorker\(|\.apply_async\(|\.delay\(|\badd_job\(|\bSpawnWorker\b", re.M)
_PY_TEST = re.compile(r"^(?:async\s+)?def\s+test_|^class\s+Test\w*|unittest\.TestCase", re.M)

_JS_WEB = {"express", "fastify", "koa", "hono", "@nestjs/common", "@hono/node-server", "@koa/router", "koa-router"}
_JS_ROUTE = re.compile(r"\b(?:app|router|server|fastify|api|route|routes)\.(?:get|post|put|delete|patch|all)\s*\(|@Controller\(|new\s+Hono\(", re.M)
_JS_MODEL_LIBS = {"typeorm", "drizzle-orm", "mongoose", "sequelize"}
_JS_MODEL = re.compile(r"@Entity\(|\b(?:pgTable|mysqlTable|sqliteTable)\(|new\s+(?:mongoose\.)?Schema\(|sequelize\.define\(", re.M)
_JS_JOB_LIBS = {"bullmq", "bull", "bee-queue", "agenda", "node-cron", "node-schedule", "@nestjs/schedule", "pg-boss"}
_JS_JOB = re.compile(r"new\s+(?:Queue|Worker|Agenda|PgBoss)\(|cron\.schedule\(|scheduleJob\(", re.M)
_JS_TEST_LIBS = {"jest", "vitest", "mocha", "@playwright/test", "cypress", "@jest/globals", "node:test", "ava", "tap"}
_JS_TEST = re.compile(r"^[ \t]*(?:describe|it|test)(?:\.\w+)?\s*\(", re.M)
_JS_TEST_NAME = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$|(?:^|/)__tests__/")
_JS_LISTEN = re.compile(r"\b(?:app|server)\.listen\(|\bserve\(", re.M)
_REACT_LIBS = {"react", "react-native", "preact", "next"}
_NEXT_APP = re.compile(r"(?:^|/)app/(?:.+/)?(?:page|layout|loading|error|not-found|template|default)\.(?:tsx|ts|jsx|js)$")
_NEXT_ROUTE = re.compile(r"(?:^|/)app/(?:.+/)?route\.(?:ts|js)$|(?:^|/)pages/api/.+\.(?:ts|js)$")
_NEXT_PAGES = re.compile(r"(?:^|/)pages/(?!_)(?!api/).+\.(?:tsx|ts|jsx|js)$")
_NEXT_HANDLER = re.compile(r"^export\s+(?:async\s+)?function\s+(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b", re.M)
_EDGE_FN = re.compile(r"(?:^|/)supabase/functions/([^/_][^/]*)/index\.(?:tsx?|jsx?|mjs)$")
_EDGE_SERVE = re.compile(r"\bDeno\.serve\(|\bserve\(", re.M)
_PY_TEST_NAME = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$|(?:^|/)conftest\.py$")


class Signals:
    """Role detection for one snapshot. `declared` maps a package.json directory to {dependency: manifest Evidence}."""

    def __init__(self, declared: dict[str, dict[str, Evidence]], supabase_functions: dict[str, Evidence]) -> None:
        self._declared = declared
        self._supabase = supabase_functions  # "<supabase dir>/functions/<name>" -> config.toml evidence

    def _nearest_declared(self, path: str, dependency: str) -> Evidence | None:
        directory = posixpath.dirname(path)
        while True:
            found = self._declared.get(directory, {}).get(dependency)
            if found:
                return found
            if not directory:
                return None
            directory = posixpath.dirname(directory)

    def roles(self, path: str, language: str, content: str, imports: dict[str, Evidence]) -> list[RoleHit]:
        """`imports` maps each external package the file imports to its first import statement's evidence."""
        hits = self._python(path, content, imports) if language == "python" else self._javascript(path, language, content, imports)
        hits = [h for h in hits if h is not None]
        if any(h.role == "test" for h in hits):
            hits = [h for h in hits if h.role == "test"]
        return sorted(hits, key=lambda h: ROLE_ORDER.index(h.role))

    # --- Python -------------------------------------------------------------------------------------------

    def _python(self, path: str, content: str, imports: dict[str, Evidence]) -> list[RoleHit]:
        hits: list[RoleHit | None] = []

        def with_import(libs: set[str], pattern: re.Pattern[str], role: str, why: str, limit: int = 2) -> RoleHit | None:
            library = next((name for name in sorted(libs) if name in imports), None)
            if library is None:
                return None
            lines = _find(content, pattern, limit)
            return RoleHit(role, "high", (imports[library], *_code(path, lines, why))) if lines else None

        hits.append(with_import(_PY_WEB, _PY_ROUTE, "route", "route decorator"))
        if "fastapi" in imports:
            hits.append(with_import({"fastapi"}, _PY_ROUTER, "route", "APIRouter"))
        hits.append(with_import({"django"}, _DJANGO_URLS, "route", "URL configuration"))
        hits.append(with_import({"sqlalchemy", "sqlmodel"}, _SA_MODEL, "model", "ORM model"))
        hits.append(with_import({"django"}, _DJANGO_MODEL, "model", "Django model"))
        hits.append(with_import({"alembic"}, _ALEMBIC, "migration", "Alembic migration"))
        hits.append(with_import({"django"}, _DJANGO_MIGRATION, "migration", "Django migration"))
        hits.append(with_import(_PY_JOB_LIBS, _PY_JOB, "job", "background job code", limit=3))
        hits.append(with_import({"fastapi", "flask", "quart", "sanic", "starlette"}, _PY_APP, "entrypoint", "application object", 1))
        main = _find(content, _PY_MAIN, 1)
        if main:
            hits.append(RoleHit("entrypoint", "high", tuple(_code(path, main, "script entry point"))))
        test_lines = _find(content, _PY_TEST, 2)
        if test_lines:
            hits.append(RoleHit("test", "high", tuple(_code(path, test_lines, "test definition"))))
        elif _PY_TEST_NAME.search(path):
            hits.append(RoleHit("test", "low", (Evidence(path=path, start_line=1, end_line=1, kind="path", detail="file name follows a test convention"),)))
        return _dedupe(hits)

    # --- JavaScript / TypeScript --------------------------------------------------------------------------

    def _javascript(self, path: str, language: str, content: str, imports: dict[str, Evidence]) -> list[RoleHit]:
        hits: list[RoleHit | None] = []

        def with_import(libs: set[str], pattern: re.Pattern[str], role: str, why: str, limit: int = 2) -> RoleHit | None:
            library = next((name for name in sorted(libs) if name in imports), None)
            if library is None:
                return None
            lines = _find(content, pattern, limit)
            return RoleHit(role, "high", (imports[library], *_code(path, lines, why))) if lines else None

        hits.append(with_import(_JS_WEB, _JS_ROUTE, "route", "route definition"))
        hits.append(with_import(_JS_MODEL_LIBS, _JS_MODEL, "model", "data model definition"))
        hits.append(with_import(_JS_JOB_LIBS, _JS_JOB, "job", "background job code", limit=3))
        hits.append(with_import({"express", "fastify", "koa"}, _JS_LISTEN, "entrypoint", "server start", 1))

        next_dep = self._nearest_declared(path, "next")
        if next_dep:
            if _NEXT_ROUTE.search(path):
                handlers = _find(content, _NEXT_HANDLER, 2)
                hits.append(RoleHit("route", "medium", (next_dep, Evidence(path=path, start_line=1, end_line=1, kind="path", detail="Next.js route file convention"), *_code(path, handlers, "HTTP handler"))))
            elif _NEXT_APP.search(path) or _NEXT_PAGES.search(path):
                hits.append(RoleHit("page", "medium", (next_dep, Evidence(path=path, start_line=1, end_line=1, kind="path", detail="Next.js page/layout file convention"))))
        if path.endswith((".tsx", ".jsx")):
            library = next((name for name in sorted(_REACT_LIBS) if name in imports), None)
            if library:
                hits.append(RoleHit("component", "high", (imports[library], Evidence(path=path, start_line=1, end_line=1, kind="path", detail="JSX source file"))))

        function = _EDGE_FN.search(path)
        if function:
            config = self._supabase.get(path[: function.end(1)])  # "<dir>/supabase/functions/<name>"
            serve = _find(content, _EDGE_SERVE, 1)
            evidence = ([config] if config else []) + _code(path, serve, "request handler")
            if evidence:
                hits.append(RoleHit("edge_function", "high" if serve else "medium", tuple(evidence)))

        test_name = _JS_TEST_NAME.search(path)
        test_lines = _find(content, _JS_TEST, 2)
        library = next((name for name in sorted(_JS_TEST_LIBS) if name in imports), None)
        if test_lines and (library or test_name):
            evidence = ([imports[library]] if library else []) + _code(path, test_lines, "test definition")
            hits.append(RoleHit("test", "high", tuple(evidence)))
        elif test_name:
            hits.append(RoleHit("test", "low", (Evidence(path=path, start_line=1, end_line=1, kind="path", detail="file name follows a test convention"),)))
        return _dedupe(hits)


def _dedupe(hits: list[RoleHit | None]) -> list[RoleHit]:
    """One hit per role, keeping the most confident."""
    best: dict[str, RoleHit] = {}
    for hit in hits:
        if hit and (hit.role not in best or _CONFIDENCE_RANK[hit.confidence] > _CONFIDENCE_RANK[best[hit.role].confidence]):
            best[hit.role] = hit
    return list(best.values())


# --- manifests and technologies ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestDep:
    ecosystem: str  # python | npm
    name: str  # normalized
    path: str
    line: int
    directory: str


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _line_of(text: str, token: str) -> int:
    index = text.lower().find(token.lower())
    return text.count("\n", 0, index) + 1 if index >= 0 else 1


_REQ = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_REQUIREMENTS = re.compile(r"(?:^|/)requirements[^/]*\.txt$|(?:^|/)requirements/[^/]+\.txt$")


def parse_manifests(texts: dict[str, str]) -> list[ManifestDep]:
    """Declared dependencies with the line that declares them. Unparseable manifests are skipped."""
    deps: list[ManifestDep] = []
    for path, text in sorted(texts.items()):
        base, directory = posixpath.basename(path), posixpath.dirname(path)
        if base == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                for name in (data.get(section) or {}) if isinstance(data, dict) else ():
                    deps.append(ManifestDep("npm", name, path, _line_of(text, f'"{name}"'), directory))
        elif _REQUIREMENTS.search(path):
            for number, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                match = None if not stripped or stripped.startswith(("#", "-", "git+", "http")) else _REQ.match(stripped)
                if match:
                    deps.append(ManifestDep("python", _norm_py(match.group()), path, number, directory))
        elif base == "pyproject.toml":
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                continue
            names: list[str] = []
            project = data.get("project") or {}
            names += [d for d in project.get("dependencies") or [] if isinstance(d, str)]
            for group in (project.get("optional-dependencies") or {}).values():
                names += [d for d in group if isinstance(d, str)]
            for group in (data.get("dependency-groups") or {}).values():
                names += [d for d in group if isinstance(d, str)]
            poetry = (data.get("tool") or {}).get("poetry") or {}
            names += list((poetry.get("dependencies") or {}).keys())
            for group in (poetry.get("group") or {}).values():
                names += list((group.get("dependencies") or {}).keys())
            for requirement in names:
                match = _REQ.match(requirement)
                if match and match.group().lower() != "python":
                    deps.append(ManifestDep("python", _norm_py(match.group()), path, _line_of(text, match.group()), directory))
    return deps


_TECH: dict[tuple[str, str], tuple[str, str]] = {
    **{("python", k): v for k, v in {
        "fastapi": ("FastAPI", "web framework"), "flask": ("Flask", "web framework"), "django": ("Django", "web framework"),
        "starlette": ("Starlette", "web framework"), "uvicorn": ("Uvicorn", "server"), "pydantic": ("Pydantic", "validation"),
        "sqlalchemy": ("SQLAlchemy", "database"), "sqlmodel": ("SQLModel", "database"), "alembic": ("Alembic", "database migrations"),
        "asyncpg": ("asyncpg", "database driver"), "psycopg": ("psycopg", "database driver"), "psycopg2": ("psycopg2", "database driver"),
        "pgvector": ("pgvector", "database"), "redis": ("Redis client", "queue/cache"), "rq": ("RQ", "background jobs"),
        "celery": ("Celery", "background jobs"), "dramatiq": ("Dramatiq", "background jobs"), "arq": ("arq", "background jobs"),
        "pytest": ("pytest", "testing"), "httpx": ("HTTPX", "http client"), "requests": ("Requests", "http client"),
        "tree-sitter": ("tree-sitter", "parsing"), "tree-sitter-language-pack": ("tree-sitter language pack", "parsing"),
        "openai": ("OpenAI SDK", "ai"), "anthropic": ("Anthropic SDK", "ai"), "groq": ("Groq SDK", "ai"), "langchain": ("LangChain", "ai"),
    }.items()},
    **{("npm", k): v for k, v in {
        "next": ("Next.js", "web framework"), "react": ("React", "ui"), "react-native": ("React Native", "ui"), "vue": ("Vue", "ui"),
        "svelte": ("Svelte", "ui"), "vite": ("Vite", "build"), "tailwindcss": ("Tailwind CSS", "ui"), "typescript": ("TypeScript", "language"),
        "express": ("Express", "web framework"), "fastify": ("Fastify", "web framework"), "koa": ("Koa", "web framework"),
        "hono": ("Hono", "web framework"), "@nestjs/core": ("NestJS", "web framework"),
        "@supabase/supabase-js": ("Supabase", "platform"), "@prisma/client": ("Prisma", "database"), "prisma": ("Prisma", "database"),
        "drizzle-orm": ("Drizzle ORM", "database"), "typeorm": ("TypeORM", "database"), "mongoose": ("Mongoose", "database"),
        "bullmq": ("BullMQ", "background jobs"), "ioredis": ("Redis client", "queue/cache"), "redis": ("Redis client", "queue/cache"),
        "jest": ("Jest", "testing"), "vitest": ("Vitest", "testing"), "@playwright/test": ("Playwright", "testing"), "cypress": ("Cypress", "testing"),
        "@tanstack/react-query": ("TanStack Query", "data fetching"), "swr": ("SWR", "data fetching"), "zod": ("Zod", "validation"),
        "openai": ("OpenAI SDK", "ai"), "@anthropic-ai/sdk": ("Anthropic SDK", "ai"), "@google/genai": ("Google Gemini SDK", "ai"),
    }.items()},
}


def detect_technologies(deps: list[ManifestDep], imports: dict[tuple[str, str], list[Evidence]]) -> list[Technology]:
    """A technology is listed only with evidence: a declared dependency and/or imports in code."""
    grouped: dict[str, tuple[str, str, list[Evidence]]] = {}
    for dep in deps:
        meta = _TECH.get((dep.ecosystem, dep.name))
        if meta:
            evidence = Evidence(path=dep.path, start_line=dep.line, end_line=dep.line, kind="manifest", detail=f"declares dependency {dep.name}")
            grouped.setdefault(meta[0], (meta[0], meta[1], []))[2].append(evidence)
    for (ecosystem, name), found in imports.items():
        meta = _TECH.get((ecosystem, _norm_py(name) if ecosystem == "python" else name))
        if meta:
            grouped.setdefault(meta[0], (meta[0], meta[1], []))[2].extend(found)
    result: list[Technology] = []
    for name, category, evidence in sorted(grouped.values(), key=lambda g: g[0].lower()):
        manifest = [e for e in evidence if e.kind == "manifest"][:2]
        code = [e for e in evidence if e.kind != "manifest"][:3]
        result.append(Technology(id="tech:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), name=name, category=category, evidence=manifest + code))
    return result
