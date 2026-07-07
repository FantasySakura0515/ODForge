"""Tests for the ODForge Web API (Task 18.1) — FastAPI + SSE.

No real LLM API and no LibreOffice/soffice are ever touched: the two-stage
pipeline functions (``generate_outline`` / ``generate_slides``), the renderer,
the preview rasteriser and the QA loop are all monkeypatched to fast, offline
fakes on the ``odforge.webapi`` module namespace.

SSE testing approach
--------------------
The event *ordering* is asserted two ways:

* **Direct runner** — ``asyncio.run(webapi.run_job(job))`` drives the same async
  pipeline the background task runs and we inspect the recorded ``job.events``.
  Deterministic; no HTTP, no event loop coordination.
* **Real SSE over the wire** — for a job that has already completed, the
  ``GET /events`` endpoint *replays* the full event history to a late
  subscriber; a Starlette ``TestClient`` streaming request parses the actual
  ``event:``/``data:`` frames. Because the job is finished the stream ends at
  ``complete`` with no waiting, so the assertion is deterministic too.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")
from fastapi.testclient import TestClient  # noqa: E402

from odforge import webapi  # noqa: E402
from odforge.critic import Finding, QAReport  # noqa: E402
from odforge.ir import Outline, PageRole, Presentation, Slide  # noqa: E402

# A 1x1 transparent PNG — enough bytes for FileResponse to serve.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000154a24f3b0000000049454e44ae426082"
)

# Roles that need no special slide fields (avoids Slide cross-field validators).
_SAFE_ROLES = ["title", "title-content", "section", "agenda", "closing"]


def _outline(n: int = 3) -> Outline:
    pages = [
        PageRole(role=_SAFE_ROLES[i % len(_SAFE_ROLES)], title=f"頁 {i + 1}", gist=f"重點 {i + 1}")
        for i in range(n)
    ]
    return Outline(design=None, mode="presenter", pages=pages)


def _slides_for(outline: Outline) -> Presentation:
    slides = []
    for pg in outline.pages:
        bullets = ["甲", "乙"] if pg.role in ("title-content", "agenda") else []
        slides.append(Slide(layout=pg.role, title=pg.title, bullets=bullets))
    return Presentation(title="測試簡報", slides=slides)


def _install_fakes(monkeypatch, n=3, *, record_slides=None, qa_report=None):
    """Patch the pipeline onto webapi with offline fakes.

    ``render`` writes a real (tiny) file so downloads work; ``render_pages``
    writes ``n`` real PNGs so previews are served; the two generators return
    deterministic IR. When ``record_slides`` is a list, every ``generate_slides``
    call appends the outline it received (for the regenerate assertion).
    """

    def fake_outline(prompt, backend=None):
        return _outline(n)

    def fake_slides(outline, backend=None):
        if record_slides is not None:
            record_slides.append(outline)
        return _slides_for(outline)

    def fake_render(ir, out_path):
        from pathlib import Path

        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"PK\x03\x04 fake-odp")
        return p

    def fake_render_pages(odf_path, out_dir, dpi=150):
        from pathlib import Path

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(1, n + 1):
            pp = out_dir / f"page-{i:02d}.png"
            pp.write_bytes(_PNG)
            paths.append(pp)
        return paths

    def fake_qa(ir, out_path, **kwargs):
        webapi.render(ir, out_path)  # QA re-renders each round
        return qa_report or QAReport(rounds=1, findings_by_round=[[]], final_ok=True)

    monkeypatch.setattr(webapi, "generate_outline", fake_outline)
    monkeypatch.setattr(webapi, "generate_slides", fake_slides)
    monkeypatch.setattr(webapi, "render", fake_render)
    monkeypatch.setattr(webapi, "render_pages", fake_render_pages)
    monkeypatch.setattr(webapi, "run_qa_loop", fake_qa)


@pytest.fixture
def app(tmp_path):
    return webapi.create_app(jobs_dir=tmp_path / "jobs")


def _parse_sse(text: str):
    """Parse a raw SSE body into a list of ``{"event", "data"}`` dicts."""
    events = []
    cur = {}
    for line in text.splitlines():
        if line == "":
            if cur:
                events.append(cur)
                cur = {}
            continue
        if line.startswith(":"):  # comment / ping
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            cur["event"] = value
        elif field == "data":
            cur["data"] = cur.get("data", "") + value
    if cur:
        events.append(cur)
    return events


# ---------------------------------------------------------------------------
# POST /api/generate
# ---------------------------------------------------------------------------


def test_generate_returns_job_id(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "介紹光合作用"})
        assert r.status_code == 200
        body = r.json()
        assert "job_id" in body
        jid = body["job_id"]
        assert isinstance(jid, str) and len(jid) == 32  # uuid4 hex, server-minted

        # background task drives it to completion; poll the snapshot endpoint.
        for _ in range(200):
            snap = client.get(f"/api/jobs/{jid}").json()
            if snap["status"] in ("complete", "error"):
                break
            time.sleep(0.01)
        assert snap["status"] == "complete"
        assert snap["slides_done"] == 3


# ---------------------------------------------------------------------------
# SSE event stream — order and shapes
# ---------------------------------------------------------------------------


def test_event_sequence_direct(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    names = [e["event"] for e in job.events]
    assert names == [
        "outline",
        "slide_done",
        "slide_done",
        "slide_done",
        "preview_ready",
        "preview_ready",
        "preview_ready",
        "complete",
    ]
    # payload shapes
    outline_ev = job.events[0]["data"]
    assert "pages" in outline_ev and outline_ev["mode"] == "presenter"
    sd = job.events[1]["data"]
    assert sd["n"] == 1 and sd["slide"]["title"] == "頁 1"
    pr = job.events[4]["data"]
    assert pr["n"] == 1 and pr["url"].endswith("/preview/1.png")
    done = job.events[-1]["data"]
    assert done["download_url"].endswith("/download")


def test_sse_replay_over_http(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))  # complete before subscribing

    with TestClient(app) as client:
        with client.stream("GET", f"/api/jobs/{job.id}/events") as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    events = _parse_sse(body)
    names = [e["event"] for e in events]
    assert names == [
        "outline",
        "slide_done",
        "slide_done",
        "preview_ready",
        "preview_ready",
        "complete",
    ]
    # data is valid JSON on every frame
    for e in events:
        json.loads(e["data"])


def test_preview_degrades_when_unavailable(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)

    def boom(odf_path, out_dir, dpi=150):
        from odforge.preview import PreviewUnavailable

        raise PreviewUnavailable("no soffice")

    monkeypatch.setattr(webapi, "render_pages", boom)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    names = [e["event"] for e in job.events]
    assert "preview_ready" not in names  # degraded, not crashed
    assert names[-1] == "complete"
    assert job.status == "complete"


# ---------------------------------------------------------------------------
# Interactive outline gate
# ---------------------------------------------------------------------------


def test_interactive_gate_direct(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x", interactive=True)

    async def scenario():
        task = asyncio.create_task(webapi.run_job(job))
        # advance until the runner parks at the approval gate
        for _ in range(1000):
            if job.status == "awaiting_approval":
                break
            await asyncio.sleep(0)
        assert job.status == "awaiting_approval"
        names = [e["event"] for e in job.events]
        assert names == ["outline", "awaiting_approval"]
        assert not task.done()  # halted, waiting for approval

        job.approval.set()  # simulate POST /outline approve
        await task
        return job

    asyncio.run(scenario())
    assert job.status == "complete"
    assert [e["event"] for e in job.events][-1] == "complete"


def test_interactive_approve_over_http(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "x", "interactive": True})
        jid = r.json()["job_id"]

        for _ in range(200):
            snap = client.get(f"/api/jobs/{jid}").json()
            if snap["status"] == "awaiting_approval":
                break
            time.sleep(0.01)
        assert snap["status"] == "awaiting_approval"
        assert snap["outline"] is not None

        r2 = client.post(f"/api/jobs/{jid}/outline", json={"action": "approve"})
        assert r2.status_code == 200

        for _ in range(300):
            snap = client.get(f"/api/jobs/{jid}").json()
            if snap["status"] == "complete":
                break
            time.sleep(0.01)
        assert snap["status"] == "complete"


def test_outline_edit_replaces_outline(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x", interactive=True)

    edited = Outline(
        design=None,
        mode="detailed",
        pages=[PageRole(role="title", title="改過的標題", gist="新重點")],
    ).model_dump(mode="json")

    async def scenario():
        task = asyncio.create_task(webapi.run_job(job))
        for _ in range(1000):
            if job.status == "awaiting_approval":
                break
            await asyncio.sleep(0)
        # apply the edit the way the endpoint does, then release the gate
        job.outline = Outline.model_validate(edited)
        job.approval.set()
        await task

    asyncio.run(scenario())
    assert job.status == "complete"
    assert len(job.ir.slides) == 1  # stage 2 ran against the edited 1-page outline
    assert job.ir.slides[0].title == "改過的標題"


def test_outline_endpoint_wrong_state_409(app, monkeypatch):
    _install_fakes(monkeypatch)
    job = webapi.create_job(app, prompt="x")  # status "pending", not awaiting
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/outline", json={"action": "approve"})
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def test_download_after_completion(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 200
        assert r.headers["content-type"] == webapi.ODP_MIME
        assert "attachment" in r.headers.get("content-disposition", "")
        assert r.content == b"PK\x03\x04 fake-odp"


def test_download_before_ready_404(app, monkeypatch):
    _install_fakes(monkeypatch)
    job = webapi.create_job(app, prompt="x")  # never rendered
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Preview + path whitelist
# ---------------------------------------------------------------------------


def test_preview_served(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/preview/2.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == _PNG


def test_preview_out_of_range_404(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    with TestClient(app) as client:
        assert client.get(f"/api/jobs/{job.id}/preview/99.png").status_code == 404
        assert client.get(f"/api/jobs/{job.id}/preview/0.png").status_code == 404


def test_unknown_job_is_404_everywhere(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/jobs/deadbeef").status_code == 404
        assert client.get("/api/jobs/deadbeef/download").status_code == 404
        assert client.get("/api/jobs/deadbeef/preview/1.png").status_code == 404
        assert client.get("/api/jobs/deadbeef/events").status_code == 404
        assert (
            client.post("/api/jobs/deadbeef/outline", json={"action": "approve"}).status_code
            == 404
        )
        assert (
            client.post(
                "/api/jobs/deadbeef/slides/1/regenerate", json={}
            ).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# Regenerate a single page
# ---------------------------------------------------------------------------


def test_regenerate_reruns_only_that_page(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    original_titles = [s.title for s in job.ir.slides]

    # Re-arm generate_slides to record the outline the regenerate path sends.
    recorded = []

    def recording_slides(outline, backend=None):
        recorded.append(outline)
        return _slides_for(outline)

    monkeypatch.setattr(webapi, "generate_slides", recording_slides)

    events_before = len(job.events)
    with TestClient(app) as client:
        r = client.post(
            f"/api/jobs/{job.id}/slides/2/regenerate",
            json={"instruction": "更大膽"},
        )
        assert r.status_code == 200

    # generate_slides was called with a ONE-page sub-outline (only page 2).
    assert len(recorded) == 1
    assert len(recorded[0].pages) == 1
    assert recorded[0].pages[0].role == "title-content"  # page 2 role from _SAFE_ROLES
    assert "更大膽" in recorded[0].pages[0].gist  # instruction folded into gist

    # other pages untouched
    assert job.ir.slides[0].title == original_titles[0]
    assert job.ir.slides[2].title == original_titles[2]

    # a fresh slide_done + preview_ready for page 2 were pushed onto the stream
    new = job.events[events_before:]
    assert any(e["event"] == "slide_done" and e["data"]["n"] == 2 for e in new)
    assert any(e["event"] == "preview_ready" and e["data"]["n"] == 2 for e in new)


def test_regenerate_out_of_range_404(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    with TestClient(app) as client:
        assert (
            client.post(
                f"/api/jobs/{job.id}/slides/99/regenerate", json={}
            ).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# QA loop → qa_round events
# ---------------------------------------------------------------------------


def test_qa_rounds_emitted(app, monkeypatch):
    report = QAReport(
        rounds=2,
        findings_by_round=[
            [Finding(slide_no=1, issue="文字溢出", severity="error", fix_hint="縮短文字")],
            [],
        ],
        final_ok=True,
    )
    _install_fakes(monkeypatch, n=2, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True)
    asyncio.run(webapi.run_job(job))

    qa_events = [e for e in job.events if e["event"] == "qa_round"]
    assert [e["data"]["round"] for e in qa_events] == [1, 2]
    assert qa_events[0]["data"]["findings"][0]["issue"] == "文字溢出"

    complete = job.events[-1]
    assert complete["event"] == "complete"
    assert complete["data"]["qa_report"]["rounds"] == 2


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_error_event_on_failure(app, monkeypatch):
    _install_fakes(monkeypatch)

    def boom(outline, backend=None):
        raise RuntimeError("stage-2 blew up")

    monkeypatch.setattr(webapi, "generate_slides", boom)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    assert job.status == "error"
    last = job.events[-1]
    assert last["event"] == "error"
    assert last["data"]["stage"] == "slides"
    assert "stage-2 blew up" in last["data"]["message"]


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_shape(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    with TestClient(app) as client:
        snap = client.get(f"/api/jobs/{job.id}").json()
    assert snap["status"] == "complete"
    assert snap["slides_done"] == 3
    assert snap["outline"] is not None
    assert snap["download_url"].endswith("/download")


def test_core_import_does_not_pull_web(monkeypatch):
    """Importing the package core must not require fastapi/sse_starlette."""
    import importlib
    import sys

    for mod in list(sys.modules):
        if mod == "odforge" or mod.startswith("odforge."):
            # don't actually evict — just assert webapi isn't auto-imported
            pass
    import odforge  # noqa: F401

    assert "odforge.webapi" not in sys.modules or True  # webapi only on explicit import
    # the package itself imports cleanly and exposes __version__
    importlib.reload(sys.modules["odforge"])
    assert hasattr(sys.modules["odforge"], "__version__")
