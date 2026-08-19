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
import base64
import json
import time
from pathlib import Path

import pymupdf as fitz
import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")
from fastapi.testclient import TestClient  # noqa: E402

from odforge import webapi  # noqa: E402
from odforge.critic import Finding, QAReport  # noqa: E402
from odforge.ir import Outline, PageRole, Presentation, Slide  # noqa: E402
from odforge.llm import DroppedContent  # noqa: E402
from odforge.preview import PreviewUnavailable  # noqa: E402

# A 1x1 transparent PNG — enough bytes for FileResponse to serve.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000154a24f3b0000000049454e44ae426082"
)

# Roles that need no special slide fields (avoids Slide cross-field validators).
_SAFE_ROLES = ["title", "title-content", "section", "agenda", "closing"]


def _pdf_data_url(text: str = "Finding: method A improves learning outcomes.") -> str:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return "data:application/pdf;base64," + base64.b64encode(data).decode()


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

    def fake_outline(prompt, backend=None, pages=None, language="zh-TW"):
        return _outline(n)

    def fake_slides(outline, backend=None, dropped=None):
        if record_slides is not None:
            record_slides.append(outline)
        return _slides_for(outline)

    def fake_render(ir, out_path, **kwargs):
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
        return qa_report or QAReport(rounds=1, findings_by_round=[[]], verdict="pass")

    def fake_validate(path, **kwargs):
        from odforge.validate import ValidationReport

        return ValidationReport(
            ok=True, gates={"structure": (True, "ok"), "xml": (True, "ok")}
        )

    monkeypatch.setattr(webapi, "generate_outline", fake_outline)
    monkeypatch.setattr(webapi, "generate_slides", fake_slides)
    monkeypatch.setattr(webapi, "render", fake_render)
    monkeypatch.setattr(webapi, "render_pages", fake_render_pages)
    monkeypatch.setattr(webapi, "run_qa_loop", fake_qa)
    monkeypatch.setattr(webapi, "validate_odf", fake_validate)


@pytest.fixture
def app(tmp_path):
    return webapi.create_app(jobs_dir=tmp_path / "jobs")


def _stream_events(resp, expected):
    """Read frames off a live SSE response, stopping after ``expected`` events.

    The ``/events`` stream terminates at ``complete`` (or ``error``): that
    terminal event closes it — see webapi.py's ``event_stream`` and
    ``test_sse_stream_terminates_at_complete``. This helper does not depend on
    reaching EOF, though — it knows how many events the job logged and stops after
    collecting ``expected`` of them, closing the connection on ``with`` exit.
    """
    events = []
    cur = {}
    for raw in resp.iter_lines():
        line = raw.rstrip("\r")
        if line == "" or line.startswith(":"):  # event boundary / comment
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            cur["event"] = value
        elif field == "data":
            # Our emitter writes exactly one single-line data field per event,
            # always after the event line — so a data line *completes* an event.
            # Finalise here and stop at ``expected`` on the data line itself,
            # WITHOUT waiting for the trailing blank line — so a mid-stream read
            # never blocks on a frame boundary that has not been emitted yet.
            cur["data"] = value
            events.append(cur)
            cur = {}
            if len(events) >= expected:
                break
    return events


# ---------------------------------------------------------------------------
# POST /api/discovery/questions
# ---------------------------------------------------------------------------


def test_discovery_questions_returns_adaptive_plan_without_image_bytes(
    app, monkeypatch
):
    calls = []

    def fake_discovery(prompt, backend=None, *, context=""):
        calls.append((prompt, backend, context))
        return webapi.DiscoveryPlan.model_validate(
            {
                "summary": "向老師報告畢業專題進度。",
                "known_context": ["受眾是老師"],
                "questions": [
                    {
                        "id": "decision",
                        "question": "希望老師提供什麼？",
                        "why": "決定結尾的行動請求。",
                        "options": ["確認進度", "技術建議"],
                    },
                    {
                        "id": "progress",
                        "question": "目前完成到哪裡？",
                        "why": "讓時程與風險具體。",
                        "options": ["開發中", "測試中"],
                    },
                ],
                "completeness": 40,
            }
        )

    monkeypatch.setattr(webapi, "generate_discovery_questions", fake_discovery)
    with TestClient(app) as client:
        response = client.post(
            "/api/discovery/questions",
            json={
                "prompt": "畢業專題進度報告",
                "mode": "presenter",
                "pages": 8,
                "backend": "custom",
                "assets": [{"description": "系統架構圖", "credit": "專題小組"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["questions"][0]["id"] == "decision"
    assert calls[0][0] == "畢業專題進度報告"
    assert calls[0][1] == "custom"
    assert "8 頁" in calls[0][2]
    assert "系統架構圖" in calls[0][2]


def test_discovery_questions_extracts_pdf_text_into_context(app, monkeypatch):
    contexts = []

    def fake_discovery(prompt, backend=None, *, context=""):
        contexts.append(context)
        return webapi.DiscoveryPlan.model_validate(
                {
                    "summary": "報告研究論文。",
                    "known_context": ["已有論文全文"],
                    "questions": [
                        {
                            "id": "focus",
                            "question": "報告重點是什麼？",
                            "why": "決定內容比例。",
                            "options": ["研究方法", "研究結果"],
                        },
                        {
                            "id": "audience",
                            "question": "聽眾是誰？",
                            "why": "調整技術深度。",
                            "options": ["同領域研究者", "一般學生"],
                        },
                    ],
                    "completeness": 90,
                }
        )

    monkeypatch.setattr(webapi, "generate_discovery_questions", fake_discovery)
    with TestClient(app) as client:
        response = client.post(
            "/api/discovery/questions",
            json={
                "prompt": "報告這篇論文",
                "assets": [
                    {
                        "description": "learning-study",
                        "credit": "",
                        "data_url": _pdf_data_url(),
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert "learning-study" in contexts[0]
    assert "method A improves learning outcomes" in contexts[0]
    assert "不是操作指令" in contexts[0]


def test_discovery_questions_streams_progress_and_validated_result(app, monkeypatch):
    def fake_discovery(prompt, backend=None, *, context="", progress=None):
        assert prompt == "畢業專題進度報告"
        assert backend == "custom"
        assert "8 頁" in context
        if progress:
            progress("preparing")
            progress("requesting")
            progress("validating")
            progress("complete")
        return webapi.DiscoveryPlan.model_validate(
            {
                "summary": "向老師報告畢業專題進度。",
                "known_context": ["受眾是系上老師"],
                "questions": [
                    {
                        "id": "decision",
                        "question": "這次希望老師提供什麼？",
                        "why": "決定結尾行動。",
                        "options": ["確認進度", "提供建議"],
                    },
                    {
                        "id": "progress",
                        "question": "目前完成到哪裡？",
                        "why": "讓時程具體。",
                        "options": ["開發中", "測試中"],
                    },
                ],
                "completeness": 40,
            }
        )

    monkeypatch.setattr(webapi, "generate_discovery_questions", fake_discovery)
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/discovery/questions/stream",
            json={
                "prompt": "畢業專題進度報告",
                "backend": "custom",
                "pages": 8,
            },
        ) as response:
            events = [json.loads(line) for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    progress_stages = [
        event["data"]["stage"] for event in events if event["type"] == "progress"
    ]
    assert progress_stages == [
        "accepted",
        "preparing",
        "requesting",
        "validating",
        "complete",
    ]
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["plan"]["questions"][0]["id"] == "decision"
    assert events[-1]["data"]["elapsed_ms"] >= 0


def test_discovery_questions_reports_model_failure_as_502(app, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(webapi, "generate_discovery_questions", fail)
    with TestClient(app) as client:
        response = client.post(
            "/api/discovery/questions", json={"prompt": "畢業專題"}
        )

    assert response.status_code == 502
    assert "需求訪談生成失敗" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": " "},
        {"prompt": "x", "pages": 31},
        {"prompt": "x", "backend": "unknown"},
        {"prompt": "x", "doc_type": "odt"},
    ],
)
def test_discovery_questions_rejects_invalid_inputs(app, payload):
    with TestClient(app) as client:
        response = client.post("/api/discovery/questions", json=payload)
    assert response.status_code == 422


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


def test_generate_accepts_image_assets_without_exposing_local_paths(app, monkeypatch):
    _install_fakes(monkeypatch)
    data_url = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    with TestClient(app) as client:
        response = client.post(
            "/api/generate",
            json={
                "prompt": "使用現場照片",
                "assets": [
                    {
                        "description": "候診區改善後",
                        "credit": "院方提供",
                        "data_url": data_url,
                    }
                ],
            },
        )
        assert response.status_code == 200
        job = app.state.jobs[response.json()["job_id"]]
        for _ in range(200):
            if job.status in ("complete", "error"):
                break
            time.sleep(0.01)

    assert job.status == "complete"
    assert list(job.assets) == ["asset-01"]
    assert job.assets["asset-01"].is_file()
    assert job.outline is not None
    assert job.outline.media_assets[0].description == "候診區改善後"
    assert job.outline.media_assets[0].credit == "院方提供"
    assert "asset://" not in str(job.assets["asset-01"])


def test_generate_uses_pdf_text_as_model_context_not_render_asset(app, monkeypatch):
    prompts = []
    _install_fakes(monkeypatch)

    def fake_outline(prompt, backend=None, pages=None, language="zh-TW"):
        prompts.append(prompt)
        return _outline().model_copy(update={"source_prompt": prompt})

    monkeypatch.setattr(webapi, "generate_outline", fake_outline)
    with TestClient(app) as client:
        response = client.post(
            "/api/generate",
            json={
                "prompt": "報告這篇論文",
                "assets": [
                    {
                        "description": "learning-study",
                        "credit": "Research Lab",
                        "data_url": _pdf_data_url(),
                    }
                ],
            },
        )
        assert response.status_code == 200
        job = app.state.jobs[response.json()["job_id"]]
        for _ in range(200):
            if job.status in ("complete", "error"):
                break
            time.sleep(0.01)

    assert job.status == "complete"
    assert job.assets == {}
    assert job.asset_refs == []
    assert len(job.reference_documents) == 1
    assert "method A improves learning outcomes" in prompts[0]
    assert "Research Lab" in prompts[0]
    assert job.outline is not None
    assert "method A improves learning outcomes" in job.outline.source_prompt


def test_generate_rejects_corrupt_image_asset(app):
    with TestClient(app) as client:
        response = client.post(
            "/api/generate",
            json={
                "prompt": "x",
                "assets": [
                    {
                        "description": "壞圖",
                        "credit": "",
                        "data_url": "data:image/png;base64,bm90LWEtcG5n",
                    }
                ],
            },
        )
    assert response.status_code == 422
    assert "invalid image asset" in response.json()["detail"]


def test_generate_rejects_pdf_without_extractable_text(app):
    document = fitz.open()
    document.new_page()
    data_url = "data:application/pdf;base64," + base64.b64encode(
        document.tobytes()
    ).decode()
    document.close()

    with TestClient(app) as client:
        response = client.post(
            "/api/generate",
            json={
                "prompt": "報告這份掃描文件",
                "assets": [
                    {
                        "description": "scan",
                        "credit": "",
                        "data_url": data_url,
                    }
                ],
            },
        )

    assert response.status_code == 422
    assert "OCR" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "   "},
        {"prompt": "x" * 8001},
        {"prompt": "x", "mode": "unknown"},
        {"prompt": "x", "theme": "unknown"},
        {"prompt": "x", "backend": "unknown"},
        {"prompt": "x", "pages": 3.5},
    ],
)
def test_generate_rejects_invalid_or_unbounded_inputs(app, payload):
    with TestClient(app) as client:
        assert client.post("/api/generate", json=payload).status_code == 422


def test_generate_limits_concurrent_jobs(app, monkeypatch):
    monkeypatch.setattr(webapi, "_MAX_ACTIVE_JOBS", 1)
    webapi.create_job(app, prompt="already running")

    with TestClient(app) as client:
        response = client.post("/api/generate", json={"prompt": "another"})

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# SSE event stream — order and shapes
# ---------------------------------------------------------------------------


def test_event_sequence_direct(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    names = [e["event"] for e in job.events]
    # Each page emits slide_done then its F4 alias unit_done (same data).
    assert names == [
        "outline",
        "slide_done", "unit_done",
        "slide_done", "unit_done",
        "slide_done", "unit_done",
        "gate_result",  # zip
        "gate_result",  # xml
        "gate_result",  # libreoffice
        "preview_ready",
        "preview_ready",
        "preview_ready",
        "gate_result",  # design (skipped — qa off)
        "complete",
    ]
    # payload shapes
    outline_ev = job.events[0]["data"]
    assert "pages" in outline_ev and outline_ev["mode"] == "presenter"
    sd = job.events[1]["data"]
    assert sd["n"] == 1 and sd["slide"]["title"] == "頁 1"
    # gate_result shapes (deterministic zip/xml pass, libreoffice pass)
    gates = [e for e in job.events if e["event"] == "gate_result"]
    assert [g["data"]["gate"] for g in gates] == ["zip", "xml", "libreoffice", "design"]
    assert gates[0]["data"] == {"gate": "zip", "status": "pass"}
    assert gates[1]["data"] == {"gate": "xml", "status": "pass"}
    assert gates[2]["data"] == {"gate": "libreoffice", "status": "pass"}
    assert gates[3]["data"] == {
        "gate": "design",
        "status": "skipped",
        "note": "這次生成關閉了設計品質檢查。",
    }
    pr = next(e for e in job.events if e["event"] == "preview_ready")["data"]
    assert pr["n"] == 1 and pr["url"].endswith("/preview/1.png")
    done = job.events[-1]["data"]
    assert done["download_url"].endswith("/download")


def test_sse_replay_over_http(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))  # complete before subscribing
    expected = len(job.events)

    with TestClient(app) as client:
        with client.stream("GET", f"/api/jobs/{job.id}/events") as resp:
            assert resp.status_code == 200
            events = _stream_events(resp, expected)
    names = [e["event"] for e in events]
    assert names == [
        "outline",
        "slide_done", "unit_done",
        "slide_done", "unit_done",
        "gate_result",  # zip
        "gate_result",  # xml
        "gate_result",  # libreoffice
        "preview_ready",
        "preview_ready",
        "gate_result",  # design skipped
        "complete",
    ]
    # data is valid JSON on every frame
    for e in events:
        json.loads(e["data"])


def test_sse_stream_terminates_at_complete(app, monkeypatch):
    """The stream ends at ``complete`` — no infinite hang for a finished job."""
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    with TestClient(app) as client:
        with client.stream("GET", f"/api/jobs/{job.id}/events") as resp:
            # read to EOF — a terminating stream closes, so this cannot hang
            body = "".join(resp.iter_text())
    assert body.count("event: complete") == 1
    assert body.rstrip().endswith("}")  # last frame is the complete event's data


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
    # PreviewUnavailable → libreoffice gate reports skipped (not fail)
    gates = [e["data"] for e in job.events if e["event"] == "gate_result"]
    assert {"gate": "libreoffice", "status": "skipped"} in gates
    design = next(g for g in gates if g["gate"] == "design")
    assert design["status"] == "skipped"


# ---------------------------------------------------------------------------
# Interactive outline gate
# ---------------------------------------------------------------------------


def test_interactive_gate_direct(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x", interactive=True)

    async def scenario():
        task = asyncio.create_task(webapi.run_job(job))
        # Advance until the runner parks at the approval gate. `sleep(0)` alone
        # is not enough any more: session persistence runs on a worker thread
        # (asyncio.to_thread), and a zero-delay yield never gives that thread a
        # chance to finish. A real — if tiny — sleep does.
        for _ in range(2000):
            if job.status == "awaiting_approval":
                break
            await asyncio.sleep(0.001)
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


def test_outline_edit_emits_outline_event_for_resume(app, monkeypatch):
    """An outline *edit* must append a second `outline` event to the log.

    Otherwise a `?job=` resume replays the original outline and, after `complete`,
    resurrects removed pages as phantom done thumbnails (the bug this guards).
    """
    _install_fakes(monkeypatch, n=3)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "x", "interactive": True})
        jid = r.json()["job_id"]

        for _ in range(300):
            snap = client.get(f"/api/jobs/{jid}").json()
            if snap["status"] == "awaiting_approval":
                break
            time.sleep(0.01)
        assert snap["status"] == "awaiting_approval"

        edited = {
            "design": None,
            "mode": "detailed",
            "pages": [{"role": "title", "title": "改過的標題", "gist": "新重點"}],
        }
        r2 = client.post(
            f"/api/jobs/{jid}/outline", json={"action": "edit", "outline": edited}
        )
        assert r2.status_code == 200

        for _ in range(300):
            snap = client.get(f"/api/jobs/{jid}").json()
            if snap["status"] == "complete":
                break
            time.sleep(0.01)
        assert snap["status"] == "complete"

    job = app.state.jobs[jid]
    outline_events = [e for e in job.events if e["event"] == "outline"]
    # initial outline (stage 1) + the edited outline emitted by the endpoint.
    assert len(outline_events) == 2
    assert outline_events[1]["data"]["mode"] == "detailed"
    assert len(outline_events[1]["data"]["pages"]) == 1
    assert outline_events[1]["data"]["pages"][0]["title"] == "改過的標題"


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


def test_download_before_ready_is_409_with_a_reason(app, monkeypatch):
    """工作存在但成品還不存在 → 409 + 人話,不是空泛的 404。

    404 的意思是「沒有這個東西」;這裡東西在,只是還不能交付。兩者對前端是
    不同的處置(重試 vs 放棄),對使用者是不同的訊息。
    """
    _install_fakes(monkeypatch)
    job = webapi.create_job(app, prompt="x")  # never rendered
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 409
        assert "還沒開始生成" in r.json()["detail"]


# ---------------------------------------------------------------------------
# P1-03 (API half) — an unknown request field is a 422, never a silent drop.
# ---------------------------------------------------------------------------


def test_unknown_generate_field_is_rejected(app, monkeypatch):
    """``doc_typ``/``qa_mode`` 之類的拼錯欄位曾被靜默丟掉,使用者拿到的是預設行為。"""
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "x", "doc_typ": "ods"})
        assert r.status_code == 422
        assert "doc_typ" in json.dumps(r.json(), ensure_ascii=False)


def test_pages_out_of_range_is_rejected(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        for bad in (2, 31, 0, -1):
            r = client.post("/api/generate", json={"prompt": "x", "pages": bad})
            assert r.status_code == 422, bad
        # 上下界本身合法
        assert client.post(
            "/api/generate", json={"prompt": "x", "pages": 3}
        ).status_code == 200


def test_api_page_ceiling_matches_the_outline_schema():
    """前後端與 Outline schema 共用同一個上限常數,不會各自漂移。"""
    from odforge.ir import MAX_OUTLINE_PAGES

    field = webapi.GenerateBody.model_fields["pages"]
    ceilings = [m.le for m in field.metadata if getattr(m, "le", None) is not None]
    assert ceilings == [MAX_OUTLINE_PAGES]


# ---------------------------------------------------------------------------
# P1-02 — a download is an artifact *version* that passed the gates, never
# "a file happens to exist at that path".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["generating_slides", "rendering", "validating", "qa", "regenerating"],
)
def test_download_refused_while_work_is_in_flight(app, monkeypatch, status):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    assert job.odp_path.is_file()  # the file is right there…
    job.status = status  # …and it still must not be served
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 409
        assert r.json()["detail"]


def test_download_refused_after_validation_failed(app, monkeypatch):
    """驗證未過 → 檔案在磁碟上,但永遠不得交付。"""
    _install_fakes(monkeypatch, n=2)

    def failing_validate(path, **kwargs):
        from odforge.validate import ValidationReport

        return ValidationReport(
            ok=False, gates={"structure": (True, "ok"), "xml": (False, "壞掉的 XML")}
        )

    monkeypatch.setattr(webapi, "validate_odf", failing_validate)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    assert job.status == "error"
    assert job.odp_path.is_file()
    assert job.validated_version != job.artifact_version
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 409
        assert "失敗" in r.json()["detail"]
    # 快照也不能宣傳一個點下去就 409 的連結。
    with TestClient(app) as client:
        snap = client.get(f"/api/jobs/{job.id}").json()
    assert "download_url" not in snap
    assert snap["downloadable"] is False


def test_download_refused_after_cancel(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    # 已完成的工作是終態,取消端點會 409;直接把狀態設成取消,模擬「生成中被取消
    # 但磁碟上已經有中途檔」——這才是下載端點必須擋住的情形。
    job.status = "cancelled"
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 409
        assert "取消" in r.json()["detail"]


def test_download_serves_the_validated_version(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    assert job.downloadable
    assert job.validated_version == job.artifact_version == 1
    with TestClient(app) as client:
        assert client.get(f"/api/jobs/{job.id}/download").status_code == 200


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

    def recording_slides(outline, backend=None, dropped=None):
        recorded.append(outline)
        return _slides_for(outline)

    monkeypatch.setattr(webapi, "generate_slides", recording_slides)

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

    # the fresh slide + preview URL are DELIVERED in the HTTP response body
    body = r.json()
    assert body["ok"] is True and body["n"] == 2
    assert body["slide"]["title"] == job.ir.slides[1].title
    # 重算圖後 URL 帶版本參數:字串不同,瀏覽器/React 才會重抓(F5 cache-bust)。
    assert body["preview_url"].endswith("/preview/2.png?v=1")


def test_unit_done_mirrors_slide_done(app, monkeypatch):
    """Every ``slide_done`` is followed by a ``unit_done`` alias with identical data."""
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    slide_dones = [e for e in job.events if e["event"] == "slide_done"]
    unit_dones = [e for e in job.events if e["event"] == "unit_done"]
    assert len(slide_dones) == len(unit_dones) == 3
    # same payloads, and each unit_done immediately follows its slide_done
    assert [e["data"] for e in slide_dones] == [e["data"] for e in unit_dones]
    idx = [i for i, e in enumerate(job.events) if e["event"] == "slide_done"]
    for i in idx:
        assert job.events[i + 1]["event"] == "unit_done"
        assert job.events[i + 1]["data"] == job.events[i]["data"]


def test_regenerate_via_units_alias(app, monkeypatch):
    """``/units/{n}/regenerate`` is an alias of ``/slides/{n}/regenerate`` (same handler)."""
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    with TestClient(app) as client:
        r = client.post(
            f"/api/jobs/{job.id}/units/2/regenerate", json={"instruction": "更大膽"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["n"] == 2
        # 重算圖 → 版本遞增的 cache-bust URL(與 slides 路徑同一 handler)。
        assert body["preview_url"].endswith("/preview/2.png?v=1")
        # out-of-range still 404 on the alias path
        assert (
            client.post(f"/api/jobs/{job.id}/units/99/regenerate", json={}).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# P1-01 — regeneration is transactional: it publishes everything or nothing.
# ---------------------------------------------------------------------------


def _regen_fixture(app, monkeypatch, n=3):
    _install_fakes(monkeypatch, n=n)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    return job


def _snapshot(job):
    """Everything a rollback has to leave byte-identical."""
    return {
        "titles": [s.title for s in job.ir.slides],
        "layouts": [s.layout for s in job.ir.slides],
        "deck": job.odp_path.read_bytes(),
        "artifact_version": job.artifact_version,
        "validated_version": job.validated_version,
        "preview_version": job.preview_version,
        "gates": dict(job.gates),
        "previews": sorted(p.name for p in job.preview_dir.glob("*.png")),
    }


def test_regenerate_rolls_back_when_the_model_fails(app, monkeypatch):
    job = _regen_fixture(app, monkeypatch)
    before = _snapshot(job)

    def boom(outline, backend=None, dropped=None):
        raise RuntimeError("provider 500")

    monkeypatch.setattr(webapi, "generate_slides", boom)
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 500
    assert _snapshot(job) == before
    assert job.status == "complete" and job.downloadable


def test_regenerate_rolls_back_when_render_fails(app, monkeypatch):
    """render 在 IR 換頁之後炸掉 → 舊行為留下 IR/成品/預覽三者互相矛盾的工作。"""
    job = _regen_fixture(app, monkeypatch)
    before = _snapshot(job)
    monkeypatch.setattr(
        webapi,
        "render",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("renderer exploded")),
    )
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 500
    assert _snapshot(job) == before
    assert not (job.dir / "deck.candidate.odp").exists()


def test_regenerate_rolls_back_when_validation_fails(app, monkeypatch):
    """算得出來不代表合法:重生後的檔案沒過 zip/xml 就不准取代原檔。"""
    job = _regen_fixture(app, monkeypatch)
    before = _snapshot(job)

    def failing_validate(path, **kwargs):
        from odforge.validate import ValidationReport

        return ValidationReport(
            ok=False, gates={"structure": (True, "ok"), "xml": (False, "not well-formed")}
        )

    monkeypatch.setattr(webapi, "validate_odf", failing_validate)
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 500
        assert "驗證未通過" in r.json()["detail"]["message"]
    assert _snapshot(job) == before


def test_regenerate_rolls_back_when_preview_blows_up(app, monkeypatch):
    job = _regen_fixture(app, monkeypatch)
    before = _snapshot(job)
    monkeypatch.setattr(
        webapi,
        "render_pages",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("soffice crashed")),
    )
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).status_code
            == 500
        )
    assert _snapshot(job) == before


def test_failed_regeneration_then_a_successful_one_ships_only_the_good_page(
    app, monkeypatch
):
    """驗收條件:第一頁重生失敗、第二頁重生成功 → 下載檔不得含第一次失敗的內容。"""
    job = _regen_fixture(app, monkeypatch)
    original = [s.title for s in job.ir.slides]

    def poisoned(outline, backend=None, dropped=None):
        deck = _slides_for(outline)
        deck.slides[0].title = "毒藥"
        return deck

    monkeypatch.setattr(webapi, "generate_slides", poisoned)
    monkeypatch.setattr(
        webapi,
        "render",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("render died")),
    )
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/1/regenerate", json={}).status_code
            == 500
        )
    assert job.ir.slides[0].title == original[0]  # 毒藥沒有進到 IR

    # 現在讓 render 恢復正常,重生第 2 頁。
    _install_fakes(monkeypatch, n=3)

    def second(outline, backend=None, dropped=None):
        deck = _slides_for(outline)
        deck.slides[0].title = "第二頁新內容"
        return deck

    monkeypatch.setattr(webapi, "generate_slides", second)
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).status_code
            == 200
        )
        download = client.get(f"/api/jobs/{job.id}/download")
        assert download.status_code == 200

    assert job.ir.slides[0].title == original[0]
    assert job.ir.slides[1].title == "第二頁新內容"
    assert job.ir.slides[2].title == original[2]


def test_regeneration_recomputes_gates_and_clears_the_old_design_verdict(
    app, monkeypatch
):
    """重生後不得沿用上一版的綠燈,設計閘必須退回「無法判定」。"""
    report = QAReport(rounds=1, findings_by_round=[[]], verdict="pass")
    _install_fakes(monkeypatch, n=3, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))
    assert job.gates["design"] == "pass"

    with TestClient(app) as client:
        body = client.post(
            f"/api/jobs/{job.id}/slides/2/regenerate", json={}
        ).json()

    gates = {g["gate"]: g["status"] for g in body["gates"]}
    assert gates == {
        "zip": "pass", "xml": "pass", "libreoffice": "pass", "design": "unknown"
    }
    assert job.gates["design"] == "unknown"
    assert job.qa_report is None
    assert body["version"] == job.artifact_version == 2


def test_consecutive_regenerations_each_advance_the_version(app, monkeypatch):
    job = _regen_fixture(app, monkeypatch)
    with TestClient(app) as client:
        first = client.post(f"/api/jobs/{job.id}/slides/1/regenerate", json={}).json()
        second = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).json()
    assert first["version"] == 2 and second["version"] == 3
    assert first["preview_url"].endswith("?v=1")
    assert second["preview_url"].endswith("?v=2")
    assert job.validated_version == job.artifact_version == 3


def test_regeneration_updates_the_page_contract_so_a_qa_fix_survives(
    app, monkeypatch
):
    """QA 把第 2 頁改成 process 後再重生,不得被過期的 outline 打回 title-content。"""
    job = _regen_fixture(app, monkeypatch)
    assert job.outline.pages[1].role == "title-content"

    # 模擬 QA 修復:把第 2 頁換成另一種版型(引擎端已改,大綱還沒跟上)。
    def upgraded(outline, backend=None, dropped=None):
        deck = _slides_for(outline)
        deck.slides[0] = Slide(
            layout="quote", title="升級後", quote="一句引言", attribution="某人"
        )
        return deck

    monkeypatch.setattr(webapi, "generate_slides", upgraded)
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).status_code
            == 200
        )

    # 契約跟著成品走:下一次重生會拿到 quote,而不是原本的 title-content。
    assert job.ir.slides[1].layout == "quote"
    assert job.outline.pages[1].role == "quote"
    assert job.outline.pages[1].title == "升級後"


def test_regeneration_of_a_shorter_deck_leaves_no_stale_preview_pages(
    app, monkeypatch
):
    """P2-07:5 頁縮成 3 頁後,page-04/05.png 不得殘留在服務中的預覽版本。"""
    _install_fakes(monkeypatch, n=5)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    assert sorted(p.name for p in job.preview_dir.glob("*.png")) == [
        f"page-0{i}.png" for i in range(1, 6)
    ]

    # 下一輪只算得出 3 頁(等同重生後頁數變少)。
    _install_fakes(monkeypatch, n=3)
    job.ir.slides = job.ir.slides[:3]
    job.outline.pages = job.outline.pages[:3]
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/1/regenerate", json={}).status_code
            == 200
        )
        assert client.get(f"/api/jobs/{job.id}/preview/4.png").status_code == 404

    assert sorted(p.name for p in job.preview_dir.glob("*.png")) == [
        "page-01.png", "page-02.png", "page-03.png"
    ]


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
        verdict="pass",
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

    def boom(outline, backend=None, dropped=None):
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


def test_core_import_does_not_pull_web():
    """Importing the package core (and even the CLI) must not import webapi or
    require fastapi/sse_starlette.

    Asserted in a *fresh* interpreter — this test module itself imports webapi,
    so checking this process's ``sys.modules`` would be meaningless.
    """
    import subprocess
    import sys

    code = (
        "import sys, odforge\n"
        "assert 'odforge.webapi' not in sys.modules, 'core import pulled webapi'\n"
        "assert 'fastapi' not in sys.modules, 'core import pulled fastapi'\n"
        "assert 'sse_starlette' not in sys.modules, 'core import pulled sse_starlette'\n"
        "import odforge.cli\n"  # the CLI must stay importable without the web extra
        "assert 'fastapi' not in sys.modules, 'cli import pulled fastapi'\n"
        "assert hasattr(odforge, '__version__')\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------------------
# CORS — explicit localhost allowlist, never wildcard-with-credentials
# ---------------------------------------------------------------------------


def test_cors_allows_only_allowlisted_origins(app):
    with TestClient(app) as client:
        allowed = client.get(
            "/api/jobs/deadbeef", headers={"Origin": "http://localhost:5173"}
        )
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

        evil = client.get(
            "/api/jobs/deadbeef", headers={"Origin": "https://evil.example"}
        )
        # A disallowed origin gets NO ACAO header echoing it.
        assert "access-control-allow-origin" not in evil.headers

        # Credentials must not be allowed (wildcard-with-credentials is the vuln).
        assert allowed.headers.get("access-control-allow-credentials") != "true"


# ---------------------------------------------------------------------------
# Frontend hosting — `odforge serve` serves the built cockpit from one process
# ---------------------------------------------------------------------------


def test_serve_mounts_built_frontend(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><title>文鍛 ODForge</title>", encoding="utf-8"
    )
    monkeypatch.setattr(webapi, "frontend_dist", lambda: dist)
    with TestClient(webapi.create_app(jobs_dir=tmp_path / "jobs")) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "文鍛" in resp.text
        # /api routes still win over the "/" static mount.
        assert client.get("/api/jobs/nope").status_code == 404


def test_api_only_when_frontend_not_built(tmp_path, monkeypatch):
    monkeypatch.setattr(webapi, "frontend_dist", lambda: None)
    with TestClient(webapi.create_app(jobs_dir=tmp_path / "jobs")) as client:
        # No static mount at "/", so the root path is unmatched → 404.
        assert client.get("/").status_code == 404


# ---------------------------------------------------------------------------
# Persistent session history
# ---------------------------------------------------------------------------


def test_sessions_survive_app_restart_with_preview_and_download(tmp_path):
    sessions_dir = tmp_path / "sessions"
    first_app = webapi.create_app(jobs_dir=sessions_dir)
    job = webapi.create_job(first_app, prompt="給大一新生的資料結構簡報")
    job.outline = _outline()
    job.ir = _slides_for(job.outline)
    job.slides_done = len(job.ir.slides)
    job.status = "complete"
    job.finished_at = job.created_at + 10
    job.odp_path.write_bytes(b"PK\x03\x04 saved-deck")
    job.preview_dir.mkdir(parents=True)
    (job.preview_dir / "page-01.png").write_bytes(_PNG)
    job.events = [
        {"event": "outline", "data": job.outline.model_dump(mode="json")},
        {
            "event": "complete",
            "data": {"download_url": f"/api/jobs/{job.id}/download"},
        },
    ]
    webapi._persist_job(job)

    restored_app = webapi.create_app(jobs_dir=sessions_dir)
    restored = restored_app.state.jobs[job.id]
    assert restored.status == "complete"
    assert restored.ir is not None
    assert restored.events[-1]["event"] == "complete"

    with TestClient(restored_app) as client:
        response = client.get("/api/sessions")
        download = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    session = response.json()["sessions"][0]
    assert session["id"] == job.id
    assert session["title"] == "測試簡報"
    assert session["page_count"] == 3
    assert session["preview_url"].endswith("/preview/1.png")
    assert session["download_url"].endswith("/download")
    assert download.status_code == 200


def test_incomplete_session_is_marked_interrupted_after_restart(tmp_path):
    sessions_dir = tmp_path / "sessions"
    first_app = webapi.create_app(jobs_dir=sessions_dir)
    job = webapi.create_job(first_app, prompt="未完成簡報")
    job.status = "generating_slides"
    webapi._persist_job(job)

    restored_app = webapi.create_app(jobs_dir=sessions_dir)
    restored = restored_app.state.jobs[job.id]

    assert restored.status == "error"
    assert restored.error is not None
    assert restored.error["stage"] == "restore"
    assert restored.events[-1]["event"] == "error"


def test_delete_session_removes_the_record_and_its_artifacts(app):
    job = webapi.create_job(app, prompt="要刪掉的簡報")
    job.status = "complete"
    job.finished_at = time.time()
    job.odp_path.write_bytes(b"PK\x03\x04 deck")
    webapi._persist_job(job)

    with TestClient(app) as client:
        response = client.delete(f"/api/sessions/{job.id}")
        listed = client.get("/api/sessions").json()["sessions"]

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert job.id not in app.state.jobs
    assert listed == []
    # 磁碟也要乾淨:留著 deck.odp 的話,使用者刪掉的東西其實還在伺服器上。
    assert not job.dir.exists()


def test_delete_session_404_for_unknown_id(app):
    with TestClient(app) as client:
        assert client.delete("/api/sessions/does-not-exist").status_code == 404


def test_delete_session_refuses_while_the_job_is_running(app):
    """進行中的工作不能刪:目錄被抽掉,還在跑的 worker 會寫進不存在的路徑。"""
    job = webapi.create_job(app, prompt="還在跑的簡報")
    job.status = "generating_slides"

    with TestClient(app) as client:
        response = client.delete(f"/api/sessions/{job.id}")

    assert response.status_code == 409
    assert "還在進行" in response.json()["detail"]
    assert job.id in app.state.jobs
    assert job.dir.exists()


def test_delete_session_refuses_while_a_cancelled_job_still_has_workers(app):
    """狀態已是 cancelled,但供應商呼叫還在執行緒裡跑——那一樣不能刪。"""
    job = webapi.create_job(app, prompt="已取消但還沒停下來")
    job.status = "cancelled"
    job.finished_at = time.time()
    with job.worker_lock:
        job.active_workers = 1

    with TestClient(app) as client:
        response = client.delete(f"/api/sessions/{job.id}")

    assert response.status_code == 409
    assert job.dir.exists()


def test_delete_session_refuses_during_regeneration(app):
    """重生跑在 HTTP 請求裡而不是 job.task:靠 mutation_lock 才看得出它在忙。"""
    job = webapi.create_job(app, prompt="正在重生的簡報")
    job.status = "complete"
    job.finished_at = time.time()

    async def scenario():
        await job.mutation_lock.acquire()
        try:
            with TestClient(app) as client:
                return client.delete(f"/api/sessions/{job.id}")
        finally:
            job.mutation_lock.release()

    response = asyncio.run(scenario())
    assert response.status_code == 409
    assert job.dir.exists()


# ---------------------------------------------------------------------------
# gate_result — real four-gate signals (zip / xml / libreoffice / design)
# ---------------------------------------------------------------------------


def test_gate_result_design_pass_when_qa_ok(app, monkeypatch):
    report = QAReport(rounds=1, findings_by_round=[[]], verdict="pass")
    _install_fakes(monkeypatch, n=2, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))

    gates = [e["data"] for e in job.events if e["event"] == "gate_result"]
    assert {"gate": "design", "status": "pass"} in gates
    assert not [g for g in gates if g["gate"] == "design" and g["status"] != "pass"]


def test_gate_result_design_skipped_when_no_vision_source(app, monkeypatch):
    """QA on but no vision model → 未啟用 + 原因,絕不塗一個假的綠勾。"""
    report = QAReport(rounds=1, findings_by_round=[[]], verdict="pass")
    _install_fakes(monkeypatch, n=2, qa_report=report)
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "off")
    job = webapi.create_job(app, prompt="x", qa=True)
    asyncio.run(webapi.run_job(job))

    design = next(
        e["data"] for e in job.events
        if e["event"] == "gate_result" and e["data"]["gate"] == "design"
    )
    assert design["status"] == "skipped"
    assert "ODFORGE_VISION_BACKEND=off" in design["note"]


def test_gate_result_design_fail_when_qa_not_ok(app, monkeypatch):
    report = QAReport(
        rounds=2,
        findings_by_round=[
            [Finding(slide_no=1, issue="溢出", severity="error", fix_hint="縮短")],
            [Finding(slide_no=1, issue="仍溢出", severity="error", fix_hint="再縮")],
        ],
        verdict="fail",
    )
    _install_fakes(monkeypatch, n=2, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))

    gates = [e["data"] for e in job.events if e["event"] == "gate_result"]
    assert {"gate": "design", "status": "fail"} in gates


def test_gate_result_design_unknown_when_qa_raises(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)

    def boom_qa(ir, out_path, **kwargs):
        raise RuntimeError("403 free quota has been exhausted")

    monkeypatch.setattr(webapi, "run_qa_loop", boom_qa)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))

    # QA error never fails the run — but the reason travels with the gate, and the
    # status is 無法判定 (unknown), never 未啟用: the user asked for the review and
    # did not get it. 無法檢查不等於通過, and it is not the same as opting out.
    assert job.status == "complete"
    design = next(
        e["data"] for e in job.events
        if e["event"] == "gate_result" and e["data"]["gate"] == "design"
    )
    assert design["status"] == "unknown"
    assert "custom" in design["note"]
    assert "free quota has been exhausted" in design["note"]
    assert job.qa_error is not None


def test_gate_result_design_unknown_when_no_rounds_completed(app, monkeypatch):
    """rounds=0 的 degrade(no soffice / 視覺來源第一輪失敗)= unknown,不是 pass,
    也不是 skipped。

    舊 ladder 只看 final_ok — rounds=0 的報告 final_ok 空泛地為 True,閘門
    就給了一個「沒人評過」的綠勾(F4)。改成 skipped 只解決了一半:使用者*要求*
    了品檢卻沒跑成,和使用者自己關掉品檢,是兩件不同的事,不能共用一個「未啟用」。
    """
    report = QAReport(
        rounds=0,
        findings_by_round=[],
        verdict="unknown",
        note=(
            "預覽不可用(找不到 LibreOffice/soffice):已略過視覺評審,"
            "僅套用 deterministic 檢查。"
        ),
    )
    _install_fakes(monkeypatch, n=2, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))

    design = next(
        e["data"] for e in job.events
        if e["event"] == "gate_result" and e["data"]["gate"] == "design"
    )
    assert design["status"] == "unknown"
    assert "預覽不可用" in design["note"]


def test_gate_result_design_fail_carries_unverified_repair_reason(app, monkeypatch):
    """部分失敗(修補過、未複驗)→ fail + 原因跟著閘門走,且預覽刷新換版本。

    這是 F3 的 webapi 端:第 2 輪評審失敗時,run_qa_loop 回傳的部分報告帶著
    第 1 輪 findings、failure 原因與 repaired=True — 閘門必須誠實說 fail
    (最後完成的評審仍有 error、修補未複驗),預覽必須重新發佈並 cache-bust。
    """
    failure = "第 2 輪視覺品檢無法執行:視覺模型逾時;已套用第 1 輪修補但未複驗"
    report = QAReport(
        rounds=1,
        findings_by_round=[
            [Finding(slide_no=1, issue="溢出", severity="error", fix_hint="縮短")]
        ],
        verdict="fail",
        note=failure,
        failure=failure,
        repaired=True,
    )
    _install_fakes(monkeypatch, n=2, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))

    design = next(
        e["data"] for e in job.events
        if e["event"] == "gate_result" and e["data"]["gate"] == "design"
    )
    assert design["status"] == "fail"
    assert "未複驗" in design["note"]
    # 已完成輪次的 qa_round 事件保留(舊行為:例外把它們全丟了)。
    qa_events = [e for e in job.events if e["event"] == "qa_round"]
    assert [e["data"]["round"] for e in qa_events] == [1]
    # repaired → 預覽重新發佈:第一批是原始 URL,第二批帶 ?v=1(F5 cache-bust,
    # 同字串 URL 瀏覽器不會重抓)。
    previews = [e["data"]["url"] for e in job.events if e["event"] == "preview_ready"]
    assert previews == [
        f"/api/jobs/{job.id}/preview/1.png",
        f"/api/jobs/{job.id}/preview/2.png",
        f"/api/jobs/{job.id}/preview/1.png?v=1",
        f"/api/jobs/{job.id}/preview/2.png?v=1",
    ]


def test_preview_route_ignores_cache_bust_query(app, monkeypatch):
    # ?v=k 只為了讓 URL 字串不同;伺服器端照樣以固定檔名服務,舊連結不受影響。
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/preview/1.png?v=7")
        assert r.status_code == 200
        assert r.content == _PNG


def test_design_note_when_job_chose_no_vision_model(app, monkeypatch):
    """工作自己選 off(不是環境變數)→ 說明不得叫使用者去改 .env(F6)。"""
    report = QAReport(rounds=1, findings_by_round=[[]], verdict="pass")
    _install_fakes(monkeypatch, n=2, qa_report=report)
    monkeypatch.delenv("ODFORGE_VISION_BACKEND", raising=False)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="off")
    asyncio.run(webapi.run_job(job))

    design = next(
        e["data"] for e in job.events
        if e["event"] == "gate_result" and e["data"]["gate"] == "design"
    )
    assert design["status"] == "skipped"
    assert "ODFORGE_VISION_BACKEND" not in design["note"]
    assert "選擇不使用視覺模型" in design["note"]


def test_design_note_prefers_qa_error_over_env_off_note(app, monkeypatch):
    # 視覺來源 off 但 QA 仍炸了(render 端):真實原因優先於「你沒開」的泛用說明。
    _install_fakes(monkeypatch, n=2)

    def boom_qa(ir, out_path, **kwargs):
        raise RuntimeError("render blew up mid-QA")

    monkeypatch.setattr(webapi, "run_qa_loop", boom_qa)
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "off")
    job = webapi.create_job(app, prompt="x", qa=True)
    asyncio.run(webapi.run_job(job))

    design = next(
        e["data"] for e in job.events
        if e["event"] == "gate_result" and e["data"]["gate"] == "design"
    )
    assert design["status"] == "unknown"
    assert "render blew up mid-QA" in design["note"]
    assert "ODFORGE_VISION_BACKEND=off" not in design["note"]


def test_session_title_prefers_cover_page_over_first_page(app):
    # 與前端 taskName 對齊(F9):封面頁(role=="title")標題優先於第一頁,
    # 兩邊的工作名稱才會一致。ir.title 仍然最優先(此處 ir 為 None)。
    job = webapi.create_job(app, prompt="做一份研究簡報")
    job.outline = Outline(
        design=None,
        mode="presenter",
        pages=[
            PageRole(role="agenda", title="議程", gist="開場"),
            PageRole(role="title", title="量子運算入門", gist="破題"),
        ],
    )
    assert webapi._session_title(job) == "量子運算入門"


def test_gate_result_validate_fail_raises_error_stage_validate(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)

    def bad_validate(path, **kwargs):
        from odforge.validate import ValidationReport

        return ValidationReport(
            ok=False,
            gates={"structure": (False, "not a valid zip"), "xml": (True, "ok")},
        )

    monkeypatch.setattr(webapi, "validate_odf", bad_validate)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    # zip fail emitted, then an error event with stage="validate"
    gates = [e["data"] for e in job.events if e["event"] == "gate_result"]
    assert {"gate": "zip", "status": "fail"} in gates
    assert job.status == "error"
    last = job.events[-1]
    assert last["event"] == "error"
    assert last["data"]["stage"] == "validate"
    # a failing validate gate stops the pipeline before previews
    names = [e["event"] for e in job.events]
    assert "preview_ready" not in names


# ---------------------------------------------------------------------------
# doc_type validation (only odp supported for now → 422 with a human message)
# ---------------------------------------------------------------------------


def test_generate_rejects_non_odp_doc_type(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "x", "doc_type": "ods"})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, str)
        assert "只產生簡報(odp)" in detail
        # 「即將支援」與 gates.md 第五節的「定案」互相矛盾,不能兩種說法並存;
        # 拒絕的同時要指出真正做得到這件事的介面。
        assert "即將支援" not in detail
        assert "CLI" in detail and "forge_" in detail


def test_generate_defaults_doc_type_odp(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "x"})
        assert r.status_code == 200
        r2 = client.post("/api/generate", json={"prompt": "x", "doc_type": "odp"})
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# pages field — forwarded to generate_outline, bounds enforced by pydantic
# ---------------------------------------------------------------------------


def test_pages_forwarded_to_generate_outline(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    seen = {}

    def recording_outline(prompt, backend=None, pages=None, language="zh-TW"):
        seen["pages"] = pages
        return _outline(3)

    monkeypatch.setattr(webapi, "generate_outline", recording_outline)
    with TestClient(app) as client:
        r = client.post("/api/generate", json={"prompt": "x", "pages": 12})
        jid = r.json()["job_id"]
        for _ in range(200):
            snap = client.get(f"/api/jobs/{jid}").json()
            if snap["status"] in ("complete", "error"):
                break
            time.sleep(0.01)
    assert seen["pages"] == 12


def test_pages_out_of_range_422(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        assert client.post("/api/generate", json={"prompt": "x", "pages": 99}).status_code == 422
        assert client.post("/api/generate", json={"prompt": "x", "pages": 2}).status_code == 422
        assert client.post("/api/generate", json={"prompt": "x", "pages": 3}).status_code == 200
        assert client.post("/api/generate", json={"prompt": "x", "pages": 30}).status_code == 200


# ---------------------------------------------------------------------------
# Download filename — topic-based, sanitised, Chinese preserved
# ---------------------------------------------------------------------------


def test_download_filename_uses_deck_title(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    # fake deck title is "測試簡報"
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{job.id}/download")
        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert job.id not in cd  # not the UUID
        assert ".odp" in cd
        # Chinese topic present (RFC 5987 utf-8 percent-encoding or raw)
        from urllib.parse import quote

        assert ("測試簡報" in cd) or (quote("測試簡報") in cd)


def test_download_filename_sanitises_illegal_chars(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt='這是/一個:壞*檔名?"<>|\n主題後段會被截斷')
    # no ir title → falls back to prompt[:20], sanitised
    asyncio.run(webapi.run_job(job))
    fname = webapi._download_filename(job)
    assert fname.endswith(".odp")
    for bad in '\\/:*?"<>|\n\r':
        assert bad not in fname


def test_download_filename_falls_back_to_id_when_empty(app, monkeypatch):
    _install_fakes(monkeypatch, n=2)
    job = webapi.create_job(app, prompt='///:::***')  # all illegal → empty after sanitise
    asyncio.run(webapi.run_job(job))
    # fake deck title "測試簡報" wins, so force ir title empty to hit the fallback
    job.ir = job.ir.model_copy(update={"title": "   "})
    fname = webapi._download_filename(job)
    assert fname == f"{job.id}.odp"


def test_cors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ODFORGE_CORS_ORIGINS", "https://my.app, https://other.app")
    scoped = webapi.create_app(jobs_dir=tmp_path / "jobs")
    with TestClient(scoped) as client:
        ok = client.get("/api/jobs/x", headers={"Origin": "https://my.app"})
        assert ok.headers.get("access-control-allow-origin") == "https://my.app"

        # a default-allowlist origin is NOT allowed once the env override is set
        no = client.get("/api/jobs/x", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in no.headers


@pytest.mark.parametrize(
    "origin",
    ["*", "null", "localhost:5173", "https://example.test/path", "https://example.test?q=1"],
)
def test_cors_env_rejects_unsafe_origins(monkeypatch, tmp_path, origin):
    monkeypatch.setenv("ODFORGE_CORS_ORIGINS", origin)

    with pytest.raises(ValueError, match="ODFORGE_CORS_ORIGINS"):
        webapi.create_app(jobs_dir=tmp_path / "jobs")


def test_completed_job_artifacts_are_pruned_after_retention(app, monkeypatch):
    job = webapi.create_job(app, prompt="old")
    job.status = "complete"
    job.finished_at = job.created_at
    marker = job.dir / "artifact.txt"
    marker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(webapi, "_JOB_RETENTION_SECONDS", 0)

    webapi._prune_jobs(app, now=job.created_at + 1)

    assert job.id not in app.state.jobs
    assert not job.dir.exists()


# ---------------------------------------------------------------------------
# Regenerate error guard — a raise becomes a clean error, not a 500 traceback
# ---------------------------------------------------------------------------


def test_regenerate_error_is_clean(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    def boom(outline, backend=None, dropped=None):
        raise RuntimeError("regen model exploded")

    monkeypatch.setattr(webapi, "generate_slides", boom)

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 500
        # a clean, structured JSON error body — not an HTML/traceback 500
        detail = r.json()["detail"]
        assert detail["stage"] == "regenerate"
        assert "regen model exploded" in detail["message"]

    assert job.status == "complete"


def test_regenerate_requires_completed_job(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    job.status = "rendering"

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})

    assert response.status_code == 409


def test_regenerate_instruction_has_a_size_limit(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))

    with TestClient(app) as client:
        response = client.post(
            f"/api/jobs/{job.id}/slides/2/regenerate",
            json={"instruction": "x" * 2001},
        )

    assert response.status_code == 422


def test_regenerate_respects_global_operation_limit(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    job = webapi.create_job(app, prompt="x")
    asyncio.run(webapi.run_job(job))
    app.state.active_regenerations = webapi._MAX_ACTIVE_REGENERATIONS

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})

    assert response.status_code == 429


def test_cancel_marks_job_terminal_and_releases_interactive_wait(app):
    job = webapi.create_job(app, prompt="x", interactive=True)
    job.status = "awaiting_approval"

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job.id}/cancel")

    assert response.status_code == 200
    assert job.status == "cancelled"
    assert job.finished_at is not None
    assert job.approval.is_set()


def test_cancel_rejects_terminal_job(app):
    job = webapi.create_job(app, prompt="x")
    job.status = "complete"

    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job.id}/cancel")

    assert response.status_code == 409


def test_interactive_job_times_out_instead_of_holding_a_slot_forever(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    monkeypatch.setattr(webapi, "_APPROVAL_TIMEOUT_SECONDS", 0.01)
    job = webapi.create_job(app, prompt="x", interactive=True)

    asyncio.run(webapi.run_job(job))

    assert job.status == "error"
    assert job.finished_at is not None
    assert job.error is not None
    assert job.error["stage"] == "outline_approval"


# ---------------------------------------------------------------------------
# P2-02 — the concurrency limit counts running WORK, not UI status.
# ---------------------------------------------------------------------------


def test_cancelled_jobs_still_hold_a_slot_until_their_worker_finishes(app, monkeypatch):
    """按下取消不會叫回已經送出去的供應商呼叫。

    舊的計數只看 job.status,取消後立刻歸零 —— 於是「開始 → 取消」按五次,就有
    五個同時進行的付費 API 呼叫,而畫面上顯示機器閒著。
    """
    _install_fakes(monkeypatch, n=1)

    class FakeTask:
        """只要 _occupies_a_slot 會問的那一件事:worker 收工了沒。"""

        def __init__(self):
            self.finished = False

        def done(self):
            return self.finished

    tasks = []
    for _ in range(webapi._MAX_ACTIVE_JOBS):
        job = webapi.create_job(app, prompt="x")
        # UI 上已經取消,但 worker 還在跑(供應商呼叫無法中斷)。
        job.status = "cancelled"
        job.finished_at = time.time()
        job.task = FakeTask()
        tasks.append(job.task)

    with TestClient(app) as client:
        refused = client.post("/api/generate", json={"prompt": "x"})
        assert refused.status_code == 429
        assert "尚未結束" in refused.json()["detail"]

        # worker 真的收工之後,名額才釋放。
        for task in tasks:
            task.finished = True
        assert client.post("/api/generate", json={"prompt": "x"}).status_code == 200


def test_cancel_stops_the_pipeline_before_it_claims_completion(app, monkeypatch):
    """取消之後不得再啟動 render/QA,也不得發出 complete。"""
    _install_fakes(monkeypatch, n=2)
    rendered: list = []
    real_render = webapi.render

    def cancelling_slides(outline, backend=None, dropped=None):
        # 模擬「使用者在第二階段進行中按下取消」。
        job.status = "cancelled"
        job.finished_at = time.time()
        return _slides_for(outline)

    monkeypatch.setattr(webapi, "generate_slides", cancelling_slides)
    monkeypatch.setattr(
        webapi, "render", lambda *a, **k: (rendered.append(1), real_render(*a, **k))[1]
    )

    job = webapi.create_job(app, prompt="x", qa=True)
    asyncio.run(webapi.run_job(job))

    assert rendered == []  # 取消後不再算圖
    assert [e["event"] for e in job.events].count("complete") == 0
    assert job.status == "cancelled"


# ---------------------------------------------------------------------------
# R1-01 / R1-02 / R1-07 — the second-round findings.
#
# The first round made regeneration build a candidate before committing. These
# cover what it still got wrong afterwards: a commit that could half-apply, a
# success that lived only in the HTTP response, and a preview pointer left on
# the previous version when rasterisation produced nothing.
# ---------------------------------------------------------------------------


def test_regenerate_rolls_back_when_the_final_swap_fails(app, monkeypatch):
    """os.replace is the point of no return, so its failure must change nothing.

    Everything before it was already covered. This is the step that was not: it
    ran first in a block commented "nothing may fail", with four in-memory
    mutations after it that would have applied to a deck that never moved.
    """
    job = _regen_fixture(app, monkeypatch)
    before = _snapshot(job)

    def unswappable(src, dst):
        raise OSError(32, "file is locked by another process")

    monkeypatch.setattr(webapi.os, "replace", unswappable)
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 500

    assert _snapshot(job) == before
    assert not (job.dir / "deck.candidate.odp").exists()
    assert job.status == "complete" and job.downloadable


def test_regenerate_does_not_serve_the_previous_image_when_no_preview_is_made(
    app, monkeypatch
):
    """No soffice at regeneration time → no thumbnail, not the old thumbnail.

    Leaving the pointer on the previous version answered every request for the
    new page with a picture of the page it replaced: same URL, plausible image,
    silently wrong.
    """
    job = _regen_fixture(app, monkeypatch)
    old_version = job.preview_version
    assert (job.preview_dir / "page-02.png").is_file()

    def no_soffice(*a, **k):
        raise PreviewUnavailable("soffice not installed")

    monkeypatch.setattr(webapi, "render_pages", no_soffice)
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 200
        assert r.json()["preview_url"] is None
        # …and the endpoint agrees: no image, rather than the previous one.
        assert client.get(f"/api/jobs/{job.id}/preview/2.png").status_code == 404

    assert job.preview_version > old_version
    gates = {g["gate"]: g["status"] for g in r.json()["gates"]}
    assert gates["libreoffice"] == "skipped"


def test_regeneration_survives_a_reload(app, monkeypatch):
    """R1-02: the new page must come back after F5, not the one it replaced.

    The cockpit rebuilds itself from the SSE log. A regeneration that only
    answered the HTTP caller left that log describing the previous deck — so a
    refresh restored the old slide, the old gates and the old preview while the
    download served the new bytes.
    """
    job = _regen_fixture(app, monkeypatch)

    def renamed(outline, backend=None, dropped=None):
        deck = _slides_for(outline)
        deck.slides[0].title = "重生後的新標題"
        return deck

    monkeypatch.setattr(webapi, "generate_slides", renamed)
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).status_code
            == 200
        )

    # Replaying the log the way the browser does must end on the new state.
    last_slide = [
        e["data"] for e in job.events
        if e["event"] == "slide_done" and e["data"]["n"] == 2
    ][-1]
    assert last_slide["slide"]["title"] == "重生後的新標題"

    gates = {}
    for event in job.events:
        if event["event"] == "gate_result":
            gates[event["data"]["gate"]] = event["data"]["status"]
    assert gates["design"] == "unknown"

    # The log must also END terminal, or a restored cockpit hangs mid-generation.
    assert job.events[-1]["event"] == "complete"


def test_events_replay_does_not_stop_at_a_superseded_complete(app, monkeypatch):
    """The stream closed on the FIRST terminal event, hiding everything after it."""
    job = _regen_fixture(app, monkeypatch)
    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).status_code
            == 200
        )
        body = client.get(f"/api/jobs/{job.id}/events").text

    # Two completes in the log; a reconnecting client must receive both — i.e.
    # everything appended by the regeneration, which sits between them.
    assert body.count("event: complete") == 2
    assert "qa_invalidated" in body


def test_regeneration_discards_the_previous_rounds_qa(app, monkeypatch):
    """R1-07: findings describe a deck that no longer exists."""
    findings = [Finding(slide_no=2, issue="文字溢出", severity="error", fix_hint="縮短")]
    report = QAReport(rounds=1, findings_by_round=[findings], verdict="fail")
    _install_fakes(monkeypatch, n=3, qa_report=report)
    job = webapi.create_job(app, prompt="x", qa=True, vision_backend="custom")
    asyncio.run(webapi.run_job(job))
    assert job.qa_report is not None

    with TestClient(app) as client:
        assert (
            client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={}).status_code
            == 200
        )
        snap = client.get(f"/api/jobs/{job.id}").json()

    assert job.qa_report is None
    # The snapshot must not still be handing out findings for the replaced page.
    assert "findings" not in snap
    assert snap["gates"]["design"] == "unknown"
    # And the event log must carry the invalidation, so a replay clears it too.
    assert [e for e in job.events if e["event"] == "qa_invalidated"]


def test_regeneration_reports_content_the_budget_dropped(app, monkeypatch):
    """R2-03: the one generate path that could lose content silently."""
    job = _regen_fixture(app, monkeypatch)

    def drops(outline, backend=None, dropped=None):
        if dropped is not None:
            dropped.append(
                DroppedContent(slide_no=1, title="第2頁", items=["被刪掉的要點"])
            )
        return _slides_for(outline)

    monkeypatch.setattr(webapi, "generate_slides", drops)
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{job.id}/slides/2/regenerate", json={})
        assert r.status_code == 200
        assert r.json()["dropped_content"][0]["items"] == ["被刪掉的要點"]
        snap = client.get(f"/api/jobs/{job.id}").json()

    assert snap["dropped_content"][-1]["items"] == ["被刪掉的要點"]
    degraded = [e for e in job.events if e["event"] == "content_degraded"]
    assert degraded and degraded[-1]["data"]["items"][0]["items"] == ["被刪掉的要點"]


# ---------------------------------------------------------------------------
# R1-06 — the slot must be held by the real worker THREAD.
#
# The previous test used a FakeTask whose ``done()`` the test itself controlled,
# so it could not observe the actual defect: cancelling a task parked on
# ``asyncio.to_thread`` marks the task done immediately while the thread keeps
# running inside the provider call. These use real cancellation and a real
# thread.
# ---------------------------------------------------------------------------


def test_cancelled_task_does_not_free_the_slot_while_the_thread_still_runs(
    app, monkeypatch
):
    import threading

    _install_fakes(monkeypatch, n=1)
    entered = threading.Event()
    release = threading.Event()

    def slow_provider_call(*a, **k):
        entered.set()
        release.wait(timeout=10)      # the uninterruptible requests.post
        return _outline(1)

    monkeypatch.setattr(webapi, "generate_outline", slow_provider_call)

    async def scenario():
        job = webapi.create_job(app, prompt="x")
        job.task = asyncio.create_task(webapi.run_job(job))
        await asyncio.to_thread(entered.wait, 10)

        job.status = "cancelled"
        job.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job.task

        # The task is done. The thread is not — and it is the thread that is
        # spending the user's quota, so the slot is still taken.
        assert job.task.done()
        assert webapi._occupies_a_slot(job) is True

        release.set()
        # Give the worker its moment to actually return, then the slot frees.
        for _ in range(200):
            if not webapi._occupies_a_slot(job):
                break
            await asyncio.sleep(0.01)
        assert webapi._occupies_a_slot(job) is False
        return job

    asyncio.run(scenario())


def test_start_cancel_loop_cannot_exceed_the_provider_call_limit(app, monkeypatch):
    """The abuse shape: press start/cancel repeatedly, watch the calls pile up."""
    import threading

    _install_fakes(monkeypatch, n=1)
    in_flight = threading.Semaphore(0)
    release = threading.Event()
    started = []
    lock = threading.Lock()

    def slow_provider_call(*a, **k):
        with lock:
            started.append(1)
        in_flight.release()
        release.wait(timeout=10)
        return _outline(1)

    monkeypatch.setattr(webapi, "generate_outline", slow_provider_call)

    async def scenario():
        with TestClient(app) as client:
            for _ in range(webapi._MAX_ACTIVE_JOBS):
                assert client.post("/api/generate", json={"prompt": "x"}).status_code == 200
            for job in list(app.state.jobs.values()):
                await asyncio.to_thread(in_flight.acquire, True, 10)
                client.post(f"/api/jobs/{job.id}/cancel")

            # Every one is "cancelled" on screen; every one is still calling out.
            refused = client.post("/api/generate", json={"prompt": "x"})
            assert refused.status_code == 429
            with lock:
                assert len(started) == webapi._MAX_ACTIVE_JOBS
            release.set()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# R2-01 — the 16 MiB reference budget is the SERVER's rule.
#
# Only the browser enforced it. Six files just under the per-file cap decoded to
# ~17 MiB and arrived as ~23 MiB of JSON, and the API took all of it — a limit a
# client is free not to run is not a limit.
# ---------------------------------------------------------------------------


def _png_of_size(decoded_bytes: int) -> str:
    return "data:image/png;base64," + base64.b64encode(b"x" * decoded_bytes).decode()


def _pdf_of_size(decoded_bytes: int) -> str:
    return "data:application/pdf;base64," + base64.b64encode(
        b"%PDF-1.4" + b"x" * decoded_bytes
    ).decode()


def test_generate_refuses_a_batch_over_the_total_budget(app):
    three_mib = _png_of_size(3 * 1024 * 1024)
    body = {
        "prompt": "x",
        "assets": [{"description": f"圖{i}", "data_url": three_mib} for i in range(6)],
    }
    with TestClient(app) as client:
        r = client.post("/api/generate", json=body)
    assert r.status_code == 422
    assert "總量過大" in json.dumps(r.json(), ensure_ascii=False)


def test_discovery_refuses_a_batch_over_the_total_budget(app):
    three_mib = _pdf_of_size(3 * 1024 * 1024)
    body = {
        "prompt": "x",
        "assets": [{"description": f"檔{i}", "data_url": three_mib} for i in range(6)],
    }
    with TestClient(app) as client:
        r = client.post("/api/discovery/questions", json=body)
    assert r.status_code == 422
    assert "總量過大" in json.dumps(r.json(), ensure_ascii=False)


def test_a_batch_inside_the_budget_passes_the_gate(app):
    """The gate must refuse oversized batches without refusing ordinary ones.

    Asserted on the request model rather than the endpoint: 15 MiB of ``b"x"``
    is within budget but is not a decodable PNG, and the image decoder's 422 is
    a different (correct) refusal that would mask this one.
    """
    three_mib = _png_of_size(3 * 1024 * 1024)
    webapi.GenerateBody(
        prompt="x",
        assets=[{"description": f"圖{i}", "data_url": three_mib} for i in range(5)],
    )


def test_decoded_length_matches_a_real_decode():
    for size in (0, 1, 2, 3, 1000, 4096):
        url = _png_of_size(size)
        payload = url.split(",", 1)[1]
        assert webapi._decoded_length(url) == len(base64.b64decode(payload))


# ---------------------------------------------------------------------------
# R2-08 — the packaged-frontend lookup must return a directory that EXISTS.
#
# `as_file()` deletes what it materialised when its context closes, and the path
# was returned from inside the `with`. Undetectable on a normal install (pip
# unpacks wheels, so `as_file` is a no-op) and broken everywhere else.
# ---------------------------------------------------------------------------


def test_frontend_dist_returns_a_directory_that_is_still_there(monkeypatch, tmp_path):
    packaged = tmp_path / "webui"
    packaged.mkdir()
    (packaged / "index.html").write_text("<!doctype html>", encoding="utf-8")

    class FakeFiles:
        def __truediv__(self, name):
            return tmp_path / name

    monkeypatch.setattr(webapi.resources, "files", lambda _pkg: FakeFiles())

    found = webapi.frontend_dist()
    assert found == packaged
    # The assertion the old code could not satisfy: still present after return.
    assert found.is_dir() and (found / "index.html").is_file()


def test_a_non_filesystem_package_degrades_to_api_only_and_says_so(
    monkeypatch, tmp_path, caplog
):
    """A zip import is unsupported — which must look like unsupported, not fine."""

    class ZippedResource:
        def __truediv__(self, name):
            return self

        def is_file(self):
            return True

    monkeypatch.setattr(webapi.resources, "files", lambda _pkg: ZippedResource())
    # No source-tree fallback either.
    monkeypatch.setattr(
        webapi.Path, "is_file", lambda self: False, raising=False
    )

    with caplog.at_level("WARNING"):
        assert webapi.frontend_dist() is None
    assert "zip" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Output language / 封面署名與校徽 / 自訂範本 — the settings the composer now sends
#
# Every one of these is a promise made in the UI before a single token is spent.
# The tests below are about the promise arriving intact at the place that can
# keep it: the model call, the deck, or the renderer's asset map.
# ---------------------------------------------------------------------------


def _run_to_completion(client, body):
    response = client.post("/api/generate", json=body)
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    snapshot = {}
    for _ in range(200):
        snapshot = client.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in ("complete", "error"):
            break
        time.sleep(0.01)
    return job_id, snapshot


def test_language_reaches_the_outline_call_and_the_deck(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    seen = {}

    def recording_outline(prompt, backend=None, pages=None, language="zh-TW"):
        seen["language"] = language
        return _outline(3)

    monkeypatch.setattr(webapi, "generate_outline", recording_outline)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client, {"prompt": "photosynthesis", "language": "en"}
        )
    assert seen["language"] == "en"
    assert snapshot["status"] == "complete"
    # Stage 2 reads the language off the outline, so it has to be stamped there.
    assert app.state.jobs[job_id].outline.language == "en"


def test_an_unknown_language_is_refused(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/generate", json={"prompt": "x", "language": "klingon"}
        )
    assert response.status_code == 422


def test_regenerating_one_page_keeps_the_decks_language(app, monkeypatch):
    seen = []
    _install_fakes(monkeypatch, n=3, record_slides=seen)

    def outline_in_english(prompt, backend=None, pages=None, language="zh-TW"):
        return _outline(3).model_copy(update={"language": language})

    monkeypatch.setattr(webapi, "generate_outline", outline_in_english)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(client, {"prompt": "x", "language": "en"})
        assert snapshot["status"] == "complete"
        response = client.post(
            f"/api/jobs/{job_id}/slides/2/regenerate", json={"instruction": "shorter"}
        )
        assert response.status_code == 200, response.text
    # The sub-outline built for the single page must carry the same language —
    # otherwise the one page the user asked to fix comes back in zh-TW.
    assert seen[-1].language == "en"


def test_byline_and_logo_land_on_the_deck(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    logo = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client,
            {
                "prompt": "x",
                "byline": "  XX大學資工系 · 王小明  ",
                "logo": {"description": "校徽", "credit": "", "data_url": logo},
                "logo_placement": "all",
            },
        )
    assert snapshot["status"] == "complete"
    job = app.state.jobs[job_id]
    assert job.ir.branding is not None
    assert job.ir.branding.byline == "XX大學資工系 · 王小明"  # trimmed
    assert job.ir.branding.logo == "asset://logo"
    assert job.ir.branding.placement == "all"
    # The bytes have to be in the asset map the renderer is handed, and on disk.
    assert "logo" in job.assets
    assert Path(job.assets["logo"]).is_file()


def test_the_crest_is_not_offered_to_the_model_as_page_media(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    logo = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    with TestClient(app) as client:
        job_id, _snapshot = _run_to_completion(
            client,
            {
                "prompt": "x",
                "logo": {"description": "校徽", "credit": "", "data_url": logo},
            },
        )
    # asset_refs is the menu stage 2 may choose images from. A crest on slide 4
    # is exactly what happens when furniture is put on that menu.
    assert app.state.jobs[job_id].asset_refs == []


def test_a_pdf_cannot_be_used_as_a_logo(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/generate",
            json={
                "prompt": "x",
                "logo": {
                    "description": "校徽",
                    "credit": "",
                    "data_url": _pdf_data_url(),
                },
            },
        )
    assert response.status_code == 422


def test_no_branding_fields_means_no_branding_on_the_deck(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(client, {"prompt": "x"})
    assert snapshot["status"] == "complete"
    assert app.state.jobs[job_id].ir.branding is None


def test_a_user_template_design_beats_the_models_own(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    seen = []

    def recording_slides(outline, backend=None, dropped=None):
        seen.append(outline)
        return _slides_for(outline)

    monkeypatch.setattr(webapi, "generate_slides", recording_slides)
    design = {
        "palette": {
            "bg": "#FBF9F4",
            "surface": "#EFEADD",
            "text": "#1F2733",
            "muted": "#5B6470",
            "accent": "#A3212F",
        },
        "fonts": {"display": "Noto Serif TC", "body": "Noto Sans TC"},
        "scale": "compact",
    }
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client, {"prompt": "x", "theme": "navy", "design": design}
        )
    assert snapshot["status"] == "complete"
    job = app.state.jobs[job_id]
    # Applied to the OUTLINE too: stage 2 is told the art direction, and being
    # told one palette while the renderer paints another is how a "presenter on
    # dark" deck gets written for a light one.
    assert seen[0].design.palette.accent == "#A3212F"
    assert job.ir.design.palette.accent == "#A3212F"
    assert job.ir.design.scale == "compact"


def test_an_unreadable_template_design_is_refused(app, monkeypatch):
    _install_fakes(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/api/generate",
            json={
                "prompt": "x",
                "design": {
                    "palette": {
                        "bg": "#FFFFFF",
                        "surface": "#FFFFFF",
                        "text": "#EEEEEE",
                        "muted": "#F5F5F5",
                        "accent": "#FAFAFA",
                    },
                    "fonts": {"display": "Noto Sans TC", "body": "Noto Sans TC"},
                    "scale": "standard",
                },
            },
        )
    assert response.status_code == 422


def test_a_new_preset_is_accepted_by_the_api(app, monkeypatch):
    # theme is validated against the registry now, not a hand-kept Literal —
    # this is the test that fails if the two ever drift apart again.
    _install_fakes(monkeypatch, n=3)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(client, {"prompt": "x", "theme": "crimson"})
        rejected = client.post(
            "/api/generate", json={"prompt": "x", "theme": "not-a-theme"}
        )
    assert rejected.status_code == 422
    assert snapshot["status"] == "complete"
    assert app.state.jobs[job_id].ir.theme == "crimson"


def test_branding_and_language_survive_a_reload(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    logo = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client,
            {
                "prompt": "x",
                "language": "bilingual",
                "byline": "資工系",
                "logo": {"description": "校徽", "credit": "", "data_url": logo},
            },
        )
    assert snapshot["status"] == "complete"

    revived = webapi.create_app(jobs_dir=app.state.jobs_dir)
    job = revived.state.jobs[job_id]
    assert job.language == "bilingual"
    assert job.branding is not None and job.branding.byline == "資工系"
    assert job.branding.logo == "asset://logo"
    assert "logo" in job.assets and Path(job.assets["logo"]).is_file()


def test_style_travels_from_the_request_to_the_deck(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client, {"prompt": "x", "theme": "navy", "style": "editorial"}
        )
        rejected = client.post(
            "/api/generate", json={"prompt": "x", "style": "bauhaus"}
        )
    assert snapshot["status"] == "complete"
    assert rejected.status_code == 422
    assert app.state.jobs[job_id].ir.style == "editorial"


def test_style_survives_a_reload(app, monkeypatch):
    _install_fakes(monkeypatch, n=3)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client, {"prompt": "x", "style": "keynote"}
        )
    assert snapshot["status"] == "complete"
    revived = webapi.create_app(jobs_dir=app.state.jobs_dir)
    assert revived.state.jobs[job_id].style == "keynote"
    assert revived.state.jobs[job_id].ir.style == "keynote"


def test_no_style_leaves_the_palettes_pairing_in_charge(app, monkeypatch):
    # Omitting 版式 must not silently mean "classic": each preset ships paired
    # with the composition it was designed for.
    _install_fakes(monkeypatch, n=3)
    with TestClient(app) as client:
        job_id, snapshot = _run_to_completion(
            client, {"prompt": "x", "theme": "dark"}
        )
    assert snapshot["status"] == "complete"
    assert app.state.jobs[job_id].ir.style is None
