"""Production hardening: security headers, start-up validation of unsafe configuration, and log redaction."""

import logging
import re

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.config import Settings

_SECRET_QUERY = re.compile(r"((?:code|state|access_token|token|client_secret)=)[^&\s\"']+", re.IGNORECASE)


def redact(text: str) -> str:
    """Hide OAuth codes, state values and tokens that appear in URLs."""
    return _SECRET_QUERY.sub(r"\1[redacted]", text)


class RedactingFilter(logging.Filter):
    """Access logs include the query string. The OAuth callback URL carries a one-time `code` and the `state`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_log_redaction() -> None:
    for name in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(RedactingFilter())


logger = logging.getLogger(__name__)


class UnhandledErrorMiddleware:
    """Converts a truly unhandled exception (a bug, a missing migration, ...) into a clean JSON 500 *before*
    Starlette's default `ServerErrorMiddleware` would. That default handler sits outside `CORSMiddleware`
    (and `app.exception_handler(Exception)` is routed there too — see `Starlette.build_middleware_stack`,
    which special-cases `Exception`/500 to the outer middleware, not the inner `ExceptionMiddleware` CORS
    wraps), so its response carries no CORS headers: the browser blocks it from JS and `fetch()` throws a
    generic network error instead of resolving, and the frontend reports "could not reach the API" for what
    is actually a real backend failure. This middleware must be registered *before* `CORSMiddleware` (so it
    ends up inside it — see `app.add_middleware`'s doc) so its response passes back through CORS's normal
    header injection like any other response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:  # headers already sent: too late to send a different response
                raise
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
            await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Adds conservative headers to every API response. The API returns JSON and event streams, never pages."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.production = settings.is_production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                extra = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                }
                if self.production:
                    extra[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
                    extra[b"content-security-policy"] = b"default-src 'none'; frame-ancestors 'none'"
                if path.startswith("/auth/"):
                    extra[b"cache-control"] = b"no-store"
                headers.extend((k, v) for k, v in extra.items() if k not in present)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def production_problems(settings: Settings) -> list[str]:
    """Everything that makes a production configuration unsafe. Empty when it is fine."""
    problems: list[str] = []
    if not settings.cookie_secure:
        problems.append("session cookies must be Secure (do not set SESSION_COOKIE_SECURE=false)")
    for name, value in (("WEB_BASE_URL", settings.web_base_url), ("API_PUBLIC_URL", settings.api_public_url)):
        if not value.startswith("https://"):
            problems.append(f"{name} must be an https:// URL")
    origins = settings.allowed_origins
    if not origins or any(o == "*" or not o.startswith("https://") for o in origins):
        problems.append("WEB_ORIGINS must list explicit https:// origins (no wildcard, no http)")
    elif settings.web_base_url.rstrip("/") not in origins:
        problems.append("WEB_BASE_URL must be one of WEB_ORIGINS")
    if not settings.github_login_enabled:
        problems.append("GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET must be set")
    if "local_dev_password" in settings.database_url:
        problems.append("DATABASE_URL still uses the local development password")
    return problems


def validate_production_settings(settings: Settings) -> None:
    if settings.is_production and (problems := production_problems(settings)):
        raise RuntimeError("Unsafe production configuration:\n- " + "\n- ".join(problems))
