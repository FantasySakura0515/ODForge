"""HTTP API for the web editor (``odforge edit deck.odp``).

One process edits one file. The browser asks for the scene, sends operations,
and gets the new scene back; every accepted operation is already on disk when
the response arrives. Fonts and pictures embedded in the package are served so
the browser draws text in the document's own typefaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .odpedit import Document, EditError
from .webapi import frontend_dist

# Package members the browser may fetch: what the scene references, nothing else.
_SERVABLE_PREFIXES = ("Fonts/", "Pictures/", "media/")
# …and only as these types. HTML, XML and scripts never get an active type.
_SERVABLE_TYPES = {
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


class OpsBody(BaseModel):
    ops: List[Dict[str, Any]] = Field(min_length=1, max_length=500)


class ClipBody(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=500)


def create_editor_app(path: Path) -> FastAPI:
    document = Document(Path(path))
    app = FastAPI(title="ODForge Editor")
    app.state.document = document

    @app.get("/api/doc")
    def get_doc() -> Dict[str, Any]:
        return document.state()

    @app.get("/api/doc/version")
    def get_version() -> Dict[str, Any]:
        # Polled by the page: a changed number means someone else saved the file.
        return {"version": document.poll()}

    @app.post("/api/doc/ops")
    def post_ops(body: OpsBody) -> Dict[str, Any]:
        try:
            return document.apply(body.ops)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/doc/clip")
    def post_clip(body: ClipBody) -> Dict[str, Any]:
        try:
            return {"items": document.clip(body.ids)}
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/doc/undo")
    def post_undo() -> Dict[str, Any]:
        return document.undo()

    @app.post("/api/doc/redo")
    def post_redo() -> Dict[str, Any]:
        return document.redo()

    @app.get("/api/doc/file/{member:path}")
    def get_member(member: str) -> Response:
        if not member.startswith(_SERVABLE_PREFIXES):
            raise HTTPException(status_code=404, detail="not found")
        data = document.member(member)
        if data is None:
            raise HTTPException(status_code=404, detail="not found")
        kind = _SERVABLE_TYPES.get(Path(member).suffix.lower(), "application/octet-stream")
        # The package is untrusted input: whatever it holds is served as inert data.
        # SVG only ever reaches the page through <img>, where scripts never run; the
        # sandbox policy covers someone opening the URL directly.
        return Response(
            data,
            media_type=kind,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            },
        )

    @app.get("/api/doc/download")
    def download() -> FileResponse:
        return FileResponse(
            document.path,
            media_type="application/vnd.oasis.opendocument.presentation",
            filename=document.path.name,
        )

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/editor.html")

    dist = frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
