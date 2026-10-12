"""Tests for ``odforge.watch`` (``odforge preview``): reload a watched IR file.

Most cases stub LibreOffice out (``convert_to_pdf`` raising PreviewUnavailable)
so they pin the event log, not the renderer; one end-to-end case renders for
real and skips itself without LibreOffice.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from odforge import watch
from odforge.cli import app
from odforge.preview import PreviewUnavailable
from odforge.validate import find_soffice

runner = CliRunner()

DECK = {
    "title": "預覽測試",
    "slides": [
        {"layout": "title", "title": "預覽測試", "subtitle": "副標"},
        {"layout": "title-content", "title": "重點", "bullets": ["一", "二"]},
    ],
}


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def no_soffice(monkeypatch):
    def _missing(*_args, **_kwargs):
        raise PreviewUnavailable("no soffice")

    monkeypatch.setattr(watch, "convert_to_pdf", _missing)


def _names(job):
    return [e["event"] for e in job.events]


# -- reading the source --------------------------------------------------------


def test_type_defaults_to_presentation(tmp_path):
    ir = watch.load_presentation(_write(tmp_path / "d.json", DECK))
    assert ir.title == "預覽測試"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{ 壞掉", "不是合法的 JSON"),
        ("[]", "最外層必須是 JSON 物件"),
        (json.dumps({"title": "x", "slides": []}), "不符合簡報格式"),
        (json.dumps({"type": "text", "title": "x", "blocks": []}), "只支援簡報"),
    ],
)
def test_unreadable_sources_say_what_is_wrong(tmp_path, content, message):
    source = tmp_path / "d.json"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(watch.SourceError, match=message):
        watch.load_presentation(source)


def test_outline_follows_the_deck(tmp_path):
    outline = watch.outline_for(watch.load_presentation(_write(tmp_path / "d.json", DECK)))
    assert [(p.role, p.title) for p in outline.pages] == [
        ("title", "預覽測試"),
        ("title-content", "重點"),
    ]


# -- reloading -----------------------------------------------------------------


def test_reload_writes_a_complete_fresh_log(tmp_path, no_soffice):
    source = _write(tmp_path / "d.json", DECK)
    out = tmp_path / "out" / "d.odp"
    _app, job = watch.create_preview_app(source, out, jobs_dir=tmp_path / "jobs")

    said: list[str] = []
    asyncio.run(watch.reload(job, source, out, said.append))

    assert said and said[-1].startswith("第 1 版:2 頁")
    names = _names(job)
    assert names[0] == "reload"
    assert names[-1] == "complete"
    assert names.count("slide_done") == 2
    assert out.is_file()
    assert job.downloadable
    layout = next(e["data"] for e in job.events if e["event"] == "layout_report")
    assert layout["status"] == "unavailable"
    gates = {e["data"]["gate"]: e["data"]["status"] for e in job.events if e["event"] == "gate_result"}
    assert gates == {"zip": "pass", "xml": "pass", "libreoffice": "skipped", "design": "skipped"}


def test_each_reload_replaces_the_log_and_bumps_the_epoch(tmp_path, no_soffice):
    source = _write(tmp_path / "d.json", DECK)
    _app, job = watch.create_preview_app(source, None, jobs_dir=tmp_path / "jobs")

    asyncio.run(watch.reload(job, source, None))
    first_epoch = job.event_epoch
    _write(source, {**DECK, "slides": DECK["slides"][:1]})
    asyncio.run(watch.reload(job, source, None))

    assert job.event_epoch == first_epoch + 1
    assert _names(job).count("reload") == 1
    assert _names(job).count("slide_done") == 1


def test_a_broken_save_keeps_the_last_good_version(tmp_path, no_soffice):
    source = _write(tmp_path / "d.json", DECK)
    _app, job = watch.create_preview_app(source, None, jobs_dir=tmp_path / "jobs")
    asyncio.run(watch.reload(job, source, None))
    good = list(job.events)

    source.write_text("{ 寫到一半", encoding="utf-8")
    said: list[str] = []
    asyncio.run(watch.reload(job, source, None, said.append))
    assert said[-1].startswith("讀不進來,畫面維持上一版")

    assert job.events[: len(good)] == good
    assert job.events[-1]["event"] == "error"
    assert "不是合法的 JSON" in job.events[-1]["data"]["message"]
    assert job.downloadable


def test_watch_loop_reloads_on_change(tmp_path, monkeypatch):
    source = _write(tmp_path / "d.json", DECK)
    seen: list[str] = []

    async def fake_reload(_job, src, _out, _announce=None):
        seen.append(src.read_text(encoding="utf-8"))

    monkeypatch.setattr(watch, "reload", fake_reload)
    monkeypatch.setattr(watch, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(watch, "SETTLE_SECONDS", 0.02)

    async def scenario():
        task = asyncio.create_task(watch.watch_loop(None, source, None))
        await asyncio.sleep(0.15)
        _write(source, {**DECK, "title": "改過了"})
        await asyncio.sleep(0.3)
        task.cancel()

    asyncio.run(scenario())
    assert len(seen) == 2
    assert "改過了" in seen[-1]


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_reload_renders_previews_and_measures_layout(tmp_path):
    source = _write(tmp_path / "d.json", DECK)
    _app, job = watch.create_preview_app(source, None, jobs_dir=tmp_path / "jobs")
    asyncio.run(watch.reload(job, source, None))

    previews = [e["data"]["url"] for e in job.events if e["event"] == "preview_ready"]
    assert len(previews) == 2
    layout = next(e["data"] for e in job.events if e["event"] == "layout_report")
    assert layout["status"] == "ok"


# -- the command ----------------------------------------------------------------


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_preview_refuses_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("odforge.webapi.frontend_dist", lambda: tmp_path)
    result = runner.invoke(app, ["preview", str(tmp_path / "nope.json"), "--no-open"])
    assert result.exit_code == 1
    assert "找不到" in _ANSI.sub("", result.output)


def test_preview_needs_a_built_frontend(tmp_path, monkeypatch):
    monkeypatch.setattr("odforge.webapi.frontend_dist", lambda: None)
    source = _write(tmp_path / "d.json", DECK)
    result = runner.invoke(app, ["preview", str(source), "--no-open"])
    assert result.exit_code == 1
    assert "npm --prefix web" in _ANSI.sub("", result.output)
