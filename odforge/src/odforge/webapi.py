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
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from sse_starlette import EventSourceResponse

from odforge import critic, llm
from odforge import __version__
from odforge.critic import QAReport, run_qa_loop
from odforge.ir import MediaAssetRef, Outline, PageRole, Presentation
from odforge.llm import (
    DiscoveryPlan,
    generate_discovery_questions,
    generate_outline,
    generate_slides,
)
from odforge.media import AssetBlob, AssetInput, MediaError, decode_data_uri
from odforge.preview import PreviewUnavailable, render_pages
from odforge.references import (
    ReferenceDocument,
    ReferenceError,
    decode_pdf_data_uri,
    format_reference_context,
    is_pdf_data_uri,
)
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
            for origin in origins:
                if not _is_explicit_http_origin(origin):
                    raise ValueError(
                        "ODFORGE_CORS_ORIGINS must contain explicit http(s) origins; "
                        f"unsafe value: {origin!r}"
                    )
            return origins
    return list(_DEFAULT_CORS_ORIGINS)


def _is_explicit_http_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    return (
        origin.lower() not in {"*", "null"}
        and parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )

# How long an idle SSE subscriber sleeps between liveness checks. Small enough
# that a newly-emitted event is delivered promptly; a fallback only — emits also
# fire ``job.updated`` to wake the subscriber immediately.
_SSE_POLL_SECONDS = 0.4
_MAX_ACTIVE_JOBS = 4
_MAX_ACTIVE_REGENERATIONS = 2
_MAX_RETAINED_JOBS = 100
_JOB_RETENTION_SECONDS = 90 * 24 * 60 * 60
_APPROVAL_TIMEOUT_SECONDS = 30 * 60
_TERMINAL_JOB_STATUSES = {"complete", "error", "cancelled"}
_SESSION_FILENAME = "session.json"
_DISCOVERY_HEARTBEAT_SECONDS = 3.0
_DISCOVERY_STAGE_MESSAGES = {
    "accepted": "已收到需求",
    "preparing": "正在整理設定與參考文件",
    "requesting": "正在產生關鍵問題",
    "retrying": "格式不符，正在重試",
    "validating": "正在檢查問題",
    "complete": "問題準備完成",
    "waiting": "模型仍在處理",
}

# Reuse uvicorn's configured error logger so lifecycle timings are visible in
# the same console (and any redirected server log) without requiring callers
# to install a separate logging configuration.
logger = logging.getLogger("uvicorn.error")


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
    # Per-job vision source; ``None`` falls back to ODFORGE_VISION_BACKEND.
    vision_backend: Optional[str] = None
    pages: Optional[int] = None
    assets: Dict[str, AssetInput] = field(default_factory=dict)
    asset_refs: List[MediaAssetRef] = field(default_factory=list)
    reference_documents: List[ReferenceDocument] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

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
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def odp_path(self) -> Path:
        return self.dir / "deck.odp"

    @property
    def preview_dir(self) -> Path:
        return self.dir / "preview"


def _job_metadata(job: Job) -> Dict[str, Any]:
    assets: dict[str, str] = {}
    for asset_id, value in job.assets.items():
        if not isinstance(value, (str, Path)):
            continue
        path = Path(value)
        try:
            assets[asset_id] = path.relative_to(job.dir).as_posix()
        except ValueError:
            continue
    return {
        "schema_version": 1,
        "id": job.id,
        "prompt": job.prompt,
        "mode": job.mode,
        "theme": job.theme,
        "interactive": job.interactive,
        "qa": job.qa,
        "vision_backend": job.vision_backend,
        "backend": job.backend,
        "pages": job.pages,
        "assets": assets,
        "asset_refs": [ref.model_dump(mode="json") for ref in job.asset_refs],
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "status": job.status,
        "outline": (
            job.outline.model_dump(mode="json") if job.outline is not None else None
        ),
        "ir": job.ir.model_dump(mode="json") if job.ir is not None else None,
        "slides_done": job.slides_done,
        "qa_report": (
            job.qa_report.model_dump(mode="json")
            if job.qa_report is not None
            else None
        ),
        "error": job.error,
        "events": job.events,
    }


def _persist_job(job: Job) -> None:
    """Atomically persist resumable session state without uploaded document bytes."""

    target = job.dir / _SESSION_FILENAME
    temporary = job.dir / f"{_SESSION_FILENAME}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                _job_metadata(job),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(target)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("session_persist_failed job_id=%s error=%s", job.id, exc)
        with suppress(OSError):
            temporary.unlink()


def _safe_restored_asset(job_dir: Path, relative: str) -> Optional[Path]:
    candidate = (job_dir / relative).resolve()
    try:
        candidate.relative_to(job_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _load_persisted_job(job_dir: Path) -> Optional[Job]:
    metadata_path = job_dir / _SESSION_FILENAME
    if not metadata_path.is_file() or not re.fullmatch(r"[0-9a-f]{32}", job_dir.name):
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        if data.get("id") != job_dir.name:
            return None
        job = Job(
            id=job_dir.name,
            dir=job_dir,
            prompt=str(data.get("prompt", "")),
            mode=data.get("mode"),
            theme=data.get("theme"),
            interactive=bool(data.get("interactive", False)),
            qa=bool(data.get("qa", False)),
            vision_backend=data.get("vision_backend"),
            backend=data.get("backend"),
            pages=data.get("pages"),
            created_at=float(data.get("created_at", time.time())),
            finished_at=(
                float(data["finished_at"])
                if data.get("finished_at") is not None
                else None
            ),
            status=str(data.get("status", "error")),
            outline=(
                Outline.model_validate(data["outline"])
                if data.get("outline") is not None
                else None
            ),
            ir=(
                Presentation.model_validate(data["ir"])
                if data.get("ir") is not None
                else None
            ),
            slides_done=int(data.get("slides_done", 0)),
            qa_report=(
                QAReport.model_validate(data["qa_report"])
                if data.get("qa_report") is not None
                else None
            ),
            error=data.get("error"),
            events=[
                event
                for event in data.get("events", [])
                if isinstance(event, dict)
                and isinstance(event.get("event"), str)
                and "data" in event
            ],
            asset_refs=[
                MediaAssetRef.model_validate(item)
                for item in data.get("asset_refs", [])
            ],
        )
        for asset_id, relative in data.get("assets", {}).items():
            if not isinstance(asset_id, str) or not isinstance(relative, str):
                continue
            restored = _safe_restored_asset(job_dir, relative)
            if restored is not None:
                job.assets[asset_id] = restored
        if job.status not in _TERMINAL_JOB_STATUSES:
            job.status = "error"
            job.finished_at = time.time()
            job.error = {
                "message": "服務重啟，此工作未完成",
                "stage": "restore",
            }
            job.events.append({"event": "error", "data": job.error})
            _persist_job(job)
        return job
    except (OSError, ValueError, TypeError, ValidationError, json.JSONDecodeError) as exc:
        logger.warning(
            "session_restore_skipped path=%s error=%s",
            metadata_path,
            exc,
        )
        return None


def _load_persisted_jobs(base: Path) -> Dict[str, Job]:
    jobs: dict[str, Job] = {}
    for job_dir in base.iterdir():
        if not job_dir.is_dir():
            continue
        job = _load_persisted_job(job_dir)
        if job is not None:
            jobs[job.id] = job
    return jobs


def create_job(
    app: FastAPI,
    *,
    prompt: str,
    mode: Optional[str] = None,
    theme: Optional[str] = None,
    interactive: bool = False,
    qa: bool = False,
    vision_backend: Optional[str] = None,
    backend: Optional[str] = None,
    pages: Optional[int] = None,
    uploads: Optional[List["AssetUpload"]] = None,
) -> Job:
    """Register a new job with a server-minted id and its own artifact dir."""
    decoded_images = []
    reference_documents: list[ReferenceDocument] = []
    for upload in uploads or []:
        if is_pdf_data_uri(upload.data_url):
            reference_documents.append(
                decode_pdf_data_uri(
                    upload.data_url,
                    description=upload.description,
                    credit=upload.credit,
                )
            )
            continue
        blob = decode_data_uri(upload.data_url)
        asset_id = f"asset-{len(decoded_images) + 1:02d}"
        decoded_images.append((asset_id, upload, blob))

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
        vision_backend=vision_backend,
        backend=backend,
        pages=pages,
        reference_documents=reference_documents,
    )
    assets_dir = job_dir / "assets"
    for asset_id, upload, blob in decoded_images:
        assets_dir.mkdir(parents=True, exist_ok=True)
        path = assets_dir / f"{asset_id}{blob.extension}"
        path.write_bytes(blob.data)
        job.assets[asset_id] = path
        job.asset_refs.append(
            MediaAssetRef(
                id=asset_id,
                description=upload.description,
                credit=upload.credit,
            )
        )
    app.state.jobs[job_id] = job
    _persist_job(job)
    return job


def _prune_jobs(app: FastAPI, *, now: Optional[float] = None) -> None:
    """Remove expired/overflow terminal jobs and their server-owned artifacts."""
    current = time.time() if now is None else now
    jobs: dict[str, Job] = app.state.jobs
    expired = [
        job
        for job in jobs.values()
        if job.status in _TERMINAL_JOB_STATUSES
        and job.finished_at is not None
        and current - job.finished_at > _JOB_RETENTION_SECONDS
    ]

    remaining_after_expiry = len(jobs) - len(expired)
    overflow = max(0, remaining_after_expiry - _MAX_RETAINED_JOBS)
    expired_ids = {job.id for job in expired}
    terminal = sorted(
        (
            job
            for job in jobs.values()
            if job.id not in expired_ids and job.status in _TERMINAL_JOB_STATUSES
        ),
        key=lambda job: job.finished_at or job.created_at,
    )
    for job in [*expired, *terminal[:overflow]]:
        jobs.pop(job.id, None)
        shutil.rmtree(job.dir, ignore_errors=True)


@asynccontextmanager
async def _regeneration_slot(app: FastAPI, job: Job) -> AsyncIterator[None]:
    """Reserve one global regeneration slot and the job's mutation lock."""
    async with app.state.regeneration_counter_lock:
        if app.state.active_regenerations >= _MAX_ACTIVE_REGENERATIONS:
            raise HTTPException(
                status_code=429,
                detail=(
                    "too many active regenerations; "
                    f"limit is {_MAX_ACTIVE_REGENERATIONS}"
                ),
            )
        app.state.active_regenerations += 1

    try:
        if job.mutation_lock.locked():
            raise HTTPException(
                status_code=409, detail="regeneration already in progress"
            )
        await job.mutation_lock.acquire()
        try:
            yield
        finally:
            job.mutation_lock.release()
    finally:
        async with app.state.regeneration_counter_lock:
            app.state.active_regenerations -= 1


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


async def _emit(job: Job, event: str, data: Any) -> None:
    """Append an SSE event to the job's log and wake any subscribers."""
    job.events.append({"event": event, "data": data})
    _persist_job(job)
    job.updated.set()


def _download_url(job: Job) -> str:
    return f"/api/jobs/{job.id}/download"


def _session_title(job: Job) -> str:
    if job.ir is not None and job.ir.title.strip():
        return job.ir.title.strip()
    if job.outline is not None and job.outline.pages:
        title = job.outline.pages[0].title.strip()
        if title:
            return title
    first_line = next(
        (line.strip() for line in job.prompt.splitlines() if line.strip()),
        "未命名簡報",
    )
    return first_line[:80]


def _session_summary(job: Job) -> Dict[str, Any]:
    preview = job.preview_dir / "page-01.png"
    page_count = (
        len(job.ir.slides)
        if job.ir is not None
        else len(job.outline.pages)
        if job.outline is not None
        else 0
    )
    data: Dict[str, Any] = {
        "id": job.id,
        "title": _session_title(job),
        "prompt": job.prompt,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.finished_at or job.created_at,
        "page_count": page_count,
        "preview_url": (
            f"/api/jobs/{job.id}/preview/1.png" if preview.is_file() else None
        ),
        "download_url": (
            _download_url(job)
            if job.status == "complete" and job.odp_path.is_file()
            else None
        ),
    }
    if job.error is not None:
        data["error"] = job.error
    return data


def _persist_generated_assets(job: Job) -> None:
    """Move generated in-memory image blobs into the job's isolated directory."""

    generated = [
        (asset_id, value)
        for asset_id, value in job.assets.items()
        if isinstance(value, AssetBlob)
    ]
    if not generated:
        return
    assets_dir = job.dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for asset_id, blob in generated:
        path = assets_dir / f"{asset_id}{blob.extension}"
        path.write_bytes(blob.data)
        job.assets[asset_id] = path


# Characters illegal in a filename on common filesystems, plus newlines.
_FILENAME_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n]')


def _sanitize_filename(name: str) -> str:
    """Strip filename-illegal characters and surrounding whitespace (CJK kept)."""
    return _FILENAME_ILLEGAL.sub("", name).strip()


def _download_filename(job: Job) -> str:
    """A human, topic-based ``.odp`` filename (never the bare UUID unless empty).

    Prefers the generated deck's title (``job.ir.title``); falls back to the
    first 20 chars of the prompt; and if sanitising leaves nothing usable,
    falls back to the job id. Chinese is preserved — FileResponse emits an
    RFC 5987 ``filename*`` for non-ASCII names.
    """
    raw = ""
    if job.ir is not None and job.ir.title and job.ir.title.strip():
        raw = job.ir.title
    elif job.prompt:
        raw = job.prompt[:20]
    cleaned = _sanitize_filename(raw)
    if not cleaned:
        cleaned = job.id
    return f"{cleaned}.odp"


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
    vision_backend = job.vision_backend or os.environ.get(
        "ODFORGE_VISION_BACKEND", "off"
    )
    try:
        qa_kwargs = {
            "outline": outline,
            "backend": vision_backend,
            "llm_backend": job.backend,
        }
        if job.assets:
            qa_kwargs["render_assets"] = job.assets
        report = await asyncio.to_thread(
            run_qa_loop,
            job.ir,
            job.odp_path,
            **qa_kwargs,
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
        model_prompt = job.prompt
        reference_context = format_reference_context(job.reference_documents)
        if reference_context:
            model_prompt = f"{model_prompt}\n\n{reference_context}"
        outline = await asyncio.to_thread(
            generate_outline, model_prompt, job.backend, pages=job.pages
        )
        if job.mode:
            outline = outline.model_copy(update={"mode": job.mode})
        outline = outline.model_copy(
            update={
                "media_assets": job.asset_refs,
                "image_generation_available": (
                    os.getenv("ODFORGE_IMAGE_BACKEND", "off").lower() == "http"
                    and bool(os.getenv("ODFORGE_IMAGE_ENDPOINT", "").strip())
                ),
            }
        )
        job.outline = outline
        await _emit(job, "outline", outline.model_dump(mode="json"))

        if job.interactive:
            stage = "outline_approval"
            job.status = "awaiting_approval"
            await _emit(job, "awaiting_approval", {})
            try:
                await asyncio.wait_for(
                    job.approval.wait(), timeout=_APPROVAL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError("大綱確認逾時，請重新建立工作") from exc
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
            payload = {"n": n, "slide": slide.model_dump(mode="json")}
            await _emit(job, "slide_done", payload)
            # unit_done 為 F4 起的正名;與 slide_done 同 data 並發。舊事件保留以向前相容。
            await _emit(job, "unit_done", payload)

        stage = "render"
        job.status = "rendering"
        needs_asset_context = bool(job.assets) or any(
            slide.image is not None and bool(slide.image.prompt)
            for slide in ir.slides
        )
        if needs_asset_context:
            await asyncio.to_thread(render, ir, job.odp_path, assets=job.assets)
        else:
            await asyncio.to_thread(render, ir, job.odp_path)
        _persist_generated_assets(job)

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
            _persist_generated_assets(job)
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
        job.finished_at = time.time()
        data: Dict[str, Any] = {"download_url": _download_url(job)}
        if job.qa_report is not None:
            data["qa_report"] = job.qa_report.model_dump(mode="json")
        await _emit(job, "complete", data)
    except Exception as exc:  # noqa: BLE001 - surface as an error event, never 500
        job.status = "error"
        job.finished_at = time.time()
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
        pages=[
            PageRole(
                role=base.role,
                title=base.title,
                gist=gist,
                visual_intent=base.visual_intent,
            )
        ],
        source_prompt=job.outline.source_prompt or job.prompt,
        media_assets=job.asset_refs,
        image_generation_available=job.outline.image_generation_available,
    )
    repaired = await asyncio.to_thread(generate_slides, sub, job.backend)
    if repaired.slides:
        job.ir.slides[n - 1] = repaired.slides[0]

    needs_asset_context = bool(job.assets) or any(
        slide.image is not None and bool(slide.image.prompt)
        for slide in job.ir.slides
    )
    if needs_asset_context:
        await asyncio.to_thread(render, job.ir, job.odp_path, assets=job.assets)
    else:
        await asyncio.to_thread(render, job.ir, job.odp_path)
    _persist_generated_assets(job)
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


class AssetUpload(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    credit: str = Field(default="", max_length=160)
    data_url: str = Field(max_length=12_000_000)

    @field_validator("description")
    @classmethod
    def description_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("asset description must not be blank")
        return value

    @field_validator("data_url")
    @classmethod
    def data_url_must_be_supported(cls, value: str) -> str:
        lowered = value.lower()
        if not (
            lowered.startswith("data:image/png;base64,")
            or lowered.startswith("data:image/jpeg;base64,")
            or lowered.startswith("data:application/pdf;base64,")
        ):
            raise ValueError("reference must be a PDF/PNG/JPEG base64 data URI")
        return value


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    mode: Optional[Literal["presenter", "detailed"]] = None
    theme: Optional[
        Literal["academic", "minimal", "teal", "forest", "navy", "dark", "violet"]
    ] = None
    interactive: bool = False
    qa: bool = False
    # Validated against the registries rather than a hand-kept Literal, so a new
    # source is one edit (the factory) instead of five.
    backend: Optional[str] = None
    vision_backend: Optional[str] = None
    # Only presentations (odp) are supported today; a non-"odp" value is a 422
    # with a human message (checked in the endpoint so ``detail`` is a plain
    # zh-TW string, not pydantic's list-of-errors shape).
    doc_type: str = "odp"
    # Optional target page count (inclusive 3..30); out of range → pydantic 422.
    pages: Optional[int] = Field(default=None, ge=3, le=30)
    assets: List[AssetUpload] = Field(default_factory=list, max_length=6)

    @field_validator("prompt")
    @classmethod
    def prompt_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value

    @field_validator("backend")
    @classmethod
    def backend_must_be_registered(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in llm.BACKENDS:
            raise ValueError(f"unknown text backend; available: {sorted(llm.BACKENDS)}")
        return value

    @field_validator("vision_backend")
    @classmethod
    def vision_backend_must_be_registered(cls, value: Optional[str]) -> Optional[str]:
        available = vision_source_names()
        if value is not None and value not in available:
            raise ValueError(f"unknown vision backend; available: {sorted(available)}")
        return value


def vision_source_names() -> set[str]:
    """Every selectable vision source, read from the registry *now*.

    A module-level snapshot would freeze at import and silently ignore a source
    registered afterwards — the exact staleness this whole change is undoing.
    "off" belongs here too: it is the source that always works, and the default.
    """
    return set(critic.VISION_BACKENDS) | {"off"}


def _source_status(kind: str, name: str) -> Dict[str, Any]:
    """Whether a source can actually run right now, and why not if it cannot.

    Availability is decided by *constructing* the backend and reporting what it
    complains about. Every factory already raises a specific, human message for
    a missing key, a missing CLI or a public bind, so there is no second list of
    preconditions to keep in step with the first.
    """
    if kind == "vision" and name == "off":
        return {"name": name, "available": True, "reason": ""}
    factory = (
        (lambda: llm.get_backend(name))
        if kind == "text"
        else (lambda: critic.get_vision_backend(name))
    )
    try:
        factory()
    except Exception as exc:  # noqa: BLE001 - the message is the whole point
        return {"name": name, "available": False, "reason": _first_line(str(exc))}
    return {"name": name, "available": True, "reason": ""}


def _first_line(message: str) -> str:
    """The headline of a multi-line factory error (the rest is shell examples)."""
    return message.strip().splitlines()[0] if message.strip() else ""


class DiscoveryAsset(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    credit: str = Field(default="", max_length=160)
    data_url: Optional[str] = Field(default=None, max_length=12_000_000)

    @field_validator("data_url")
    @classmethod
    def data_url_must_be_pdf(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not is_pdf_data_uri(value):
            raise ValueError("discovery reference bytes must be a PDF data URI")
        return value


class DiscoveryBody(BaseModel):
    """Preflight payload; PDF text is read while image bytes stay client-side."""

    prompt: str = Field(min_length=1, max_length=8000)
    mode: Optional[Literal["presenter", "detailed"]] = None
    theme: Optional[
        Literal["academic", "minimal", "teal", "forest", "navy", "dark", "violet"]
    ] = None
    backend: Optional[Literal["deepseek", "ollama", "custom"]] = None
    doc_type: str = "odp"
    pages: Optional[int] = Field(default=None, ge=3, le=30)
    assets: List[DiscoveryAsset] = Field(default_factory=list, max_length=6)

    @field_validator("prompt")
    @classmethod
    def prompt_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value


class OutlineActionBody(BaseModel):
    action: str
    outline: Optional[dict] = None


class RegenerateBody(BaseModel):
    instruction: Optional[str] = Field(default=None, max_length=2000)


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


def _default_sessions_dir() -> Path:
    configured = os.environ.get("ODFORGE_SESSIONS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "ODForge" / "sessions"
    xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data:
        return Path(xdg_data) / "odforge" / "sessions"
    return Path.home() / ".local" / "share" / "odforge" / "sessions"


def create_app(jobs_dir: Optional[Path] = None) -> FastAPI:
    """Build the ODForge Web API application.

    ``jobs_dir`` overrides where per-job artifacts live. By default sessions are
    stored under the user's local application-data directory; tests point it at
    a temporary path for isolation.
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

    app.state.active_regenerations = 0
    app.state.regeneration_counter_lock = asyncio.Lock()
    base = Path(jobs_dir) if jobs_dir is not None else _default_sessions_dir()
    base.mkdir(parents=True, exist_ok=True)
    app.state.jobs_dir = base
    app.state.jobs = _load_persisted_jobs(base)
    _prune_jobs(app)

    def _get_job(job_id: str) -> Job:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    def _discovery_context(body: DiscoveryBody) -> str:
        context: list[str] = []
        images: list[str] = []
        documents: list[ReferenceDocument] = []
        if body.mode:
            context.append(
                "講述型態：" + ("講者型" if body.mode == "presenter" else "自讀型")
            )
        if body.theme:
            context.append(f"視覺主題：{body.theme}")
        if body.pages is not None:
            context.append(f"目標頁數：{body.pages} 頁")
        for asset in body.assets:
            if asset.data_url:
                documents.append(
                    decode_pdf_data_uri(
                        asset.data_url,
                        description=asset.description,
                        credit=asset.credit,
                    )
                )
            else:
                images.append(asset.description)
        if images:
            context.append(f"可用圖片素材：{'、'.join(images)}")
        document_context = format_reference_context(documents, max_chars=30_000)
        if document_context:
            context.append(document_context)
        return "\n".join(context)

    def _validate_discovery_type(body: DiscoveryBody) -> None:
        if body.doc_type != "odp":
            raise HTTPException(
                status_code=422,
                detail="目前僅支援簡報(odp)的需求訪談。",
            )

    @app.post("/api/discovery/questions", response_model=DiscoveryPlan)
    async def discovery_questions(body: DiscoveryBody) -> DiscoveryPlan:
        _validate_discovery_type(body)
        request_id = uuid.uuid4().hex[:10]
        started = time.perf_counter()
        logger.info(
            "discovery_start request_id=%s backend=%s prompt_chars=%d",
            request_id,
            body.backend or os.environ.get("ODFORGE_BACKEND", "deepseek"),
            len(body.prompt),
        )
        try:
            context = await asyncio.to_thread(_discovery_context, body)
            plan = await asyncio.to_thread(
                generate_discovery_questions,
                body.prompt,
                body.backend,
                context=context,
            )
        except ReferenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "discovery_error request_id=%s elapsed_ms=%d",
                request_id,
                round((time.perf_counter() - started) * 1000),
            )
            raise HTTPException(
                status_code=502,
                detail=f"需求訪談生成失敗：{str(exc)[:500]}",
            ) from exc
        logger.info(
            "discovery_complete request_id=%s elapsed_ms=%d questions=%d",
            request_id,
            round((time.perf_counter() - started) * 1000),
            len(plan.questions),
        )
        return plan

    @app.post("/api/discovery/questions/stream")
    async def discovery_questions_stream(
        body: DiscoveryBody,
        request: Request,
    ) -> StreamingResponse:
        """Stream operational progress while the structured model call runs.

        Events deliberately expose lifecycle state and elapsed time, never the
        model's private chain-of-thought. Each frame is one UTF-8 NDJSON line.
        """

        _validate_discovery_type(body)
        request_id = uuid.uuid4().hex[:10]
        try:
            context = await asyncio.to_thread(_discovery_context, body)
        except ReferenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        async def event_stream() -> AsyncIterator[str]:
            started = time.perf_counter()
            loop = asyncio.get_running_loop()
            progress_queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()

            def elapsed_ms() -> int:
                return round((time.perf_counter() - started) * 1000)

            def report(stage: str) -> None:
                loop.call_soon_threadsafe(
                    progress_queue.put_nowait,
                    (stage, elapsed_ms()),
                )

            def line(event_type: str, data: dict[str, Any]) -> str:
                return (
                    json.dumps(
                        {"type": event_type, "data": data},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )

            def progress_data(stage: str, at_ms: int) -> dict[str, Any]:
                return {
                    "request_id": request_id,
                    "stage": stage,
                    "message": _DISCOVERY_STAGE_MESSAGES.get(
                        stage, "讀題工作持續進行中"
                    ),
                    "elapsed_ms": at_ms,
                }

            logger.info(
                "discovery_start request_id=%s backend=%s prompt_chars=%d stream=true",
                request_id,
                body.backend or os.environ.get("ODFORGE_BACKEND", "deepseek"),
                len(body.prompt),
            )
            yield line("progress", progress_data("accepted", 0))

            task = asyncio.create_task(
                asyncio.to_thread(
                    generate_discovery_questions,
                    body.prompt,
                    body.backend,
                    context=context,
                    progress=report,
                )
            )
            try:
                while not task.done():
                    if await request.is_disconnected():
                        logger.info(
                            "discovery_disconnected request_id=%s elapsed_ms=%d",
                            request_id,
                            elapsed_ms(),
                        )
                        task.cancel()
                        return
                    queued = asyncio.create_task(progress_queue.get())
                    done, _pending = await asyncio.wait(
                        {task, queued},
                        timeout=_DISCOVERY_HEARTBEAT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if queued in done:
                        stage, at_ms = queued.result()
                        logger.info(
                            "discovery_progress request_id=%s stage=%s elapsed_ms=%d",
                            request_id,
                            stage,
                            at_ms,
                        )
                        yield line("progress", progress_data(stage, at_ms))
                    else:
                        queued.cancel()
                        with suppress(asyncio.CancelledError):
                            await queued

                    if task in done:
                        break
                    if not done:
                        at_ms = elapsed_ms()
                        logger.info(
                            "discovery_progress request_id=%s stage=waiting elapsed_ms=%d",
                            request_id,
                            at_ms,
                        )
                        yield line(
                            "progress",
                            progress_data("waiting", at_ms),
                        )

                while not progress_queue.empty():
                    stage, at_ms = progress_queue.get_nowait()
                    yield line("progress", progress_data(stage, at_ms))

                plan = await task
            except asyncio.CancelledError:
                task.cancel()
                raise
            except Exception as exc:
                at_ms = elapsed_ms()
                logger.exception(
                    "discovery_error request_id=%s elapsed_ms=%d stream=true",
                    request_id,
                    at_ms,
                )
                yield line(
                    "error",
                    {
                        "request_id": request_id,
                        "message": f"需求訪談生成失敗：{str(exc)[:500]}",
                        "elapsed_ms": at_ms,
                    },
                )
                return

            at_ms = elapsed_ms()
            logger.info(
                "discovery_complete request_id=%s elapsed_ms=%d questions=%d stream=true",
                request_id,
                at_ms,
                len(plan.questions),
            )
            yield line(
                "result",
                {
                    "request_id": request_id,
                    "elapsed_ms": at_ms,
                    "plan": plan.model_dump(mode="json"),
                },
            )

        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/generate")
    async def generate(body: GenerateBody) -> Dict[str, str]:
        if body.doc_type != "odp":
            raise HTTPException(
                status_code=422,
                detail="目前僅支援簡報(odp);文件(odt)與試算表(ods)即將支援。",
            )
        _prune_jobs(app)
        active_jobs = sum(
            job.status not in _TERMINAL_JOB_STATUSES
            for job in app.state.jobs.values()
        )
        if active_jobs >= _MAX_ACTIVE_JOBS:
            raise HTTPException(
                status_code=429,
                detail=f"too many active jobs; limit is {_MAX_ACTIVE_JOBS}",
            )
        try:
            job = create_job(
                app,
                prompt=body.prompt,
                mode=body.mode,
                theme=body.theme,
                interactive=body.interactive,
                qa=body.qa,
                vision_backend=body.vision_backend,
                backend=body.backend,
                pages=body.pages,
                uploads=body.assets,
            )
        except MediaError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid image asset: {exc}"
            ) from exc
        except ReferenceError as exc:
            raise HTTPException(
                status_code=422, detail=f"參考文件無法讀取：{exc}"
            ) from exc
        # Background task on the running loop; keep a ref so it isn't GC'd.
        job.task = asyncio.create_task(run_job(job))
        return {"job_id": job.id}

    @app.get("/api/sessions")
    async def list_sessions() -> Dict[str, Any]:
        _prune_jobs(app)
        sessions = sorted(
            (_session_summary(job) for job in app.state.jobs.values()),
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        return {"sessions": sessions}

    @app.get("/api/sources")
    async def sources() -> Dict[str, Any]:
        """Which model sources exist, which can run, and which are in force.

        Derived from the registries, so the frontend never carries its own copy
        of the list and a newly registered backend is selectable immediately.
        """
        return {
            "text": [_source_status("text", n) for n in sorted(llm.BACKENDS)],
            "vision": [
                _source_status("vision", n) for n in sorted(vision_source_names())
            ],
            "defaults": {
                "text": os.environ.get("ODFORGE_BACKEND", "deepseek"),
                "vision": os.environ.get("ODFORGE_VISION_BACKEND", "off"),
            },
        }

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
                edited = Outline.model_validate(body.outline)
                edited = edited.model_copy(
                    update={
                        "source_prompt": edited.source_prompt or job.prompt,
                        "media_assets": job.asset_refs,
                        "image_generation_available": (
                            job.outline.image_generation_available
                            if job.outline is not None
                            else False
                        ),
                    }
                )
                job.outline = edited
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"invalid outline: {exc}")
            # Record the edited outline in the event log so a ?job= resume replays
            # the *edited* outline, not the original. Without this, a reconnecting
            # subscriber rebuilds from the stale first outline; after `complete`
            # every cell is painted done, resurrecting the removed pages as phantom
            # "done" thumbnails absent from the downloaded deck. Live subscribers
            # receive a second `outline` event, equivalent (and idempotent) to the
            # front-end's synthetic re-sync dispatch.
            await _emit(job, "outline", job.outline.model_dump(mode="json"))
        elif body.action != "approve":
            raise HTTPException(
                status_code=422, detail="action must be 'approve' or 'edit'"
            )
        job.approval.set()
        return {"ok": True, "status": job.status}

    # units/{n}/regenerate 為 F4 正名別名;slides/{n}/regenerate 保留向前相容,同一 handler。
    @app.post("/api/jobs/{job_id}/units/{n}/regenerate")
    @app.post("/api/jobs/{job_id}/slides/{n}/regenerate")
    async def regenerate(job_id: str, n: int, body: RegenerateBody) -> Dict[str, Any]:
        job = _get_job(job_id)
        if job.status != "complete" or job.ir is None or job.outline is None:
            raise HTTPException(
                status_code=409, detail="job is not ready for regeneration"
            )
        if not 1 <= n <= len(job.ir.slides):
            raise HTTPException(status_code=404, detail="slide index out of range")
        async with _regeneration_slot(app, job):
            try:
                result = await regenerate_slide(job, n, body.instruction)
                _persist_job(job)
            except Exception as exc:  # noqa: BLE001 - clean error, never a 500 traceback
                # Clean, structured error (the run_job error shape) instead of a bare
                # 500 traceback; job.status is left intact — the existing deck is fine.
                raise HTTPException(
                    status_code=500,
                    detail={"message": str(exc), "stage": "regenerate"},
                )
        return {"ok": True, **result}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> Dict[str, Any]:
        job = _get_job(job_id)
        if job.status in _TERMINAL_JOB_STATUSES:
            raise HTTPException(status_code=409, detail="job is already terminal")
        job.status = "cancelled"
        job.finished_at = time.time()
        job.approval.set()
        if job.task is not None and not job.task.done():
            job.task.cancel()
        await _emit(job, "error", {"message": "工作已取消", "stage": "cancel"})
        return {"ok": True, "status": job.status}

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
            job.odp_path, media_type=ODP_MIME, filename=_download_filename(job)
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
