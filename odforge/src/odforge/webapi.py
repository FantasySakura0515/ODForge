"""ODForge Web API — FastAPI + SSE over the two-stage generation pipeline.

This module wraps the *same* machinery the CLI uses (``generate_outline`` →
``generate_slides`` → ``render`` → ``render_pages`` → optional ``run_qa_loop``)
behind an HTTP + Server-Sent-Events surface a browser front-end drives. It is an
**optional extra**: nothing in ``odforge``'s core import path imports this module,
and FastAPI / sse-starlette are only required when serving or testing the web
layer (``pip install "odforge[web]"``). Construct the app via :func:`create_app`.

Security model (path whitelist)
-------------------------------
No caller-supplied filesystem path is ever opened. A job id is a server-minted
``uuid4().hex`` (never echoed caller input) that maps — only as a dict key — to a
per-job directory the server created under ``%TEMP%/odforge-jobs/{id}/``. Page
indices arrive as validated integers and are formatted into a fixed server-owned
name (``page-NN.png``); an out-of-range page is a 404, never a path lookup. There
is therefore no way for a request to name a path outside its own job directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from sse_starlette import EventSourceResponse

from odforge import __version__
from odforge.critic import QAReport, run_qa_loop
from odforge.ir import Outline, PageRole, Presentation
from odforge.llm import generate_outline, generate_slides
from odforge.preview import render_pages
from odforge.render import render

# The odp mimetype, verbatim per the project's ODF mimetype constants.
ODP_MIME = "application/vnd.oasis.opendocument.presentation"

# How long an idle SSE subscriber sleeps between liveness checks. Small enough
# that a newly-emitted event is delivered promptly; a fallback only — emits also
# fire ``job.updated`` to wake the subscriber immediately.
_SSE_POLL_SECONDS = 0.4


# ---------------------------------------------------------------------------
# Job model + in-memory store
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """One generation request and everything the API needs to serve it.

    Lives only in memory (``app.state.jobs``); its artifacts live under the
    server-owned :attr:`dir`. ``events`` is an append-only log of every SSE
    event emitted, so a late/reconnecting subscriber can be replayed the whole
    history and the snapshot endpoint can reconstruct state.
    """

    id: str
    dir: Path
    prompt: str
    mode: Optional[str] = None
    theme: Optional[str] = None
    interactive: bool = False
    qa: bool = False
    backend: Optional[str] = None

    status: str = "pending"
    outline: Optional[Outline] = None
    ir: Optional[Presentation] = None
    slides_done: int = 0
    qa_report: Optional[QAReport] = None
    error: Optional[Dict[str, str]] = None

    events: List[Dict[str, Any]] = field(default_factory=list)
    # Set on approve (interactive gate) and on every emit (wakes SSE subscribers).
    approval: asyncio.Event = field(default_factory=asyncio.Event)
    updated: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def odp_path(self) -> Path:
        return self.dir / "deck.odp"

    @property
    def preview_dir(self) -> Path:
        return self.dir / "preview"


def create_job(
    app: FastAPI,
    *,
    prompt: str,
    mode: Optional[str] = None,
    theme: Optional[str] = None,
    interactive: bool = False,
    qa: bool = False,
    backend: Optional[str] = None,
) -> Job:
    """Register a new job with a server-minted id and its own artifact dir."""
    job_id = uuid.uuid4().hex
    job_dir = Path(app.state.jobs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=job_id,
        dir=job_dir,
        prompt=prompt,
        mode=mode,
        theme=theme,
        interactive=interactive,
        qa=qa,
        backend=backend,
    )
    app.state.jobs[job_id] = job
    return job


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


async def _emit(job: Job, event: str, data: Any) -> None:
    """Append an SSE event to the job's log and wake any subscribers."""
    job.events.append({"event": event, "data": data})
    job.updated.set()


def _download_url(job: Job) -> str:
    return f"/api/jobs/{job.id}/download"


def _preview_url(job: Job, n: int) -> str:
    return f"/api/jobs/{job.id}/preview/{n}.png"


# ---------------------------------------------------------------------------
# The pipeline runner (a background asyncio task per job)
# ---------------------------------------------------------------------------


async def _emit_previews(job: Job) -> None:
    """Rasterise the deck to PNGs and emit a ``preview_ready`` per page.

    Best-effort: if LibreOffice/soffice is unavailable (or the conversion
    fails for any reason) the previews are simply skipped — the pipeline
    degrades, it never crashes on a missing preview.
    """
    if job.ir is None:
        return
    try:
        paths = await asyncio.to_thread(render_pages, job.odp_path, job.preview_dir)
    except Exception:  # noqa: BLE001 - preview is best-effort (PreviewUnavailable et al.)
        return
    for n, _path in enumerate(paths, start=1):
        await _emit(job, "preview_ready", {"n": n, "url": _preview_url(job, n)})


async def _run_qa_loop(job: Job, outline: Optional[Outline]) -> None:
    """Run the design-QA loop and emit one ``qa_round`` per round.

    Uses the same ``run_qa_loop`` the CLI's ``--qa`` calls. The vision backend
    comes from ``ODFORGE_VISION_BACKEND`` (default ``off``); with it off (or no
    soffice) the loop degrades to zero rounds. Any QA error is swallowed — QA
    must never fail the generation. Because ``run_qa_loop`` runs its rounds
    internally, the per-round events are emitted from its returned report.
    """
    vision_backend = os.environ.get("ODFORGE_VISION_BACKEND", "off")
    try:
        report = await asyncio.to_thread(
            run_qa_loop,
            job.ir,
            job.odp_path,
            outline=outline,
            backend=vision_backend,
            llm_backend=job.backend,
        )
    except Exception:  # noqa: BLE001 - QA is opt-in and must never crash the run
        return
    job.qa_report = report
    for round_no, findings in enumerate(report.findings_by_round, start=1):
        await _emit(
            job,
            "qa_round",
            {
                "round": round_no,
                "findings": [f.model_dump(mode="json") for f in findings],
            },
        )


async def run_job(job: Job) -> None:
    """Drive one job through the two-stage pipeline, emitting SSE events.

    Stages: outline → (await approval if interactive) → slides → render →
    previews → optional QA → complete. Any unexpected failure emits a single
    ``error`` event carrying the stage it failed in and stops the job.
    """
    stage = "outline"
    try:
        job.status = "generating_outline"
        outline = await asyncio.to_thread(generate_outline, job.prompt, job.backend)
        if job.mode:
            outline = outline.model_copy(update={"mode": job.mode})
        job.outline = outline
        await _emit(job, "outline", outline.model_dump(mode="json"))

        if job.interactive:
            job.status = "awaiting_approval"
            await _emit(job, "awaiting_approval", {})
            await job.approval.wait()
            # The /outline endpoint may have replaced job.outline (an edit).
            outline = job.outline

        stage = "slides"
        job.status = "generating_slides"
        ir = await asyncio.to_thread(generate_slides, outline, job.backend)
        # --theme parity: lock a preset and drop any LLM-chosen design.
        if job.theme:
            ir = ir.model_copy(update={"theme": job.theme, "design": None})
        job.ir = ir
        for n, slide in enumerate(ir.slides, start=1):
            job.slides_done = n
            await _emit(
                job, "slide_done", {"n": n, "slide": slide.model_dump(mode="json")}
            )

        stage = "render"
        job.status = "rendering"
        await asyncio.to_thread(render, ir, job.odp_path)

        stage = "preview"
        await _emit_previews(job)

        if job.qa:
            stage = "qa"
            job.status = "qa"
            await _run_qa_loop(job, outline)
            # QA may repair slides + re-render; refresh the previews once more.
            await _emit_previews(job)

        job.status = "complete"
        data: Dict[str, Any] = {"download_url": _download_url(job)}
        if job.qa_report is not None:
            data["qa_report"] = job.qa_report.model_dump(mode="json")
        await _emit(job, "complete", data)
    except Exception as exc:  # noqa: BLE001 - surface as an error event, never 500
        job.status = "error"
        job.error = {"message": str(exc), "stage": stage}
        await _emit(job, "error", {"message": str(exc), "stage": stage})


async def regenerate_slide(job: Job, n: int, instruction: Optional[str]) -> None:
    """Regenerate ONLY page ``n`` and push a fresh slide + preview.

    Builds a one-page sub-outline from that page's role/title/gist (the same
    shape the QA repair path uses), folding ``instruction`` into the gist, and
    re-runs stage 2 against just that page. Untouched pages are never re-sent,
    so they cannot drift. Then the deck is re-rendered and page ``n``'s preview
    refreshed.
    """
    assert job.ir is not None and job.outline is not None  # guarded by caller
    base = job.outline.pages[n - 1]
    gist = base.gist
    if instruction:
        gist = f"{gist}（調整指示：{instruction}）"
    sub = Outline(
        design=job.outline.design,
        mode=job.outline.mode,
        pages=[PageRole(role=base.role, title=base.title, gist=gist)],
    )
    repaired = await asyncio.to_thread(generate_slides, sub, job.backend)
    if repaired.slides:
        job.ir.slides[n - 1] = repaired.slides[0]
    await _emit(
        job,
        "slide_done",
        {"n": n, "slide": job.ir.slides[n - 1].model_dump(mode="json")},
    )

    await asyncio.to_thread(render, job.ir, job.odp_path)
    try:
        paths = await asyncio.to_thread(render_pages, job.odp_path, job.preview_dir)
    except Exception:  # noqa: BLE001 - best-effort (PreviewUnavailable et al.)
        paths = []
    if len(paths) >= n:
        await _emit(job, "preview_ready", {"n": n, "url": _preview_url(job, n)})


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class GenerateBody(BaseModel):
    prompt: str
    mode: Optional[str] = None
    theme: Optional[str] = None
    interactive: bool = False
    qa: bool = False
    backend: Optional[str] = None


class OutlineActionBody(BaseModel):
    action: str
    outline: Optional[dict] = None


class RegenerateBody(BaseModel):
    instruction: Optional[str] = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(jobs_dir: Optional[Path] = None) -> FastAPI:
    """Build the ODForge Web API application.

    ``jobs_dir`` overrides where per-job artifacts live (defaults to
    ``%TEMP%/odforge-jobs``); tests point it at a temp path for isolation.
    """
    app = FastAPI(title="ODForge Web API", version=__version__)
    # Local developer tool: CORS wide open so any front-end origin can drive it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.jobs = {}
    base = Path(jobs_dir) if jobs_dir is not None else Path(tempfile.gettempdir()) / "odforge-jobs"
    base.mkdir(parents=True, exist_ok=True)
    app.state.jobs_dir = base

    def _get_job(job_id: str) -> Job:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.post("/api/generate")
    async def generate(body: GenerateBody) -> Dict[str, str]:
        job = create_job(
            app,
            prompt=body.prompt,
            mode=body.mode,
            theme=body.theme,
            interactive=body.interactive,
            qa=body.qa,
            backend=body.backend,
        )
        # Fire-and-forget background task on the running loop.
        asyncio.create_task(run_job(job))
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request) -> EventSourceResponse:
        job = _get_job(job_id)

        async def event_stream():
            cursor = 0
            while True:
                # Replay/emit everything appended since our cursor.
                while cursor < len(job.events):
                    ev = job.events[cursor]
                    cursor += 1
                    yield {
                        "event": ev["event"],
                        "data": json.dumps(ev["data"], ensure_ascii=False),
                    }
                    if ev["event"] in ("complete", "error"):
                        return
                if await request.is_disconnected():
                    return
                # Wait for the next emit; the timeout bounds liveness (and makes
                # a missed wakeup at worst a sub-second delay, never a lost event
                # since the loop re-reads len(job.events) each pass).
                job.updated.clear()
                try:
                    await asyncio.wait_for(job.updated.wait(), timeout=_SSE_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass

        return EventSourceResponse(event_stream())

    @app.post("/api/jobs/{job_id}/outline")
    async def outline_action(job_id: str, body: OutlineActionBody) -> Dict[str, Any]:
        job = _get_job(job_id)
        if job.status != "awaiting_approval":
            raise HTTPException(
                status_code=409, detail="job is not awaiting outline approval"
            )
        if body.action == "edit":
            if body.outline is None:
                raise HTTPException(
                    status_code=422, detail="action 'edit' requires an outline"
                )
            try:
                job.outline = Outline.model_validate(body.outline)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"invalid outline: {exc}")
        elif body.action != "approve":
            raise HTTPException(
                status_code=422, detail="action must be 'approve' or 'edit'"
            )
        job.approval.set()
        return {"ok": True, "status": job.status}

    @app.post("/api/jobs/{job_id}/slides/{n}/regenerate")
    async def regenerate(job_id: str, n: int, body: RegenerateBody) -> Dict[str, Any]:
        job = _get_job(job_id)
        if job.ir is None or job.outline is None:
            raise HTTPException(
                status_code=409, detail="job is not ready for regeneration"
            )
        if not 1 <= n <= len(job.ir.slides):
            raise HTTPException(status_code=404, detail="slide index out of range")
        await regenerate_slide(job, n, body.instruction)
        return {"ok": True, "n": n}

    @app.get("/api/jobs/{job_id}/preview/{n}.png")
    async def preview(job_id: str, n: int) -> FileResponse:
        job = _get_job(job_id)
        total = len(job.ir.slides) if job.ir is not None else 0
        if not 1 <= n <= total:
            raise HTTPException(status_code=404, detail="preview page out of range")
        # Server-owned path only: fixed name in the job's own preview dir.
        path = job.preview_dir / f"page-{n:02d}.png"
        if not path.exists():
            raise HTTPException(status_code=404, detail="preview not available")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str) -> FileResponse:
        job = _get_job(job_id)
        if not job.odp_path.exists():
            raise HTTPException(status_code=404, detail="deck not ready")
        return FileResponse(
            job.odp_path, media_type=ODP_MIME, filename=f"{job.id}.odp"
        )

    @app.get("/api/jobs/{job_id}")
    async def snapshot(job_id: str) -> Dict[str, Any]:
        job = _get_job(job_id)
        data: Dict[str, Any] = {
            "status": job.status,
            "slides_done": job.slides_done,
            "outline": job.outline.model_dump(mode="json") if job.outline else None,
        }
        if job.qa_report is not None and job.qa_report.findings_by_round:
            last = job.qa_report.findings_by_round[-1]
            data["findings"] = [f.model_dump(mode="json") for f in last]
        if job.status == "complete":
            data["download_url"] = _download_url(job)
        if job.error is not None:
            data["error"] = job.error
        return data

    return app
