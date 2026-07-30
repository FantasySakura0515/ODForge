"""Tests for source discovery and per-job source selection.

Adding a backend used to mean editing five places: the registry, two ``Literal``
annotations in the web API, and two TypeScript unions in the frontend. Miss one
and the source exists but cannot be requested. The API derives its choices from
the registries instead, and the frontend asks ``GET /api/sources`` rather than
carrying its own list.
"""

import pytest
from fastapi.testclient import TestClient

from odforge import critic, llm
from odforge.webapi import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


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


def test_generate_accepts_a_vision_backend_per_job(client):
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


def test_generate_accepts_every_registered_text_backend(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    for name in llm.BACKENDS:
        assert _generate(client, backend=name).status_code == 200, name
