"""Tests for the vision-model design critic (Task 16.2).

Fully mocked: no real API calls, no anthropic install, no API keys.

* The ``claude`` backend is exercised by constructing ``ClaudeVisionBackend``
  with a hand-rolled fake anthropic client (the SDK is imported lazily in the
  factory, so the class itself needs nothing installed).
* The ``ollama`` backend is exercised by monkeypatching ``critic.OpenAI`` with a
  fake client factory — the same trick as ``test_llm.py`` — so the OpenAI SDK is
  never actually called.
"""

import json
from types import SimpleNamespace

import pytest

from odforge import critic
from odforge.critic import (
    ClaudeVisionBackend,
    Finding,
    OllamaVisionBackend,
    critique,
)
from odforge.ir import Presentation, Slide


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_pngs(tmp_path, n: int):
    """Create ``n`` tiny placeholder PNG files; content is irrelevant (mocked)."""
    pngs = []
    for i in range(1, n + 1):
        p = tmp_path / f"page-{i:02d}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 8)
        pngs.append(p)
    return pngs


def _ir() -> Presentation:
    return Presentation(title="光合作用", slides=[Slide(layout="title", title="封面")])


_TWO_FINDINGS = [
    {
        "slide_no": 2,
        "issue": "標題文字超出版面右緣被截斷",
        "severity": "error",
        "fix_hint": "縮短標題或縮小字級",
    },
    {
        "slide_no": 5,
        "issue": "連續三頁使用相同的 title-content 版型,略顯單調",
        "severity": "warn",
        "fix_hint": "穿插一頁 section 或 big-fact 調節節奏",
    },
]


# ---- fake anthropic client (claude backend) -------------------------------


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []  # kwargs of each create()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropic:
    """Mimics ``anthropic.Anthropic()``: ``.messages.create(...) -> response``."""

    def __init__(self, findings_payload):
        block = SimpleNamespace(type="tool_use", name=critic.TOOL_NAME,
                                input={"findings": findings_payload})
        text = SimpleNamespace(type="text", text="我已完成評審。")
        response = SimpleNamespace(content=[text, block])
        self.messages = _FakeMessages(response)


# ---- fake OpenAI client (ollama backend) ----------------------------------


class _FakeCompletions:
    def __init__(self, arguments_json):
        self._arguments_json = arguments_json
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name=critic.TOOL_NAME, arguments=self._arguments_json)
        )
        message = SimpleNamespace(tool_calls=[tool_call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeOpenAIClient:
    def __init__(self, arguments_json):
        self.completions = _FakeCompletions(arguments_json)
        self.chat = SimpleNamespace(completions=self.completions)


def _install_fake_openai(monkeypatch, arguments_json):
    client = _FakeOpenAIClient(arguments_json)

    def factory(base_url=None, api_key=None, **_):
        client.base_url = base_url
        client.api_key = api_key
        return client

    monkeypatch.setattr(critic, "OpenAI", factory)
    return client


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


def test_finding_model_roundtrip():
    f = Finding.model_validate(_TWO_FINDINGS[0])
    assert f.slide_no == 2
    assert f.severity == "error"
    assert f.fix_hint == "縮短標題或縮小字級"


def test_finding_rejects_bad_severity():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Finding.model_validate({**_TWO_FINDINGS[0], "severity": "fatal"})


# ---------------------------------------------------------------------------
# ① fake vision response (two findings) -> list[Finding], correct fields
# ---------------------------------------------------------------------------


def test_claude_parses_two_findings(tmp_path):
    pngs = _make_pngs(tmp_path, 5)
    backend = ClaudeVisionBackend(_FakeAnthropic(_TWO_FINDINGS), "test-model")
    findings = backend.critique(pngs, _ir())

    assert all(isinstance(f, Finding) for f in findings)
    assert [f.slide_no for f in findings] == [2, 5]
    assert [f.severity for f in findings] == ["error", "warn"]
    assert findings[0].issue.startswith("標題文字超出")
    assert findings[1].fix_hint.startswith("穿插")


def test_ollama_parses_two_findings(monkeypatch, tmp_path):
    _install_fake_openai(monkeypatch, json.dumps({"findings": _TWO_FINDINGS}))
    pngs = _make_pngs(tmp_path, 5)
    findings = critique(pngs, _ir(), backend="ollama")
    assert [f.slide_no for f in findings] == [2, 5]
    assert [f.severity for f in findings] == ["error", "warn"]


def test_no_tool_call_yields_empty(monkeypatch, tmp_path):
    # A model that returns no tool call -> no findings (not a crash).
    client = _FakeOpenAIClient(None)
    client.completions.create = lambda **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None))]
    )
    monkeypatch.setattr(critic, "OpenAI", lambda **_: client)
    assert critique(_make_pngs(tmp_path, 1), _ir(), backend="ollama") == []


# ---------------------------------------------------------------------------
# ② off backend -> [] and constructs no client
# ---------------------------------------------------------------------------


def test_off_backend_arg_returns_empty_no_client(monkeypatch, tmp_path):
    # If any client were constructed, this OpenAI boobytrap would blow up.
    def boom(**_):
        raise AssertionError("off must not construct any client")

    monkeypatch.setattr(critic, "OpenAI", boom)
    assert critique(_make_pngs(tmp_path, 3), _ir(), backend="off") == []


def test_off_is_the_env_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ODFORGE_VISION_BACKEND", raising=False)
    monkeypatch.setattr(critic, "OpenAI", lambda **_: (_ for _ in ()).throw(
        AssertionError("default off must not construct a client")))
    assert critique(_make_pngs(tmp_path, 2), _ir()) == []


def test_off_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ODFORGE_VISION_BACKEND", "off")
    assert critique(_make_pngs(tmp_path, 2), _ir()) == []


# ---------------------------------------------------------------------------
# ③ request payload carries len(pngs) images + the checklist keywords
# ---------------------------------------------------------------------------


def test_ollama_request_payload_has_images_and_checklist(monkeypatch, tmp_path):
    client = _install_fake_openai(monkeypatch, json.dumps({"findings": []}))
    pngs = _make_pngs(tmp_path, 4)
    critique(pngs, _ir(), backend="ollama")

    (call,) = client.completions.calls
    messages = call["messages"]
    system = next(m["content"] for m in messages if m["role"] == "system")
    user = next(m["content"] for m in messages if m["role"] == "user")

    # checklist keyword present in the (system) framing
    assert "溢出" in system
    # one image block per page, base64 data-URI encoded
    image_blocks = [b for b in user if b["type"] == "image_url"]
    assert len(image_blocks) == len(pngs)
    assert all(
        b["image_url"]["url"].startswith("data:image/png;base64,") for b in image_blocks
    )
    # the forced tool is named and selected
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": critic.TOOL_NAME},
    }
    assert call["tools"][0]["function"]["name"] == critic.TOOL_NAME


def test_claude_request_payload_has_images_and_checklist(tmp_path):
    fake = _FakeAnthropic([])
    backend = ClaudeVisionBackend(fake, "test-model")
    pngs = _make_pngs(tmp_path, 3)
    backend.critique(pngs, _ir())

    (call,) = fake.messages.calls
    assert "溢出" in call["system"]
    content = call["messages"][0]["content"]
    image_blocks = [b for b in content if b["type"] == "image"]
    assert len(image_blocks) == len(pngs)
    assert all(b["source"]["media_type"] == "image/png" for b in image_blocks)
    # independent context: exactly one user turn, no generation history
    assert len(call["messages"]) == 1
    assert call["messages"][0]["role"] == "user"
    assert call["tool_choice"] == {"type": "tool", "name": critic.TOOL_NAME}


# ---------------------------------------------------------------------------
# registry / facade
# ---------------------------------------------------------------------------


def test_all_pages_in_one_request(monkeypatch, tmp_path):
    client = _install_fake_openai(monkeypatch, json.dumps({"findings": []}))
    critique(_make_pngs(tmp_path, 12), _ir(), backend="ollama")
    assert len(client.completions.calls) == 1  # one round-trip for all pages


def test_get_vision_backend_unknown_raises():
    with pytest.raises(ValueError, match="banana"):
        critic.get_vision_backend("banana")


def test_get_vision_backend_off_no_client(monkeypatch):
    monkeypatch.setattr(critic, "OpenAI", lambda **_: (_ for _ in ()).throw(
        AssertionError("off backend must not construct a client")))
    backend = critic.get_vision_backend("off")
    assert backend.critique([], _ir()) == []


def test_get_vision_backend_claude_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        critic.get_vision_backend("claude")


def test_ollama_backend_targets_localhost(monkeypatch):
    _install_fake_openai(monkeypatch, json.dumps({"findings": []}))
    backend = critic.get_vision_backend("ollama")
    assert isinstance(backend, OllamaVisionBackend)
    assert "11434" in backend.base_url
    assert backend.model == "qwen2.5vl"  # default when env unset


def test_ollama_vision_model_env_override(monkeypatch):
    _install_fake_openai(monkeypatch, json.dumps({"findings": []}))
    monkeypatch.setenv("ODFORGE_OLLAMA_VISION_MODEL", "llava:13b")
    backend = critic.get_vision_backend("ollama")
    assert backend.model == "llava:13b"
