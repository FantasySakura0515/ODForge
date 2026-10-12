"""Auto-opening the web editor from MCP."""

import shutil
from pathlib import Path

import pytest

from odforge import editor_launch
from odforge.webapi import frontend_dist

DEMO = Path(__file__).resolve().parents[1] / "demo" / "tree.odp"


def test_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ODFORGE_NO_EDITOR", "1")
    assert editor_launch.disabled()
    assert editor_launch.editor_note(tmp_path / "x.odp") is None


def test_note_reports_a_failure_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("ODFORGE_NO_EDITOR", "0")

    def boom(path, open_browser=True):
        raise RuntimeError("沒有前端")

    monkeypatch.setattr(editor_launch, "ensure_editor", boom)
    assert "沒有自動打開網頁編輯器" in editor_launch.editor_note(tmp_path / "x.odp")


@pytest.mark.skipif(frontend_dist() is None, reason="frontend not built")
def test_starts_once_and_reuses(monkeypatch, tmp_path):
    monkeypatch.setattr(editor_launch, "STATE_DIR", tmp_path / "state")
    deck = tmp_path / "d.odp"
    shutil.copyfile(DEMO, deck)
    first = editor_launch.ensure_editor(deck, open_browser=False)
    try:
        assert first.startswith("http://127.0.0.1:") and first.endswith("/editor.html")
        assert editor_launch.ensure_editor(deck, open_browser=False) == first
    finally:
        import json
        import os
        import signal

        entry = json.loads((tmp_path / "state" / "editors.json").read_text())[str(deck.resolve())]
        os.kill(entry["pid"], signal.SIGTERM)
