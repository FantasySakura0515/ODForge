"""Tests for source discovery and per-job source selection.

Adding a backend used to mean editing five places: the registry, two ``Literal``
annotations in the web API, and two TypeScript unions in the frontend. Miss one
and the source exists but cannot be requested. The API derives its choices from
the registries instead, and the frontend asks ``GET /api/sources`` rather than
carrying its own list.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odforge import critic, llm, webapi
from odforge.ir import Outline, PageRole, Presentation, Slide
from odforge.webapi import create_app, vision_source_names


def _stub_pipeline(monkeypatch):
    """Replace the two generation stages and the renderer with offline fakes."""
    outline = Outline(
        design=None,
        mode="presenter",
        pages=[PageRole(role="title", title="封面", gist="開場")],
    )
    deck = Presentation(title="測試", slides=[Slide(layout="title", title="封面")])
    monkeypatch.setattr(webapi, "generate_outline", lambda *a, **k: outline)
    monkeypatch.setattr(webapi, "generate_slides", lambda *a, **k: deck)
    monkeypatch.setattr(
        webapi, "render", lambda ir, out, **k: out.write_bytes(b"PK") or out
    )
    monkeypatch.setattr(
        webapi,
        "render_pages",
        lambda *a, **k: (_ for _ in ()).throw(webapi.PreviewUnavailable("no soffice")),
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    # jobs_dir is not optional in practice: these tests POST /api/generate, and
    # without it the jobs land in the operator's real session history.
    return TestClient(create_app(jobs_dir=tmp_path / "sessions"))


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_sources_lists_every_registered_backend(client):
    body = client.get("/api/sources").json()
    assert {s["name"] for s in body["text"]} == set(llm.BACKENDS)
    assert {s["name"] for s in body["vision"]} == set(critic.VISION_BACKENDS) | {"off"}


def test_sources_reports_which_are_usable(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    body = client.get("/api/sources").json()
    deepseek = next(s for s in body["text"] if s["name"] == "deepseek")
    assert deepseek["available"] is False
    assert "DEEPSEEK_API_KEY" in deepseek["reason"]


def test_an_available_source_carries_no_complaint(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    body = client.get("/api/sources").json()
    deepseek = next(s for s in body["text"] if s["name"] == "deepseek")
    assert deepseek["available"] is True
    assert deepseek["reason"] == ""


def test_off_is_always_an_available_vision_source(client):
    body = client.get("/api/sources").json()
    off = next(s for s in body["vision"] if s["name"] == "off")
    assert off["available"] is True


def test_sources_reports_the_current_defaults(client, monkeypatch):
    monkeypatch.setenv("ODFORGE_BACKEND", "ollama")
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "custom")
    body = client.get("/api/sources").json()
    assert body["defaults"]["text"] == "ollama"
    assert body["defaults"]["vision"] == "custom"


def test_a_new_backend_shows_up_without_touching_the_api(client, monkeypatch):
    # The point of the whole exercise: registering a source is the only edit.
    monkeypatch.setitem(critic.VISION_BACKENDS, "invented", lambda: None)
    body = client.get("/api/sources").json()
    assert "invented" in {s["name"] for s in body["vision"]}


# ---------------------------------------------------------------------------
# per-job selection
# ---------------------------------------------------------------------------


def _generate(client, **overrides):
    payload = {"prompt": "測試", "doc_type": "odp", "interactive": True}
    payload.update(overrides)
    return client.post("/api/generate", json=payload)


def test_generate_accepts_a_vision_backend_per_job(client, monkeypatch):
    # 這些測試要驗的是「請求被接受」,不是「生成跑得完」。所以把兩段式生成換掉:
    # 否則每個斷言都會在背景真的打一次供應商 API,測試變成在花別人的額度,而且
    # 在沒有金鑰的 CI 上結果完全不同。
    _stub_pipeline(monkeypatch)
    resp = _generate(client, qa=True, vision_backend="off")
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").status_code == 200


def test_generate_rejects_an_unknown_text_backend(client):
    resp = _generate(client, backend="banana")
    assert resp.status_code == 422


def test_generate_rejects_an_unknown_vision_backend(client):
    resp = _generate(client, vision_backend="banana")
    assert resp.status_code == 422


@pytest.mark.parametrize("name", sorted(llm.BACKENDS))
def test_every_registered_text_backend_is_accepted_by_the_schema(name):
    """純 schema 驗證:註冊過的名稱一定通過,不需要啟動任何背景工作。"""
    body = webapi.GenerateBody(prompt="測試", backend=name)
    assert body.backend == name


@pytest.mark.parametrize("name", sorted(vision_source_names()))
def test_every_registered_vision_source_is_accepted_by_the_schema(name):
    body = webapi.GenerateBody(prompt="測試", vision_backend=name)
    assert body.vision_backend == name


# ---------------------------------------------------------------------------
# R2-07 — test isolation must not fall through to the developer's real login.
# ---------------------------------------------------------------------------

def test_codex_home_points_at_an_empty_directory_not_the_real_one():
    """Deleting CODEX_HOME meant "use the default" — i.e. ``~/.codex``.

    The scrub that existed to isolate the suite was handing it the maintainer's
    live subscription credentials, so every "not logged in" branch was dead code
    on the only machine anyone ran the suite on.
    """
    from odforge.critic import _codex_auth_path

    codex_home = os.environ.get("CODEX_HOME")
    assert codex_home, "CODEX_HOME must be set (to a throwaway), never unset"

    path = _codex_auth_path()
    assert Path(codex_home) in path.parents
    # The specific file that must never be reached. (A pytest tmp dir lives
    # under the home directory on Windows, so "not under $HOME" is the wrong
    # assertion; "not the real credential file" is the right one.)
    assert path != Path.home() / ".codex" / "auth.json"
    assert not path.exists(), "an isolated CODEX_HOME must look logged-out"


def test_the_codex_cli_cannot_be_launched_from_a_test():
    """Environment isolation stops the read; this stops the spend."""
    import subprocess

    with pytest.raises(AssertionError, match="codex"):
        subprocess.run(["codex", "exec", "hello"], capture_output=True)
    with pytest.raises(AssertionError, match="codex"):
        subprocess.Popen(["codex"])


def test_the_guard_does_not_block_other_tools():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "print('ok')"], capture_output=True, text=True
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "ok"
