"""Fixtures and helpers for the architecture analysis tests."""

from apps.api.services.architecture.build import AnalysisResult, SnapshotFile, analyze_snapshot

SHA = "c" * 40


def snap(files: dict[str, str], *, only: set[str] | None = None) -> list[SnapshotFile]:
    return [SnapshotFile(path, "source", len(text.encode()), text) for path, text in sorted(files.items()) if only is None or path in only]


def analyze(files: dict[str, str], **kwargs) -> AnalysisResult:
    return analyze_snapshot(SHA, snap(files), **kwargs)


def file_edges(result: AnalysisResult) -> dict[tuple[str, str, str], dict]:
    """(source path, target path, relationship) -> edge, from the stored file-level detail."""
    return {(e["source"][5:], e["target"][5:], e["relationship"]): e for e in result.detail["edges"]}


def file_nodes(result: AnalysisResult) -> dict[str, dict]:
    return {n["path"]: n for n in result.detail["nodes"]}


# --- a realistic repository: FastAPI + SQLAlchemy + RQ + Alembic, a Next.js app, Supabase edge functions ------------

REALISTIC = {
    "pyproject.toml": '[project]\nname = "demo"\ndependencies = [\n  "fastapi>=0.100",\n  "sqlalchemy",\n  "alembic",\n  "rq",\n]\n',
    "apps/api/__init__.py": "",
    "apps/api/main.py": "from fastapi import FastAPI\n\nfrom apps.api.routes import users\n\napp = FastAPI()\napp.include_router(users.router)\n",
    "apps/api/routes/__init__.py": "",
    "apps/api/routes/users.py": (
        "from fastapi import APIRouter\n\nfrom apps.api.schemas import UserOut\nfrom apps.api.services import accounts\n\n"
        "router = APIRouter()\n\n\n@router.get(\"/users\")\nasync def list_users() -> list[UserOut]:\n    return accounts.all_users()\n"
    ),
    "apps/api/services/__init__.py": "",
    "apps/api/services/accounts.py": (
        "from typing import TYPE_CHECKING\n\nfrom apps.api.jobs.email import send_welcome\nfrom apps.api.models import User\n\n"
        "if TYPE_CHECKING:\n    from apps.api.schemas import UserOut\n\n\ndef all_users():\n    return [User]\n\n\ndef signup():\n    send_welcome()\n"
    ),
    "apps/api/models.py": (
        "from sqlalchemy.orm import DeclarativeBase\n\n\nclass Base(DeclarativeBase):\n    pass\n\n\n"
        "class User(Base):\n    __tablename__ = \"users\"\n"
    ),
    "apps/api/schemas.py": "from pydantic import BaseModel\n\n\nclass UserOut(BaseModel):\n    id: int\n",
    "apps/api/jobs/__init__.py": "",
    "apps/api/jobs/email.py": (
        "from rq import Queue\nfrom redis import Redis\n\n\ndef send_welcome():\n    from apps.api.services import accounts\n\n"
        "    Queue(connection=Redis()).enqueue(accounts.signup)\n"
    ),
    "database/migrations/versions/0001_init.py": (
        "from alembic import op\nimport sqlalchemy as sa\n\nrevision = '0001'\ndown_revision = None\n\n\n"
        "def upgrade():\n    op.create_table('users', sa.Column('id', sa.Integer()))\n"
    ),
    "lib/routes/helpers.py": "def route_name(x):\n    return x.strip('/')\n",
    "tests/test_users.py": "import pytest\n\nfrom apps.api.routes.users import router\n\n\ndef test_router():\n    assert router\n",
    "apps/web/package.json": '{"name": "web", "dependencies": {"next": "16.0.0", "react": "19.0.0"}}',
    "apps/web/tsconfig.json": '{\n  // path aliases\n  "compilerOptions": {"paths": {"@/*": ["./*"],},},\n}\n',
    "apps/web/app/layout.tsx": "export default function Layout({ children }: { children: React.ReactNode }) {\n  return <html>{children}</html>\n}\n",
    "apps/web/app/page.tsx": (
        "import { Header } from \"@/components/header\"\nimport { api } from \"@/lib/api\"\nimport './globals.css'\n\n"
        "export default function Page() {\n  return <Header title={api.name} />\n}\n"
    ),
    "apps/web/app/globals.css": "body { margin: 0 }\n",
    "apps/web/components/header.tsx": (
        "import React from \"react\"\nimport { api } from \"../lib/api\"\nimport type { User } from \"@/types\"\n\n"
        "export function Header({ title }: { title: string; user?: User }) {\n  return <h1>{title}{api.name}</h1>\n}\n"
    ),
    "apps/web/lib/api.ts": "import { User } from \"@/types\"\nexport * from \"./http\"\nexport const api = { name: \"api\" as string, user: null as User | null }\n",
    "apps/web/lib/http.ts": "export const get = (url: string) => fetch(url)\n",
    "apps/web/types/index.ts": "export interface User { id: number }\n",
    "supabase/config.toml": "[functions.hello]\nverify_jwt = true\n",
    "supabase/functions/hello/index.ts": (
        "import { serve } from \"https://deno.land/std@0.168.0/http/server.ts\"\n"
        "import { createClient } from \"npm:@supabase/supabase-js@2\"\n"
        "import { cors } from \"../_shared/cors.ts\"\n\nserve((req) => new Response(cors, { headers: {} }))\n"
    ),
    "supabase/functions/_shared/cors.ts": "export const cors = 'ok'\n",
    "supabase/migrations/20240101_init.sql": "create table users (id serial primary key);\n",
    "README.md": "# demo\n",
}
