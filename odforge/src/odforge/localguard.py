"""Keep a local tool's HTTP API to the browser tab it opened.

Binding to 127.0.0.1 stops other machines, not other web pages: any site the
user visits can send requests to ``http://127.0.0.1:<port>``, and a DNS-rebinding
attack can even read the answers. So every request must name this server in its
Host header, and anything that changes state must come from this server's own
origin. Requests without an Origin header (curl, scripts) are not cross-site
browser requests and pass.
"""

from __future__ import annotations

from typing import Iterable

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import PlainTextResponse

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def local_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def add_local_guard(app: FastAPI, hosts: Iterable[str]) -> None:
    allowed_hosts = {h.lower() for h in hosts}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        if host not in allowed_hosts:
            return PlainTextResponse("forbidden host", status_code=403)
        origin = request.headers.get("origin")
        if (
            request.method not in _SAFE_METHODS
            and origin is not None
            and origin.lower() not in allowed_origins
        ):
            return PlainTextResponse("forbidden origin", status_code=403)
        return await call_next(request)
