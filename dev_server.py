"""Local stand-in for `vercel dev`: serves web/ and forwards POST /api/query to web/api/query.py.

Run from the repo root: `.venv/bin/python3 dev_server.py`, then open http://localhost:8000.
Loads ANTHROPIC_API_KEY, UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN from web/.env into this
process only, and never prints them. Without the Upstash values, search fails closed with a 503.
Sends `Cache-Control: no-store`, so a browser never shows stale CSS or JS after an edit.
"""
import functools
import importlib.util
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8000"))
WEB = Path(__file__).resolve().parent / "web"

SECRETS = ("ANTHROPIC_API_KEY", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")
env_file = WEB / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key in SECRETS:
            os.environ[key] = value.strip().strip("\"'")

spec = importlib.util.spec_from_file_location("query", WEB / "api" / "query.py")
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)


class Dev(query.handler, SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/api/query":
            return query.handler.do_GET(self)
        SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        if self.path.split("?")[0] == "/api/query":
            return query.handler.do_POST(self)
        self.send_error(404)


if __name__ == "__main__":
    print(f"Serving {WEB} on http://localhost:{PORT} (search calls the real API)")
    ThreadingHTTPServer(("", PORT), functools.partial(Dev, directory=str(WEB))).serve_forever()
