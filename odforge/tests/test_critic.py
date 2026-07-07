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
from pathlib import Path
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


# --- malformed payload degrades gracefully (skip-bad, keep-good; never raises) ---

# one valid finding + one bad-enum severity + one missing-fields finding
_MIXED_FINDINGS = [
    _TWO_FINDINGS[0],  # valid (slide_no 2, severity "error")
    {"slide_no": 3, "issue": "壞的嚴重度", "severity": "fatal", "fix_hint": "x"},  # bad enum
    {"slide_no": 4, "issue": "缺少欄位"},  # missing severity + fix_hint
]


def test_ollama_skips_malformed_findings_keeps_valid(monkeypatch, tmp_path):
    _install_fake_openai(monkeypatch, json.dumps({"findings": _MIXED_FINDINGS}))
    findings = critique(_make_pngs(tmp_path, 5), _ir(), backend="ollama")
    # only the valid finding survives; no ValidationError escapes critique()
    assert [f.slide_no for f in findings] == [2]
    assert all(isinstance(f, Finding) for f in findings)


def test_claude_skips_malformed_findings_keeps_valid(tmp_path):
    # valid one is slide 5, sandwiched between two malformed items
    payload = [
        {"slide_no": 1, "issue": "x", "severity": "oops", "fix_hint": "y"},  # bad enum
        _TWO_FINDINGS[1],  # valid (slide_no 5, severity "warn")
        {"issue": "缺頁碼與嚴重度"},  # missing fields
    ]
    backend = ClaudeVisionBackend(_FakeAnthropic(payload), "test-model")
    findings = backend.critique(_make_pngs(tmp_path, 5), _ir())
    assert [f.slide_no for f in findings] == [5]


def test_all_malformed_findings_yields_empty(monkeypatch, tmp_path):
    _install_fake_openai(monkeypatch, json.dumps({"findings": [_MIXED_FINDINGS[1]]}))
    assert critique(_make_pngs(tmp_path, 2), _ir(), backend="ollama") == []


def test_framing_omits_ir_title(monkeypatch, tmp_path):
    # independent context: the deck title must not leak into the sent framing
    client = _install_fake_openai(monkeypatch, json.dumps({"findings": []}))
    critique(_make_pngs(tmp_path, 3), _ir(), backend="ollama")
    (call,) = client.completions.calls
    user = next(m["content"] for m in call["messages"] if m["role"] == "user")
    text = next(b["text"] for b in user if b["type"] == "text")
    assert "光合作用" not in text  # ir.title must be absent
    assert "3 頁" in text  # slide count still present


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


# ===========================================================================
# QA loop (Task 16.3): render -> critique -> repair -> re-critique, bounded.
#
# Fully mocked: critic.render / critic.render_pages / critic.critique /
# critic.generate_slides are monkeypatched, so no soffice, no vision backend
# and no LLM are ever touched. The repair contract under test: only the pages
# flagged severity=="error" are regenerated and swapped back in place, so the
# unaffected pages never drift.
# ===========================================================================

from odforge.ir import Outline, PageRole  # noqa: E402
from odforge.preview import PreviewUnavailable  # noqa: E402


def _ir_n(n: int) -> Presentation:
    """A deck of ``n`` slides: page 1 is a title, pages 2..n are title-content."""
    slides = [Slide(layout="title", title="第1頁封面")]
    for i in range(2, n + 1):
        slides.append(Slide(layout="title-content", title=f"第{i}頁", bullets=["a", "b"]))
    return Presentation(title="測試簡報", slides=slides)


def _outline_n(n: int) -> Outline:
    """A matching page-role outline for :func:`_ir_n`."""
    pages = [PageRole(role="title", title="第1頁封面", gist="封面破題")]
    for i in range(2, n + 1):
        pages.append(PageRole(role="title-content", title=f"第{i}頁", gist=f"第{i}頁重點"))
    return Outline(mode="presenter", pages=pages)


def _err(slide_no: int, hint: str = "縮短標題") -> Finding:
    return Finding(slide_no=slide_no, issue="文字溢出", severity="error", fix_hint=hint)


def _warn(slide_no: int) -> Finding:
    return Finding(slide_no=slide_no, issue="版型單調", severity="warn", fix_hint="穿插分節")


def _install_loop_mocks(monkeypatch, critique_rounds, repair_record):
    """Wire the four loop seams to fakes.

    ``critique_rounds`` is an iterable of per-round findings lists (consumed in
    order). ``repair_record`` collects each sub-outline the repair path builds.
    """
    rounds = iter(critique_rounds)
    monkeypatch.setattr(critic, "render", lambda ir, out: Path(out))
    monkeypatch.setattr(
        critic, "render_pages", lambda odf, td, dpi=150: [Path(td) / "page-01.png"]
    )
    monkeypatch.setattr(critic, "critique", lambda pngs, ir, backend=None: next(rounds))

    def fake_generate_slides(sub_outline, backend=None):
        repair_record.append(sub_outline)
        return Presentation(
            title="修訂",
            slides=[
                Slide(layout=p.role, title=f"修好了-{p.title}", bullets=["x"])
                for p in sub_outline.pages
            ],
        )

    monkeypatch.setattr(critic, "generate_slides", fake_generate_slides)


# ---- ① first round 2 errors -> repair regenerates ONLY those 2 pages -------


def test_repair_regenerates_only_flagged_slides(tmp_path, monkeypatch):
    ir = _ir_n(5)
    outline = _outline_n(5)
    repairs: list = []
    # Round 1: errors on slides 2 and 5. Round 2: clean.
    _install_loop_mocks(
        monkeypatch,
        critique_rounds=[[_err(2, "縮短標題"), _err(5, "移開重疊")], []],
        repair_record=repairs,
    )

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=outline, max_rounds=2)

    # The repair ran exactly once and regenerated exactly the two flagged pages.
    assert len(repairs) == 1
    sub = repairs[0]
    assert len(sub.pages) == 2
    assert [p.title for p in sub.pages] == ["第2頁", "第5頁"]
    # The fix_hint from each finding is folded into the sub-outline's gist.
    assert "縮短標題" in sub.pages[0].gist
    assert "移開重疊" in sub.pages[1].gist

    # Only the flagged slides changed; the other three pages did not drift.
    assert ir.slides[0].title == "第1頁封面"
    assert ir.slides[2].title == "第3頁"
    assert ir.slides[3].title == "第4頁"
    assert ir.slides[1].title.startswith("修好了")
    assert ir.slides[4].title.startswith("修好了")

    assert report.final_ok is True
    assert report.rounds == 2


# ---- ② second round returns 0 findings -> stop at round 2, final_ok=True ---


def test_loop_stops_when_second_round_clean(tmp_path, monkeypatch):
    ir = _ir_n(3)
    repairs: list = []
    _install_loop_mocks(
        monkeypatch,
        critique_rounds=[[_err(2)], []],  # round 1 error, round 2 clean
        repair_record=repairs,
    )

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=_outline_n(3))

    assert report.rounds == 2
    assert report.final_ok is True
    assert len(repairs) == 1  # repaired once, after round 1
    assert [len(r) for r in report.findings_by_round] == [1, 0]


# ---- ③ always an error -> stop at max_rounds, final_ok=False (no infinite) --


def test_loop_bounded_by_max_rounds_when_errors_persist(tmp_path, monkeypatch):
    ir = _ir_n(3)
    repairs: list = []
    # Critique keeps flagging an error forever; the loop must still terminate.
    _install_loop_mocks(
        monkeypatch,
        critique_rounds=[[_err(2)], [_err(2)], [_err(2)], [_err(2)]],
        repair_record=repairs,
    )

    report = critic.run_qa_loop(
        ir, tmp_path / "out.odp", outline=_outline_n(3), max_rounds=2
    )

    assert report.rounds == 2
    assert report.final_ok is False
    # Repair runs only between rounds: once after round 1, NOT after the final
    # round (we detect the cap and stop rather than regenerate again).
    assert len(repairs) == 1


# ---- warn-only findings are not errors: stop round 1, final_ok=True --------


def test_warn_only_findings_stop_without_repair(tmp_path, monkeypatch):
    ir = _ir_n(3)
    repairs: list = []
    _install_loop_mocks(
        monkeypatch, critique_rounds=[[_warn(2), _warn(3)]], repair_record=repairs
    )

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=_outline_n(3))

    assert report.rounds == 1
    assert report.final_ok is True
    assert repairs == []  # nothing regenerated for warn-only


# ---- empty critique (off backend degrades to []) stops round 1 ------------


def test_empty_critique_stops_round_one(tmp_path, monkeypatch):
    ir = _ir_n(3)
    repairs: list = []
    _install_loop_mocks(monkeypatch, critique_rounds=[[]], repair_record=repairs)

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=_outline_n(3))

    assert report.rounds == 1
    assert report.final_ok is True
    assert repairs == []


# ---- preview unavailable (no soffice) degrades, never crashes -------------


def test_preview_unavailable_degrades_gracefully(tmp_path, monkeypatch):
    ir = _ir_n(3)
    monkeypatch.setattr(critic, "render", lambda ir, out: Path(out))

    def boom(odf, td, dpi=150):
        raise PreviewUnavailable("no soffice")

    monkeypatch.setattr(critic, "render_pages", boom)
    # critique must never be reached; make it explode if it is.
    monkeypatch.setattr(
        critic,
        "critique",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("critique reached")),
    )

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=_outline_n(3))

    assert report.rounds == 0
    assert report.final_ok is True
    assert report.note  # a human-readable note explains the degrade
    assert report.findings_by_round == []


# ---- repair without an outline: PageRole is derived from the slide itself --


def test_repair_without_outline_derives_pageroles(tmp_path, monkeypatch):
    ir = _ir_n(4)
    repairs: list = []
    _install_loop_mocks(
        monkeypatch, critique_rounds=[[_err(3, "加大字級")], []], repair_record=repairs
    )

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=None, max_rounds=2)

    assert len(repairs) == 1
    (sub,) = repairs
    assert len(sub.pages) == 1
    # role derived from the flagged slide's own layout; hint folded into gist.
    assert sub.pages[0].role == "title-content"
    assert "加大字級" in sub.pages[0].gist
    assert report.final_ok is True
    assert ir.slides[2].title.startswith("修好了")


# ---- out-of-range error slide_no cannot be repaired: bounded, final_ok=False


def test_out_of_range_error_does_not_loop_forever(tmp_path, monkeypatch):
    ir = _ir_n(3)
    repairs: list = []
    # An error that references a non-existent page 99 — unrepairable.
    _install_loop_mocks(
        monkeypatch, critique_rounds=[[_err(99)], [_err(99)]], repair_record=repairs
    )

    report = critic.run_qa_loop(
        ir, tmp_path / "out.odp", outline=_outline_n(3), max_rounds=2
    )

    assert report.final_ok is False
    assert repairs == []  # never tried to regenerate a page that doesn't exist


# ---- QAReport carries per-round findings for the CLI summary --------------


def test_qareport_records_findings_by_round(tmp_path, monkeypatch):
    ir = _ir_n(4)
    repairs: list = []
    _install_loop_mocks(
        monkeypatch,
        critique_rounds=[[_err(2), _warn(3)], []],
        repair_record=repairs,
    )

    report = critic.run_qa_loop(ir, tmp_path / "out.odp", outline=_outline_n(4))

    assert len(report.findings_by_round) == 2
    first = report.findings_by_round[0]
    assert sum(1 for f in first if f.severity == "error") == 1
    assert sum(1 for f in first if f.severity == "warn") == 1
    assert report.findings_by_round[1] == []
