"""Tests for the LLM backend abstraction layer.

All tests are fully mocked: no network, no API keys. The OpenAI client factory
``odforge.llm.OpenAI`` is monkeypatched with a fake that returns queued,
preprogrammed chat-completion responses.
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from odforge import llm
from odforge.ir import Presentation


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_response(arguments_json: str | None):
    """Build a fake chat-completion response.

    ``arguments_json`` None => a message with no tool_calls (empty list),
    otherwise a single tool_call carrying that JSON string in
    ``function.arguments``.
    """
    if arguments_json is None:
        message = SimpleNamespace(tool_calls=None)
    else:
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="emit_document", arguments=arguments_json)
        )
        message = SimpleNamespace(tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # each entry is the kwargs dict passed to create()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("create() called more times than responses queued")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def _install_fake_openai(monkeypatch, responses):
    """Patch ``odforge.llm.OpenAI`` with a factory that returns a FakeClient.

    Returns the (shared) FakeClient so the test can assert on recorded calls.
    """
    client = FakeClient(responses)

    def factory(base_url=None, api_key=None, **_):
        # record construction args for backend-selection tests
        client.base_url = base_url
        client.api_key = api_key
        return client

    monkeypatch.setattr(llm, "OpenAI", factory)
    return client


# valid Presentation payload (deliberately omits "type" — see test 2)
_VALID_PRESENTATION = {
    "title": "光合作用入門",
    "slides": [
        {"layout": "title", "title": "光合作用", "subtitle": "生物課", "notes": "開場"},
        {"layout": "big-fact", "fact": "6CO2", "notes": "強調反應物"},
    ],
}

# invalid: slide missing required "layout"
_INVALID_PRESENTATION = {"title": "壞的簡報", "slides": [{"title": "無版面"}]}


def _backend(monkeypatch, responses):
    _install_fake_openai(monkeypatch, responses)
    return llm.OpenAICompatBackend("https://example.test", "test-token", "test-model")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_generate_ir_presentation_roundtrip(monkeypatch):
    backend = _backend(monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))])
    result = backend.generate_ir("做一份光合作用簡報", "presentation")
    assert isinstance(result, Presentation)
    assert result.title == "光合作用入門"
    assert len(result.slides) == 2


def test_type_field_injected(monkeypatch):
    # payload has no "type" key; implementation must inject the doc_type
    assert "type" not in _VALID_PRESENTATION
    backend = _backend(monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))])
    result = backend.generate_ir("x", "presentation")
    assert isinstance(result, Presentation)
    assert result.type == "presentation"


def test_invalid_payload_retries_then_raises(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_INVALID_PRESENTATION)),
            _make_response(json.dumps(_INVALID_PRESENTATION)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(ValidationError):
        backend.generate_ir("x", "presentation")
    assert len(client.completions.calls) == 2


def test_retry_succeeds_second_time(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_INVALID_PRESENTATION)),
            _make_response(json.dumps(_VALID_PRESENTATION)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_ir("x", "presentation")
    assert isinstance(result, Presentation)
    assert len(client.completions.calls) == 2
    # second call's messages must carry an error summary the first lacked
    second_messages = client.completions.calls[1]["messages"]
    combined = " ".join(m["content"] for m in second_messages)
    first_messages = client.completions.calls[0]["messages"]
    first_combined = " ".join(m["content"] for m in first_messages)
    assert len(combined) > len(first_combined)
    assert any(
        kw in combined for kw in ("錯誤", "error", "layout", "修正")
    ), combined


def test_non_dict_arguments_retries_then_succeeds(monkeypatch):
    # A model can emit valid JSON that is not an object (e.g. the string
    # "123"). Assigning data["type"] would raise TypeError; that must route
    # into the retry path, not escape.
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps("123")),  # arguments == '"123"'
            _make_response(json.dumps(_VALID_PRESENTATION)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_ir("x", "presentation")
    assert isinstance(result, Presentation)
    assert len(client.completions.calls) == 2


def test_no_tool_call_raises_runtime_error(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(None), _make_response(None)]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(RuntimeError, match="did not return a tool call"):
        backend.generate_ir("x", "presentation")
    assert len(client.completions.calls) == 2


def test_tool_choice_and_schema_sent(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_ir("x", "presentation")
    call = client.completions.calls[0]
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": "emit_document"},
    }
    assert call["tools"][0]["function"]["name"] == "emit_document"
    assert call["tools"][0]["function"]["parameters"] == Presentation.model_json_schema()


def test_get_backend_deepseek_requires_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        llm.get_backend("deepseek")


def test_get_backend_env_default(monkeypatch):
    _install_fake_openai(monkeypatch, [])
    monkeypatch.setenv("ODFORGE_BACKEND", "ollama")
    backend = llm.get_backend(None)
    assert isinstance(backend, llm.OpenAICompatBackend)
    assert "11434" in backend.base_url


def test_get_backend_unknown_raises(monkeypatch):
    with pytest.raises(ValueError, match="banana"):
        llm.get_backend("banana")


def test_unknown_doc_type_raises(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(ValueError, match="banana"):
        backend.generate_ir("x", "banana")
    # no API call should have been made
    assert len(client.completions.calls) == 0
