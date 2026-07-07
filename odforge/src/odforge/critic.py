"""ODForge vision-model design critic — the 第四道閘 (design gate).

Given rendered slide PNGs (from :mod:`odforge.preview`), an *independent-context*
vision model returns structured findings — 文字溢出/截斷、元素重疊、對比不足、
對齊歪斜、版型連續重複、留白失衡 — that deterministic checks (textmetrics,
contrast validation) cannot see. This is the qualitative half of the QA harness.

The vision-backend abstraction mirrors :mod:`odforge.llm`'s registry pattern
(``VISION_BACKENDS = {name: factory}`` + env-var selection). Two real backends
are provided:

* ``claude``  — the anthropic SDK's Messages API with base64 images and a forced
  tool call (structured output). ``anthropic`` is imported **lazily**, inside the
  backend factory, so its absence never breaks importing this module or the
  ``off`` / ``ollama`` code paths (tests run without it installed).
* ``ollama`` — an OpenAI-compatible chat endpoint (already a hard dependency) with
  base64 ``image_url`` blocks and forced function calling.

The default backend is ``off``: :func:`critique` returns ``[]`` and constructs no
client, so the QA loop degrades gracefully to deterministic-only checks rather
than crashing when no vision model is configured.

API-key material is read from environment variables only, never hard-coded. No
real network calls are made in tests — the clients are monkeypatched.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Protocol, runtime_checkable

from openai import OpenAI
from pydantic import BaseModel

from odforge.ir import Presentation

# ---------------------------------------------------------------------------
# Finding — the structured output the critic produces.
#
# Choice: a pydantic ``BaseModel`` (not a dataclass). The vision model returns a
# JSON object per finding; ``Finding.model_validate`` validates it directly
# (including the ``severity`` enum) with no manual parsing, and drives the JSON
# schema handed to the model as a tool definition.
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """One design problem the vision critic spotted on a rendered slide."""

    slide_no: int
    issue: str
    severity: Literal["error", "warn"]
    fix_hint: str


# The design checklist (from the Claude pptx skill), zh-TW. Sent as the *only*
# framing (a system prompt) alongside the images — no generation history — so
# the critic judges purely from what it sees, in a context independent of the
# pipeline that produced the deck.
CHECKLIST = """\
你是 ODForge 的簡報設計評審,負責「第四道閘」——只依據算圖後的投影片影像本身,
逐頁挑出設計問題。請對照以下檢查清單,不要臆測影像以外的內容:

1. 文字溢出/截斷:文字超出版面邊界,或被邊框、其他元素裁切。
2. 元素重疊:文字、圖形或色塊互相覆蓋,造成閱讀困難。
3. 對比不足:文字與背景對比過低,難以辨識。
4. 對齊歪斜:元素未對齊、參差不齊或明顯歪斜。
5. 版型連續重複:多頁版面單調,連續重複同一種版型、缺乏節奏。
6. 留白失衡:留白過多或過少,畫面重心失衡。

每偵測到一個問題,就輸出一筆 Finding:
- slide_no:出問題的頁碼(從 1 開始)。
- issue:問題描述(繁體中文,簡潔具體)。
- severity:"error"(嚴重、必須修正)或 "warn"(建議、尚可容忍)。
- fix_hint:一句話的修正建議(繁體中文)。

務必只透過工具回傳一份 findings 清單。若整份簡報沒有任何問題,回傳空清單。"""


# The forced-tool / structured-output contract. Both backends ask the model to
# call this one tool, whose single ``findings`` array is validated into
# ``list[Finding]``.
TOOL_NAME = "report_findings"
TOOL_DESCRIPTION = "回傳這份簡報所有偵測到的設計問題(findings);沒有問題則回傳空清單。"

# Vision-capable Haiku 4.5 — cheapest current vision model (full snapshot id).
# Overridable via env ``ODFORGE_VISION_MODEL``.
_CLAUDE_VISION_MODEL = "claude-haiku-4-5-20251001"

# Ollama's default vision model; overridable via ``ODFORGE_OLLAMA_VISION_MODEL``.
_OLLAMA_VISION_MODEL = "qwen2.5vl"

_OLLAMA_BASE_URL = "http://localhost:11434/v1"

# Findings are small; a modest cap is plenty. Overridable for parity with llm.py.
_MAX_TOKENS_DEFAULT = "2048"


def _require_env(name: str) -> str:
    """Return env var ``name`` or raise a helpful RuntimeError (mirrors llm.py)."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"環境變數 {name} 未設定。請先設定後再執行,例如:\n"
            f'    PowerShell:  $env:{name} = "你的金鑰"\n'
            f'    bash:        export {name}="你的金鑰"'
        )
    return value


def _max_tokens() -> int:
    return int(os.environ.get("ODFORGE_MAX_TOKENS", _MAX_TOKENS_DEFAULT))


def _encode_png(path: Path) -> str:
    """Read a PNG file and return its base64 (ASCII, no newlines)."""
    return base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


def _findings_schema() -> dict:
    """JSON schema for the forced tool: ``{"findings": [Finding, ...]}``."""
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "description": "所有偵測到的設計問題;沒有問題則為空陣列。",
                "items": Finding.model_json_schema(),
            }
        },
        "required": ["findings"],
    }


def _findings_from_payload(data: object) -> List[Finding]:
    """Validate a ``{"findings": [...]}`` payload into ``list[Finding]``."""
    if not isinstance(data, dict):
        return []
    items = data.get("findings", [])
    if not isinstance(items, list):
        return []
    return [Finding.model_validate(item) for item in items]


def _framing_text(pngs: List[Path], ir: Presentation) -> str:
    """Minimal per-request framing: how many pages, and the deck title."""
    return (
        f"這份簡報共 {len(pngs)} 頁,標題為「{ir.title}」。"
        "請依系統提示的檢查清單,逐頁檢視下列影像並透過工具回傳所有設計問題。"
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


@runtime_checkable
class VisionBackend(Protocol):
    """A source that critiques rendered slides into structured findings."""

    def critique(
        self, pngs: List[Path], ir: Presentation
    ) -> List[Finding]:  # pragma: no cover
        ...


class _OffBackend:
    """The degrade path: no vision model → no findings, no client, no crash."""

    def critique(self, pngs: List[Path], ir: Presentation) -> List[Finding]:
        return []


class ClaudeVisionBackend:
    """Vision critic backed by the anthropic SDK's Messages API.

    Independent context: a fresh message carrying ONLY the checklist (system) +
    all page images (base64) + minimal framing. Structured output is forced via
    a single tool call whose ``findings`` array is validated into ``list[Finding]``.

    The client is injected (built lazily in the factory) so this class can be
    constructed and tested without ``anthropic`` installed.
    """

    def __init__(self, client: object, model: str):
        self._client = client
        self.model = model

    def critique(self, pngs: List[Path], ir: Presentation) -> List[Finding]:
        content: list = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _encode_png(png),
                },
            }
            for png in pngs
        ]
        content.append({"type": "text", "text": _framing_text(pngs, ir)})

        tools = [
            {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "input_schema": _findings_schema(),
            }
        ]

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=_max_tokens(),
            system=CHECKLIST,
            messages=[{"role": "user", "content": content}],
            tools=tools,
            tool_choice={"type": "tool", "name": TOOL_NAME},
        )

        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return _findings_from_payload(block.input)
        return []


class OllamaVisionBackend:
    """Vision critic backed by an OpenAI-compatible chat endpoint (Ollama).

    Uses the OpenAI SDK (already a dependency) with base64 ``image_url`` blocks
    and forced function calling. Same independent-context contract as the claude
    backend: checklist system prompt + images + framing, one request, all pages.
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def critique(self, pngs: List[Path], ir: Presentation) -> List[Finding]:
        content: list = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_encode_png(png)}"},
            }
            for png in pngs
        ]
        content.append({"type": "text", "text": _framing_text(pngs, ir)})

        tools = [
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "parameters": _findings_schema(),
                },
            }
        ]

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": CHECKLIST},
                {"role": "user", "content": content},
            ],
            tools=tools,
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            max_tokens=_max_tokens(),
        )

        tool_calls = resp.choices[0].message.tool_calls
        if not tool_calls:
            return []
        try:
            data = json.loads(tool_calls[0].function.arguments)
        except json.JSONDecodeError:
            return []
        return _findings_from_payload(data)


# ---------------------------------------------------------------------------
# Backend registry + facade
# ---------------------------------------------------------------------------


def _make_claude() -> VisionBackend:
    # Env guard first, so a missing key reports cleanly even without anthropic
    # installed; the SDK is then imported lazily — only when claude is selected.
    api_key = _require_env("ANTHROPIC_API_KEY")
    model = os.environ.get("ODFORGE_VISION_MODEL", _CLAUDE_VISION_MODEL)
    import anthropic  # lazy: never imported on the off/ollama paths or at import time

    return ClaudeVisionBackend(anthropic.Anthropic(api_key=api_key), model)


def _make_ollama() -> VisionBackend:
    model = os.environ.get("ODFORGE_OLLAMA_VISION_MODEL", _OLLAMA_VISION_MODEL)
    return OllamaVisionBackend(_OLLAMA_BASE_URL, "ollama", model)


VISION_BACKENDS: Dict[str, Callable[[], VisionBackend]] = {
    "claude": _make_claude,
    "ollama": _make_ollama,
}


def get_vision_backend(name: Optional[str] = None) -> VisionBackend:
    """Resolve and construct a vision backend by name.

    ``name=None`` reads ``ODFORGE_VISION_BACKEND`` (default ``"off"``). ``"off"``
    yields a no-op backend that constructs no client. An unknown name raises
    ``ValueError`` listing the available keys.
    """
    if name is None:
        name = os.environ.get("ODFORGE_VISION_BACKEND", "off")
    if name == "off":
        return _OffBackend()
    factory = VISION_BACKENDS.get(name)
    if factory is None:
        available = sorted(VISION_BACKENDS) + ["off"]
        raise ValueError(f"未知的 vision backend: {name!r};可用值:{available}")
    return factory()


def critique(
    pngs: List[Path], ir: Presentation, backend: Optional[str] = None
) -> List[Finding]:
    """Critique rendered slide PNGs into a list of design findings.

    Resolves the backend (arg > ``ODFORGE_VISION_BACKEND`` > ``"off"``). The
    ``off`` path short-circuits to ``[]`` **before** any client is constructed,
    so the QA loop degrades to deterministic-only checks without a vision model.

    All page images are sent in one request (≤ 20 pages is fine) against a fresh,
    history-free context: the zh-TW checklist as the system prompt, the images,
    and minimal framing. Structured output is forced into ``list[Finding]``.
    """
    if backend is None:
        backend = os.environ.get("ODFORGE_VISION_BACKEND", "off")
    if backend == "off":
        return []
    return get_vision_backend(backend).critique(pngs, ir)
