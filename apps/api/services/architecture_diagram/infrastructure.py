"""Deterministic detection of major infrastructure the repository's own code and manifests evidence.

Generic across stacks: one vocabulary of infrastructure *categories* (database, object storage, AI provider,
queue/worker, auth provider, external service) matched against names the existing Tree-sitter analysis already
extracted (declared dependencies, imported packages/URLs) and against SDK-usage patterns in stored source.
Nothing is repository-specific and nothing is invented: an item exists only with a verbatim evidence line, and
its `strength` says how much that evidence proves —

* `runtime_usage`: a source line that uses the SDK/endpoint (a connect call, a storage call, a provider
  request URL). Strong, but it still does not prove what any particular request does.
* `imported`: the package is imported by code, no usage line was found in the files scanned.
* `declared`: only a manifest dependency — a hint, not evidence of runtime use.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from apps.api.schemas.architecture import ExternalDependency, FileExternal, Technology

# (category, label, name regex on a lowercased package/URL/technology name, SDK-usage line regex or None,
#  words a component must use to count as representing it)
_RULES: tuple[tuple[str, str, str, str | None, frozenset[str]], ...] = (
    ("database", "MongoDB", r"mongoose|mongodb", r"mongoose\.connect|new\s+MongoClient|MongoClient\.connect|mongoose\.createConnection",
     frozenset({"mongodb", "mongo", "database", "db"})),
    ("database", "SQL database", r"(?:^|[/@])pg(?:$|[/@-])|postgres|psycopg|asyncpg|sqlalchemy|sqlmodel|prisma|typeorm|drizzle|sequelize|knex|mysql|sqlite|mariadb",
     r"create_engine\(|create_async_engine\(|new\s+Pool\(|createPool\(|\$connect\(|new\s+Sequelize\(|knex\(",
     frozenset({"postgres", "postgresql", "mysql", "sqlite", "sql", "database", "db"})),
    ("database", "Supabase Database", r"supabase", r"\.from\(\s*['\"][\w.]+['\"]\s*\)\s*\.(?:select|insert|update|upsert|delete)\b|\.rpc\(",
     frozenset({"supabase", "postgres", "postgresql", "database", "db"})),
    ("object storage", "Supabase Storage", r"supabase", r"\.storage\s*\.from\(", frozenset({"supabase", "storage", "bucket"})),
    ("object storage", "Cloudinary", r"cloudinary", r"cloudinary\.(?:v2\.)?(?:uploader|config|api)|CloudinaryStorage", frozenset({"cloudinary", "storage", "bucket"})),
    ("object storage", "Amazon S3", r"aws-sdk|@aws-sdk/client-s3|boto3", r"S3Client|new\s+AWS\.S3|boto3\.(?:client|resource)\(\s*['\"]s3|putObject|upload_file\(",
     frozenset({"s3", "amazon", "aws", "storage", "bucket"})),
    ("object storage", "Google Cloud Storage", r"@google-cloud/storage|google-cloud-storage", r"storage\.bucket\(|storage\.Client\(", frozenset({"gcs", "storage", "bucket"})),
    ("ai provider", "Google Gemini", r"@google/genai|generative-?ai|genai|gemini|generativelanguage\.googleapis\.com",
     r"generativelanguage\.googleapis\.com|\.generateContent\(|GoogleGenerativeAI\(|genai\.Client\(", frozenset({"gemini", "google", "genai", "ai", "llm"})),
    ("ai provider", "OpenAI", r"openai", r"api\.openai\.com|OpenAI\(|chat\.completions", frozenset({"openai", "gpt", "ai", "llm"})),
    ("ai provider", "Anthropic", r"anthropic", r"api\.anthropic\.com|Anthropic\(|messages\.create\(", frozenset({"anthropic", "claude", "ai", "llm"})),
    ("ai provider", "Groq", r"\bgroq\b", r"api\.groq\.com|Groq\(", frozenset({"groq", "ai", "llm"})),
    ("queue / worker", "Job queue", r"bullmq|\bbull\b|bee-queue|celery|dramatiq|\brq\b|\barq\b|huey|taskiq|agenda",
     r"new\s+Queue\(|Celery\(|\.delay\(|\.enqueue\(|new\s+Worker\(", frozenset({"queue", "worker", "workers", "job", "jobs", "celery", "bullmq"})),
    ("cache / queue store", "Redis", r"redis", r"createClient\(|Redis\(|redis\.from_url", frozenset({"redis", "cache", "queue"})),
    ("auth provider", "Auth provider", r"passport|next-auth|@clerk|auth0|lucia|@supabase/auth|firebase/auth|keycloak",
     r"passport\.(?:use|authenticate)|NextAuth\(|auth\.signIn|signInWith\w+\(", frozenset({"auth", "authentication", "passport", "login", "session"})),
    ("external service", "External service SDK", r"mapbox|stripe|twilio|sendgrid|resend|nodemailer|sentry|algolia|pusher|pinecone", None,
     frozenset({"mapbox", "stripe", "twilio", "sendgrid", "resend", "email", "payment", "payments", "maps", "external", "service", "services"})),
)
_COMPILED = tuple((c, l, re.compile(n), re.compile(u) if u else None, w) for c, l, n, u, w in _RULES)
_MAX_ITEMS = 14
_MAX_EVIDENCE = 2
_MAX_LINE_CHARS = 160
_STRENGTH_ORDER = {"runtime_usage": 0, "imported": 1, "declared": 2}


@dataclass(frozen=True, slots=True)
class InfraEvidence:
    path: str
    line: int
    text: str


@dataclass(frozen=True, slots=True)
class InfraItem:
    category: str
    name: str
    strength: str  # "runtime_usage" | "imported" | "declared"
    evidence: tuple[InfraEvidence, ...]
    aliases: frozenset[str] = frozenset()


def detect_infrastructure(
    technologies: Sequence[Technology],
    external_dependencies: Sequence[ExternalDependency],
    file_externals: Sequence[FileExternal],
    texts: Mapping[str, str],
) -> tuple[InfraItem, ...]:
    """`texts` is the stored source of a bounded set of files (importers, entry points, candidates)."""
    named: list[tuple[str, bool, InfraEvidence | None]] = []  # (name, evidenced by code, first evidence)
    for t in technologies:
        code = [e for e in t.evidence if e.kind != "manifest"]
        pick = (code or list(t.evidence) or [None])[0]
        named.append((t.name.lower(), bool(code), InfraEvidence(pick.path, pick.start_line, pick.detail[:_MAX_LINE_CHARS]) if pick else None))
    for d in external_dependencies:
        e = d.evidence[0] if d.evidence else None
        named.append((d.name.lower(), True, InfraEvidence(e.path, e.start_line, e.detail[:_MAX_LINE_CHARS]) if e else None))
    for f in file_externals:
        named.append((f.name.lower(), True, InfraEvidence(f.evidence.path, f.evidence.start_line, f.evidence.detail[:_MAX_LINE_CHARS])))

    lines_by_path = {p: t.split("\n") for p, t in sorted(texts.items())}
    items: list[InfraItem] = []
    supabase_package_seen = False
    for category, label, name_re, use_re, aliases in _COMPILED:
        usage: list[InfraEvidence] = []
        if use_re is not None:
            for path, lines in lines_by_path.items():
                for i, line in enumerate(lines):
                    if use_re.search(line):
                        usage.append(InfraEvidence(path, i + 1, line.strip()[:_MAX_LINE_CHARS]))
                        break  # one line per file is enough evidence
                if len(usage) >= _MAX_EVIDENCE:
                    break
        if usage:
            items.append(InfraItem(category, label, "runtime_usage", tuple(usage), aliases))
            continue
        matches = [m for m in named if name_re.search(m[0])]
        if not matches:
            continue
        if label.startswith("Supabase"):  # known only by its package: one "imported" platform item, not one per capability
            if supabase_package_seen or any(i.name.startswith("Supabase") for i in items):
                continue
            supabase_package_seen, label, category = True, "Supabase", "platform"
            aliases = frozenset({"supabase", "postgres", "postgresql", "database", "db", "storage"})
        evidence = tuple(dict.fromkeys(m[2] for m in sorted(matches, key=lambda m: (m[2] is None, m[2].path if m[2] else "")) if m[2] is not None))[:_MAX_EVIDENCE]
        items.append(InfraItem(category, label, "imported" if any(m[1] for m in matches) else "declared", evidence, aliases))
    items.sort(key=lambda i: (_STRENGTH_ORDER[i.strength], i.category, i.name))
    return tuple(items[:_MAX_ITEMS])


def is_represented(item: InfraItem, components: Iterable[tuple[str, str]]) -> bool:
    """Whether some component's own label/description (not the files it contains) names this infrastructure."""
    for label, description in components:
        tokens = set(re.split(r"[^a-z0-9]+", f"{label} {description}".lower()))
        if tokens & item.aliases:
            return True
    return False
