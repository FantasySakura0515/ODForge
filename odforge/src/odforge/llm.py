"""ODForge LLM backend abstraction layer.

Turns a natural-language prompt into a validated Document IR. Everything that
decides *what* to ask — the prompts, the retries, the gates — lives once, in
``StructuredLLMBackend``; each transport implements a single method:

* ``OpenAICompatBackend`` drives an OpenAI-compatible chat endpoint with forced
  function calling — DeepSeek, Ollama, or any hosted provider (``custom``).
* ``CodexCliBackend`` drives the locally installed Codex CLI as a subprocess,
  where ``--output-schema`` plays the part the forced tool call plays above.
  It authenticates through the operator's own ChatGPT login, so it spends no
  API key and hits no per-key quota.

The ``BACKENDS`` registry makes adding a source a one-liner.

This module contains no rendering logic. API-key material is read from
environment variables only and never hard-coded.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import (
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Type,
    Union,
    runtime_checkable,
)

import json_repair
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator

from odforge.ir import (
    LANGUAGES,
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
- 最後一頁一定是 closing 收尾;若需求含決策、風險或下一步,closing 的 gist
  必須明確寫出決策請求與具體行動,不得只寫「謝謝」或空泛口號。
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
- title / section:標題精煉。
- closing:title 寫結論或決策請求;bullets 放 2 到 4 個具體風險、責任或下一步。
  使用者要求的內容必須出現在投影片可見欄位,不得只藏在 notes。
- agenda:bullets 逐條對應大綱各分節(section)的標題,順序一致。
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


OUTLINE_VISUAL_GUIDANCE = """

【視覺敘事規則】
- 每頁除了 gist，也要填 visual_intent，具體描述觀眾應該看見的關係，
  例如「四階段由左到右推進」或「三個關鍵指標並列比較」，不要只寫「簡潔」。
- 遇到步驟、方法或工作流，使用 process；遇到時間演進，使用 timeline；
  遇到兩到四個 KPI 或成果數字，使用 metrics；遇到兩到四個平行觀點、
  支柱或功能，使用 cards。
- 遇到系統架構、因果關係、中心與周邊或上下層級，使用 diagram。
- 不要把本來應該是圖解的內容塞進 title-content。除封面、章節與結尾外，
  title-content 不應壟斷整份簡報；語意適合時優先使用視覺頁型。
- 版型服務敘事，不要為了湊版型捏造數字、日期或來源。
"""

SLIDES_VISUAL_GUIDANCE = """

【內容與視覺元件規則】
- 原始需求是事實、受眾與語氣的主要依據；outline 的 gist 只是頁面摘要，
  不得用 gist 取代或遺忘原始需求。
- presenter 的字數限制是上限，不是要求內容空泛。每頁要有可講述的觀點、
  因果或證據；notes 補充講者需要的脈絡。
- process：steps 放 2–5 個步驟，每個 step 使用 title 與 detail。
- timeline：events 放 2–5 個事件，每個 event 使用 label、title、detail。
- metrics：metrics 放 2–4 個指標，每個 metric 使用 value、label、detail。
- cards：bullets 放 2–4 個平行且可獨立閱讀的觀點。
- diagram：diagram.kind 使用 hub 或 hierarchy；nodes 放 2–6 個節點，
  edges 使用 source、target 與可選 label，所有 id 必須存在且唯一。
- sources：只有原始需求確實提供資料來源時才填，最多 3 筆；
  label 要短，url 必須是真實的 http(s) 網址。不得捏造引用。
- 不得用「圖解示意」、「放圖片」、「待補」等佔位文字假裝已完成視覺內容。
- 原始需求沒有提供的精確統計數字、日期、引言或來源，不要自行捏造。
- 原始需求沒有提供的技術棧、程式框架、資料庫、雲端服務、產品名稱或版本，
  一律不得自行補上。資訊不足時使用「前端介面」「API 服務」「資料庫」等通用名稱。
- 送出前自查所有英文技術名與品牌名：若原始需求沒有逐字提供，就改成通用名稱。
"""

# Image layouts are intentionally described in a clean, machine-stable suffix:
# some legacy prompt text above is kept byte-for-byte for compatibility.
OUTLINE_VISUAL_GUIDANCE += """

- 有可用圖片素材，而且圖片本身承載主要證據或情境時，優先規劃
  image-focus；圖片與三到四個結論並列時使用 image-split。
- 不要為了裝飾而安排圖片頁；visual_intent 必須說明圖片要證明什麼。
"""

SLIDES_VISUAL_GUIDANCE += """

- image-focus / image-split：必須填 image。
- 若「可用圖片素材」列出 asset id，image.src 只能填 asset://<id>，
  並依描述選擇真正相關的素材，不得虛構 id。
- 若圖片生成可用且沒有合適素材，可留空 src 並填具體的 image.prompt。
- image.alt 必填；caption 與 credit 要精簡且不可捏造。
- image-split 的 bullets 放 2–4 個由圖片支持的結論。
"""

# Keep the established prompt intact while extending its source-grounding and
# visual-planning contract.
OUTLINE_SYSTEM_PROMPT += OUTLINE_VISUAL_GUIDANCE
SLIDES_SYSTEM_PROMPT += SLIDES_VISUAL_GUIDANCE


# ---------------------------------------------------------------------------
# Output language
#
# The three system prompts above all say「一律繁體中文」, and they stay that way
# byte-for-byte: zh-TW is the default and by far the common case. A non-default
# language is expressed as an OVERRIDE block appended after that rule, which
# says outright that it supersedes it. Editing the original line per language
# would fork three prompts into nine; an explicit, later, more specific
# instruction is both shorter and what models actually follow.
# ---------------------------------------------------------------------------

_LANGUAGE_OVERRIDES: Dict[str, str] = {
    "en": """

【語言(覆寫上面的語言規則)】
- 這一份文件的輸出語言是 English:所有讀者看得到的文字(title、subtitle、
  bullets、left/right、fact、quote、attribution、kicker、圖表標籤、
  notes)一律以英文撰寫,不得夾雜中文。
- 需求以中文寫成時由你翻譯成自然、專業的英文;專有名詞沿用需求中的原文寫法。
- 標點使用英文半形標點,句首大寫,標題採 Title Case 或 Sentence case 擇一並全份一致。
""",
    "bilingual": """

【語言(覆寫上面的語言規則)】
- 這一份文件採「中英對照」:每一則讀者看得到的文字先寫繁體中文,再以半形括號
  補上英文,格式為「中文（English）」。
- notes(講者備忘稿)只寫繁體中文,不必對照——備忘稿加倍長只會讓講者更難用。
- 對照會讓每一行大約加長一倍:請主動把句子縮短,寧可少一條重點,
  也不要讓一頁塞不下(超載的頁面會被系統自動刪減內容)。
""",
}


def language_label(language: str) -> str:
    """Human-readable name of an output language (falls back to the code)."""
    return LANGUAGES.get(language, language)


def system_prompt_for(base: str, language: str) -> str:
    """``base`` with the output-language override appended (no-op for zh-TW)."""
    return base + _LANGUAGE_OVERRIDES.get(language, "")


DISCOVERY_TOOL_NAME = "emit_discovery_questions"

DISCOVERY_SYSTEM_PROMPT = """\
你是 ODForge 的「需求編輯」。使用者通常只會提供一句模糊的簡報需求；你的工作不是
立刻寫簡報，而是找出最會影響成品質量、目前卻缺少的資訊。

【任務】
- 先用一句繁體中文 summary 重述你已經理解的需求。
- known_context 只列出使用者已明確提供的事實，不得自行補完。
- 提出 2 到 8 個高資訊量問題；一般情況以 3 到 5 題為佳，需求龐大、缺口確實很多時
  最多問到 8 題。題數由缺口決定，不得為湊滿而問。
- 優先釐清：受眾要做的決策、核心訊息、具體內容／證據、目前進度、
  風險、可用圖片或數據。依題目動態選擇，不要每次照同一份問卷。
- 簡報是立刻生成的，交期不存在：絕對不要詢問這份簡報何時要用、截稿日、
  可用製作時間或緊急程度，那些答案不會改變任何一頁內容。
  (簡報「內容裡」要講的專案時程、里程碑或排程仍可詢問。)
- 提案型需求若尚未說明「希望聽眾做什麼決定」，必須優先詢問決策目標。
- 若需求明確要求說明「系統架構」，但沒有提供實際技術棧、模組或資料流，
  必須詢問其中一項具體架構資訊，不得只問架構完成到哪個階段。
- 問題要彼此獨立且不重疊；送出前先自查，不得用兩題換句話詢問同一件事。
- 一題只問一個決策變數，不得把產業與階段、受眾與輪次等兩件事綁在同一題。
- question 只放直接、簡短的問句；原因與解釋全部放在 why。
- 先問內容、事實與證據，再問表達方式。除非使用者明確要求，或內容資訊已完整，
  否則不要浪費問題詢問視覺風格、配色或版型偏好。
- 不要詢問 prompt 或已有設定中已經回答的資訊。
- 每題提供 2 到 4 個互斥、可快速選擇的 options；不要提供「其他」，
  介面會另外提供自由輸入。
- options 必須直接回答該題，不得發明使用者未提供的金額、日期、比例、效能數字、
  公司名稱或技術成果。需要數字時可問「已有核定範圍／有初估／尚未確認」。
- 若需求已相當完整，只問仍會改變內容策略的缺口，不得為湊題數詢問低價值偏好。
- 問題必須讓一般使用者看得懂，不使用簡報或模型術語。
- question 與 why 都不要使用 Call to Action、pipeline、framework 等英文術語。
- id 使用簡短 ASCII snake_case，所有內容使用繁體中文。
- completeness 是目前需求完整度的保守估計，0 到 100。

只透過 emit_discovery_questions 工具輸出，不要輸出一般文字。"""


# The UI supplies its own free-text field, so a catch-all "other" option is
# dead weight — but only when the option IS the catch-all: bare 其他/其它,
# optionally trailed by punctuation or a parenthetical like 其他（請說明）.
# A real answer that merely *starts* with 其他 ("其他部門主導") must survive,
# or a valid plan can be rejected for having too few options.
_CATCH_ALL_OPTION = re.compile(r"(?:其他|其它)(?:[\s（(【\[:：,，、。．].*)?$")


class DiscoveryQuestion(BaseModel):
    """One high-information question asked before presentation generation."""

    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(min_length=1, max_length=160)
    why: str = Field(min_length=1, max_length=160)
    options: list[str] = Field(min_length=2, max_length=4)

    @field_validator("options")
    @classmethod
    def options_are_direct_choices(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            option = value.strip()
            if not option or _CATCH_ALL_OPTION.fullmatch(option):
                continue
            if option not in cleaned:
                cleaned.append(option)
        if len(cleaned) < 2:
            raise ValueError("questions need at least two distinct direct options")
        return cleaned


# The two list caps live here, not inline in Field(...), because the validators
# below have to trim to exactly the same numbers.
#
# 已知資訊的上限放寬到 10:6 是憑空訂的,而需求越長、模型越會列出 7、8 條——那些
# 條目全是使用者自己給過的事實,沒有一條該被丟掉。10 條 bullet 對 prompt 預算不
# 痛不癢,卻能讓「裁切」退回它該待的位置:最後一道防線,不是日常行為。
DISCOVERY_MAX_KNOWN_CONTEXT = 10
# 問題數的上限是刻意的產品決定而非技術限制:每多一題都是使用者多按一次的成本。
# 8 是 2026-08-17 使用者定的上限;系統提示仍然要求「一般 3 到 5 題」,8 是留給
# 需求龐大、缺口真的很多的那種案子,不是預設題數。
DISCOVERY_MAX_QUESTIONS = 8


class DiscoveryPlan(BaseModel):
    """Structured pre-generation interview produced from a short prompt.

    Both lists are *trimmed* to their cap rather than rejected for exceeding it.
    Over-supply used to end the interview with a raw pydantic ``too_long`` on
    screen, and the retry — same long prompt — overshoots again, so the user
    simply never got past this screen.

    Trimming is not free, so the caps carry the weight: ``known_context`` is set
    high enough that a real plan fits inside it, and what it restates reaches
    generation anyway (the user's prompt goes in verbatim, and reference PDFs
    are re-attached at generation time). ``questions`` is capped by choice at a
    number the user picked, so anything past it is a question nobody wanted.
    """

    summary: str = Field(min_length=1, max_length=500)
    known_context: list[str] = Field(
        default_factory=list, max_length=DISCOVERY_MAX_KNOWN_CONTEXT
    )
    questions: list[DiscoveryQuestion] = Field(
        min_length=2, max_length=DISCOVERY_MAX_QUESTIONS
    )
    completeness: int = Field(ge=0, le=100)

    @field_validator("known_context", mode="before")
    @classmethod
    def known_context_fits(cls, value: object) -> object:
        """Drop blank/duplicate facts, then keep at most the cap.

        Blanks and duplicates go first on purpose: they are the cheapest things
        to lose, and clearing them usually means the real facts all fit.
        """
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            # Not our shape — let the field's own validation report it properly.
            return value
        kept: list[str] = []
        for item in value:
            text = item.strip()
            if text and text not in kept:
                kept.append(text)
        return kept[:DISCOVERY_MAX_KNOWN_CONTEXT]

    @field_validator("questions", mode="before")
    @classmethod
    def questions_fit(cls, value: object) -> object:
        """Keep the first N questions; asking six is not worth a failed run."""
        if isinstance(value, list) and len(value) > DISCOVERY_MAX_QUESTIONS:
            return value[:DISCOVERY_MAX_QUESTIONS]
        return value


# Generation is immediate, so this deck has no delivery date: asking when it is
# needed, when it is due, or how urgent it is cannot change a single page — it
# only spends one of the user's few answers. A timeline the deck must *describe*
# (專案時程、里程碑、排程) is content and stays; so does how long the speaker has
# on stage, which sets the page count. Hence subject + timing, not timing alone.
_DELIVERY_SUBJECTS = ("簡報", "投影片", "這份", "成品", "檔案", "報告", "文件")
_DELIVERY_TIMING = (
    "何時",
    "什麼時候",
    "甚麼時候",
    "哪一天",
    "期限",
    "截稿",
    "截止",
    "交期",
    "交付",
    "deadline",
    "時間點",
)

# Bare 緊急 is not enough to condemn a question: emergency response (緊急應變、
# 緊急救護) is a realistic deck *topic*, and "簡報要涵蓋哪些緊急應變流程？" is a
# content question we must keep. Only urgency-about-the-deck phrasing — how
# urgent the request itself is — marks a delivery question.
_DELIVERY_URGENCY = ("緊急程度", "多緊急", "有多急", "急著要", "趕著要")

# Subject + timing can still collide with content: "這份系統的交付時程規劃" and
# "報告涵蓋的期間到何時" ask about the timeline the deck *describes*, not when
# the deck is due. These markers flag material the slides must cover, so any
# question carrying one is treated as a content question and kept.
_CONTENT_MARKERS = (
    "時程規劃",
    "里程碑",
    "排程",
    "涵蓋",
    "流程",
    "應變",
    "進度",
    "階段",
)


def _asks_delivery_deadline(question: DiscoveryQuestion) -> bool:
    """Is this a question about *this deck's* due date or urgency?"""
    text = question.question
    if any(marker in text for marker in _CONTENT_MARKERS):
        return False
    if any(phrase in text for phrase in _DELIVERY_URGENCY):
        return True
    return any(subject in text for subject in _DELIVERY_SUBJECTS) and any(
        timing in text for timing in _DELIVERY_TIMING
    )


def _without_delivery_deadline_questions(plan: DiscoveryPlan) -> DiscoveryPlan:
    """Drop delivery-deadline questions, keeping the plan valid.

    Two questions is the schema's floor, but the floor is no reason to keep
    every delivery question: drop as many as possible, then re-add dropped
    ones in their original order only until the floor is met again. One
    wasted question beats a failed interview — but never waste more than the
    floor demands.
    """
    keep = [not _asks_delivery_deadline(q) for q in plan.questions]
    missing = 2 - sum(keep)
    for index, kept in enumerate(keep):
        if missing <= 0:
            break
        if not kept:
            keep[index] = True
            missing -= 1
    if all(keep):
        return plan
    questions = [q for q, kept in zip(plan.questions, keep, strict=True) if kept]
    return plan.model_copy(update={"questions": questions})


@runtime_checkable
class LLMBackend(Protocol):
    """A source that turns a prompt into a validated Document IR."""

    def generate_ir(
        self, prompt: str, doc_type: str, language: str = "zh-TW"
    ) -> Document:  # pragma: no cover
        ...

    def generate_outline(
        self, prompt: str, pages: int | None = None, language: str = "zh-TW"
    ) -> Outline:  # pragma: no cover
        ...

    def generate_slides(
        self, outline: Outline, dropped: Optional[List[DroppedContent]] = None
    ) -> Presentation:  # pragma: no cover
        ...

    def discover_questions(
        self,
        prompt: str,
        context: str = "",
        progress: Optional[Callable[[str], None]] = None,
    ) -> DiscoveryPlan:  # pragma: no cover
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
    if outline.source_prompt.strip():
        lines.extend(
            [
                "【原始需求／資料】",
                outline.source_prompt.strip(),
                "【頁面藍圖】",
            ]
        )
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
    if outline.media_assets:
        lines.append("【可用圖片素材】只能用下列 asset id：")
        for asset in outline.media_assets:
            credit = f"；來源：{asset.credit}" if asset.credit else ""
            lines.append(
                f"  - asset://{asset.id}：{asset.description}{credit}"
            )
    else:
        lines.append("【可用圖片素材】無")
    lines.append(
        "【圖片生成】"
        + (
            "可用，可在 image.prompt 描述需求"
            if outline.image_generation_available
            else "不可用"
        )
    )
    for i, page in enumerate(outline.pages, start=1):
        lines.append(
            f"  第 {i} 頁 | 版型(layout)={page.role} | 標題={page.title} | "
            f"這頁要點(gist):{page.gist}"
        )
        if page.visual_intent:
            lines.append(f"           視覺意圖：{page.visual_intent}")
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
    for i, (slide, role) in enumerate(zip(got, expected, strict=False), start=1):
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


def _budget_feedback(
    overloads: list[tuple[int, list[str]]], prior_deck_json: str
) -> str:
    """One retry turn naming the overloaded pages and asking to shorten only them.

    The previous deck's JSON is echoed back so 「其餘頁面照抄」 is an instruction a
    stateless retry can actually keep — without it the model has no record of
    what "the rest" contained.
    """
    header = (
        "這是你上一次的輸出:\n"
        f"{prior_deck_json}\n\n"
        "其中下列頁面的文字超出版面(超載)。請「只」精簡這些頁面——縮短字數或"
        "減少每頁條數——其餘頁面照抄上面的輸出,並重新輸出「完整」的簡報:"
    )
    lines = [f"第 {n} 頁超載:{' '.join(msgs)}" for n, msgs in overloads]
    return header + "\n" + "\n".join(lines)


class DroppedContent(BaseModel):
    """Content the layout budget forced off a page, verbatim.

    A note saying 「部分要點因版面限制省略」 tells the user that *something* was
    lost without telling them *what* — which, for their purposes, is the same as
    losing it silently. This carries the removed lines back to the caller so the
    CLI can print them and the web console can show them, and so "information is
    never lost without saying so" becomes a property that can be tested rather
    than hoped for.
    """

    slide_no: int
    title: str
    items: List[str] = Field(default_factory=list)


def _degrade_slide(slide, theme: Theme, slide_no: int = 0) -> Optional[DroppedContent]:
    """Truncate an over-budget slide's bullets in place until it fits.

    Last resort only: the caller has already asked the model to rewrite the page
    shorter (see ``_budget_feedback``), and this runs when that did not work. The
    order of sacrifice goes from least to most costly:

      1. If any bullet is a ``BulletItem`` carrying children, drop the **last**
         child of the last such bullet (children are finer-grained than items).
      2. Otherwise, if more than one bullet remains, drop the last bullet.
      3. Otherwise stop (always keep at least one bullet).

    Everything removed is both returned to the caller and named in ``notes``, so
    it survives into the .odp itself. A slide that overruns on a non-bullet frame
    (e.g. a very long title) has no bullets to shed — it is left as-is, and the
    budget never raises.
    """
    removed: List[str] = []
    while check_budget(slide, theme):
        child_idx: Optional[int] = None
        for i in range(len(slide.bullets) - 1, -1, -1):
            item = slide.bullets[i]
            if isinstance(item, BulletItem) and item.children:
                child_idx = i
                break
        if child_idx is not None:
            item = slide.bullets[child_idx]
            children = list(item.children)
            removed.append(children[-1])
            item.children = children[:-1]
        elif len(slide.bullets) > 1:
            bullets = list(slide.bullets)
            last = bullets[-1]
            removed.append(last if isinstance(last, str) else last.text)
            slide.bullets = bullets[:-1]
        else:
            break
    if not removed:
        return None
    removed.reverse()  # back into the order they appeared on the page
    note = "(因版面限制省略:" + "、".join(removed) + ")"
    slide.notes = f"{slide.notes}\n{note}" if slide.notes else note
    return DroppedContent(slide_no=slide_no, title=slide.title, items=removed)


class StructuredOutputError(RuntimeError):
    """The backend answered, but not with a payload we can use.

    Kept distinct from every other failure because the response is *different*:
    this one is the model's mistake, so the policy layer feeds the reason back
    and retries once. A provider 403 or a CLI that will not start is not this —
    those propagate, because asking again spends another minute to print the
    same sentence.
    """


# JSON Schema keywords that carry documentation, not constraints. They are
# dropped before a schema is handed to a strict validator: `title` in
# particular is both an annotation *and* a legal field name, and conflating the
# two is how a schema ends up requiring a key it just deleted.
_SCHEMA_ANNOTATIONS = frozenset({"title", "default", "examples", "$comment"})
# Keywords whose value maps *names* to schemas. Their keys are field names, so
# the walk must recurse into the values without filtering the keys.
_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions", "patternProperties"})


def _strict_schema(schema: object) -> object:
    """A pydantic JSON schema in OpenAI structured-output *strict* form.

    Strict mode rejects any object that does not carry ``additionalProperties:
    false`` and list every property in ``required``. Pydantic emits neither, so
    the schema is walked and tightened rather than maintained by hand beside the
    models — a second copy would drift the first time a field is added.

    Making every property required is safe here precisely because the models are
    the gate: an optional field arrives as ``null`` and pydantic puts the default
    back. Nothing downstream can tell the difference.
    """
    if isinstance(schema, list):
        return [_strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key in _SCHEMA_ANNOTATIONS:
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _strict_schema(sub) for name, sub in value.items()}
        else:
            out[key] = _strict_schema(value)
    if "properties" in out:
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out


class StructuredLLMBackend:
    """Everything a backend does *except* carry the bytes to a model.

    The four pipeline entry points below — 訪談、大綱、逐頁、單檔 IR — are policy:
    which prompt, how many retries, what gets fed back, when a bad palette may be
    stripped instead of raised, when an over-budget page is degraded instead of
    retried. None of that depends on whether the model is reached over HTTP or
    through a subprocess, so it lives here once and each transport implements
    :meth:`_structured_call`.

    Subclasses also supply the four model names the policy picks between
    (``model`` / ``discovery_model`` / ``outline_model`` / ``slides_model``);
    a backend with one model just points all four at it.
    """

    model: str
    discovery_model: str
    outline_model: str
    slides_model: str

    def _structured_call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict,
        name: str,
        description: str,
        max_tokens: int,
    ) -> dict:
        """One request → the structured payload it produced, as a dict.

        Raises :class:`StructuredOutputError` when the backend answered but the
        answer is unusable (no tool call, unreadable JSON, not an object) — the
        caller retries those. Anything else propagates untouched.
        """
        raise NotImplementedError

    def discover_questions(
        self,
        prompt: str,
        context: str = "",
        progress: Optional[Callable[[str], None]] = None,
    ) -> DiscoveryPlan:
        """Generate a short, adaptive interview before outline generation."""

        def report(stage: str) -> None:
            if progress is None:
                return
            try:
                progress(stage)
            except Exception:
                # Progress is observability only. A disconnected browser must
                # not invalidate an otherwise healthy model response.
                pass

        report("preparing")
        base_content = (
            f"【使用者原始需求】\n{prompt.strip()}\n\n"
            f"【介面已有設定】\n{context.strip() or '無'}"
        )
        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        for _attempt in range(2):
            user_content = _with_error_feedback(base_content, error_summary)
            report("requesting" if _attempt == 0 else "retrying")
            try:
                data = self._structured_call(
                    model=self.discovery_model,
                    system=DISCOVERY_SYSTEM_PROMPT,
                    user=user_content,
                    schema=DiscoveryPlan.model_json_schema(),
                    name=DISCOVERY_TOOL_NAME,
                    description="輸出需求摘要、已知資訊與高價值追問",
                    max_tokens=int(
                        os.environ.get("ODFORGE_DISCOVERY_MAX_TOKENS", "2048")
                    ),
                )
                report("validating")
                plan = DiscoveryPlan.model_validate(data)
            except (
                StructuredOutputError,
                json.JSONDecodeError,
                TypeError,
                ValidationError,
            ) as exc:
                error_summary = str(exc)
                last_exc = exc
                continue
            report("complete")
            return plan
        assert last_exc is not None
        raise last_exc

    def generate_ir(
        self, prompt: str, doc_type: str, language: str = "zh-TW"
    ) -> Document:
        ir_cls = DOC_TYPES.get(doc_type)
        if ir_cls is None:
            raise ValueError(
                f"未知的 doc_type: {doc_type!r};可用值:{sorted(DOC_TYPES)}"
            )

        # First attempt, then exactly one retry on failure.
        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        for _attempt in range(2):
            user_content = prompt
            if error_summary is not None:
                user_content = (
                    f"{prompt}\n\n"
                    f"[系統提示] 上一次的輸出無法通過驗證,錯誤如下,請修正後重新輸出:\n"
                    f"{error_summary}"
                )
            try:
                data = self._structured_call(
                    model=self.model,
                    system=system_prompt_for(SYSTEM_PROMPT, language),
                    user=user_content,
                    schema=ir_cls.model_json_schema(),
                    name=TOOL_NAME,
                    description="輸出結構化文件內容",
                    max_tokens=int(os.environ.get("ODFORGE_MAX_TOKENS", "8192")),
                )
                data["type"] = doc_type  # guard against a missing discriminator
                return parse_ir(data)
            except (
                StructuredOutputError,
                json.JSONDecodeError,
                TypeError,
                ValidationError,
            ) as exc:
                error_summary = str(exc)
                last_exc = exc
                continue

        assert last_exc is not None
        raise last_exc

    def generate_outline(
        self, prompt: str, pages: int | None = None, language: str = "zh-TW"
    ) -> Outline:
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

        ``pages`` (when given) folds a target page-count instruction into the
        user prompt (「目標頁數約 N 頁…」); it is a soft target, not a schema
        constraint. ``language`` picks the deck's output language and is
        re-attached to the returned outline, so stage 2 inherits it without the
        caller having to remember.
        """
        base_prompt = prompt
        if pages is not None:
            base_prompt = (
                f"{prompt}\n\n"
                f"【目標頁數】整份簡報約 {pages} 頁(含封面與結尾,可 ±1)。"
            )
        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            is_last = attempt == 1
            user_content = base_prompt
            if error_summary is not None:
                user_content = (
                    f"{base_prompt}\n\n"
                    f"[系統提示] 上一次的輸出無法通過驗證,錯誤如下,請修正後重新輸出:\n"
                    f"{error_summary}"
                )
            try:
                data = self._structured_call(
                    model=self.outline_model,
                    system=system_prompt_for(OUTLINE_SYSTEM_PROMPT, language),
                    user=user_content,
                    schema=Outline.model_json_schema(),
                    name=OUTLINE_TOOL_NAME,
                    description="輸出簡報的美術方向(DesignSpec)與 page-role 大綱",
                    max_tokens=int(os.environ.get("ODFORGE_MAX_TOKENS", "8192")),
                )
            except (StructuredOutputError, json.JSONDecodeError, TypeError) as exc:
                error_summary = str(exc)
                last_exc = exc
                continue

            try:
                return Outline.model_validate(data).model_copy(
                    update={"source_prompt": prompt, "language": language}
                )
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
                        return Outline.model_validate(stripped).model_copy(
                            update={"source_prompt": prompt, "language": language}
                        )
                    # First design failure: feed the error back, retry once.
                    continue
                # Pages are structurally invalid: retry-once-then-raise.
                continue

        assert last_exc is not None
        raise last_exc

    def _emit_slides(
        self,
        system: str,
        user: str,
        outline: Outline,
    ) -> tuple[Optional[Presentation], Optional[Exception]]:
        """One stage-2 round.

        Returns ``(Presentation, None)`` when the call yields a structurally
        valid deck (outline's design re-attached, slide count + layouts aligned),
        otherwise ``(None, exc)`` whose ``str(exc)`` is fed back as the retry hint.
        """
        try:
            data = self._structured_call(
                model=self.slides_model,
                system=system,
                user=user,
                schema=Presentation.model_json_schema(),
                name=SLIDES_TOOL_NAME,
                description="輸出填好每頁內容的完整簡報(Presentation)",
                # Stage-2 fills every page, so it defaults higher than the
                # one-shot generate_ir/generate_outline paths (8192). Env
                # override still wins.
                max_tokens=int(os.environ.get("ODFORGE_MAX_TOKENS", "16384")),
            )
        except (StructuredOutputError, json.JSONDecodeError, TypeError) as exc:
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

    def generate_slides(
        self, outline: Outline, dropped: Optional[List[DroppedContent]] = None
    ) -> Presentation:
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

        The deck's output language rides along on ``outline.language`` (set by
        stage 1 / the caller), so no call site has to pass it twice.
        """
        slides_system = system_prompt_for(SLIDES_SYSTEM_PROMPT, outline.language)
        base_user = _slides_user_content(outline)

        # -- Gate 1: obtain a structurally valid deck (retry once, then raise) --
        error_summary: Optional[str] = None
        last_exc: Optional[Exception] = None
        presentation: Optional[Presentation] = None
        for _attempt in range(2):
            pres, exc = self._emit_slides(
                slides_system,
                _with_error_feedback(base_user, error_summary),
                outline,
            )
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

        retry_pres, _exc = self._emit_slides(
            slides_system,
            base_user
            + "\n\n"
            + _budget_feedback(overloads, presentation.model_dump_json()),
            outline,
        )
        if retry_pres is not None:
            # A structurally valid (hopefully lighter) deck. If the retry itself
            # failed structurally we keep the previous deck and degrade that.
            presentation = retry_pres

        theme = resolve_design(presentation)
        for n, _msgs in _budget_overloads(presentation, theme):
            record = _degrade_slide(presentation.slides[n - 1], theme, slide_no=n)
            if record is not None and dropped is not None:
                dropped.append(record)
        return presentation


class OpenAICompatBackend(StructuredLLMBackend):
    """Backend for any OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        discovery_model: str | None = None,
        outline_model: str | None = None,
        slides_model: str | None = None,
        extra_body: dict | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.outline_model = outline_model or model
        self.discovery_model = discovery_model or self.outline_model
        self.slides_model = slides_model or model
        self.extra_body = dict(extra_body or {})
        self._client = OpenAI(base_url=base_url, api_key=api_key)

    def _structured_call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict,
        name: str,
        description: str,
        max_tokens: int,
    ) -> dict:
        """A forced function call; its ``arguments`` are the payload.

        The schema goes over the wire exactly as pydantic emits it — no strict
        tightening. Chat-completions tool schemas are a *description* of the
        shape, not a validator the provider enforces, and every provider here
        already tolerates the pydantic dialect.
        """
        resp = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": name}},
            max_tokens=max_tokens,
            **({"extra_body": self.extra_body} if self.extra_body else {}),
        )
        tool_calls = resp.choices[0].message.tool_calls
        if not tool_calls:
            raise StructuredOutputError(
                f"模型未呼叫 {name} 工具,請務必透過該工具輸出。"
            )
        return _loads_tool_args(tool_calls[0].function.arguments)


# One CLI, one model choice: the design gate imports this rather than keeping
# its own copy (critic.py), because a text/vision split here would mean two
# different models claiming to be "the codex backend".
#
# Naming a model explicitly rather than taking the CLI's own default is not
# caution about taste — an id the installed CLI cannot drive fails with
# "requires a newer version of Codex" (that is exactly what `gpt-5.6-terra`
# did on codex-cli 0.142.5; it needs >= 0.147.0).
CODEX_MODEL_DEFAULT = "gpt-5.6-terra"
# Stage 2 fills every page of a deck in one call; 600s (the design gate's
# budget) is not enough headroom for a long one.
_CODEX_TIMEOUT_DEFAULT = "1800"


class CodexCliBackend(StructuredLLMBackend):
    """Text generation driven through the local Codex CLI as a subprocess.

    The sibling of :class:`odforge.critic.CodexCliVisionBackend`, and it inherits
    that class's hard-won argv: the prompt rides **stdin** (on Windows the CLI is
    ``codex.CMD``, whose cmd.exe command line caps at 8,191 characters — a
    stage-2 prompt is far past that), reasoning effort is pinned rather than
    inherited from the operator's ``~/.codex/config.toml``, and the sandbox is
    read-only because writing a deck is this process's job, not the model's.

    ``--output-schema`` plays the part forced function calling plays on the HTTP
    backends — with one difference that matters: the CLI hands the schema to
    OpenAI's *strict* structured-output mode, which enforces it. Hence
    :func:`_strict_schema`, and hence a payload that is almost always already
    valid by the time pydantic sees it.

    There is no API key and no per-key quota here: the CLI authenticates with the
    operator's own ChatGPT login.
    """

    def __init__(
        self,
        model: str,
        *,
        executable: str = "codex",
        discovery_model: str | None = None,
        outline_model: str | None = None,
        slides_model: str | None = None,
        timeout: int | None = None,
        runner=subprocess.run,
    ):
        self.model = model
        self.outline_model = outline_model or model
        self.discovery_model = discovery_model or self.outline_model
        self.slides_model = slides_model or model
        # The *resolved* path, not the bare name: on Windows the CLI installs as
        # ``codex.CMD``, which ``subprocess.run(["codex", ...])`` cannot execute.
        self.executable = executable
        self.timeout = (
            timeout
            if timeout is not None
            else int(os.environ.get("ODFORGE_CODEX_TIMEOUT", _CODEX_TIMEOUT_DEFAULT))
        )
        self._run = runner

    def _structured_call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict,
        name: str,
        description: str,
        max_tokens: int,
    ) -> dict:
        # ``max_tokens`` has no CLI equivalent — the length budget belongs to the
        # model's own configuration here. It stays in the signature because the
        # policy layer is transport-agnostic, not because this backend needs it.
        with tempfile.TemporaryDirectory(prefix="odforge-codex-") as tmp:
            work = Path(tmp)
            schema_path = work / "schema.json"
            # UTF-8 explicitly: the CLI rejects a schema file it cannot decode,
            # and Python's default encoding on Windows is not UTF-8.
            schema_path.write_text(
                json.dumps(_strict_schema(schema), ensure_ascii=False),
                encoding="utf-8",
            )
            out_path = work / "payload.json"
            argv = [
                self.executable,
                "exec",
                "-m",
                model,
                "--output-schema",
                str(schema_path),
                "-o",
                str(out_path),
                "-c",
                "model_reasoning_effort="
                + os.environ.get("ODFORGE_CODEX_REASONING_EFFORT", "medium"),
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                # Explicit "read the prompt from stdin" marker; omitting the
                # positional is merely today's synonym for it.
                "-",
            ]
            # ``codex exec`` has no system/user split, so the system prompt
            # travels at the head of the one prompt there is.
            prompt = f"{system}\n\n{user}"
            try:
                proc = self._run(
                    argv,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                )
            except (subprocess.TimeoutExpired, OSError, UnicodeError) as exc:
                raise RuntimeError(
                    f"codex exec 無法執行:{type(exc).__name__}: {exc}"
                ) from exc
            returncode = getattr(proc, "returncode", 0)
            if returncode:
                # Not a StructuredOutputError: the CLI never got as far as
                # answering, so there is nothing to feed back and retrying just
                # spends the time again.
                detail = " ".join((getattr(proc, "stderr", "") or "").split())[-300:]
                raise RuntimeError(
                    f"codex exec 結束碼 {returncode}"
                    + (f":{detail}" if detail else "")
                )
            try:
                data = json.loads(out_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StructuredOutputError(
                    f"codex exec 沒有輸出可解析的 {name} JSON:{type(exc).__name__}"
                ) from exc
        if not isinstance(data, dict):
            raise StructuredOutputError(
                f"codex exec 的 {name} 輸出不是 JSON 物件,而是 {type(data).__name__}"
            )
        return data


# ---------------------------------------------------------------------------
# Backend registry + facade
# ---------------------------------------------------------------------------


def _codex_auth_path() -> Path:
    """Where the Codex CLI keeps its OAuth credentials."""
    home = os.environ.get("CODEX_HOME")
    return (Path(home) if home else Path.home() / ".codex") / "auth.json"


def _make_codex() -> LLMBackend:
    """Text backend through the local Codex CLI (subscription-authenticated).

    Refuses on three conditions, each with a fixable message: no CLI, no login,
    and — the one that is not about convenience — a publicly-bound server.
    ODForge's web console has no authentication, and OpenAI's own guidance is not
    to expose Codex execution in untrusted environments; on a non-loopback bind
    this backend would spend the operator's ChatGPT subscription for whoever
    reaches the port. ``serve`` sets ``ODFORGE_PUBLIC_BIND`` when it binds beyond
    loopback. (The design gate's codex source refuses on the same three — see
    ``critic._make_codex``.)
    """
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError(
            "找不到 codex CLI。請先安裝 Codex(npm i -g @openai/codex)後再使用此後端。"
        )
    if not _codex_auth_path().exists():
        raise RuntimeError(
            "Codex 尚未登入。請先執行 codex login(無瀏覽器環境用 "
            "codex login --device-auth)。"
        )
    if os.environ.get("ODFORGE_PUBLIC_BIND"):
        raise RuntimeError(
            "codex 後端僅限本機使用:此服務沒有身分驗證,對外綁定時任何人都能"
            "花用你的 ChatGPT 訂閱額度。請改用 API key 後端,或只綁定 127.0.0.1。"
        )
    return CodexCliBackend(
        os.environ.get("ODFORGE_CODEX_MODEL", CODEX_MODEL_DEFAULT),
        executable=executable,
        discovery_model=os.environ.get("ODFORGE_CODEX_DISCOVERY_MODEL"),
        outline_model=os.environ.get("ODFORGE_CODEX_OUTLINE_MODEL"),
        slides_model=os.environ.get("ODFORGE_CODEX_SLIDES_MODEL"),
    )


def _custom_extra_body() -> dict | None:
    """Parse optional provider-specific OpenAI-compatible request fields."""

    raw = os.environ.get("ODFORGE_CUSTOM_EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "ODFORGE_CUSTOM_EXTRA_BODY must be a valid JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(
            "ODFORGE_CUSTOM_EXTRA_BODY must be a JSON object"
        )
    return value


BACKENDS: Dict[str, Callable[[], LLMBackend]] = {
    "codex": _make_codex,
    "deepseek": lambda: OpenAICompatBackend(
        "https://api.deepseek.com",
        _require_env("DEEPSEEK_API_KEY"),
        os.environ.get("ODFORGE_MODEL", "deepseek-chat"),
        discovery_model=os.environ.get("ODFORGE_DISCOVERY_MODEL"),
        outline_model=os.environ.get("ODFORGE_OUTLINE_MODEL"),
        slides_model=os.environ.get("ODFORGE_SLIDES_MODEL"),
    ),
    "ollama": lambda: OpenAICompatBackend(
        "http://localhost:11434/v1",
        "ollama",
        os.environ.get("ODFORGE_OLLAMA_MODEL", "qwen2.5"),
        discovery_model=os.environ.get("ODFORGE_OLLAMA_DISCOVERY_MODEL"),
        outline_model=os.environ.get("ODFORGE_OLLAMA_OUTLINE_MODEL"),
        slides_model=os.environ.get("ODFORGE_OLLAMA_SLIDES_MODEL"),
    ),
    "custom": lambda: OpenAICompatBackend(
        _require_env("ODFORGE_CUSTOM_BASE_URL"),
        os.environ.get("ODFORGE_CUSTOM_API_KEY", "not-needed"),
        _require_env("ODFORGE_CUSTOM_MODEL"),
        discovery_model=os.environ.get("ODFORGE_CUSTOM_DISCOVERY_MODEL"),
        outline_model=os.environ.get("ODFORGE_CUSTOM_OUTLINE_MODEL"),
        slides_model=os.environ.get("ODFORGE_CUSTOM_SLIDES_MODEL"),
        extra_body=_custom_extra_body(),
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
    prompt: str,
    doc_type: str,
    backend: Optional[str] = None,
    language: str = "zh-TW",
) -> Document:
    """Facade: resolve a backend and generate a validated Document IR."""
    return get_backend(backend).generate_ir(prompt, doc_type, language)


def generate_discovery_questions(
    prompt: str,
    backend: Optional[str] = None,
    *,
    context: str = "",
    progress: Optional[Callable[[str], None]] = None,
) -> DiscoveryPlan:
    """Facade: ask only the missing, high-value questions for a short prompt.

    Delivery-deadline questions are dropped on the way out (see
    :func:`_without_delivery_deadline_questions`) — the prompt forbids them, this
    is the net for when the model asks anyway.
    """
    resolved = get_backend(backend)
    if progress is None:
        plan = resolved.discover_questions(prompt, context=context)
    else:
        plan = resolved.discover_questions(
            prompt, context=context, progress=progress
        )
    return _without_delivery_deadline_questions(plan)


def generate_outline(
    prompt: str,
    backend: Optional[str] = None,
    pages: int | None = None,
    language: str = "zh-TW",
) -> Outline:
    """Facade: resolve a backend and generate a stage-1 design + outline.

    The chosen ``language`` is stamped onto the returned outline, so stage 2
    (``generate_slides``) inherits it without a second parameter.
    """
    return get_backend(backend).generate_outline(
        prompt, pages=pages, language=language
    )


def generate_slides(
    outline: Outline,
    backend: Optional[str] = None,
    dropped: Optional[List[DroppedContent]] = None,
) -> Presentation:
    """Facade: resolve a backend and fill a stage-1 outline into a full deck.

    Pass a list as ``dropped`` to be told what the layout budget had to remove.
    Callers that ignore it keep the old behaviour (the loss is still named in the
    slide's speaker notes) — but every user-facing caller should pass one:
    content vanishing with no visible trace is the failure this exists to prevent.
    """
    return get_backend(backend).generate_slides(outline, dropped)
