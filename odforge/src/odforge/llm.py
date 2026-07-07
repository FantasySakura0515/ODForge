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

import json_repair
from openai import OpenAI
from pydantic import ValidationError

from odforge.ir import (
    BulletItem,
    Outline,
    Presentation,
    Spreadsheet,
    TextDoc,
    parse_ir,
)
from odforge.textmetrics import check_budget
from odforge.themes import Theme, resolve_design

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
- 工具參數必須是嚴格合法的 JSON,所有字串值(含 notes)一律以雙引號包裹。

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


OUTLINE_TOOL_NAME = "emit_outline"

OUTLINE_SYSTEM_PROMPT = """\
你是 ODForge 的簡報總監,負責在正式撰稿之前,先為整份簡報「定調」:一次決定
美術方向(DesignSpec)與整份大綱的頁面角色(page roles)。這是兩段式流程的第一段,
你只做設計與骨架,不寫每頁的細節內容(那是下一段的工作)。

【語言】
- 一律以繁體中文(zh-TW)思考與輸出,用詞、標點皆採台灣慣用寫法。

【輸出方式】
- 只透過 emit_outline 工具輸出結果,不要輸出任何一般文字。
- 產出必須完全符合工具參數的 JSON Schema,且為嚴格合法的 JSON(所有字串以雙引號包裹)。

【第一步:美術方向(design)】
1. 先從主題「內心」推導 2–3 個候選美術方向(例如:學術淨白、科技深色控制室、暖調人文…),
   衡量各自的調性、受眾與場合。
2. 從中「挑定一個」並具體化為 DesignSpec 輸出,不要交出多個選項:
   - palette:五色 bg / surface / text / muted / accent,皆為 6 位十六進位色碼("#RRGGBB")。
   - fonts:display 與 body 各挑一,只能從白名單選:
     Noto Sans TC、Noto Serif TC、微軟正黑體、標楷體。
   - scale:compact / standard / display 三選一(講者型偏 display,自讀型偏 compact/standard)。
3. 對比度自我檢查(務必在送出前自行驗算,否則會被退件):
   - text 對 bg 的對比度需 ≥ 4.5。
   - accent 對 bg、muted 對 bg 的對比度需 ≥ 3.0。
   深色底就搭亮色字、亮色底就搭深色字。若沒把握,寧可選對比明確的安全色。

【第二步:頁面角色大綱(pages)】
可用的 role(與投影片版型一致):
title、agenda、section、title-content、two-col、comparison、big-fact、quote、chart、closing。
排版紀律:
- 「一頁一個想法」:每頁只承載一個重點,gist 用一句話說清楚這頁要講什麼。
- 開場第一頁一定是 title;若整份 ≥ 8 頁,title 之後緊接一頁 agenda。
- 用 section 分節,把內容切成幾個段落區塊。
- 適時穿插 big-fact(關鍵數據)、quote(引言)、chart(圖表)來調節敘事節奏。
- 同一種 role 不得連續出現 ≥ 3 頁,避免版面單調。
- 最後一頁一定是 closing 收尾。
- chart 只有在「使用者的需求裡有真實數據」時才可出現;沒有數據就不要放 chart 頁(不要編造數字)。

【mode:講述型態】
- presenter(講者型):大字級、極少字,靠講者口述;適合上台簡報。
- detailed(自讀型):文字完整,能獨立閱讀;適合講義或寄送。
從使用者意圖判斷該用哪一種;無明確線索時預設 presenter。

請據此輸出一份「設計已定案、骨架已排好」的大綱。"""


SLIDES_TOOL_NAME = "emit_slides"

SLIDES_SYSTEM_PROMPT = """\
你是 ODForge 的簡報撰稿人,負責兩段式流程的第二段:大綱與美術方向都已定案,
你要「逐頁填內容」,把每一頁的角色(role)實作成一張完整的投影片(Slide)。

【語言】
- 一律以繁體中文(zh-TW)撰寫,用詞、標點皆採台灣慣用寫法。

【輸出方式】
- 只透過 emit_slides 工具輸出結果,不要輸出任何一般文字。
- 產出必須完全符合工具參數的 JSON Schema,且為嚴格合法的 JSON(所有字串以雙引號包裹)。
- 投影片的「張數」與「每一頁的版型(layout)」必須與大綱完全一致、逐頁對齊,
  不得增刪、不得改動順序、不得換版型。

【蒸餾鐵則(依講述型態 mode)】
- presenter(講者型):每一條 bullet ≤ 16 字、每頁 ≤ 4 條;靠大字與口述,寧缺勿雜。
- detailed(自讀型):每一條 bullet ≤ 30 字、每頁 ≤ 6 條;文字可完整、能獨立閱讀。
- 內容文字裡「不要」寫出版型名稱(如「title-content」「two-col」),
  那是給引擎看的,不是給讀者看的。

【各版型填寫要點】
- title / section / closing:標題精煉;closing 以 title 欄寫收尾語。
- agenda:items 逐條對應大綱各分節(section)的標題,順序一致。
- title-content:bullets 逐條列重點,必要時用巢狀 children 補一層次要細節。
- two-col:left / right 兩欄各放各自的重點。
- comparison:left[0] 與 right[0] 是兩欄的「欄位標題」,其後才是各欄內容。
- big-fact:fact 放關鍵數據或一句重話,bullets 放一行輔助說明。
- quote:quote 放引言原文,attribution 放出處。
- chart:chart 欄必須給「真數據」——labels 與 values 一一對應,
  數據來源是該頁 gist 或使用者需求,不可捏造。

【講者備忘稿】
- 每一張投影片都必須填寫 notes(講者備忘稿),說明這頁怎麼講與延伸重點,不可留空。

請依大綱逐頁填出完整、精簡、可直接上台的投影片內容。"""


@runtime_checkable
class LLMBackend(Protocol):
    """A source that turns a prompt into a validated Document IR."""

    def generate_ir(self, prompt: str, doc_type: str) -> Document:  # pragma: no cover
        ...

    def generate_outline(self, prompt: str) -> Outline:  # pragma: no cover
        ...

    def generate_slides(self, outline: Outline) -> Presentation:  # pragma: no cover
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


def _loads_tool_args(args_json: str) -> dict:
    """Parse a tool call's ``arguments`` string into a dict.

    Mirrors ``generate_ir``'s JSON handling: plain ``json.loads`` first, then a
    best-effort ``json_repair`` (DeepSeek occasionally emits malformed JSON).
    Raises ``json.JSONDecodeError`` if repair also fails, or ``TypeError`` if the
    payload is valid JSON but not an object.
    """
    try:
        data = json.loads(args_json)
    except json.JSONDecodeError:
        data = json_repair.loads(args_json)
        if not isinstance(data, dict):
            raise  # repair failed too: re-raise the original JSONDecodeError
    if not isinstance(data, dict):
        raise TypeError(
            f"tool arguments must be a JSON object, got {type(data).__name__}"
        )
    return data


def _outline_parses_without_design(data: dict) -> bool:
    """True iff dropping ``design`` makes ``data`` validate as an Outline.

    Used to tell a *design-only* failure (a bad palette — tolerable, the design
    can be stripped) apart from a *pages-invalid* failure (structural — must be
    retried then raised).
    """
    probe = dict(data)
    probe.pop("design", None)
    try:
        Outline.model_validate(probe)
        return True
    except ValidationError:
        return False


# ---------------------------------------------------------------------------
# Stage-2 (generate_slides) helpers: outline → prompt, structural + budget gates
# ---------------------------------------------------------------------------


def _render_outline_for_prompt(outline: Outline) -> str:
    """Serialize an outline into a page-numbered brief for the stage-2 prompt."""
    lines: list[str] = []
    if outline.mode == "presenter":
        lines.append("【講述型態】presenter(講者型):每條 bullet ≤ 16 字、每頁 ≤ 4 條。")
    else:
        lines.append("【講述型態】detailed(自讀型):每條 bullet ≤ 30 字、每頁 ≤ 6 條。")
    if outline.design is not None:
        d = outline.design
        pal = d.palette
        lines.append(
            f"【美術方向】scale={d.scale};display 字體={d.fonts.display}、"
            f"body 字體={d.fonts.body};色盤 bg={pal.bg}/surface={pal.surface}/"
            f"text={pal.text}/muted={pal.muted}/accent={pal.accent}"
        )
    else:
        lines.append("【美術方向】未指定(引擎將套用預設主題)。")
    lines.append("【頁面大綱】請「逐頁」填內容;張數與每頁版型(layout)必須與下列完全一致:")
    for i, page in enumerate(outline.pages, start=1):
        lines.append(
            f"  第 {i} 頁 | 版型(layout)={page.role} | 標題={page.title} | "
            f"這頁要點(gist):{page.gist}"
        )
    return "\n".join(lines)


def _slides_user_content(outline: Outline) -> str:
    """The base user turn for stage-2: the instruction plus the outline brief."""
    return (
        "請根據以下「已定案」的大綱與美術方向,逐頁填出完整的投影片內容,"
        "並透過 emit_slides 工具回傳整份簡報:\n\n" + _render_outline_for_prompt(outline)
    )


def _with_error_feedback(base: str, error_summary: Optional[str]) -> str:
    """Append a structural-error retry hint to ``base`` (no-op when None)."""
    if error_summary is None:
        return base
    return (
        f"{base}\n\n[系統提示] 上一次的輸出無法通過驗證,錯誤如下,請修正後重新輸出:\n"
        f"{error_summary}"
    )


def _slides_structure_errors(pres: Presentation, outline: Outline) -> Optional[str]:
    """Return a zh-TW summary if the deck's shape drifts from the outline, else None.

    Enforces the two structural invariants: the slide count equals the outline's
    page count, and each ``slide.layout`` matches the outline's role at that index.
    """
    expected = [page.role for page in outline.pages]
    got = pres.slides
    problems: list[str] = []
    if len(got) != len(expected):
        problems.append(
            f"投影片張數不符:大綱共 {len(expected)} 頁,但收到 {len(got)} 張——"
            "必須逐頁對齊,不得增刪。"
        )
    for i, (slide, role) in enumerate(zip(got, expected), start=1):
        if slide.layout != role:
            problems.append(
                f"第 {i} 頁版型不符:大綱要求 layout「{role}」,但收到「{slide.layout}」。"
            )
    return " ".join(problems) if problems else None


def _budget_overloads(pres: Presentation, theme: Theme) -> list[tuple[int, list[str]]]:
    """List ``(page_number, messages)`` for every slide that overruns its frames."""
    overloads: list[tuple[int, list[str]]] = []
    for i, slide in enumerate(pres.slides, start=1):
        msgs = check_budget(slide, theme)
        if msgs:
            overloads.append((i, msgs))
    return overloads


def _budget_feedback(overloads: list[tuple[int, list[str]]]) -> str:
    """One retry turn naming the overloaded pages and asking to shorten only them."""
    header = (
        "下列頁面的文字超出版面(超載)。請「只」精簡這些頁面——縮短字數或減少每頁條數,"
        "其餘頁面維持不變——並重新輸出「完整」的簡報:"
    )
    lines = [f"第 {n} 頁超載:{' '.join(msgs)}" for n, msgs in overloads]
    return header + "\n" + "\n".join(lines)


def _degrade_slide(slide, theme: Theme) -> None:
    """Truncate an over-budget slide's bullets in place until it fits.

    Algorithm — repeat while :func:`check_budget` still complains:
      1. If any bullet is a ``BulletItem`` carrying children, drop the **last**
         child of the last such bullet (children are finer-grained than items).
      2. Otherwise, if more than one bullet remains, drop the last bullet.
      3. Otherwise stop (always keep at least one bullet).
    If (and only if) anything was dropped, append an honesty note to ``notes``.
    A slide that overruns on a non-bullet frame (e.g. a very long title) has no
    bullets to shed — it is left as-is; budget never raises.
    """
    dropped = False
    while check_budget(slide, theme):
        child_idx: Optional[int] = None
        for i in range(len(slide.bullets) - 1, -1, -1):
            item = slide.bullets[i]
            if isinstance(item, BulletItem) and item.children:
                child_idx = i
                break
        if child_idx is not None:
            item = slide.bullets[child_idx]
            item.children = list(item.children)[:-1]
            dropped = True
        elif len(slide.bullets) > 1:
            slide.bullets = list(slide.bullets)[:-1]
            dropped = True
        else:
            break
    if dropped:
        note = "(部分要點因版面限制省略)"
        slide.notes = f"{slide.notes}\n{note}" if slide.notes else note


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
                max_tokens=int(os.environ.get("ODFORGE_MAX_TOKENS", "8192")),
            )

            tool_calls = resp.choices[0].message.tool_calls
            if not tool_calls:
                error_summary = "模型未呼叫 emit_document 工具,請務必透過該工具輸出。"
                last_exc = RuntimeError("model did not return a tool call")
                continue

            args_json = tool_calls[0].function.arguments
            try:
                try:
                    data = json.loads(args_json)
                except json.JSONDecodeError:
                    # DeepSeek occasionally emits malformed JSON in tool
                    # arguments (e.g. an unquoted CJK-bracket-initial string
                    # value). Attempt a best-effort repair; pydantic below
                    # remains the correctness gate — repair never bypasses it.
                    data = json_repair.loads(args_json)
                    if not isinstance(data, dict):
                        raise  # repair failed too: original JSONDecodeError
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

    def generate_outline(self, prompt: str) -> Outline:
        """Stage-1 of the pipeline: design + page-role outline in one call.

        Forces a function call against ``Outline.model_json_schema()`` (same
        mechanics as :meth:`generate_ir`). Failure handling is deliberately
        asymmetric:

        * A **design-only** failure (a palette that can't clear contrast, while
          the pages themselves are valid) is fed back and retried once; if the
          retry still can't produce a legible palette, the design is stripped
          (``design=None`` → preset fallback) and the rest of the outline is
          accepted. A bad palette never crashes generation.
        * A **pages-invalid** failure is retried once then raised, exactly like
          ``generate_ir``.
        """
        schema = Outline.model_json_schema()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": OUTLINE_TOOL_NAME,
                    "description": "輸出簡報的美術方向(DesignSpec)與 page-role 大綱",
                    "parameters": schema,
                },
            }
        ]
        tool_choice = {"type": "function", "function": {"name": OUTLINE_TOOL_NAME}}

        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            is_last = attempt == 1
            messages = [{"role": "system", "content": OUTLINE_SYSTEM_PROMPT}]
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
                max_tokens=int(os.environ.get("ODFORGE_MAX_TOKENS", "8192")),
            )

            tool_calls = resp.choices[0].message.tool_calls
            if not tool_calls:
                error_summary = "模型未呼叫 emit_outline 工具,請務必透過該工具輸出。"
                last_exc = RuntimeError("model did not return a tool call")
                continue

            args_json = tool_calls[0].function.arguments
            try:
                data = _loads_tool_args(args_json)
            except (json.JSONDecodeError, TypeError) as exc:
                error_summary = str(exc)
                last_exc = exc
                continue

            try:
                return Outline.model_validate(data)
            except ValidationError as exc:
                last_exc = exc
                error_summary = str(exc)
                # Only-the-design-is-bad? Then the palette is the problem.
                if _outline_parses_without_design(data):
                    if is_last:
                        # Retry already spent on a bad palette: strip it and
                        # accept the rest — never crash on a bad palette.
                        stripped = dict(data)
                        stripped.pop("design", None)
                        return Outline.model_validate(stripped)
                    # First design failure: feed the error back, retry once.
                    continue
                # Pages are structurally invalid: retry-once-then-raise.
                continue

        assert last_exc is not None
        raise last_exc

    def _emit_slides(
        self,
        messages: list,
        outline: Outline,
        tools: list,
        tool_choice: dict,
    ) -> tuple[Optional[Presentation], Optional[Exception]]:
        """One stage-2 LLM round.

        Returns ``(Presentation, None)`` when the call yields a structurally
        valid deck (outline's design re-attached, slide count + layouts aligned),
        otherwise ``(None, exc)`` whose ``str(exc)`` is fed back as the retry hint.
        """
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            # Stage-2 fills every page, so it defaults higher than the one-shot
            # generate_ir/generate_outline paths (8192). Env override still wins.
            max_tokens=int(os.environ.get("ODFORGE_MAX_TOKENS", "16384")),
        )

        tool_calls = resp.choices[0].message.tool_calls
        if not tool_calls:
            return None, RuntimeError(
                "模型未透過 emit_slides 工具輸出,請務必以該工具回傳完整簡報。"
            )

        try:
            data = _loads_tool_args(tool_calls[0].function.arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, exc

        data["type"] = "presentation"
        # Art direction comes from stage 1, not from whatever the model returns.
        data["design"] = (
            outline.design.model_dump(mode="json")
            if outline.design is not None
            else None
        )

        try:
            pres = Presentation.model_validate(data)
        except ValidationError as exc:
            return None, exc

        structural = _slides_structure_errors(pres, outline)
        if structural is not None:
            return None, RuntimeError(structural)
        return pres, None

    def generate_slides(self, outline: Outline) -> Presentation:
        """Stage-2 of the pipeline: fill every outline page into a full deck.

        ONE LLM call fills all pages against ``Presentation.model_json_schema()``
        (chosen over a slides-only sub-schema for parity with ``generate_ir``);
        the outline's ``design`` is always re-attached so art direction comes
        from stage 1. Two gates then guard the result:

        * **Structural consistency** — the returned slide count and each
          ``slide.layout`` must match the outline's page roles, index-for-index.
          A mismatch (or an unparseable / schema-invalid payload) is fed back and
          retried once, then raised.
        * **Layout budget** — every slide is run through :func:`check_budget`.
          Overloaded pages are fed back exactly once ("第 N 頁超載:…") asking the
          model to shorten *only* those pages and return the full deck. If any
          page is still over budget afterwards it is **auto-degraded**
          (:func:`_degrade_slide` truncates its bullets with an honesty note) —
          budget never raises.
        """
        schema = Presentation.model_json_schema()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": SLIDES_TOOL_NAME,
                    "description": "輸出填好每頁內容的完整簡報(Presentation)",
                    "parameters": schema,
                },
            }
        ]
        tool_choice = {"type": "function", "function": {"name": SLIDES_TOOL_NAME}}
        base_user = _slides_user_content(outline)

        # -- Gate 1: obtain a structurally valid deck (retry once, then raise) --
        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        presentation: Optional[Presentation] = None
        for _attempt in range(2):
            messages = [
                {"role": "system", "content": SLIDES_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _with_error_feedback(base_user, error_summary),
                },
            ]
            pres, exc = self._emit_slides(messages, outline, tools, tool_choice)
            if pres is not None:
                presentation = pres
                break
            last_exc = exc
            error_summary = str(exc)
        if presentation is None:
            assert last_exc is not None
            raise last_exc

        # -- Gate 2: layout budget (feed back once, then auto-degrade) ----------
        theme = resolve_design(presentation)
        overloads = _budget_overloads(presentation, theme)
        if not overloads:
            return presentation

        messages = [
            {"role": "system", "content": SLIDES_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": base_user + "\n\n" + _budget_feedback(overloads),
            },
        ]
        retry_pres, _exc = self._emit_slides(messages, outline, tools, tool_choice)
        if retry_pres is not None:
            # A structurally valid (hopefully lighter) deck. If the retry itself
            # failed structurally we keep the previous deck and degrade that.
            presentation = retry_pres

        theme = resolve_design(presentation)
        for n, _msgs in _budget_overloads(presentation, theme):
            _degrade_slide(presentation.slides[n - 1], theme)
        return presentation


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


def generate_outline(prompt: str, backend: Optional[str] = None) -> Outline:
    """Facade: resolve a backend and generate a stage-1 design + outline."""
    return get_backend(backend).generate_outline(prompt)


def generate_slides(
    outline: Outline, backend: Optional[str] = None
) -> Presentation:
    """Facade: resolve a backend and fill a stage-1 outline into a full deck."""
    return get_backend(backend).generate_slides(outline)
