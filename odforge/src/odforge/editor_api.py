"""``odforge edit deck.odp``: one process, one file, the editor page.

The routes themselves live in :mod:`odforge.editor_routes`, shared with the web
console (``odforge serve``), which mounts them per generated deck.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .editor_routes import add_editor_routes
from .odpedit import Document
from .webapi import frontend_dist


def create_editor_app(path: Path) -> FastAPI:
    document = Document(Path(path))
    app = FastAPI(title="ODForge Editor")
    app.state.document = document
    add_editor_routes(app, "/api/doc", lambda _doc_id: document)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/editor.html")

    dist = frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
