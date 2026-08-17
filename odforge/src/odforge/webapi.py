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
import base64
import binascii
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sse_starlette import EventSourceResponse

from odforge import __version__, critic, llm
from odforge.critic import QAReport, run_qa_loop
from odforge.extract import TemplateExtractionError, extract_design
from odforge.ir import (
    LANGUAGES,
    MAX_OUTLINE_PAGES,
    Branding,
    DesignSpec,
    MediaAssetRef,
    Outline,
    PageRole,
    Presentation,
)
from odforge.llm import (
    DiscoveryPlan,
    DroppedContent,
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
from odforge.templates import (
    MAX_NAME_CHARS,
    all_templates,
    delete_template,
    save_template,
    templates_path,
)
from odforge.themes import STYLES, THEMES
from odforge.validate import validate_odf

# The odp mimetype, verbatim per the project's ODF mimetype constants.
ODP_MIME = "application/vnd.oasis.opendocument.presentation"

# 頁數下限只在 API 這一層有意義(產品不接受 1–2 頁的請求);上限直接沿用 IR 的
# MAX_OUTLINE_PAGES,前後端與 Outline schema 因此共用同一個數字。
MIN_REQUESTED_PAGES = 3

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
    # Output language of the deck (ir.LANGUAGES); stamped onto the outline after
    # stage 1 so stage 2 and any later single-page regeneration inherit it.
    language: str = "zh-TW"
    # A user template's design tokens. Beats both the model's own DesignSpec and
    # ``theme``: the user picked this look explicitly.
    design: Optional[DesignSpec] = None
    # 版式 (layout personality). None = the palette's paired default.
    style: Optional[str] = None
    # Cover byline + logo. ``branding.logo`` points at ``assets["logo"]``.
    branding: Optional[Branding] = None
    assets: Dict[str, AssetInput] = field(default_factory=dict)
    asset_refs: List[MediaAssetRef] = field(default_factory=list)
    reference_documents: List[ReferenceDocument] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    status: str = "pending"
    outline: Optional[Outline] = None
    ir: Optional[Presentation] = None
    slides_done: int = 0
    # 每次「重算圖後再發佈預覽」遞增。URL 帶上 ?v={n} 讓字串不同,否則瀏覽器/
    # React 對同字串 URL 不會重抓,修補後的頁面永遠顯示舊圖(F5)。同時也是預覽
    # 目錄名(preview/v{n}):換版本 = 換目錄,舊頁數的殘骸不可能混進新的一輪。
    # ``-1`` = 還沒發佈過任何一批;第一批發佈為 v0(URL 不帶 ?v=,與舊行為一致)。
    preview_version: int = -1
    # ---- artifact lifecycle -------------------------------------------------
    # ``artifact_version`` 每成功發佈一次 deck.odp 就 +1;``validated_version``
    # 記錄「通過必要閘門的是哪一版」。下載端點比對兩者:相等才交付。檔案存在
    # 不等於檔案可信 —— 生成中、驗證失敗、重生失敗的中途檔都存在於磁碟上。
    artifact_version: int = 0
    validated_version: int = -1
    # 最近一次的四道閘結果,重生後要能整批重算(不得沿用上一版的綠燈)。
    gates: Dict[str, str] = field(default_factory=dict)
    qa_report: Optional[QAReport] = None
    # 版面預算被迫刪掉的內容。使用者要求過這些字,消失了就必須說出來。
    dropped_content: List[DroppedContent] = field(default_factory=list)
    # Why the design gate could not run (missing key, exhausted quota, 4xx from
    # the vision provider…). QA never fails the run, but the reason must reach the
    # user: "未啟用" with no cause is indistinguishable from a silent bug.
    qa_error: Optional[str] = None
    error: Optional[Dict[str, str]] = None
    # Strong ref to the background runner so the loop doesn't GC a "fire and
    # forget" task before it finishes.
    task: Optional[asyncio.Task] = None
    # How many worker threads this job still has running. Incremented when work
    # is handed to the executor, decremented *by the thread itself* when it
    # returns — so a cancelled ``await`` cannot make it look idle while a
    # provider call is still burning quota. Touched from two threads: guarded.
    active_workers: int = 0
    worker_lock: threading.Lock = field(default_factory=threading.Lock)

    events: List[Dict[str, Any]] = field(default_factory=list)
    # Set on approve (interactive gate) and on every emit (wakes SSE subscribers).
    approval: asyncio.Event = field(default_factory=asyncio.Event)
    updated: asyncio.Event = field(default_factory=asyncio.Event)
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # When session.json was last rewritten — the debounce clock for high-
    # frequency events (see ``_emit``). Not persisted.
    last_persist: float = 0.0

    @property
    def odp_path(self) -> Path:
        return self.dir / "deck.odp"

    @property
    def previews_root(self) -> Path:
        return self.dir / "preview"

    @property
    def preview_dir(self) -> Path:
        """The *published* preview directory for the current version.

        Each publish writes a fresh ``preview/v{n}`` and then moves the pointer
        (``preview_version``) — so a 5-page deck re-generated down to 3 pages
        cannot leave ``page-04.png``/``page-05.png`` behind to be served as if
        they were still part of the deck. Version 0 (nothing published yet) is a
        directory that never exists, which is exactly the right answer.
        """
        return self.previews_root / f"v{self.preview_version}"

    def preview_dir_for(self, version: int) -> Path:
        return self.previews_root / f"v{version}"

    @property
    def downloadable(self) -> bool:
        """Whether the artifact on disk is the one the gates actually approved.

        Three things must line up: the job finished, the file exists, and the
        version that passed validation is the version sitting on disk. A job that
        is still generating, is mid-regeneration, failed validation, errored or
        was cancelled fails at least one of them — even though ``deck.odp`` may
        well exist, because a partial or rolled-back render leaves a file behind.
        """
        return (
            self.status == "complete"
            and self.artifact_version > 0
            and self.validated_version == self.artifact_version
            and self.odp_path.is_file()
        )


def _occupies_a_slot(job: Job) -> bool:
    """Whether this job still ties up one of the concurrent-work slots.

    Two independent facts, deliberately kept apart:

    * ``job.status`` is what the **UI** shows. Cancel sets it to ``cancelled``
      instantly, because a user who pressed cancel should see it stop.
    * ``job.task`` is what is **actually running**. ``Task.cancel()`` only
      requests cancellation, and a thread already inside ``requests.post`` to a
      provider cannot be interrupted at all — it runs to completion, spending
      the user's quota, long after the UI said "cancelled".

    The limit has to be enforced on the second one. Otherwise start/cancel in a
    loop is a way to launch unlimited simultaneous provider calls while the
    console shows an idle machine.

    And ``job.task`` alone is still not the second one. Cancelling a task that
    is parked on ``asyncio.to_thread`` marks the *task* done the moment
    ``CancelledError`` propagates — while the thread it handed the work to keeps
    running inside ``requests.post`` with no idea any of this happened. Between
    those two moments the slot was already free. ``active_workers`` is
    decremented by that thread, when it actually finishes, which is the only
    event that means the provider call is over.
    """
    if job.status not in _TERMINAL_JOB_STATUSES:
        return True
    if job.task is not None and not job.task.done():
        return True
    with job.worker_lock:
        return job.active_workers > 0


async def _offload(job: Job, fn, /, *args, **kwargs):
    """Run ``fn`` in a worker thread, counted against the job for its real life.

    ``asyncio.to_thread`` with bookkeeping: the count goes up before the work is
    handed over and comes down inside the thread, so cancelling the awaiting
    coroutine cannot release the slot early. Every provider call, render and
    validation in the pipeline goes through here.
    """
    with job.worker_lock:
        job.active_workers += 1

    def _tracked():
        try:
            return fn(*args, **kwargs)
        finally:
            with job.worker_lock:
                job.active_workers -= 1

    return await asyncio.to_thread(_tracked)


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
        "language": job.language,
        "style": job.style,
        "design": (
            job.design.model_dump(mode="json") if job.design is not None else None
        ),
        "branding": (
            job.branding.model_dump(mode="json")
            if job.branding is not None
            else None
        ),
        "assets": assets,
        "asset_refs": [ref.model_dump(mode="json") for ref in job.asset_refs],
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "status": job.status,
        "preview_version": job.preview_version,
        "artifact_version": job.artifact_version,
        "validated_version": job.validated_version,
        "gates": dict(job.gates),
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

    job.last_persist = time.time()
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
            # Jobs persisted before these fields existed restore as the
            # defaults, which is exactly what they ran as.
            language=str(data.get("language") or "zh-TW"),
            style=data.get("style"),
            design=(
                DesignSpec.model_validate(data["design"])
                if data.get("design") is not None
                else None
            ),
            branding=(
                Branding.model_validate(data["branding"])
                if data.get("branding") is not None
                else None
            ),
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
            preview_version=int(data.get("preview_version", -1)),
            artifact_version=int(data.get("artifact_version", 0)),
            validated_version=int(data.get("validated_version", -1)),
            gates={
                str(k): str(v)
                for k, v in (data.get("gates") or {}).items()
                if isinstance(k, str) and isinstance(v, str)
            },
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
        # Sessions written before artifact versioning existed carry neither
        # field. They reached ``complete`` under the old rule (completion implied
        # the gates had passed), so honour that rather than retroactively making
        # every archived deck undownloadable.
        if job.artifact_version == 0 and job.status == "complete":
            job.artifact_version = 1
            job.validated_version = 1
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
    uploads: Optional[List[AssetUpload]] = None,
    language: str = "zh-TW",
    design: Optional[DesignSpec] = None,
    style: Optional[str] = None,
    byline: str = "",
    logo: Optional[AssetUpload] = None,
    logo_placement: str = "cover-closing",
) -> Job:
    """Register a new job with a server-minted id and its own artifact dir.

    The cover logo is decoded and stored like any other image asset, under the
    reserved id ``logo`` — it is deliberately NOT added to ``asset_refs``, which
    is the list the model may draw from: a school crest is furniture, and a
    model offered it as page media would eventually put it on a slide.
    """
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

    # Decoded before the job dir exists so a bad logo fails the request (422)
    # rather than leaving a half-built job behind.
    logo_blob = decode_data_uri(logo.data_url) if logo is not None else None
    branding = None
    if byline.strip() or logo_blob is not None:
        branding = Branding(
            byline=byline.strip(),
            logo="asset://logo" if logo_blob is not None else "",
            placement=logo_placement,
        )

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
        language=language,
        design=design,
        style=style,
        branding=branding,
        reference_documents=reference_documents,
    )
    assets_dir = job_dir / "assets"
    if logo_blob is not None:
        assets_dir.mkdir(parents=True, exist_ok=True)
        logo_path = assets_dir / f"logo{logo_blob.extension}"
        logo_path.write_bytes(logo_blob.data)
        job.assets["logo"] = logo_path
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


# Per-slide events arrive in bursts (two per slide plus a preview each) and
# each persist rewrites the WHOLE event log — O(N²) blocking file I/O on the
# event loop for a 30-page deck. They ride a debounce instead: a crash between
# debounced writes only costs recent per-slide detail, and a restored
# non-terminal session is marked interrupted anyway. Structural events
# (outline, gates, complete, error…) stay durable immediately.
_DEBOUNCED_EVENTS = frozenset({"slide_done", "unit_done", "preview_ready"})
_PERSIST_DEBOUNCE_SECONDS = 2.0


async def _emit(job: Job, event: str, data: Any) -> None:
    """Append an SSE event to the job's log and wake any subscribers.

    The write is pushed off the event loop with ``asyncio.to_thread``: a session
    rewrite is a JSON serialisation of the whole job plus a file write, and doing
    that inline blocked every other request (including the SSE stream this event
    is meant to reach) for the duration.
    """
    job.events.append({"event": event, "data": data})
    if (
        event not in _DEBOUNCED_EVENTS
        or time.time() - job.last_persist >= _PERSIST_DEBOUNCE_SECONDS
    ):
        # Stamp the clock synchronously so a burst of events cannot all decide
        # they are due while the first write is still in flight.
        job.last_persist = time.time()
        await asyncio.to_thread(_persist_job, job)
    job.updated.set()


async def _emit_gate(
    job: Job, gate: str, status: str, note: Optional[str] = None
) -> None:
    """Record a gate outcome on the job *and* emit it.

    Keeping the latest verdict on the job is what lets a regeneration reset the
    board honestly: the four ticks belong to a specific artifact version, so a
    new version has to earn them again rather than inherit them.
    """
    job.gates[gate] = status
    data: Dict[str, Any] = {"gate": gate, "status": status}
    if note:
        data["note"] = note
    await _emit(job, "gate_result", data)


def _prune_preview_versions(job: Job, keep: int = 2) -> None:
    """Drop superseded ``preview/v{n}`` directories (best effort).

    The published version and one predecessor are kept — a browser that already
    has an old ``?v=`` URL in flight still gets an image instead of a 404.
    """
    root = job.previews_root
    if not root.is_dir():
        return
    versions = []
    for child in root.iterdir():
        if child.is_dir() and re.fullmatch(r"v(\d+)", child.name):
            versions.append((int(child.name[1:]), child))
    for _version, path in sorted(versions, reverse=True)[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def _preview_file(job: Job, n: int) -> Optional[Path]:
    """The PNG for page ``n`` of the *published* preview version, if it exists.

    Falls back to the pre-versioning flat layout (``preview/page-NN.png``) so
    sessions written by an older build still show their thumbnails.
    """
    name = f"page-{n:02d}.png"
    candidate = job.preview_dir / name
    if candidate.is_file():
        return candidate
    legacy = job.previews_root / name
    return legacy if legacy.is_file() else None


def _download_url(job: Job) -> str:
    return f"/api/jobs/{job.id}/download"


# Why an artifact is not servable, in the user's language. "deck not ready" told
# someone staring at a finished-looking screen precisely nothing.
_UNDOWNLOADABLE_BY_STATUS: Dict[str, str] = {
    "pending": "這份工作還沒開始生成。",
    "generating_outline": "大綱還在生成中,檔案尚未產生。",
    "awaiting_approval": "還在等待你確認大綱,檔案尚未產生。",
    "generating_slides": "內容還在逐頁生成中,檔案尚未產生。",
    "rendering": "檔案正在算圖中,請稍候。",
    "validating": "檔案正在通過格式驗證,請稍候。",
    "qa": "設計品檢進行中,完成後才會釋出下載。",
    "regenerating": "有一頁正在重生,完成後才會釋出新版本的下載。",
    "cancelled": "這份工作已取消,沒有可下載的成品。",
}


def _undownloadable_reason(job: Job) -> str:
    if job.status == "error":
        detail = (job.error or {}).get("message", "")
        return "這份工作在生成過程中失敗,沒有通過驗證的成品可以下載。" + (
            f"（原因：{detail}）" if detail else ""
        )
    known = _UNDOWNLOADABLE_BY_STATUS.get(job.status)
    if known:
        return known
    if not job.odp_path.is_file():
        return "找不到這份工作的成品檔案。"
    # complete, file present, but the version on disk never passed the gates.
    return (
        "磁碟上的檔案沒有通過品質閘門(或不是最新一版),為避免交付未驗證的成品,"
        "這裡不提供下載。請重新生成。"
    )


def _session_title(job: Job) -> str:
    if job.ir is not None and job.ir.title.strip():
        return job.ir.title.strip()
    if job.outline is not None and job.outline.pages:
        # 與前端 taskName 同步:封面頁(role=="title")的標題優先於第一頁。
        pages = job.outline.pages
        cover = next((p for p in pages if p.role == "title"), pages[0])
        title = cover.title.strip()
        if title:
            return title
    first_line = next(
        (line.strip() for line in job.prompt.splitlines() if line.strip()),
        "未命名簡報",
    )
    return first_line[:80]


def _session_summary(job: Job) -> Dict[str, Any]:
    preview = _preview_file(job, 1)
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
            f"/api/jobs/{job.id}/preview/1.png" if preview is not None else None
        ),
        # A card only offers a download when the artifact is actually servable —
        # otherwise the link 409s the moment it is clicked.
        "download_url": _download_url(job) if job.downloadable else None,
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
    """Preview URL for page ``n``, cache-busted after any re-render.

    ``?v={k}`` only appears once the deck was re-rendered (QA repair, per-page
    regenerate): the changed string forces the browser to refetch. The GET
    route ignores the query — old un-versioned links keep working.
    """
    base = f"/api/jobs/{job.id}/preview/{n}.png"
    return f"{base}?v={job.preview_version}" if job.preview_version > 0 else base


# ---------------------------------------------------------------------------
# The pipeline runner (a background asyncio task per job)
# ---------------------------------------------------------------------------


async def _rasterise_next_version(job: Job, source: Path) -> List[Path]:
    """Rasterise ``source`` into the *next* preview version directory.

    Nothing is published here — the caller decides whether this version becomes
    the live one. Raises :class:`PreviewUnavailable` (no soffice) or whatever
    ``render_pages`` raises; both are the caller's to interpret.
    """
    target = job.preview_dir_for(job.preview_version + 1)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return await _offload(job, render_pages, source, target)


def _publish_previews(job: Job) -> None:
    """Move the pointer to the version just rasterised, and prune the old ones."""
    job.preview_version += 1
    _prune_preview_versions(job)


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

    Pages are written to a fresh version directory and only published once the
    whole rasterisation succeeded, so a half-written set never replaces a good
    one and a shorter deck never inherits the previous deck's extra pages.
    """
    if job.ir is None:
        return
    try:
        paths = await _rasterise_next_version(job, job.odp_path)
    except PreviewUnavailable:
        await _emit_gate(job, "libreoffice", "skipped")
        return
    except Exception:
        await _emit_gate(job, "libreoffice", "fail")
        return
    _publish_previews(job)
    await _emit_gate(job, "libreoffice", "pass")
    for n, _path in enumerate(paths, start=1):
        await _emit(job, "preview_ready", {"n": n, "url": _preview_url(job, n)})


def _qa_failure_reason(vision_backend: str, exc: BaseException) -> str:
    """A zh-TW one-liner explaining why the design gate could not run.

    The provider's own message is kept (whitespace-collapsed, truncated) — a line
    like "the free quota has been exhausted" is exactly what tells the operator
    what to fix — and the selected source is named, because one ``.env`` line
    decides it.
    """
    detail = " ".join(str(exc).split())[:240] or exc.__class__.__name__
    return f"視覺品檢無法執行（來源：{vision_backend}）：{detail}"


async def _run_qa_loop(job: Job, outline: Optional[Outline]) -> None:
    """Run the design-QA loop and emit one ``qa_round`` per round.

    Uses the same ``run_qa_loop`` the CLI's ``--qa`` calls. The vision backend
    comes from ``ODFORGE_VISION_BACKEND`` (default ``off``); with it off (or no
    soffice) the loop degrades to zero rounds. A QA error never fails the
    generation — but it is *recorded* (logged + ``job.qa_error``) so the design
    gate can say why it did not run, instead of reporting a bare "未啟用" that
    looks identical to the gate being turned off. Because ``run_qa_loop`` runs its
    rounds internally, the per-round events are emitted from its returned report.
    """
    vision_backend = job.vision_backend or os.environ.get(
        "ODFORGE_VISION_BACKEND", "off"
    )
    # The repair path re-runs the layout budget, so QA is a place content can be
    # dropped. It was the only such place with no collector attached.
    dropped: List[DroppedContent] = []
    try:
        qa_kwargs = {
            "outline": outline,
            "backend": vision_backend,
            "llm_backend": job.backend,
            "dropped": dropped,
        }
        if job.assets:
            qa_kwargs["render_assets"] = job.assets
        report = await _offload(
            job,
            run_qa_loop,
            job.ir,
            job.odp_path,
            **qa_kwargs,
        )
    except Exception as exc:
        job.qa_error = _qa_failure_reason(vision_backend, exc)
        logger.warning(
            "qa_failed job_id=%s vision_backend=%s error=%s",
            job.id,
            vision_backend,
            exc,
            exc_info=True,
        )
        return
    job.qa_report = report
    if dropped:
        job.dropped_content = [*job.dropped_content, *dropped]
        await _emit(
            job,
            "content_degraded",
            {"items": [d.model_dump(mode="json") for d in dropped]},
        )
    for round_no, findings in enumerate(report.findings_by_round, start=1):
        await _emit(
            job,
            "qa_round",
            {
                "round": round_no,
                "findings": [f.model_dump(mode="json") for f in findings],
            },
        )


class JobCancelled(Exception):
    """The job was cancelled between stages; stop without emitting completion."""


def _abort_if_cancelled(job: Job) -> None:
    """Checkpoint before any stage that starts new work or claims success.

    ``Task.cancel()`` alone is not enough. A cancel that lands while the job is
    inside ``asyncio.to_thread`` does not interrupt the thread — the awaited call
    finishes first, and only then does the CancelledError surface. Between those
    two moments the pipeline would happily start a render, kick off a QA loop, or
    emit ``complete`` with a download link for a job the user stopped. Explicit
    checkpoints close that window.
    """
    if job.status == "cancelled":
        raise JobCancelled


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
        outline = await _offload(
            job,
            generate_outline,
            model_prompt,
            job.backend,
            pages=job.pages,
            language=job.language,
        )
        if job.mode:
            outline = outline.model_copy(update={"mode": job.mode})
        # Language is application-owned: the facade stamps it too, but stage 2
        # reads it off the outline, so the pipeline enforces it here rather than
        # trusting every backend implementation to have remembered.
        outline = outline.model_copy(update={"language": job.language})
        # A user template beats the model's own art direction, and it has to be
        # applied HERE rather than only on the finished deck: stage 2 is shown
        # the outline's design, and telling the writer one palette while the
        # renderer paints another is how "presenter on a dark theme" ends up
        # written for a light one.
        if job.design is not None:
            outline = outline.model_copy(update={"design": job.design})
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
            except TimeoutError as exc:
                raise RuntimeError("大綱確認逾時，請重新建立工作") from exc
            # The /outline endpoint may have replaced job.outline (an edit).
            outline = job.outline

        _abort_if_cancelled(job)
        stage = "slides"
        job.status = "generating_slides"
        # Collect anything the layout budget had to remove, so it can be shown
        # rather than buried in a speaker note nobody opens.
        dropped: List[DroppedContent] = []
        ir = await _offload(job, generate_slides, outline, job.backend, dropped)
        if dropped:
            job.dropped_content = dropped
            await _emit(
                job,
                "content_degraded",
                {"items": [d.model_dump(mode="json") for d in dropped]},
            )
        # --theme parity: lock a preset and drop any LLM-chosen design.
        if job.theme:
            ir = ir.model_copy(update={"theme": job.theme, "design": None})
        # A user template wins over both: it is the one look the user picked by
        # name. (Re-applied here because stage 2 could have returned its own.)
        if job.design is not None:
            ir = ir.model_copy(update={"design": job.design})
        # 版式 and cover branding are both app-owned: whatever the model may
        # have emitted in these fields is replaced by what the user chose.
        if job.style:
            ir = ir.model_copy(update={"style": job.style})
        ir = ir.model_copy(update={"branding": job.branding})
        job.ir = ir
        for n, slide in enumerate(ir.slides, start=1):
            job.slides_done = n
            payload = {"n": n, "slide": slide.model_dump(mode="json")}
            await _emit(job, "slide_done", payload)
            # unit_done 為 F4 起的正名;與 slide_done 同 data 並發。舊事件保留以向前相容。
            await _emit(job, "unit_done", payload)

        _abort_if_cancelled(job)
        stage = "render"
        job.status = "rendering"
        needs_asset_context = bool(job.assets) or any(
            slide.image is not None and bool(slide.image.prompt)
            for slide in ir.slides
        )
        if needs_asset_context:
            await _offload(job, render, ir, job.odp_path, assets=job.assets)
        else:
            await _offload(job, render, ir, job.odp_path)
        _persist_generated_assets(job)
        # A new artifact exists on disk; it is NOT validated until the gates below
        # say so, and the download endpoint refuses anything unvalidated.
        job.artifact_version += 1

        # Deterministic validation gates (zip/mimetype + XML well-formed) on the
        # freshly rendered deck, using validate.py's existing functions. Each
        # emits a gate_result; a failure of either stops the pipeline (the
        # existing except emits an ``error`` event carrying stage="validate").
        stage = "validate"
        job.status = "validating"
        report = await _offload(job, validate_odf, job.odp_path)
        zip_ok, zip_msg = report.gates["structure"]
        xml_ok, xml_msg = report.gates["xml"]
        await _emit_gate(job, "zip", "pass" if zip_ok else "fail")
        await _emit_gate(job, "xml", "pass" if xml_ok else "fail")
        if not (zip_ok and xml_ok):
            problems = [m for ok, m in ((zip_ok, zip_msg), (xml_ok, xml_msg)) if not ok]
            raise RuntimeError("ODF 驗證未通過:" + ";".join(problems))
        job.validated_version = job.artifact_version

        stage = "preview"
        await _emit_previews(job)

        if job.qa:
            _abort_if_cancelled(job)
            stage = "qa"
            job.status = "qa"
            # Snapshot before the loop so the repaired pages can be identified and
            # re-emitted. Without this the cockpit keeps showing the pre-repair
            # title/role for a page whose content QA replaced — the thumbnail
            # updates (new PNG) while the caption underneath it lies.
            before_qa = [slide.model_dump(mode="json") for slide in ir.slides]
            await _run_qa_loop(job, outline)
            _persist_generated_assets(job)
            # ``repaired`` means QA mutated slides and re-rendered the deck, so
            # the first-pass PNGs are stale — refresh them (with a bumped cache
            # version so the browser refetches the same-named pages). Round
            # count is NOT the signal: a partial report can be rounds=1 yet
            # repaired. A clean unrepaired run changed nothing — don't re-emit.
            if job.qa_report is not None and job.qa_report.repaired:
                # QA wrote a NEW deck to disk. The zip/xml/LibreOffice ticks the
                # user is looking at were earned by the pre-repair file; carrying
                # them over would certify bytes nobody checked. Re-run all three
                # against the artifact actually being shipped.
                stage = "validate"
                job.artifact_version += 1
                report = await _offload(job, validate_odf, job.odp_path)
                zip_ok, zip_msg = report.gates["structure"]
                xml_ok, xml_msg = report.gates["xml"]
                await _emit_gate(job, "zip", "pass" if zip_ok else "fail")
                await _emit_gate(job, "xml", "pass" if xml_ok else "fail")
                if not (zip_ok and xml_ok):
                    problems = [
                        m for ok, m in ((zip_ok, zip_msg), (xml_ok, xml_msg)) if not ok
                    ]
                    raise RuntimeError("QA 修補後的 ODF 驗證未通過:" + ";".join(problems))
                job.validated_version = job.artifact_version
                # Re-emit every page QA actually rewrote, so the outline rail,
                # the thumbnail caption and the single-page detail all describe
                # the deck that is about to be downloaded.
                for n, slide in enumerate(ir.slides, start=1):
                    payload = slide.model_dump(mode="json")
                    if n <= len(before_qa) and before_qa[n - 1] == payload:
                        continue
                    event = {"n": n, "slide": payload}
                    await _emit(job, "slide_done", event)
                    await _emit(job, "unit_done", event)
                    if job.outline is not None and n <= len(job.outline.pages):
                        job.outline.pages[n - 1] = _page_contract(
                            slide, job.outline.pages[n - 1], None
                        )
                stage = "preview"
                await _emit_previews(job)

        # Design gate: reflects the QA outcome, in the same four-way vocabulary
        # the other gates use — and the distinction the whole gate rests on is
        # ``skipped`` vs ``unknown``:
        #
        # * ``skipped``  — nobody *asked* for a review (QA off, or this job chose
        #   no vision source). Nothing is wrong; the user opted out.
        # * ``unknown``  — a review *was* asked for and could not be delivered
        #   (no soffice, provider refused, response unreadable). The deck's design
        #   is genuinely unverified, which is not the same as opting out and is
        #   emphatically not a pass.
        # * ``pass`` / ``fail`` — a review completed and returned a verdict.
        note: Optional[str] = None
        resolved_vision = job.vision_backend or os.environ.get(
            "ODFORGE_VISION_BACKEND", "off"
        )
        if not job.qa:
            design_status = "skipped"
            note = "這次生成關閉了設計品質檢查。"
        elif resolved_vision == "off":
            # No vision source means the critic returns zero findings by
            # construction — that is an opt-out, not a review.
            design_status = "skipped"
            if job.qa_error:
                # 真實的失敗原因永遠優先於「你沒開」的泛用說明。
                design_status = "unknown"
                note = job.qa_error
            elif job.vision_backend == "off":
                # 是這次工作自己選了 off,不是環境變數 — 別叫使用者去改 .env。
                note = "這次生成選擇不使用視覺模型，這一輪只跑了前三道格式驗證。"
            else:
                note = (
                    "沒有設定視覺模型（ODFORGE_VISION_BACKEND=off），"
                    "這一輪只跑了前三道格式驗證。"
                )
        elif job.qa_report is None:
            # QA was requested and threw: unverified, not opted out.
            design_status = "unknown"
            note = job.qa_error or "視覺品檢未能完成。"
        else:
            # The report's own verdict is authoritative — it already encodes
            # "nobody looked" as ``unknown`` rather than a vacuous pass.
            design_status = job.qa_report.verdict
            note = job.qa_report.failure or job.qa_report.note
        await _emit_gate(job, "design", design_status, note)

        _abort_if_cancelled(job)
        job.status = "complete"
        job.finished_at = time.time()
        data: Dict[str, Any] = {"download_url": _download_url(job)}
        if job.qa_report is not None:
            data["qa_report"] = job.qa_report.model_dump(mode="json")
        await _emit(job, "complete", data)
    except JobCancelled:
        # 取消端點已經寫過狀態與事件了;這裡安靜收工,不要再蓋一個 error 上去,
        # 更不要發出 complete —— 使用者按過取消,就不該再收到「完成」。
        logger.info("job_cancelled job_id=%s stage=%s", job.id, stage)
    except Exception as exc:
        job.status = "error"
        job.finished_at = time.time()
        job.error = {"message": str(exc), "stage": stage}
        await _emit(job, "error", {"message": str(exc), "stage": stage})


def _page_contract(slide, fallback: PageRole, instruction: Optional[str]) -> PageRole:
    """The outline row a regenerated slide should be described by afterwards.

    Regeneration used to leave ``outline.pages[n-1]`` frozen at whatever stage 1
    first decided. That is how a QA repair got undone: QA upgrades page 4 from
    ``title-content`` to ``process``, the user then regenerates page 4, and the
    stale outline row hands the model back the *original* role — silently
    reverting the fix. The contract has to follow the artifact.
    """
    gist = fallback.gist
    if instruction:
        # 使用者的調整指示是新的頁面意圖,不是一次性的補充;下次重生要看得到。
        gist = f"{gist}（調整指示：{instruction}）"
    return PageRole(
        role=slide.layout,
        title=slide.title or fallback.title,
        gist=gist,
        visual_intent=fallback.visual_intent,
    )


async def regenerate_slide(
    job: Job, n: int, instruction: Optional[str]
) -> Dict[str, Any]:
    """Regenerate ONLY page ``n``, transactionally.

    Builds a one-page sub-outline from that page's role/title/gist (the same
    shape the QA repair path uses), folding ``instruction`` into the gist, and
    re-runs stage 2 against just that page. Untouched pages are never re-sent,
    so they cannot drift.

    **Nothing user-visible changes until everything succeeds.** The candidate
    slide is rendered to a side file, validated, and rasterised into an unused
    preview version; only then are the in-memory IR, the deck on disk, the
    published previews and the version counters swapped over — in that order, as
    one step. Any failure before the swap leaves the previous IR, the previous
    ``deck.odp``, the previous previews and the previous gate results exactly as
    they were, and raises. The alternative — the old in-place mutation — meant a
    render that blew up after ``job.ir.slides[n-1]`` was replaced left a job
    whose IR, artifact and previews disagreed with each other, and whose download
    still served the failed page's predecessor under the new page's title.

    Because the artifact changes, the deterministic gates are re-run and the
    design gate is invalidated: the vision critic reviewed the *old* page 4.

    This is a **synchronous** operation (unlike initial generation): the result
    comes back to the caller in the HTTP response, not via the SSE stream — that
    stream's lifecycle already ended at ``complete``. Returns ``{"n", "slide",
    "preview_url", "gates", "version"}``; ``preview_url`` is ``None`` when
    previews are unavailable (no soffice).
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
        # Without this a regenerated page comes back in zh-TW while the rest of
        # the deck is English — the one page the user asked to fix.
        language=job.outline.language,
    )
    # The layout budget can silently drop bullets here exactly as it can during
    # the first generation. Until this collector existed, regeneration was the
    # one path where content vanished with nobody told (R2-03).
    dropped: List[DroppedContent] = []
    generated = await _offload(job, generate_slides, sub, job.backend, dropped)
    if not generated.slides:
        raise RuntimeError("模型沒有回傳這一頁的內容,原本的版本維持不變")

    # ---- build the candidate, entirely off to the side ----------------------
    candidate_ir = job.ir.model_copy(deep=True)
    candidate_ir.slides[n - 1] = generated.slides[0]
    candidate_path = job.dir / "deck.candidate.odp"
    candidate_preview = job.preview_dir_for(job.preview_version + 1)

    def _discard() -> None:
        candidate_path.unlink(missing_ok=True)
        shutil.rmtree(candidate_preview, ignore_errors=True)

    try:
        needs_asset_context = bool(job.assets) or any(
            slide.image is not None and bool(slide.image.prompt)
            for slide in candidate_ir.slides
        )
        if needs_asset_context:
            await _offload(
                job, render, candidate_ir, candidate_path, assets=job.assets
            )
        else:
            await _offload(job, render, candidate_ir, candidate_path)

        report = await _offload(job, validate_odf, candidate_path)
        zip_ok, zip_msg = report.gates["structure"]
        xml_ok, xml_msg = report.gates["xml"]
        if not (zip_ok and xml_ok):
            problems = [m for ok, m in ((zip_ok, zip_msg), (xml_ok, xml_msg)) if not ok]
            raise RuntimeError("重生後的 ODF 驗證未通過:" + ";".join(problems))

        # Previews are best-effort *as a feature*, but a preview run that dies
        # unexpectedly is a signal the deck is broken — so only the documented
        # degrade (no soffice) is tolerated here. Anything else rolls back.
        libreoffice_status = "pass"
        try:
            paths = await _rasterise_next_version(job, candidate_path)
        except PreviewUnavailable:
            paths = []
            libreoffice_status = "skipped"
        # Writing generated image blobs to disk is the last thing that can fail,
        # so it happens here — inside the rollback — rather than as the opening
        # line of a commit block that is supposed to be infallible.
        _persist_generated_assets(job)
    except BaseException:
        _discard()
        raise

    # ---- commit --------------------------------------------------------------
    # ``os.replace`` is the single point of no return and the only step here that
    # touches the filesystem. It goes first: if it fails (a Windows share lock,
    # a full disk) the candidate is discarded and the job still has its previous
    # IR, deck, previews, gates and versions — all of them, consistently. Every
    # statement after it is an in-memory assignment that cannot raise, which is
    # what makes "all or nothing" a property of the code rather than a hope.
    try:
        os.replace(candidate_path, job.odp_path)
    except BaseException:
        _discard()
        raise

    job.ir = candidate_ir
    job.outline.pages[n - 1] = _page_contract(
        candidate_ir.slides[n - 1], base, instruction
    )
    job.artifact_version += 1
    job.validated_version = job.artifact_version
    # Publish unconditionally, even when the rasterisation produced nothing.
    # Leaving the pointer on the previous version means every thumbnail request
    # for the new deck is answered with a picture of the old one — the preview
    # would be quietly, confidently wrong. An empty version directory 404s, and
    # "no image" is the truth here.
    _publish_previews(job)
    job.gates["zip"] = "pass"
    job.gates["xml"] = "pass"
    job.gates["libreoffice"] = libreoffice_status
    # The critic reviewed the page this one just replaced. Its verdict does not
    # transfer — a stale green tick on regenerated content is exactly the "舊綠燈"
    # this whole path exists to prevent.
    design_note = "這一頁重生後尚未經過視覺品檢,設計閘結果已重設。"
    job.gates["design"] = "unknown"
    job.qa_report = None
    if dropped:
        job.dropped_content = [*job.dropped_content, *dropped]

    preview_url = _preview_url(job, n) if len(paths) >= n else None
    gates = [
        {"gate": "zip", "status": "pass"},
        {"gate": "xml", "status": "pass"},
        {"gate": "libreoffice", "status": libreoffice_status},
        {"gate": "design", "status": "unknown", "note": design_note},
    ]

    # ---- make the new state survive a refresh (R1-02) ------------------------
    # The cockpit rebuilds itself by replaying this log. Until these events were
    # appended, a successful regeneration existed only in the HTTP response: F5
    # replayed the *previous* slide, the previous gates and the previous preview
    # while the download served the new deck.
    await _emit(job, "qa_invalidated", {"n": n, "reason": design_note})
    payload = {"n": n, "slide": job.ir.slides[n - 1].model_dump(mode="json")}
    await _emit(job, "slide_done", payload)
    await _emit(job, "unit_done", payload)
    # ``url: null`` is meaningful, not an omission: it says "this page has no
    # current thumbnail". The alternative — staying silent — leaves the previous
    # deck's image on screen labelled as the new page.
    await _emit(job, "preview_ready", {"n": n, "url": preview_url})
    if dropped:
        await _emit(
            job,
            "content_degraded",
            {"items": [d.model_dump(mode="json") for d in dropped]},
        )
    for gate in gates:
        await _emit_gate(job, gate["gate"], gate["status"], gate.get("note"))
    # Re-terminating the log: the job really is complete again, and a replay that
    # stopped before this point would restore a cockpit stuck mid-generation.
    await _emit(job, "complete", {"download_url": _download_url(job)})

    return {
        "n": n,
        "slide": job.ir.slides[n - 1].model_dump(mode="json"),
        "preview_url": preview_url,
        "version": job.artifact_version,
        "dropped_content": [d.model_dump(mode="json") for d in dropped],
        "gates": gates,
    }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class StrictBody(BaseModel):
    """Request bodies reject unknown fields instead of dropping them.

    ``doc_type`` was silently discarded for a whole release: the frontend sent
    ``"ods"``, pydantic dropped the key, and the user downloaded a ``.odp``
    believing they had asked for a spreadsheet. A 422 naming the field is the
    only version of that story where anyone finds out.
    """

    model_config = ConfigDict(extra="forbid")


# The whole batch of reference material, not one file of it. Six assets each
# just under the per-file cap decoded to ~17 MiB and arrived as ~23 MiB of JSON —
# accepted, parsed and held in memory, because nothing had ever added the totals
# up. The frontend enforces the same number, which is a courtesy to the user and
# no kind of gate: a client is free not to run it.
MAX_TOTAL_ASSET_BYTES = 16 * 1024 * 1024
# Base64 costs 4 bytes per 3, plus a short ``data:...;base64,`` prefix each. This
# is the cheap pre-check that runs *before* any decoding is attempted.
MAX_TOTAL_ASSET_ENCODED = (MAX_TOTAL_ASSET_BYTES // 3 + 1) * 4 + 6 * 128


def _decoded_length(data_url: str) -> int:
    """Decoded byte count of a base64 data URI, computed without decoding it.

    Deriving the size from the string's length is the point: calling
    ``base64.b64decode`` to find out how big something is means allocating the
    thing you are trying to refuse.
    """
    _, _, payload = data_url.partition(",")
    padding = len(payload) - len(payload.rstrip("="))
    return max(0, (len(payload) * 3) // 4 - padding)


def _check_asset_budget(assets: List[Any]) -> None:
    """Reject a batch of references whose *total* exceeds the budget."""
    urls = [a.data_url for a in assets if getattr(a, "data_url", None)]
    encoded = sum(len(u) for u in urls)
    if encoded > MAX_TOTAL_ASSET_ENCODED:
        raise ValueError(
            f"參考檔案總量過大（編碼後約 {encoded / 1024 / 1024:.1f} MiB）;"
            f"全部檔案合計上限為 {MAX_TOTAL_ASSET_BYTES // 1024 // 1024} MiB，"
            "請減少檔案數量或改用較小的檔案。"
        )
    decoded = sum(_decoded_length(u) for u in urls)
    if decoded > MAX_TOTAL_ASSET_BYTES:
        raise ValueError(
            f"參考檔案總量過大（{decoded / 1024 / 1024:.1f} MiB）;"
            f"全部檔案合計上限為 {MAX_TOTAL_ASSET_BYTES // 1024 // 1024} MiB，"
            "請減少檔案數量或改用較小的檔案。"
        )


class AssetUpload(StrictBody):
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


class GenerateBody(StrictBody):
    prompt: str = Field(min_length=1, max_length=8000)
    mode: Optional[Literal["presenter", "detailed"]] = None
    # Validated against the theme registry (like ``backend``), so adding a
    # built-in preset does not mean editing a Literal in three files.
    theme: Optional[str] = None
    # A user template's tokens, sent instead of ``theme``. Validated by
    # DesignSpec itself — an unreadable palette is a 422, not a grey deck.
    design: Optional[DesignSpec] = None
    # 版式: independent of colour, so a palette can be worn by any composition.
    style: Optional[str] = None
    language: str = "zh-TW"
    # Cover branding. ``logo`` is one more base64 image upload; it is stored
    # apart from ``assets`` so the model is never offered the crest as media.
    byline: str = Field(default="", max_length=160)
    logo: Optional[AssetUpload] = None
    logo_placement: Literal["cover", "cover-closing", "all"] = "cover-closing"
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
    pages: Optional[int] = Field(default=None, ge=MIN_REQUESTED_PAGES, le=MAX_OUTLINE_PAGES)
    assets: List[AssetUpload] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def assets_must_fit_the_batch_budget(self) -> GenerateBody:
        _check_asset_budget(self.assets)
        return self

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

    @field_validator("theme")
    @classmethod
    def theme_must_be_a_preset(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in THEMES:
            raise ValueError(f"unknown theme; available: {sorted(THEMES)}")
        return value

    @field_validator("language")
    @classmethod
    def language_must_be_supported(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"unknown language; available: {sorted(LANGUAGES)}")
        return value

    @field_validator("style")
    @classmethod
    def style_must_be_registered(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in STYLES:
            raise ValueError(f"unknown style; available: {sorted(STYLES)}")
        return value

    @field_validator("logo")
    @classmethod
    def logo_must_be_a_raster_image(
        cls, value: Optional[AssetUpload]
    ) -> Optional[AssetUpload]:
        # AssetUpload also accepts PDFs (they are reference documents elsewhere);
        # a crest has to be an image the renderer can actually draw.
        if value is not None and value.data_url.lower().startswith(
            "data:application/pdf"
        ):
            raise ValueError("校徽必須是 PNG 或 JPEG 圖片")
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
    except Exception as exc:
        return {"name": name, "available": False, "reason": _first_line(str(exc))}
    return {"name": name, "available": True, "reason": ""}


def _first_line(message: str) -> str:
    """The headline of a multi-line factory error (the rest is shell examples).

    The raw error's first line ends mid-sentence(「…請先設定後再執行,例如:」)
    because the shell examples live on the lines this cut drops — a dangling
    「例如:」promising an example that never comes reads as a broken UI, so the
    suffix goes too.
    """
    if not message.strip():
        return ""
    line = message.strip().splitlines()[0].rstrip()
    for suffix in ("例如:", "例如："):
        if line.endswith(suffix):
            line = line[: -len(suffix)].rstrip("，, ").rstrip()
    return line


# An .otp/.odp is a zip; a few MiB is a generous ceiling for one, and the cap
# is what stops a 200 MB "template" from being base64-inflated into memory.
MAX_TEMPLATE_BYTES = 12 * 1024 * 1024
_TEMPLATE_MIMES = (
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.presentation-template",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.text-template",
    # Windows browsers commonly send .otp as one of these.
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
)


def decode_template_upload(data_url: str) -> bytes:
    """Decode a base64 ``data:`` URI holding an ODF template file."""
    header, _, encoded = data_url.partition(",")
    lowered = header.lower()
    if not lowered.startswith("data:") or ";base64" not in lowered:
        raise ValueError("範本必須是 base64 data URI")
    if not any(f"data:{mime}" == lowered.split(";")[0] for mime in _TEMPLATE_MIMES):
        raise ValueError("請上傳 .otp / .odp / .ott / .odt 範本檔")
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("範本檔內容不是有效的 base64") from exc
    if not blob:
        raise ValueError("範本檔是空的")
    if len(blob) > MAX_TEMPLATE_BYTES:
        raise ValueError(
            f"範本檔超過 {MAX_TEMPLATE_BYTES // (1024 * 1024)} MiB 上限"
        )
    if not blob.startswith(b"PK"):
        raise ValueError("範本檔不是有效的 ODF(zip)檔案")
    return blob


class TemplateBody(StrictBody):
    """Save (or overwrite) one user template."""

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    design: DesignSpec
    style: str = "classic"
    source: Literal["custom", "extracted"] = "custom"
    # Present = overwrite that template; absent = create a new one.
    id: Optional[str] = Field(default=None, max_length=64)


class TemplateExtractBody(StrictBody):
    """An uploaded ODF template to sample a DesignSpec from."""

    data_url: str = Field(min_length=1, max_length=17_000_000)


class DiscoveryAsset(StrictBody):
    description: str = Field(min_length=1, max_length=240)
    credit: str = Field(default="", max_length=160)
    data_url: Optional[str] = Field(default=None, max_length=12_000_000)

    @field_validator("data_url")
    @classmethod
    def data_url_must_be_pdf(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not is_pdf_data_uri(value):
            raise ValueError("discovery reference bytes must be a PDF data URI")
        return value


class DiscoveryBody(StrictBody):
    """Preflight payload; PDF text is read while image bytes stay client-side."""

    prompt: str = Field(min_length=1, max_length=8000)
    mode: Optional[Literal["presenter", "detailed"]] = None
    theme: Optional[str] = None
    language: str = "zh-TW"
    backend: Optional[Literal["deepseek", "ollama", "custom"]] = None
    doc_type: str = "odp"
    pages: Optional[int] = Field(default=None, ge=MIN_REQUESTED_PAGES, le=MAX_OUTLINE_PAGES)
    assets: List[DiscoveryAsset] = Field(default_factory=list, max_length=6)

    @field_validator("theme")
    @classmethod
    def theme_must_be_a_preset(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in THEMES:
            raise ValueError(f"unknown theme; available: {sorted(THEMES)}")
        return value

    @field_validator("language")
    @classmethod
    def language_must_be_supported(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"unknown language; available: {sorted(LANGUAGES)}")
        return value

    @model_validator(mode="after")
    def assets_must_fit_the_batch_budget(self) -> DiscoveryBody:
        # Discovery takes the same payload shape and so had the same hole.
        _check_asset_budget(self.assets)
        return self

    @field_validator("prompt")
    @classmethod
    def prompt_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value


class OutlineActionBody(StrictBody):
    action: str
    outline: Optional[dict] = None


class RegenerateBody(StrictBody):
    instruction: Optional[str] = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def frontend_dist() -> Optional[Path]:
    """Where the built web cockpit lives, or ``None`` if this install has none.

    Two locations, in priority order:

    1. ``odforge/webui`` **inside the installed package** — put there by the
       wheel's build hook (see ``hatch_build.py``) and found with
       ``importlib.resources``, so it works from site-packages and from a venv
       on another machine.
    2. ``<repo>/web/dist`` — the developer's live ``npm run build`` output, so a
       git checkout still serves the cockpit without reinstalling after every
       frontend change.

    The old implementation had only (2), computed by walking up from
    ``__file__``: correct in a checkout, wrong in every wheel, and the resulting
    API-only server looked exactly like a server whose frontend you forgot to
    build.

    **Import from inside a zip is not supported**, and the previous code only
    appeared to support it. ``resources.as_file`` materialises a zipped resource
    into a temp directory and *deletes it when its context closes* — and the
    path was returned from inside the ``with``, so the zipped case handed
    ``StaticFiles`` a directory that no longer existed. On Python 3.11 it did
    not get that far: ``as_file`` could not handle a directory at all. Nothing
    caught either, because pip always unpacks a wheel, which makes ``as_file``
    a no-op on every install this project actually ships. A claim that is never
    exercised and does not work is worse than no claim, so it is gone: a zip
    import now degrades to API-only with a log line saying so.
    """
    try:
        packaged = resources.files("odforge") / "webui"
        if (packaged / "index.html").is_file():
            if isinstance(packaged, Path):
                return packaged
            logger.warning(
                "the web cockpit is packaged inside a non-filesystem loader "
                "(%r); serving API-only. Install the wheel normally — "
                "importing odforge from a zip is not supported.",
                type(packaged).__name__,
            )
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        pass
    source_tree = Path(__file__).resolve().parents[2] / "web" / "dist"
    return source_tree if (source_tree / "index.html").is_file() else None


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
        if body.language != "zh-TW":
            # The interview stays in zh-TW (that is the user's language); this
            # only tells the editor what the DECK will be written in, so it can
            # ask about terminology instead of asking which language to use.
            context.append(
                f"簡報輸出語言：{LANGUAGES[body.language]}"
                "（訪談問題仍以繁體中文提問）"
            )
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
                detail=(
                    "Web 控制台只產生簡報(odp)。文件(odt)與試算表(ods)"
                    "請用 CLI(odforge new -o out.odt)或 MCP 的 forge_* 工具——"
                    "核心引擎三種格式都支援,是控制台的頁面模型只適用簡報"
                    "(見 docs/gates.md 第五節)。"
                ),
            )
        _prune_jobs(app)
        # Count jobs whose WORKER is still alive, not jobs whose UI status looks
        # busy. Cancelling flips the status to "cancelled" at once, but the
        # provider call already in flight keeps running inside its thread: the old
        # count let a user press start/cancel five times and end up with five
        # concurrent paid API calls while the console showed nothing running.
        active_jobs = sum(_occupies_a_slot(job) for job in app.state.jobs.values())
        if active_jobs >= _MAX_ACTIVE_JOBS:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"同時進行的工作已達上限 {_MAX_ACTIVE_JOBS}"
                    "(含已取消但外部呼叫尚未結束的工作),請稍候再試。"
                ),
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
                language=body.language,
                design=body.design,
                style=body.style,
                byline=body.byline,
                logo=body.logo,
                logo_placement=body.logo_placement,
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

    # ---- 範本庫 (deck templates) ------------------------------------------
    #
    # Built-ins and user templates are listed together but travel differently:
    # a built-in is applied as ``theme: <id>`` (a preset the renderer already
    # knows), a user template as a full ``design`` payload. The frontend does
    # not need to know that rule — each row says which it is.

    def _templates_file() -> Path:
        return templates_path(app.state.jobs_dir)

    @app.get("/api/templates")
    async def list_templates() -> Dict[str, Any]:
        return {
            "templates": [
                t.model_dump(mode="json") for t in all_templates(_templates_file())
            ],
            "languages": [
                {"id": code, "label": label} for code, label in LANGUAGES.items()
            ],
            # The style registry travels with the list so the gallery can offer
            # 版式 without keeping its own copy of the names and descriptions.
            "styles": [
                {"id": s.id, "label": s.label, "blurb": s.blurb}
                for s in STYLES.values()
            ],
        }

    @app.post("/api/templates")
    async def create_template(body: TemplateBody) -> Dict[str, Any]:
        try:
            template = save_template(
                _templates_file(),
                name=body.name,
                design=body.design,
                style=body.style,
                source=body.source,
                template_id=body.id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return template.model_dump(mode="json")

    @app.delete("/api/templates/{template_id}")
    async def remove_template(template_id: str) -> Dict[str, Any]:
        try:
            removed = delete_template(_templates_file(), template_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="找不到這個範本")
        return {"ok": True}

    @app.post("/api/templates/extract")
    async def extract_template(body: TemplateExtractBody) -> Dict[str, Any]:
        """Sample an existing ODF template's look into an editable DesignSpec.

        Nothing is saved here — the caller previews the extracted palette and
        then POSTs it back under a name. That split is deliberate: a template
        whose colours came out wrong should never silently join the library.
        """
        try:
            blob = decode_template_upload(body.data_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.odp"
            path.write_bytes(blob)
            try:
                design = await asyncio.to_thread(extract_design, path)
            except TemplateExtractionError as exc:
                raise HTTPException(
                    status_code=422, detail=f"無法讀取這個範本檔：{exc}"
                ) from exc
        return {"design": design.model_dump(mode="json")}

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
                # Replay/emit everything appended since our cursor. The stream
                # closes on a terminal event — but only on the *last* one. A
                # successful regeneration appends a fresh page, gates and its own
                # ``complete`` after the original, so a reconnecting client that
                # stopped at the first terminal event restored the deck as it
                # looked several versions ago while the download served the
                # newest. "Terminal" has to mean "nothing came after it".
                while cursor < len(job.events):
                    ev = job.events[cursor]
                    cursor += 1
                    yield {
                        "event": ev["event"],
                        "data": json.dumps(ev["data"], ensure_ascii=False),
                    }
                    if (
                        ev["event"] in ("complete", "error")
                        and cursor >= len(job.events)
                        and job.status in _TERMINAL_JOB_STATUSES
                    ):
                        return
                if await request.is_disconnected():
                    return
                # Wait for the next emit; the timeout bounds liveness (and makes
                # a missed wakeup at worst a sub-second delay, never a lost event
                # since the loop re-reads len(job.events) each pass).
                job.updated.clear()
                try:
                    await asyncio.wait_for(job.updated.wait(), timeout=_SSE_POLL_SECONDS)
                except TimeoutError:
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
                raise HTTPException(status_code=422, detail=f"invalid outline: {exc}") from None
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
            # While a candidate is being built the artifact on disk is mid-swap,
            # so the download endpoint must refuse it. ``complete`` is restored
            # either way — the rollback path leaves a perfectly good deck.
            job.status = "regenerating"
            try:
                result = await regenerate_slide(job, n, body.instruction)
            except Exception as exc:
                # Clean, structured error (the run_job error shape) instead of a bare
                # 500 traceback. The rollback already restored everything, so the
                # job goes back to being complete and downloadable.
                job.status = "complete"
                _persist_job(job)
                raise HTTPException(
                    status_code=500,
                    detail={"message": str(exc), "stage": "regenerate"},
                ) from exc
            job.status = "complete"
            _persist_job(job)
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
        # Server-owned path only: fixed name inside the job's own published
        # preview version — a page beyond the current deck cannot resolve.
        path = _preview_file(job, n)
        if path is None:
            raise HTTPException(status_code=404, detail="preview not available")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str) -> FileResponse:
        """Serve the deck only when the gates approved *this* version of it.

        The presence of ``deck.odp`` proves nothing: a job that is still
        generating, that failed validation, that was cancelled, or whose
        regeneration rolled back all leave a file at that path. Handing it over
        would ship an artifact under quality claims it never earned, so anything
        short of "finished and validated at the current version" is a 409 with a
        message saying which of those it is.
        """
        job = _get_job(job_id)
        if job.downloadable:
            return FileResponse(
                job.odp_path, media_type=ODP_MIME, filename=_download_filename(job)
            )
        raise HTTPException(status_code=409, detail=_undownloadable_reason(job))

    @app.get("/api/jobs/{job_id}")
    async def snapshot(job_id: str) -> Dict[str, Any]:
        job = _get_job(job_id)
        data: Dict[str, Any] = {
            "status": job.status,
            "slides_done": job.slides_done,
            "outline": job.outline.model_dump(mode="json") if job.outline else None,
            "gates": dict(job.gates),
            "artifact_version": job.artifact_version,
            "downloadable": job.downloadable,
        }
        if job.dropped_content:
            data["dropped_content"] = [
                d.model_dump(mode="json") for d in job.dropped_content
            ]
        if job.qa_report is not None and job.qa_report.findings_by_round:
            last = job.qa_report.findings_by_round[-1]
            data["findings"] = [f.model_dump(mode="json") for f in last]
        # Only advertise the link when it will actually serve; a snapshot that
        # promises a download the endpoint then 409s is the same lie one layer up.
        if job.downloadable:
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
