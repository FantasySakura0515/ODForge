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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sse_starlette import EventSourceResponse

from odforge import __version__
from odforge.critic import QAReport, run_qa_loop
from odforge.ir import Outline, PageRole, Presentation
from odforge.llm import generate_outline, generate_slides
from odforge.preview import PreviewUnavailable, render_pages
from odforge.render import render
from odforge.validate import validate_odf

# The odp mimetype, verbatim per the project's ODF mimetype constants.
ODP_MIME = "application/vnd.oasis.opendocument.presentation"

# CORS: an explicit localhost dev allowlist (never wildcard-with-credentials).
# This API has no auth and spends the user's real LLM key, so a permissive
# ``*`` + credentials origin would let ANY site the user visits drive it from
# their browser. Override with ``ODFORGE_CORS_ORIGINS`` (comma-separated).
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def _cors_origins() -> List[str]:
    """Resolve the CORS allowlist from ``ODFORGE_CORS_ORIGINS`` or the default."""
    raw = os.environ.get("ODFORGE_CORS_ORIGINS")
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if origins:
            return origins
    return list(_DEFAULT_CORS_ORIGINS)

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
    # Strong ref to the background runner so the loop doesn't GC a "fire and
    # forget" task before it finishes.
    task: Optional["asyncio.Task"] = None

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
    """Rasterise the deck to PNGs, emit the ``libreoffice`` gate + a ``preview_ready``.

    Best-effort: the previews degrade, they never crash the pipeline. The
    ``libreoffice`` gate reports the real outcome:

    * ``render_pages`` succeeds → ``gate_result{libreoffice, pass}`` then one
      ``preview_ready`` per page.
    * :class:`PreviewUnavailable` (no soffice) → ``gate_result{libreoffice,
      skipped}`` (an absent tool is skipped, not a failure).
    * any other exception → ``gate_result{libreoffice, fail}`` but the job
      continues — previews are best-effort.
    """
    if job.ir is None:
        return
    try:
        paths = await asyncio.to_thread(render_pages, job.odp_path, job.preview_dir)
    except PreviewUnavailable:
        await _emit(job, "gate_result", {"gate": "libreoffice", "status": "skipped"})
        return
    except Exception:  # noqa: BLE001 - preview is best-effort; report + continue
        await _emit(job, "gate_result", {"gate": "libreoffice", "status": "fail"})
        return
    await _emit(job, "gate_result", {"gate": "libreoffice", "status": "pass"})
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

        # Deterministic validation gates (zip/mimetype + XML well-formed) on the
        # freshly rendered deck, using validate.py's existing functions. Each
        # emits a gate_result; a failure of either stops the pipeline (the
        # existing except emits an ``error`` event carrying stage="validate").
        stage = "validate"
        report = await asyncio.to_thread(validate_odf, job.odp_path)
        zip_ok, zip_msg = report.gates["structure"]
        xml_ok, xml_msg = report.gates["xml"]
        await _emit(
            job, "gate_result", {"gate": "zip", "status": "pass" if zip_ok else "fail"}
        )
        await _emit(
            job, "gate_result", {"gate": "xml", "status": "pass" if xml_ok else "fail"}
        )
        if not (zip_ok and xml_ok):
            problems = [m for ok, m in ((zip_ok, zip_msg), (xml_ok, xml_msg)) if not ok]
            raise RuntimeError("ODF 驗證未通過:" + ";".join(problems))

        stage = "preview"
        await _emit_previews(job)

        if job.qa:
            stage = "qa"
            job.status = "qa"
            await _run_qa_loop(job, outline)
            # More than one round means QA repaired slides and re-rendered, so the
            # first-pass PNGs are now stale — refresh them. A clean single round
            # changed nothing, so don't re-emit redundant previews.
            if job.qa_report is not None and job.qa_report.rounds > 1:
                await _emit_previews(job)

        # Design gate: reflects the QA outcome. QA off → skipped; QA on →
        # pass/fail from ``final_ok``; a swallowed QA exception (report is None)
        # → skipped (QA must never fail the run — existing behaviour).
        if not job.qa:
            design_status = "skipped"
        elif job.qa_report is None:
            design_status = "skipped"
        elif job.qa_report.final_ok:
            design_status = "pass"
        else:
            design_status = "fail"
        await _emit(job, "gate_result", {"gate": "design", "status": design_status})

        job.status = "complete"
        data: Dict[str, Any] = {"download_url": _download_url(job)}
        if job.qa_report is not None:
            data["qa_report"] = job.qa_report.model_dump(mode="json")
        await _emit(job, "complete", data)
    except Exception as exc:  # noqa: BLE001 - surface as an error event, never 500
        job.status = "error"
        job.error = {"message": str(exc), "stage": stage}
        await _emit(job, "error", {"message": str(exc), "stage": stage})


async def regenerate_slide(
    job: Job, n: int, instruction: Optional[str]
) -> Dict[str, Any]:
    """Regenerate ONLY page ``n`` and return the fresh slide + preview URL.

    Builds a one-page sub-outline from that page's role/title/gist (the same
    shape the QA repair path uses), folding ``instruction`` into the gist, and
    re-runs stage 2 against just that page. Untouched pages are never re-sent,
    so they cannot drift. Then the deck is re-rendered and page ``n``'s preview
    refreshed.

    This is a **synchronous** operation (unlike initial generation): the result
    comes back to the caller in the HTTP response, not via the SSE stream — that
    stream's lifecycle already ended at ``complete``. Returns
    ``{"n", "slide", "preview_url"}``; ``preview_url`` is ``None`` when previews
    are unavailable (no soffice).
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

    await asyncio.to_thread(render, job.ir, job.odp_path)
    try:
        paths = await asyncio.to_thread(render_pages, job.odp_path, job.preview_dir)
    except Exception:  # noqa: BLE001 - best-effort (PreviewUnavailable et al.)
        paths = []
    preview_url = _preview_url(job, n) if len(paths) >= n else None
    return {
        "n": n,
        "slide": job.ir.slides[n - 1].model_dump(mode="json"),
        "preview_url": preview_url,
    }


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


def frontend_dist() -> Optional[Path]:
    """Path to the built web frontend (``odforge/web/dist``) if present, else None.

    Lets ``odforge serve`` host the whole cockpit from one process. Absent when
    the frontend hasn't been built (or when installed without the web tree) — the
    API then runs standalone and a dev server proxies ``/api`` to it.
    """
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    return dist if dist.is_dir() else None


def create_app(jobs_dir: Optional[Path] = None) -> FastAPI:
    """Build the ODForge Web API application.

    ``jobs_dir`` overrides where per-job artifacts live (defaults to
    ``%TEMP%/odforge-jobs``); tests point it at a temp path for isolation.
    """
    app = FastAPI(title="ODForge Web API", version=__version__)
    # Explicit origin allowlist, credentials OFF. A local browser can still reach
    # 127.0.0.1, so binding to localhost is NOT a substitute — the origin check
    # is what stops a random visited site from driving this tool.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
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
        # Background task on the running loop; keep a ref so it isn't GC'd.
        job.task = asyncio.create_task(run_job(job))
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request) -> EventSourceResponse:
        job = _get_job(job_id)

        async def event_stream():
            cursor = 0
            while True:
                # Replay/emit everything appended since our cursor. The generation
                # lifecycle ends at ``complete`` (or ``error``); the stream closes
                # there. Post-generation edits use the synchronous /regenerate
                # endpoint (its result comes back in the HTTP response, not here),
                # so nothing meaningful is appended after the terminal event.
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
        try:
            result = await regenerate_slide(job, n, body.instruction)
        except Exception as exc:  # noqa: BLE001 - clean error, never a 500 traceback
            # Clean, structured error (the run_job error shape) instead of a bare
            # 500 traceback; job.status is left intact — the existing deck is fine.
            raise HTTPException(
                status_code=500,
                detail={"message": str(exc), "stage": "regenerate"},
            )
        return {"ok": True, **result}

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

    # Host the built frontend at "/" so one `odforge serve` runs the whole cockpit.
    # Mounted last, after the /api routes, so those still take precedence; skipped
    # entirely when the frontend hasn't been built (API-only mode).
    dist = frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app
