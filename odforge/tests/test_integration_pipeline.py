"""Cross-layer integration: one real pipeline, only the model is faked.

Every other web test replaces the renderer, the validator and the rasteriser
with fakes that write ``b"PK\\x03\\x04 fake-odp"``. That is fine for exercising
the API's control flow, and it is exactly why a whole class of bug survived: the
suite could be green while the bytes the endpoint served were not a valid ODF
package at all. Nothing checked that the *download* was openable.

So here the only fake is the LLM (deterministic IR, no network, no key). The
renderer, the three validation gates, the preview rasteriser and the download
endpoint all run for real, and the file that comes out of the HTTP response is
opened and inspected as a ZIP.

The LibreOffice-dependent parts skip themselves when soffice is absent; CI's
integration lane installs it and asserts the skips did not swallow the suite.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from odforge import webapi
from odforge.ir import Outline, PageRole, Presentation, Slide
from odforge.package import ODP_MIMETYPE
from odforge.validate import find_soffice
from odforge.webapi import create_app

# A deck that exercises the layouts most likely to break the renderer: nested
# bullets, a two-column split, a chart with real numbers, a quote and a closing.
_PAGES = [
    ("title", "ODForge 整合測試"),
    ("agenda", "本次內容"),
    ("title-content", "重點"),
    ("two-col", "比較"),
    ("big-fact", "關鍵數據"),
    ("quote", "引言"),
    ("chart", "成長"),
    ("closing", "結論"),
]


def _outline() -> Outline:
    return Outline(
        design=None,
        mode="presenter",
        pages=[
            PageRole(role=role, title=title, gist=f"{title}要說的一句話")
            for role, title in _PAGES
        ],
    )


def _deck(outline: Outline) -> Presentation:
    slides = []
    for page in outline.pages:
        slide = {"layout": page.role, "title": page.title, "notes": page.gist}
        if page.role in {"title-content", "agenda"}:
            slide["bullets"] = ["第一個重點", "第二個重點", "第三個重點"]
        elif page.role == "two-col":
            slide["left"] = ["傳統做法", "耗時"]
            slide["right"] = ["ODForge", "自動化"]
        elif page.role == "big-fact":
            slide["fact"] = "99%"
            slide["bullets"] = ["自動化涵蓋率"]
        elif page.role == "quote":
            slide["quote"] = "格式的正確性不該靠人工目視。"
            slide["attribution"] = "ODForge"
        elif page.role == "chart":
            slide["chart"] = {
                "labels": ["2023", "2024", "2025"],
                "values": [120.0, 300.0, 480.0],
                "unit": "件",
                "highlight": 2,
            }
        slides.append(Slide(**slide))
    return Presentation(title="ODForge 整合測試", slides=slides)


@pytest.fixture
def app(tmp_path):
    return create_app(jobs_dir=tmp_path / "sessions")


@pytest.fixture
def only_the_model_is_fake(monkeypatch):
    """Fake stage 1 and stage 2. Everything downstream is the real thing."""
    outline = _outline()
    monkeypatch.setattr(webapi, "generate_outline", lambda *a, **k: outline)
    monkeypatch.setattr(
        webapi, "generate_slides", lambda o, backend=None, dropped=None: _deck(o)
    )
    return outline


def test_a_real_deck_travels_from_ir_to_a_downloadable_odf(
    app, only_the_model_is_fake
):
    job = webapi.create_job(app, prompt="整合測試")
    asyncio.run(webapi.run_job(job))

    assert job.status == "complete", job.error
    # The deterministic gates ran against the real package, not a stub.
    assert job.gates["zip"] == "pass"
    assert job.gates["xml"] == "pass"
    assert job.validated_version == job.artifact_version == 1

    with TestClient(app) as client:
        response = client.get(f"/api/jobs/{job.id}/download")

    assert response.status_code == 200
    body = response.content

    # The bytes that reached the browser are a real ODF package: mimetype first,
    # stored uncompressed, correct media type, manifest present.
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype").decode() == ODP_MIMETYPE
        names = set(archive.namelist())
        assert {"content.xml", "styles.xml", "META-INF/manifest.xml"} <= names
        content = archive.read("content.xml").decode("utf-8")

    # And it is the deck we asked for, not a placeholder.
    for _role, title in _PAGES:
        assert title in content
    assert "99%" in content


def test_the_download_carries_a_human_filename_and_the_odp_mimetype(
    app, only_the_model_is_fake
):
    job = webapi.create_job(app, prompt="整合測試")
    asyncio.run(webapi.run_job(job))
    with TestClient(app) as client:
        response = client.get(f"/api/jobs/{job.id}/download")
    assert response.headers["content-type"] == ODP_MIMETYPE
    # RFC 5987 form, because the deck title is Chinese.
    assert "filename*=" in response.headers["content-disposition"]


def test_a_deck_that_fails_validation_is_never_served(app, only_the_model_is_fake, monkeypatch):
    """真渲染 + 假驗證失敗:檔案在磁碟上,但端點必須拒絕交付。"""

    def failing(path, **kwargs):
        from odforge.validate import ValidationReport

        return ValidationReport(
            ok=False,
            gates={"structure": (True, "ok"), "xml": (False, "not well-formed")},
        )

    monkeypatch.setattr(webapi, "validate_odf", failing)
    job = webapi.create_job(app, prompt="整合測試")
    asyncio.run(webapi.run_job(job))

    assert job.odp_path.is_file()  # 真的算出來了
    with TestClient(app) as client:
        assert client.get(f"/api/jobs/{job.id}/download").status_code == 409


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_the_real_deck_opens_in_libreoffice_and_previews_every_page(
    app, only_the_model_is_fake
):
    """真 LibreOffice:三道閘之外,實際開檔 + 逐頁點陣化。"""
    job = webapi.create_job(app, prompt="整合測試")
    asyncio.run(webapi.run_job(job))

    assert job.gates["libreoffice"] == "pass", job.gates
    previews = sorted(p.name for p in job.preview_dir.glob("*.png"))
    assert previews == [f"page-{i:02d}.png" for i in range(1, len(_PAGES) + 1)]

    with TestClient(app) as client:
        for n in range(1, len(_PAGES) + 1):
            page = client.get(f"/api/jobs/{job.id}/preview/{n}.png")
            assert page.status_code == 200
            assert page.content.startswith(b"\x89PNG"), n


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_regeneration_keeps_the_artifact_openable(app, only_the_model_is_fake, monkeypatch):
    """重生走完整條交易路徑(真算圖 + 真驗證 + 真預覽),產物仍是可開的 ODF。"""
    job = webapi.create_job(app, prompt="整合測試")
    asyncio.run(webapi.run_job(job))

    def replacement(o, backend=None, dropped=None):
        deck = _deck(_outline())
        deck.slides[0] = Slide(layout="title", title="重生後的封面")
        return deck

    monkeypatch.setattr(webapi, "generate_slides", replacement)
    with TestClient(app) as client:
        result = client.post(f"/api/jobs/{job.id}/slides/1/regenerate", json={})
        assert result.status_code == 200, result.text
        gates = {g["gate"]: g["status"] for g in result.json()["gates"]}
        assert gates["zip"] == "pass" and gates["libreoffice"] == "pass"

        body = client.get(f"/api/jobs/{job.id}/download").content

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert "重生後的封面" in archive.read("content.xml").decode("utf-8")
