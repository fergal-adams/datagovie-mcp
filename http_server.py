#!/usr/bin/env python3
"""
Public, hosted version of datagovie-mcp (remote MCP over Streamable HTTP).

Anyone can add the deployed URL (https://<your-host>/mcp) as a custom connector
in Claude, with no install or config file needed. Read-only, so no login is required.

Run locally:  python3 http_server.py      -> http://localhost:8000/mcp
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

os.environ.setdefault("DATAGOVIE_MAX_MB", "25")  # tighter default for a shared server
import server  # noqa: E402  (reads the env var above at import time)

PORT = int(os.environ.get("PORT", "8000"))
GLOBAL_PER_MIN = int(os.environ.get("RATE_LIMIT_GLOBAL_PER_MIN", "300"))
IP_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_IP_PER_MIN", "0"))  # 0 = off (see README)


class Bucket:
    """Token bucket: `rate` requests per minute, with bursts up to `rate`."""

    def __init__(self, rate: int):
        self.rate, self.tokens, self.t = rate, float(rate), time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.rate, self.tokens + (now - self.t) * self.rate / 60)
        self.t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class RateLimit:
    """ASGI middleware limiting MCP calls (POST /mcp) globally and, optionally, per IP."""

    def __init__(self, app):
        self.app = app
        self.global_bucket = Bucket(GLOBAL_PER_MIN) if GLOBAL_PER_MIN else None
        self.ip_buckets: dict[str, Bucket] = defaultdict(lambda: Bucket(IP_PER_MIN))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].startswith("/mcp"):
            ok = True
            if self.global_bucket and not self.global_bucket.take():
                ok = False
            if ok and IP_PER_MIN:
                headers = dict(scope.get("headers") or [])
                fwd = headers.get(b"x-forwarded-for", b"").decode().split(",")[0].strip()
                ip = fwd or (scope.get("client") or ("?",))[0]
                if len(self.ip_buckets) > 50_000:
                    self.ip_buckets.clear()
                ok = self.ip_buckets[ip].take()
            if not ok:
                body = json.dumps({"error": "Rate limit reached. Please try again in a minute."}).encode()
                await send({"type": "http.response.start", "status": 429,
                            "headers": [(b"content-type", b"application/json"), (b"retry-after", b"30")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


@server.mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


@server.mcp.custom_route("/", methods=["GET"])
async def home(_: Request) -> JSONResponse:
    return JSONResponse({
        "name": "data.gov.ie MCP server",
        "mcp_endpoint": "/mcp",
        "how_to_use": "In Claude: Customize > Connectors > + > Add custom connector, then paste this site's URL followed by /mcp",
        "data": "https://data.gov.ie (mostly CC BY 4.0, credit the publisher)",
    })


def build_app():
    # DNS-rebinding protection is for servers on localhost; this one is public and
    # read-only, so it's left off (the SDK default for non-localhost hosts).
    app = server.mcp.streamable_http_app(
        stateless_http=True,   # no sticky sessions: simple to host, safe to restart
        json_response=True,
        host="0.0.0.0",
    )
    return RateLimit(app)


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, proxy_headers=True, forwarded_allow_ips="*")
