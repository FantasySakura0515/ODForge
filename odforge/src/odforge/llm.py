"""ODForge LLM backend abstraction layer.

Turns a natural-language prompt into a validated Document IR by driving an
OpenAI-compatible chat endpoint with forced function calling. A single
``OpenAICompatBackend`` covers DeepSeek (the default), Ollama and any other
OpenAI-compatible server; the ``BACKENDS`` registry makes adding a source a
one-liner.

This module contains no rendering logic. API-key material is read from
environment variables only and never hard-coded.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, Optional, Protocol, Type, Union, runtime_checkable

from openai import OpenAI
from pydantic import ValidationError

from odforge.ir import Presentation, Spreadsheet, TextDoc, parse_ir

Document = Union[TextDoc, Presentation, Spreadsheet]

# doc_type -> IR model class
DOC_TYPES: Dict[str, Type[Document]] = {
    "text": TextDoc,
    "presentation": Presentation,
    "spreadsheet": Spreadsheet,
}

TOOL_NAME = "emit_document"

SYSTEM_PROMPT = """\
你是 ODForge 的文件內容產生器,專門為教學場景產出結構化文件。請嚴格遵守以下規則:

【語言與語氣】
- 一律輸出繁體中文(zh-TW),用詞、標點皆使用台灣慣用寫法。
- 面向教學用途,內容須準確、清楚、有條理,適合教師授課或學生自學。

【輸出方式】
- 只透過 emit_document 工具輸出結果,不要輸出任何一般文字或說明。
- 產出內容必須完全符合工具參數的 JSON Schema。

【一般文件(text)】
- 建立清楚的標題階層(章、節、小節),善用標題、段落、清單與表格。
- 段落文字流暢完整,必要時使用引言(quote)或註記(note)樣式。

【簡報(presentation)】
- 張數控制在 8 到 20 張之間。
- 每張投影片的重點條列(bullets)不超過 5 條,每一條不超過 30 個字。
- 適時穿插 section(分節)與 big-fact(關鍵數據)版面,調整敘事節奏。
- 每一張投影片都必須填寫 notes(講者備忘稿),說明講述重點與延伸。

【試算表(spreadsheet)】
- 數字欄位一律使用數值(number),不要用字串包裝數字。
- 公式使用 of:= 命名空間(OpenFormula),欄位標題清楚。

請依使用者的需求,產出恰當且完整的文件內容。"""


@runtime_checkable
class LLMBackend(Protocol):
    """A source that turns a prompt into a validated Document IR."""

    def generate_ir(self, prompt: str, doc_type: str) -> Document:  # pragma: no cover
        ...


def _require_env(name: str) -> str:
    """Return the value of env var ``name`` or raise a helpful RuntimeError."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"環境變數 {name} 未設定。請先設定後再執行,例如:\n"
            f'    PowerShell:  $env:{name} = "你的金鑰"\n'
            f'    bash:        export {name}="你的金鑰"'
        )
    return value


class OpenAICompatBackend:
    """Backend for any OpenAI-compatible chat-completions endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def generate_ir(self, prompt: str, doc_type: str) -> Document:
        ir_cls = DOC_TYPES.get(doc_type)
        if ir_cls is None:
            raise ValueError(
                f"未知的 doc_type: {doc_type!r};可用值:{sorted(DOC_TYPES)}"
            )

        schema = ir_cls.model_json_schema()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": "輸出結構化文件內容",
                    "parameters": schema,
                },
            }
        ]
        tool_choice = {"type": "function", "function": {"name": TOOL_NAME}}

        # First attempt, then exactly one retry on failure.
        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            user_content = prompt
            if error_summary is not None:
                user_content = (
                    f"{prompt}\n\n"
                    f"[系統提示] 上一次的輸出無法通過驗證,錯誤如下,請修正後重新輸出:\n"
                    f"{error_summary}"
                )
            messages.append({"role": "user", "content": user_content})

            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )

            tool_calls = resp.choices[0].message.tool_calls
            if not tool_calls:
                error_summary = "模型未呼叫 emit_document 工具,請務必透過該工具輸出。"
                last_exc = RuntimeError("model did not return a tool call")
                continue

            args_json = tool_calls[0].function.arguments
            try:
                data = json.loads(args_json)
                if not isinstance(data, dict):
                    # Valid JSON that is not an object (e.g. the string "123");
                    # route into the retry path instead of letting the ensuing
                    # item assignment raise an unhandled TypeError.
                    raise TypeError(
                        f"tool arguments must be a JSON object, got {type(data).__name__}"
                    )
                data["type"] = doc_type  # guard against a missing discriminator
                return parse_ir(data)
            except (json.JSONDecodeError, TypeError, ValidationError) as exc:
                error_summary = str(exc)
                last_exc = exc
                continue

        assert last_exc is not None
        raise last_exc


# ---------------------------------------------------------------------------
# Backend registry + facade
# ---------------------------------------------------------------------------

BACKENDS: Dict[str, Callable[[], LLMBackend]] = {
    "deepseek": lambda: OpenAICompatBackend(
        "https://api.deepseek.com",
        _require_env("DEEPSEEK_API_KEY"),
        os.environ.get("ODFORGE_MODEL", "deepseek-chat"),
    ),
    "ollama": lambda: OpenAICompatBackend(
        "http://localhost:11434/v1",
        "ollama",
        os.environ.get("ODFORGE_OLLAMA_MODEL", "qwen2.5"),
    ),
}


def get_backend(name: Optional[str] = None) -> LLMBackend:
    """Resolve and construct a backend by name.

    ``name=None`` reads ``ODFORGE_BACKEND`` (default ``"deepseek"``). An unknown
    name raises ``ValueError`` listing the available keys.
    """
    if name is None:
        name = os.environ.get("ODFORGE_BACKEND", "deepseek")
    factory = BACKENDS.get(name)
    if factory is None:
        raise ValueError(
            f"未知的 backend: {name!r};可用值:{sorted(BACKENDS)}"
        )
    return factory()


def generate_ir(
    prompt: str, doc_type: str, backend: Optional[str] = None
) -> Document:
    """Facade: resolve a backend and generate a validated Document IR."""
    return get_backend(backend).generate_ir(prompt, doc_type)
