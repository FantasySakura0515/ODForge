"""Live preview (``odforge preview``): watch a Document IR file, keep the cockpit current.

In this mode ODForge writes no content. The user's own agent (Claude Code,
Codex…) edits a presentation IR file on disk; every time the file changes, this
module re-renders it, lays it out with LibreOffice, and pushes the result to the
web cockpit that is already open in the browser — pages, the format gates, and
where text overflows (跑版). LibreOffice never shows a window: it only renders.

The cockpit is reused as is. A preview is an ordinary :class:`~odforge.webapi.Job`
marked ``watching``: each reload replaces its event log with a fresh, complete
one (``reload`` → ``outline`` → pages → gates → previews → ``layout_report`` →
``complete``), so a browser that connects at any moment replays exactly the
current version and nothing older.

A file that does not parse (a half-written save, a schema error) does not wipe
the screen: the last good version stays up and an ``error`` event says what is
wrong with the new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Tuple

from fastapi import FastAPI
from pydantic import ValidationError

from .ir import Outline, PageRole, Presentation, parse_ir
from .layoutcheck import LayoutReport, check_layout_pdf
from .preview import PreviewUnavailable, convert_to_pdf, rasterise_pdf
from .render import render
from .validate import validate_odf
from .webapi import (
    Job,
    _download_url,
    _emit,
    _emit_gate,
    _preview_url,
    _prune_preview_versions,
    create_app,
)

POLL_SECONDS = 0.4
# An agent's save may land in several writes; wait for the file to sit still
# this long before reading it, so a half-written file is the exception.
SETTLE_SECONDS = 0.3

_DESIGN_NOTE = "預覽模式:內容由你的 agent 撰寫,不跑視覺審稿;版面問題請看跑版檢查。"


class SourceError(ValueError):
    """The watched file could not be turned into a presentation (shown to the user)."""


def outline_for(ir: Presentation) -> Outline:
    """The outline rail's rows, derived from the deck itself (no stage 1 here)."""
    return Outline(
        design=ir.design,
        style=ir.style,
        mode=ir.design.mode if ir.design is not None else "presenter",
        pages=[
            PageRole(
                role=slide.layout,
                title=slide.title,
                gist=(slide.subtitle or slide.title or slide.layout)[:600],
            )
            for slide in ir.slides
        ],
    )


def load_presentation(source: Path) -> Presentation:
    """Read and validate the watched file, with errors a person can act on."""
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceError(f"找不到 {source}") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"{source.name} 不是合法的 JSON(第 {exc.lineno} 行):{exc.msg}") from exc
    if not isinstance(data, dict):
        raise SourceError(f"{source.name} 的最外層必須是 JSON 物件")
    data.setdefault("type", "presentation")
    try:
        ir = parse_ir(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()[:5]
        )
        raise SourceError(f"{source.name} 不符合簡報格式:{problems}") from exc
    if not isinstance(ir, Presentation):
        raise SourceError("預覽目前只支援簡報(type 必須是 presentation)")
    return ir


def _render_and_measure(
    ir: Presentation, job: Job, version_dir: Path
) -> Tuple[dict, list, str, LayoutReport]:
    """Blocking half of a reload: render, validate, rasterise, measure.

    Returns ``(gates, pngs, libreoffice_status, layout)``. Raises
    :class:`SourceError` when the deck fails a blocking gate.
    """
    candidate = job.dir / "deck.candidate.odp"
    render(ir, candidate)
    report = validate_odf(candidate)
    gates = {
        "zip": "pass" if report.gates["structure"][0] else "fail",
        "xml": "pass" if report.gates["xml"][0] else "fail",
    }
    if "fail" in gates.values():
        problems = "; ".join(m for ok, m in report.gates.values() if not ok)
        candidate.unlink(missing_ok=True)
        raise SourceError(f"產出的檔案沒有通過格式驗證:{problems}")
    os.replace(candidate, job.odp_path)

    workdir = Path(tempfile.mkdtemp(prefix="odforge-watch-"))
    try:
        try:
            pdf = convert_to_pdf(job.odp_path, workdir)
        except PreviewUnavailable:
            return gates, [], "skipped", LayoutReport(
                "unavailable", "找不到 LibreOffice(soffice),無法預覽與檢查跑版"
            )
        except Exception as exc:
            return gates, [], "fail", LayoutReport(
                "unavailable", f"LibreOffice 轉檔失敗:{exc}"
            )
        shutil.rmtree(version_dir, ignore_errors=True)
        pngs = rasterise_pdf(pdf, version_dir)
        return gates, pngs, "pass", check_layout_pdf(job.odp_path, pdf)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


Announce = Callable[[str], None]


async def reload(
    job: Job, source: Path, out: Optional[Path], announce: Optional[Announce] = None
) -> None:
    """Re-render ``source`` and replace the job's event log with the new version.

    ``announce`` receives a short plain-text summary of each outcome. The agent
    that started ``odforge preview`` cannot see the browser; this is how it
    learns which page overflowed without asking the user.
    """
    say = announce or (lambda _text: None)
    try:
        ir = load_presentation(source)
        gates, pngs, libreoffice, layout = await asyncio.to_thread(
            _render_and_measure, ir, job, job.preview_dir_for(job.preview_version + 1)
        )
    except SourceError as exc:
        # Keep the last good version on screen; say what is wrong with this one.
        job.error = {"message": str(exc), "stage": "source"}
        await _emit(job, "error", job.error)
        say(f"讀不進來,畫面維持上一版:{exc}")
        return

    job.ir = ir
    job.outline = outline_for(ir)
    job.error = None
    job.artifact_version += 1
    job.validated_version = job.artifact_version
    if pngs:
        job.preview_version += 1
        _prune_preview_versions(job)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job.odp_path, out)

    # Swap in a fresh log; open streams see the epoch change and replay it.
    job.events = []
    job.event_epoch += 1
    job.gates = {}
    job.status = "rendering"
    await _emit(job, "reload", {"source": source.name, "out": str(out) if out else None})
    await _emit(job, "outline", job.outline.model_dump(mode="json"))
    for n, slide in enumerate(ir.slides, start=1):
        payload = {"n": n, "slide": slide.model_dump(mode="json")}
        await _emit(job, "slide_done", payload)
    for gate, status in gates.items():
        await _emit_gate(job, gate, status)
    await _emit_gate(job, "libreoffice", libreoffice)
    for n in range(1, len(pngs) + 1):
        await _emit(job, "preview_ready", {"n": n, "url": _preview_url(job, n)})
    await _emit(
        job,
        "layout_report",
        {
            "status": layout.status,
            "message": layout.message,
            "findings": [
                {"slide_no": f.page, "issue": f.detail()} for f in layout.findings
            ],
        },
    )
    await _emit_gate(job, "design", "skipped", _DESIGN_NOTE)
    job.status = "complete"
    job.finished_at = time.time()
    await _emit(job, "complete", {"download_url": _download_url(job)})

    summary = f"第 {job.artifact_version} 版:{len(ir.slides)} 頁,{layout.message}"
    say("\n".join([summary, *(f"  - {f.describe()}" for f in layout.findings)]))


def _stamp(source: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = source.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


async def watch_loop(
    job: Job, source: Path, out: Optional[Path], announce: Optional[Announce] = None
) -> None:
    """Reload whenever ``source`` changes (and once at start)."""
    last: Any = object()
    while True:
        stamp = _stamp(source)
        if stamp != last:
            await asyncio.sleep(SETTLE_SECONDS)
            if _stamp(source) == stamp:
                last = stamp
                await reload(job, source, out, announce)
                continue
        await asyncio.sleep(POLL_SECONDS)


def create_preview_app(
    source: Path,
    out: Optional[Path],
    jobs_dir: Optional[Path] = None,
    announce: Optional[Announce] = None,
) -> Tuple[FastAPI, Job]:
    """A cockpit app with one watching job for ``source``; the watcher runs with it."""
    source = Path(source).resolve()
    out = Path(out).resolve() if out is not None else None
    base = Path(jobs_dir) if jobs_dir else Path(tempfile.mkdtemp(prefix="odforge-preview-"))
    job_id = uuid.uuid4().hex
    job = Job(id=job_id, dir=base / job_id, prompt=f"預覽 {source.name}", watching=True)
    job.dir.mkdir(parents=True, exist_ok=True)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(watch_loop(job, source, out, announce))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = create_app(base, lifespan=lifespan)
    app.state.jobs[job.id] = job
    return app, job
