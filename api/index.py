"""Vercel entrypoint: the FastAPI application as a Python Serverless Function.

Vercel routes every `/api/*` and `/healthz` request here (see vercel.json) and serves the Vite
build from the CDN for everything else, so both halves live behind one domain and no CORS or
API key ever reaches the browser.

The app itself is unchanged: this module only puts `src/` on the import path, because the
function bundle ships the repository as-is rather than an installed package.

If the app cannot start (a missing or malformed environment variable, a dependency that did not
install), the platform would otherwise answer every request with an opaque
FUNCTION_INVOCATION_FAILED. Instead a minimal ASGI app answers 503 with the reason, stripped of
any configured secret value, and the full traceback goes to the function logs.
"""

import json
import os
import sys
import traceback
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

# Values that must never appear in a response or a log line.
SECRET_VARIABLES = (
    "DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "JWT_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "GEMINI_API_KEY",
)


def _scrub(text: str) -> str:
    secrets: set[str] = set()
    for name in SECRET_VARIABLES:
        value = os.environ.get(name, "")
        if len(value) >= 4:
            secrets.add(value)
        if "://" in value and "@" in value:
            # The credentials alone, in case a library echoed only part of the URL.
            credentials = value.split("://", 1)[1].split("@", 1)[0]
            secrets.update(part for part in credentials.split(":", 1) if len(part) >= 4)
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def _describe(exc: BaseException) -> str:
    """A reason safe to show anyone: names the setting at fault, never its value."""
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            problems = [f"{'.'.join(str(part) for part in error['loc']).upper()}: {error['msg']}" for error in exc.errors()]
            return "Invalid environment configuration. " + "; ".join(problems)
    except ImportError:
        pass
    if isinstance(exc, ModuleNotFoundError):
        return f"The Python dependency '{exc.name}' is not installed. Check that requirements.txt was installed by the build."
    module = type(exc).__module__
    if module.startswith("sqlalchemy"):
        # SQLAlchemy quotes the whole URL, password included, in its URL errors.
        return "DATABASE_URL could not be parsed. Check it has no quotes, spaces or line breaks around it."
    if isinstance(exc, (RuntimeError, ValueError)) and module == "builtins":
        # The app's own configuration errors, e.g. "DATABASE_URL is not set".
        return str(exc)
    return f"{type(exc).__name__} while starting the API. The traceback is in the Vercel function logs."


def _unavailable_app(reason: str):
    body = json.dumps({"error": {"code": "startup_failed", "message": _scrub(reason), "details": None}}).encode()

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
                }
            )
            await send({"type": "http.response.body", "body": body})

    return application


try:
    from invoice_auditor.main import create_app

    # Vercel looks for a top-level ASGI application called `app`.
    app = create_app()
except Exception as exc:  # noqa: BLE001 - any startup failure is reported rather than crashing
    print(_scrub("API startup failed:\n" + "".join(traceback.format_exception(exc))), file=sys.stderr, flush=True)
    app = _unavailable_app(_describe(exc))
