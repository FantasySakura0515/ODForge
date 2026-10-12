"""Editor HTTP routes, mountable on any app (``odforge edit`` or ``odforge serve``).

The browser asks for the scene, sends operations, and gets the new scene back;
every accepted operation is already on disk when the response arrives. Fonts
and pictures embedded in the package are served so the browser draws text in
the document's own typefaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .odpedit import Document, EditError

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


def add_editor_routes(app: FastAPI, base: str, resolve: Callable[[str], Document]) -> None:
    """Register the editor API under ``base``.

    ``base`` may hold a ``{doc_id}`` segment (``/api/docs/{doc_id}``) for a server
    that edits many documents; without one, ``resolve`` is called with ``""``.
    ``resolve`` raises ``HTTPException`` for a document that cannot be edited.
    """

    def guarded(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(base)
    def get_doc(doc_id: str = "") -> Dict[str, Any]:
        return resolve(doc_id).state()

    @app.get(f"{base}/version")
    def get_version(doc_id: str = "") -> Dict[str, Any]:
        # Polled by the page: a changed number means someone else saved the file.
        return {"version": resolve(doc_id).poll()}

    @app.post(f"{base}/ops")
    def post_ops(body: OpsBody, doc_id: str = "") -> Dict[str, Any]:
        document = resolve(doc_id)
        return guarded(lambda: document.apply(body.ops))

    @app.post(f"{base}/clip")
    def post_clip(body: ClipBody, doc_id: str = "") -> Dict[str, Any]:
        document = resolve(doc_id)
        return guarded(lambda: {"items": document.clip(body.ids)})

    @app.post(f"{base}/undo")
    def post_undo(doc_id: str = "") -> Dict[str, Any]:
        return resolve(doc_id).undo()

    @app.post(f"{base}/redo")
    def post_redo(doc_id: str = "") -> Dict[str, Any]:
        return resolve(doc_id).redo()

    @app.get(base + "/file/{member:path}")
    def get_member(member: str, doc_id: str = "") -> Response:
        if not member.startswith(_SERVABLE_PREFIXES):
            raise HTTPException(status_code=404, detail="not found")
        data = resolve(doc_id).member(member)
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

    @app.get(f"{base}/download")
    def download(doc_id: str = "") -> FileResponse:
        document = resolve(doc_id)
        return FileResponse(
            document.path,
            media_type="application/vnd.oasis.opendocument.presentation",
            filename=document.path.name,
        )
