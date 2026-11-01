"""A small stand-in for GitHub, for browser and end-to-end tests. It is NOT real GitHub and proves nothing about it.

It serves just what RepoLens calls: the OAuth authorize and token endpoints (it verifies the PKCE verifier, so a
client that skips PKCE fails here), `GET /user`, token revocation, repository metadata, a commit sha, and a ZIP
archive. Point RepoLens at it with GITHUB_API_BASE_URL, GITHUB_OAUTH_AUTHORIZE_URL and GITHUB_OAUTH_TOKEN_URL.

    python -m tests.e2e.fake_github --port 8771

Control it with `POST /_control {"user": "alice", "mode": "approve" | "deny"}` and read `GET /_state`.
"""

import argparse
import base64
import hashlib
import io
import json
import secrets
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

USERS = {
    "alice": {"id": 101, "login": "alice", "name": "Alice Example", "avatar_url": "https://avatars.githubusercontent.com/u/101"},
    "bob": {"id": 102, "login": "bob", "name": None, "avatar_url": "https://avatars.githubusercontent.com/u/102"},
}
REPOS = {
    "octo/demo": {"id": 5_000_001, "default_branch": "main", "description": "A tiny repository for tests"},
    "octo/second": {"id": 5_000_002, "default_branch": "main", "description": "Another one"},
    "octo/third": {"id": 5_000_003, "default_branch": "main", "description": "And a third"},
}
FILES = {
    "README.md": "# demo\n\nA tiny repository. `hash_password` lives in app/auth.py.\n",
    "app/__init__.py": "",
    "app/main.py": "from app.auth import hash_password\n\n\ndef run():\n    return hash_password('x')\n",
    "app/auth.py": "import hashlib\n\n\ndef hash_password(password):\n    return hashlib.sha256(password.encode()).hexdigest()\n",
}

STATE: dict = {"user": "alice", "mode": "approve", "codes": {}, "tokens": {}, "revoked": [], "authorize_queries": []}


def _sha(full_name: str) -> str:
    return hashlib.sha1(full_name.encode()).hexdigest()


def _zipball(full_name: str, sha: str) -> bytes:
    buffer = io.BytesIO()
    root = f"{full_name.replace('/', '-')}-{sha[:7]}"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in FILES.items():
            archive.writestr(f"{root}/{path}", content)
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        pass

    def _send(self, status: int, body: bytes = b"", content_type: str = "application/json", headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode())

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/login/oauth/authorize":
            STATE["authorize_queries"].append(query)
            redirect = query["redirect_uri"]
            if STATE["mode"] == "deny":
                return self._send(302, headers={"Location": f"{redirect}?{urlencode({'error': 'access_denied', 'state': query['state']})}"})
            code = secrets.token_urlsafe(16)
            STATE["codes"][code] = {"challenge": query.get("code_challenge"), "user": STATE["user"]}
            return self._send(302, headers={"Location": f"{redirect}?{urlencode({'code': code, 'state': query['state']})}"})
        if url.path == "/user":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            user = STATE["tokens"].get(token)
            return self._json(200, USERS[user]) if user and token not in STATE["revoked"] else self._json(401, {"message": "Bad credentials"})
        if url.path == "/_state":
            return self._json(200, {k: STATE[k] for k in ("user", "mode", "revoked", "authorize_queries")})
        parts = [p for p in url.path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "repos":
            full_name = f"{parts[1]}/{parts[2]}"
            repo = REPOS.get(full_name)
            if repo is None:
                return self._json(404, {"message": "Not Found"})
            if len(parts) == 3:
                return self._json(200, {"id": repo["id"], "full_name": full_name, "default_branch": repo["default_branch"], "description": repo["description"]})
            if parts[3] == "commits":
                return self._json(200, {"sha": _sha(full_name)})
            if parts[3] == "zipball":
                return self._send(200, _zipball(full_name, parts[4]), "application/zip")
        self._json(404, {"message": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        body = self._body()
        if url.path == "/login/oauth/access_token":
            entry = STATE["codes"].pop(body.get("code", ""), None)
            if entry is None:
                return self._json(200, {"error": "bad_verification_code"})
            verifier = body.get("code_verifier") or ""
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if entry["challenge"] and challenge != entry["challenge"]:
                return self._json(200, {"error": "bad_verification_code"})  # PKCE failed
            token = "gho_" + secrets.token_urlsafe(24)
            STATE["tokens"][token] = entry["user"]
            return self._json(200, {"access_token": token, "token_type": "bearer", "scope": ""})
        if url.path == "/_control":
            for key in ("user", "mode"):
                if key in body:
                    STATE[key] = body[key]
            return self._json(200, {"user": STATE["user"], "mode": STATE["mode"]})
        self._json(404, {"message": "Not Found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if "/applications/" in self.path and self.path.endswith("/token"):
            STATE["revoked"].append(self._body().get("access_token"))
            return self._send(204)
        self._json(404, {"message": "Not Found"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
