"""The local tools' HTTP APIs answer only their own browser tab."""

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from odforge.editor_api import create_editor_app
from odforge.localguard import add_local_guard, local_hosts

DEMO = Path(__file__).resolve().parents[1] / "demo" / "tree.odp"


def _client(tmp_path):
    deck = tmp_path / "d.odp"
    shutil.copyfile(DEMO, deck)
    app = create_editor_app(deck)
    add_local_guard(app, local_hosts(8790))
    return TestClient(app, base_url="http://127.0.0.1:8790")


def test_own_tab_works(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/doc").status_code == 200
    ok = client.post("/api/doc/undo", headers={"Origin": "http://127.0.0.1:8790"})
    assert ok.status_code == 200


def test_another_site_cannot_change_the_document(tmp_path):
    client = _client(tmp_path)
    blocked = client.post("/api/doc/undo", headers={"Origin": "https://evil.example"})
    assert blocked.status_code == 403


def test_rebound_hostname_is_refused(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/doc", headers={"Host": "evil.example:8790"}).status_code == 403
