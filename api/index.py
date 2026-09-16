"""Vercel entrypoint: the FastAPI application as a Python Serverless Function.

Vercel routes every `/api/*` and `/healthz` request here (see vercel.json) and serves the Vite
build from the CDN for everything else, so both halves live behind one domain and no CORS or
API key ever reaches the browser.

The app itself is unchanged: this module only puts `src/` on the import path, because the
function bundle ships the repository as-is rather than an installed package.
"""

import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from invoice_auditor.main import create_app  # noqa: E402  (import needs the path above)

# Vercel looks for a top-level ASGI application called `app`.
app = create_app()
