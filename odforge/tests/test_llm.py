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
from odforge.ir import BulletItem, MediaAssetRef, Outline, PageRole, Presentation


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

_VALID_DISCOVERY = {
    "summary": "向系上老師報告畢業專題進度，聚焦架構與時程。",
    "known_context": ["受眾是系上老師", "內容包含系統架構與時程"],
    "questions": [
        {
            "id": "decision",
            "question": "這次希望老師提供什麼？",
            "why": "決定簡報最後要收束到哪個行動。",
            "options": ["確認進度", "提供技術建議", "核准下一階段"],
        },
        {
            "id": "progress",
            "question": "目前完成到哪個階段？",
            "why": "才能把時程與風險說具體。",
            "options": ["規劃完成", "核心功能開發中", "進入測試"],
        },
    ],
    "completeness": 42,
}


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


def test_discovery_questions_use_outline_model_and_structured_schema(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_DISCOVERY))]
    )
    backend = llm.OpenAICompatBackend(
        "https://example.test",
        "tok",
        "base",
        outline_model="planner",
        extra_body={"enable_thinking": False},
    )

    result = backend.discover_questions(
        "畢業專題進度報告", context="模式：講者型；目標頁數：8 頁"
    )

    assert result.completeness == 42
    assert result.questions[0].id == "decision"
    call = client.completions.calls[0]
    assert call["model"] == "planner"
    assert call["tool_choice"]["function"]["name"] == llm.DISCOVERY_TOOL_NAME
    assert (
        call["tools"][0]["function"]["parameters"]
        == llm.DiscoveryPlan.model_json_schema()
    )
    assert call["extra_body"] == {"enable_thinking": False}
    user = " ".join(
        message["content"]
        for message in call["messages"]
        if message["role"] == "user"
    )
    assert "畢業專題進度報告" in user
    assert "8 頁" in user


def test_discovery_questions_can_use_dedicated_model_and_report_progress(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_DISCOVERY))]
    )
    stages = []
    backend = llm.OpenAICompatBackend(
        "https://example.test",
        "tok",
        "base",
        discovery_model="fast-reader",
        outline_model="planner",
    )

    backend.discover_questions("畢業專題", progress=stages.append)

    assert client.completions.calls[0]["model"] == "fast-reader"
    assert stages == ["preparing", "requesting", "validating", "complete"]


def test_discovery_invalid_payload_retries_with_feedback(monkeypatch):
    invalid = {**_VALID_DISCOVERY, "questions": []}
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(invalid)),
            _make_response(json.dumps(_VALID_DISCOVERY)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")

    result = backend.discover_questions("x")

    assert len(result.questions) == 2
    assert len(client.completions.calls) == 2
    retry_text = " ".join(
        message["content"]
        for message in client.completions.calls[1]["messages"]
    )
    assert "questions" in retry_text


def test_discovery_question_removes_other_and_duplicate_options():
    question = llm.DiscoveryQuestion(
        id="topic",
        question="專題屬於哪個領域？",
        why="用來安排內容。",
        options=["Web 應用", "其他領域", "Web 應用", "資料分析"],
    )
    assert question.options == ["Web 應用", "資料分析"]


def test_prompts_forbid_invented_technology_stack_and_probe_architecture_gap():
    assert "技術棧" in llm.SLIDES_SYSTEM_PROMPT
    assert "所有英文技術名與品牌名" in llm.SLIDES_SYSTEM_PROMPT
    assert "系統架構" in llm.DISCOVERY_SYSTEM_PROMPT
    assert "實際技術棧" in llm.DISCOVERY_SYSTEM_PROMPT


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


def test_malformed_json_repaired(monkeypatch):
    # DeepSeek field failure: unquoted CJK-bracket-initial string value.
    malformed = (
        '{"title": "T", "theme": "academic", "slides": '
        '[{"layout": "title", "title": "封面", "notes": 【開場】大家好}]}'
    )
    client = _install_fake_openai(monkeypatch, [_make_response(malformed)])
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_ir("x", "presentation")
    # repaired in-place: no second API call
    assert len(client.completions.calls) == 1
    assert isinstance(result, Presentation)
    assert "開場" in result.slides[0].notes


def test_repair_failure_still_retries(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response("@@@@"),  # hopeless garbage: repair yields non-dict
            _make_response(json.dumps(_VALID_PRESENTATION)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_ir("x", "presentation")
    assert isinstance(result, Presentation)
    assert len(client.completions.calls) == 2


def test_max_tokens_default_sent(monkeypatch):
    monkeypatch.delenv("ODFORGE_MAX_TOKENS", raising=False)
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_ir("x", "presentation")
    assert client.completions.calls[0]["max_tokens"] == 8192


def test_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("ODFORGE_MAX_TOKENS", "4000")
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_ir("x", "presentation")
    assert client.completions.calls[0]["max_tokens"] == 4000


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


# ---------------------------------------------------------------------------
# Task 15.1 — generate_outline (stage-1: design + page-role outline)
# ---------------------------------------------------------------------------

# A palette whose contrasts all clear WCAG against a white bg (reused from ir).
_GOOD_PALETTE = {
    "bg": "#FFFFFF",
    "surface": "#F5F5F5",
    "text": "#1A1A1A",
    "muted": "#6B7280",
    "accent": "#2563EB",
}
_GOOD_FONTS = {"display": "Noto Serif TC", "body": "Noto Sans TC"}

_VALID_OUTLINE = {
    "mode": "presenter",
    "design": {
        "palette": _GOOD_PALETTE,
        "fonts": _GOOD_FONTS,
        "scale": "display",
        "mode": "presenter",
    },
    "pages": [
        {"role": "title", "title": "光合作用", "gist": "開場,點出主題"},
        {"role": "agenda", "title": "本日大綱", "gist": "三個段落預告"},
        {"role": "section", "title": "反應原理", "gist": "進入第一節"},
        {"role": "closing", "title": "結語", "gist": "回顧與提問"},
    ],
}

# Design fails contrast (text #EEEEEE on #FFFFFF ≈ 1.1:1 < 4.5) but the pages
# are perfectly valid → a *design-only* failure.
_BAD_DESIGN_OUTLINE = {
    "mode": "presenter",
    "design": {
        "palette": {
            "bg": "#FFFFFF",
            "surface": "#FFFFFF",
            "text": "#EEEEEE",
            "muted": "#F0F0F0",
            "accent": "#FAFAFA",
        },
        "fonts": _GOOD_FONTS,
        "scale": "standard",
        "mode": "presenter",
    },
    "pages": [
        {"role": "title", "title": "標題", "gist": "開場"},
        {"role": "closing", "title": "結語", "gist": "收尾"},
    ],
}

# Design is fine; a page carries an unknown role → a *pages-invalid* failure
# that survives dropping "design", so it must retry-then-raise (not be tolerated).
_INVALID_PAGES_OUTLINE = {
    "mode": "presenter",
    "design": {
        "palette": _GOOD_PALETTE,
        "fonts": _GOOD_FONTS,
        "scale": "standard",
        "mode": "presenter",
    },
    "pages": [{"role": "banana", "title": "壞版型", "gist": "x"}],
}


def test_outline_model_defaults():
    o = Outline.model_validate(
        {"pages": [{"role": "title", "title": "封面", "gist": "開場"}]}
    )
    assert o.mode == "presenter"  # default when omitted
    assert o.design is None  # optional
    assert isinstance(o.pages[0], PageRole)
    # JSON-serializable for the front-end.
    assert json.loads(o.model_dump_json())["pages"][0]["role"] == "title"


def test_outline_requires_at_least_one_page():
    with pytest.raises(ValidationError):
        Outline.model_validate({"pages": []})


# ① fake tool_call returning valid Outline JSON → Outline instance
def test_generate_outline_valid_roundtrip(monkeypatch):
    backend = _backend(monkeypatch, [_make_response(json.dumps(_VALID_OUTLINE))])
    result = backend.generate_outline("做一份光合作用簡報")
    assert isinstance(result, Outline)
    assert result.mode == "presenter"
    assert result.design is not None
    assert result.design.scale == "display"
    assert [p.role for p in result.pages] == ["title", "agenda", "section", "closing"]
    assert result.source_prompt == "做一份光合作用簡報"


# ② design contrast failure → retry once (call count 2); second failure →
#    design is None and no exception is raised
def test_generate_outline_design_failure_strips_design(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_BAD_DESIGN_OUTLINE)),
            _make_response(json.dumps(_BAD_DESIGN_OUTLINE)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_outline("x")
    assert isinstance(result, Outline)
    assert result.design is None  # bad palette stripped → preset fallback
    assert len(result.pages) == 2  # the rest of the outline survived
    assert len(client.completions.calls) == 2  # a retry did happen
    # the retry must have fed the contrast error back to the model
    second = client.completions.calls[1]["messages"]
    combined = " ".join(m["content"] for m in second)
    assert "contrast" in combined


def test_generate_outline_design_recovers_on_retry(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_BAD_DESIGN_OUTLINE)),
            _make_response(json.dumps(_VALID_OUTLINE)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_outline("x")
    assert result.design is not None  # second (good) design accepted
    assert len(client.completions.calls) == 2


def test_generate_outline_missing_gist_retries_then_succeeds(monkeypatch):
    # gist is required (and non-blank): a page omitting it, or emitting "", is
    # a pages-invalid failure → error fed back (mentioning gist), retry once,
    # and a valid second answer is accepted.
    missing_gist = {
        "mode": "presenter",
        "design": {
            "palette": _GOOD_PALETTE,
            "fonts": _GOOD_FONTS,
            "scale": "standard",
            "mode": "presenter",
        },
        "pages": [
            {"role": "title", "title": "封面"},  # gist missing
            {"role": "closing", "title": "結語", "gist": ""},  # gist blank
        ],
    }
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(missing_gist)),
            _make_response(json.dumps(_VALID_OUTLINE)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_outline("x")
    assert isinstance(result, Outline)
    assert all(p.gist for p in result.pages)
    assert len(client.completions.calls) == 2
    # the retry must carry the gist validation error back to the model
    second = client.completions.calls[1]["messages"]
    combined = " ".join(m["content"] for m in second)
    assert "gist" in combined


def test_generate_outline_invalid_pages_retries_then_raises(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_INVALID_PAGES_OUTLINE)),
            _make_response(json.dumps(_INVALID_PAGES_OUTLINE)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(ValidationError):
        backend.generate_outline("x")
    assert len(client.completions.calls) == 2  # normal retry-once-then-raise


# ③ schema assertion: tools param == Outline.model_json_schema(); tool_choice
#    forces the function
def test_generate_outline_tool_choice_and_schema_sent(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_OUTLINE))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_outline("x")
    call = client.completions.calls[0]
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": llm.OUTLINE_TOOL_NAME},
    }
    assert call["tools"][0]["function"]["name"] == llm.OUTLINE_TOOL_NAME
    assert call["tools"][0]["function"]["parameters"] == Outline.model_json_schema()


def test_generate_outline_no_tool_call_raises(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(None), _make_response(None)]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(RuntimeError, match="did not return a tool call"):
        backend.generate_outline("x")
    assert len(client.completions.calls) == 2


def test_generate_outline_facade_uses_backend(monkeypatch):
    # ollama backend needs no API key; OpenAI is patched to the fake client.
    _install_fake_openai(monkeypatch, [_make_response(json.dumps(_VALID_OUTLINE))])
    monkeypatch.setenv("ODFORGE_BACKEND", "ollama")
    result = llm.generate_outline("x")
    assert isinstance(result, Outline)
    assert result.design is not None


def test_generate_outline_pages_hint_in_prompt(monkeypatch):
    # A target page count is folded into the stage-1 user prompt.
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_OUTLINE))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_outline("做一份簡報", pages=12)
    user = " ".join(
        m["content"]
        for m in client.completions.calls[0]["messages"]
        if m["role"] == "user"
    )
    assert "12" in user
    assert "頁" in user


def test_generate_outline_no_pages_no_hint(monkeypatch):
    # Without pages, no page-count instruction is injected.
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_OUTLINE))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_outline("做一份簡報")
    user = " ".join(
        m["content"]
        for m in client.completions.calls[0]["messages"]
        if m["role"] == "user"
    )
    assert "目標頁數" not in user


def test_generate_outline_facade_forwards_pages(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_OUTLINE))]
    )
    monkeypatch.setenv("ODFORGE_BACKEND", "ollama")
    llm.generate_outline("x", pages=7)
    user = " ".join(
        m["content"]
        for m in client.completions.calls[0]["messages"]
        if m["role"] == "user"
    )
    assert "7" in user


# ---------------------------------------------------------------------------
# Task 15.2 — generate_slides (stage-2: fill pages + layout-budget feedback)
# ---------------------------------------------------------------------------

# A two-page outline (no design → preset "academic"): title + title-content.
_SLIDES_OUTLINE = Outline.model_validate(
    {
        "mode": "presenter",
        "pages": [
            {"role": "title", "title": "光合作用", "gist": "開場,點出主題"},
            {"role": "title-content", "title": "反應原理", "gist": "光反應與暗反應"},
        ],
    }
)

# Same outline but carrying a validated DesignSpec (to prove design carries over).
_SLIDES_OUTLINE_DESIGNED = Outline.model_validate(
    {
        "mode": "presenter",
        "design": {
            "palette": _GOOD_PALETTE,
            "fonts": _GOOD_FONTS,
            "scale": "display",
            "mode": "presenter",
        },
        "pages": [
            {"role": "title", "title": "光合作用", "gist": "開場"},
            {"role": "title-content", "title": "反應原理", "gist": "兩階段"},
        ],
    }
)

# A structurally matching deck whose text comfortably fits (no design in payload —
# the outline's design, if any, is re-attached by generate_slides).
_FITTING_DECK = {
    "title": "光合作用入門",
    "slides": [
        {"layout": "title", "title": "光合作用", "subtitle": "生物", "notes": "開場白"},
        {
            "layout": "title-content",
            "title": "反應原理",
            "bullets": ["光反應", "暗反應"],
            "notes": "講述兩階段",
        },
    ],
}

# 48-CJK-char bullet × 10 → the title-content page overruns its 11cm frame.
# Page 1 carries a distinctive subtitle that exists ONLY in this deck (never in
# the outline or any prompt text) so tests can detect the prior deck being echoed
# back to the model in the budget-retry user turn.
_LONG_BULLET = "版面預算測試" * 8  # 6 chars × 8 = 48 chars
_PRIOR_DECK_MARKER = "高一生物講義"
_OVERLOADED_DECK = {
    "title": "光合作用入門",
    "slides": [
        {
            "layout": "title",
            "title": "光合作用",
            "subtitle": _PRIOR_DECK_MARKER,
            "notes": "開場",
        },
        {
            "layout": "title-content",
            "title": "反應原理",
            "bullets": [_LONG_BULLET] * 10,
            "notes": "細節",
        },
    ],
}

# Structurally matching count, but the first slide's layout is wrong (section vs
# the outline's title role) → a structural mismatch, must retry-then-raise.
_WRONG_LAYOUT_DECK = {
    "title": "壞版型",
    "slides": [
        {"layout": "section", "title": "光合作用", "notes": "n"},
        {"layout": "title-content", "title": "反應原理", "bullets": ["a"], "notes": "n"},
    ],
}

# A single title-content bullet with 8 children → overruns; dropping children
# (not the whole item) is enough to make it fit.
_CHILD_HEAVY_DECK = {
    "title": "巢狀",
    "slides": [
        {"layout": "title", "title": "光合作用", "notes": "n"},
        {
            "layout": "title-content",
            "title": "細節",
            "bullets": [
                {
                    "text": "主要要點",
                    "children": [
                        "子項一",
                        "子項二",
                        "子項三",
                        "子項四",
                        "子項五",
                        "子項六",
                        "子項七",
                        "子項八",
                    ],
                }
            ],
            "notes": "n",
        },
    ],
}


def _user_text(messages) -> str:
    """Join only the role=='user' message contents (assert on user turns only)."""
    return " ".join(m["content"] for m in messages if m["role"] == "user")


# ① valid return → Presentation, design carried from the Outline
def test_generate_slides_carries_design_from_outline(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_FITTING_DECK))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_slides(_SLIDES_OUTLINE_DESIGNED)
    assert isinstance(result, Presentation)
    assert [s.layout for s in result.slides] == ["title", "title-content"]
    # design comes from the outline, not from the (design-less) payload
    assert result.design is not None
    assert result.design.scale == "display"
    assert result.design.palette.accent == _GOOD_PALETTE["accent"]
    assert len(client.completions.calls) == 1  # fits first try, no retry


def test_generate_slides_valid_roundtrip_no_design(monkeypatch):
    backend = _backend(monkeypatch, [_make_response(json.dumps(_FITTING_DECK))])
    result = backend.generate_slides(_SLIDES_OUTLINE)
    assert isinstance(result, Presentation)
    assert result.design is None  # outline had none → preset fallback
    assert [s.layout for s in result.slides] == ["title", "title-content"]


# ② one page over budget → second call's USER message names the page and 超載,
#    and echoes the prior deck so 「其餘頁面照抄」 is a keepable instruction
def test_generate_slides_over_budget_feeds_back_page(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_OVERLOADED_DECK)),  # page 2 overruns
            _make_response(json.dumps(_FITTING_DECK)),  # shortened deck fits
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_slides(_SLIDES_OUTLINE)
    assert len(client.completions.calls) == 2  # exactly one budget retry
    combined = _user_text(client.completions.calls[1]["messages"])
    assert "超載" in combined
    assert "第 2 頁" in combined
    # the retry turn must carry the prior deck (marker lives only in that deck's
    # unaffected page 1) — a stateless model can't "keep the rest" otherwise
    assert _PRIOR_DECK_MARKER in combined
    # the fitting retry deck is what we return
    assert result.slides[1].bullets == ["光反應", "暗反應"]
    # ...and neither 超載 nor the prior deck leaks into the *first* user turn
    first_user = _user_text(client.completions.calls[0]["messages"])
    assert "超載" not in first_user
    assert _PRIOR_DECK_MARKER not in first_user


# ③ both attempts over budget → the offending page's bullets are truncated and
#    a note records the omission (never raises for budget)
def test_generate_slides_both_over_budget_degrades(monkeypatch):
    from odforge.textmetrics import check_budget
    from odforge.themes import resolve_design

    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_OVERLOADED_DECK)),
            _make_response(json.dumps(_OVERLOADED_DECK)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_slides(_SLIDES_OUTLINE)
    assert len(client.completions.calls) == 2  # one budget retry, no more
    slide = result.slides[1]
    assert 1 <= len(slide.bullets) < 10  # truncated from the end, ≥1 kept
    assert "部分要點因版面限制省略" in slide.notes
    # degradation actually resolved the overflow
    assert check_budget(slide, resolve_design(result)) == []


def test_generate_slides_degrade_drops_children_first(monkeypatch):
    _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_CHILD_HEAVY_DECK)),
            _make_response(json.dumps(_CHILD_HEAVY_DECK)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_slides(_SLIDES_OUTLINE)
    slide = result.slides[1]
    # whole item kept; only a child was shed (children dropped before items)
    assert len(slide.bullets) == 1
    assert isinstance(slide.bullets[0], BulletItem)
    assert len(slide.bullets[0].children) < 8
    assert "部分要點因版面限制省略" in slide.notes


# layout-mismatch: retry once, then raise
def test_generate_slides_layout_mismatch_retries_then_raises(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_WRONG_LAYOUT_DECK)),
            _make_response(json.dumps(_WRONG_LAYOUT_DECK)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(RuntimeError):
        backend.generate_slides(_SLIDES_OUTLINE)
    assert len(client.completions.calls) == 2
    # the retry fed the mismatch back on the user turn
    combined = _user_text(client.completions.calls[1]["messages"])
    assert "版型" in combined and "第 1 頁" in combined


def test_generate_slides_layout_mismatch_recovers_on_retry(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_WRONG_LAYOUT_DECK)),
            _make_response(json.dumps(_FITTING_DECK)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_slides(_SLIDES_OUTLINE)
    assert [s.layout for s in result.slides] == ["title", "title-content"]
    assert len(client.completions.calls) == 2


def test_generate_slides_count_mismatch_retries_then_raises(monkeypatch):
    one_slide = {
        "title": "少一張",
        "slides": [{"layout": "title", "title": "光合作用", "notes": "n"}],
    }
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(one_slide)),
            _make_response(json.dumps(one_slide)),
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(RuntimeError):
        backend.generate_slides(_SLIDES_OUTLINE)
    assert len(client.completions.calls) == 2


def test_generate_slides_no_tool_call_raises(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(None), _make_response(None)]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    with pytest.raises(RuntimeError):
        backend.generate_slides(_SLIDES_OUTLINE)
    assert len(client.completions.calls) == 2


def test_generate_slides_tool_choice_and_schema_sent(monkeypatch):
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_FITTING_DECK))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_slides(_SLIDES_OUTLINE)
    call = client.completions.calls[0]
    assert call["tool_choice"] == {
        "type": "function",
        "function": {"name": llm.SLIDES_TOOL_NAME},
    }
    assert call["tools"][0]["function"]["name"] == llm.SLIDES_TOOL_NAME
    assert call["tools"][0]["function"]["parameters"] == Presentation.model_json_schema()


def test_generate_slides_max_tokens_default_16384(monkeypatch):
    monkeypatch.delenv("ODFORGE_MAX_TOKENS", raising=False)
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_FITTING_DECK))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_slides(_SLIDES_OUTLINE)
    assert client.completions.calls[0]["max_tokens"] == 16384


def test_generate_slides_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("ODFORGE_MAX_TOKENS", "5000")
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_FITTING_DECK))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_slides(_SLIDES_OUTLINE)
    assert client.completions.calls[0]["max_tokens"] == 5000


def test_generate_ir_still_defaults_8192(monkeypatch):
    # the 16384 bump is stage-2 only: generate_ir must stay untouched at 8192.
    monkeypatch.delenv("ODFORGE_MAX_TOKENS", raising=False)
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_VALID_PRESENTATION))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_ir("x", "presentation")
    assert client.completions.calls[0]["max_tokens"] == 8192


def test_generate_slides_facade_uses_backend(monkeypatch):
    _install_fake_openai(monkeypatch, [_make_response(json.dumps(_FITTING_DECK))])
    monkeypatch.setenv("ODFORGE_BACKEND", "ollama")
    result = llm.generate_slides(_SLIDES_OUTLINE)
    assert isinstance(result, Presentation)
    assert [s.layout for s in result.slides] == ["title", "title-content"]


# agenda pages render from Slide.bullets (there is no Slide.items field) —
# content returned there must survive parsing and reach the final deck
def test_generate_slides_agenda_content_in_bullets_survives(monkeypatch):
    agenda_outline = Outline.model_validate(
        {
            "mode": "presenter",
            "pages": [
                {"role": "title", "title": "光合作用", "gist": "開場"},
                {"role": "agenda", "title": "本日大綱", "gist": "預告三節"},
            ],
        }
    )
    agenda_deck = {
        "title": "光合作用",
        "slides": [
            {"layout": "title", "title": "光合作用", "notes": "n"},
            {
                "layout": "agenda",
                "title": "本日大綱",
                "bullets": ["反應原理", "影響因素", "生活應用"],
                "notes": "n",
            },
        ],
    }
    backend = _backend(monkeypatch, [_make_response(json.dumps(agenda_deck))])
    result = backend.generate_slides(agenda_outline)
    assert result.slides[1].layout == "agenda"
    assert result.slides[1].bullets == ["反應原理", "影響因素", "生活應用"]


def test_slides_prompt_layout_lines_name_only_real_fields():
    # The per-layout guidance must only name fields the Slide schema actually
    # has (plus BulletItem / ChartSpec / PageRole sub-fields and layout names) —
    # a hallucinated field like "items" is silently dropped by pydantic and the
    # page ships empty. In particular, agenda content goes in "bullets".
    import re

    from odforge.ir import ChartSpec, PageRole, Slide

    prompt = llm.SLIDES_SYSTEM_PROMPT
    section = prompt.split("【各版型填寫要點】")[1].split("【")[0]
    agenda_line = next(
        line for line in section.splitlines() if line.strip().startswith("- agenda")
    )
    assert "bullets" in agenda_line
    layouts = {
        "title", "agenda", "section", "title-content", "two-col",
        "comparison", "big-fact", "quote", "chart", "closing",
    }
    allowed = (
        layouts
        | set(Slide.model_fields)
        | set(BulletItem.model_fields)
        | set(ChartSpec.model_fields)
        | set(PageRole.model_fields)
    )
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9-]*", section))
    assert tokens <= allowed, f"non-schema field names in prompt: {tokens - allowed}"
    assert "items" not in tokens  # Slide has no "items" field


# worst case: structural retry (call 2) then budget retry (call 3) — 3 calls total
def test_generate_slides_worst_case_three_calls(monkeypatch):
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(_WRONG_LAYOUT_DECK)),  # 1: layout mismatch
            _make_response(json.dumps(_OVERLOADED_DECK)),  # 2: valid but overloaded
            _make_response(json.dumps(_FITTING_DECK)),  # 3: shortened, fits
        ],
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    result = backend.generate_slides(_SLIDES_OUTLINE)
    assert len(client.completions.calls) == 3
    assert [s.layout for s in result.slides] == ["title", "title-content"]
    assert result.slides[1].bullets == ["光反應", "暗反應"]
    assert "部分要點因版面限制省略" not in result.slides[1].notes  # no degrade
    # call 2's user turn carries the structural feedback, call 3's the budget one
    assert "版型" in _user_text(client.completions.calls[1]["messages"])
    assert "超載" in _user_text(client.completions.calls[2]["messages"])


def test_stage2_prompt_keeps_source_and_visual_intent(monkeypatch):
    outline = _SLIDES_OUTLINE.model_copy(
        update={
            "source_prompt": "受眾是醫院主管，重點是降低等候時間。",
            "pages": [
                _SLIDES_OUTLINE.pages[0],
                _SLIDES_OUTLINE.pages[1].model_copy(
                    update={"visual_intent": "用兩個階段呈現前後因果"}
                ),
            ],
        }
    )
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_FITTING_DECK))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_slides(outline)
    user_text = _user_text(client.completions.calls[0]["messages"])
    assert "醫院主管" in user_text
    assert "降低等候時間" in user_text
    assert "用兩個階段呈現前後因果" in user_text


def test_stage2_prompt_lists_only_app_owned_image_ids(monkeypatch):
    outline = _SLIDES_OUTLINE.model_copy(
        update={
            "media_assets": [
                MediaAssetRef(
                    id="asset-01",
                    description="改善後的候診區",
                    credit="院方提供",
                )
            ],
            "image_generation_available": True,
        }
    )
    client = _install_fake_openai(
        monkeypatch, [_make_response(json.dumps(_FITTING_DECK))]
    )
    backend = llm.OpenAICompatBackend("https://example.test", "tok", "m")
    backend.generate_slides(outline)
    user_text = _user_text(client.completions.calls[0]["messages"])
    assert "asset://asset-01" in user_text
    assert "改善後的候診區" in user_text
    assert "院方提供" in user_text
    assert "圖片生成】可用" in user_text


def test_outline_and_slides_can_use_different_models(monkeypatch):
    outline_payload = {
        "mode": "presenter",
        "pages": [{"role": "title", "title": "封面", "gist": "破題"}],
    }
    deck_payload = {
        "title": "測試",
        "slides": [{"layout": "title", "title": "封面", "notes": "開場"}],
    }
    client = _install_fake_openai(
        monkeypatch,
        [
            _make_response(json.dumps(outline_payload)),
            _make_response(json.dumps(deck_payload)),
        ],
    )
    backend = llm.OpenAICompatBackend(
        "https://example.test",
        "tok",
        "fallback",
        outline_model="planner",
        slides_model="writer",
        extra_body={"enable_thinking": False},
    )
    outline = backend.generate_outline("題目")
    backend.generate_slides(outline)
    assert [call["model"] for call in client.completions.calls] == [
        "planner",
        "writer",
    ]
    assert all(
        call["extra_body"] == {"enable_thinking": False}
        for call in client.completions.calls
    )


def test_custom_extra_body_requires_json_object(monkeypatch):
    monkeypatch.setenv("ODFORGE_CUSTOM_EXTRA_BODY", '{"enable_thinking":false}')
    assert llm._custom_extra_body() == {"enable_thinking": False}

    monkeypatch.setenv("ODFORGE_CUSTOM_EXTRA_BODY", "[]")
    with pytest.raises(RuntimeError, match="JSON object"):
        llm._custom_extra_body()
