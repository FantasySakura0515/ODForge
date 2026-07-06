# ODForge 開發計畫(SDD / TDD)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打造 ODForge——自然語言 → 原生 ODF(.odt/.odp/.ods)的開發者工具,含 CLI 與 MCP server,48 小時內完成競賽 MVP。

**Architecture:** LLM 只產出 Document IR(pydantic 驗證的 JSON),確定性渲染引擎把 IR 轉成原生 ODF XML;.odt/.ods 走 odfdo,.odp 走自寫 ODF package writer(mimetype 規則 + 手寫 content.xml/styles.xml + 資料驅動的版面系統)。每個輸出檔過三道驗證關卡。

**Tech Stack:** Python ≥3.10、pydantic v2、odfdo ≥3.22、typer + rich、anthropic SDK、mcp(FastMCP)、lxml、pytest。

**Spec(SDD 規格源):** `ODForge-SPEC.md`。每個 Phase 標註 [SPEC §n]。規格與計畫衝突時以 SPEC 為準,並回頭修 SPEC。

## Global Constraints

- Python 版本下限 **3.10**(使用者機器是 3.10.5,不得用 3.11+ 語法如 `Self`、`except*`)。
- 所有檔案 I/O 一律 `encoding="utf-8"`(Windows cp950 陷阱)。
- 產出文件預設語言 `zh-TW`,中文字型 `Noto Sans TC`,fallback `微軟正黑體`。
- ODF mimetype 常數(逐字):
  - odt: `application/vnd.oasis.opendocument.text`
  - odp: `application/vnd.oasis.opendocument.presentation`
  - ods: `application/vnd.oasis.opendocument.spreadsheet`
- zip 打包鐵律:`mimetype` 必須是**第一個 entry 且 ZIP_STORED 不壓縮**。
- LLM 後端抽象化(定案):`LLMBackend` Protocol,**預設後端 DeepSeek**(OpenAI 相容 API),可切 ollama / claude / 任何 OpenAI 相容端點。環境變數:`DEEPSEEK_API_KEY`(**只放環境變數,絕不寫進 repo——repo 之後要公開**)、`ODFORGE_BACKEND`(預設 `deepseek`)、`ODFORGE_MODEL`(預設 `deepseek-chat`)。
- LLM 絕不直接產 XML,只產 IR JSON [SPEC §1 關鍵設計決策]。
- git repo 在 ODF 根目錄(`c:\Users\User\Desktop\project\ODF\`,remote:`FantasySakura0515/ODForge`),程式碼在 `odforge/` 子資料夾(MIT license)。
- commit message 一律乾淨的 conventional commits,**不加任何 AI 署名或 co-author trailer**;每完成一個 task 就 commit + push 到 origin。
- `報名文件/` 目錄含個人資料,已列入 .gitignore,永不入版控。
- 每個 Phase 結束跑全量 `pytest`,綠燈才進下一個 Phase。

## 檔案結構(定案,不再變)

```
odforge/
├── pyproject.toml
├── README.md
├── LICENSE                     # MIT
├── src/odforge/
│   ├── __init__.py             # __version__
│   ├── ir.py                   # Document IR:pydantic 模型 + parse_ir()
│   ├── package.py              # ODF zip package writer(mimetype 規則、manifest)
│   ├── themes.py               # Theme / Frame / LAYOUTS / THEMES(.odp 資料驅動版面)
│   ├── render/
│   │   ├── __init__.py         # render(ir, out_path) 依 type 分派
│   │   ├── odt.py              # TextDoc → .odt(odfdo)
│   │   ├── odp.py              # Presentation → .odp(package.py + themes.py)
│   │   └── ods.py              # Spreadsheet → .ods(odfdo)
│   ├── validate.py             # 三道驗證關卡 + find_soffice()
│   ├── llm.py                  # generate_ir(prompt, doc_type):Claude structured output
│   ├── check.py                # odforge check:結構報告 + docx↔odt diff
│   ├── cli.py                  # typer app:new / check
│   └── mcp_server.py           # FastMCP:forge_* 工具(收 IR,不收 prompt)
└── tests/
    ├── conftest.py             # 共用 fixture:sample IR × 3
    ├── test_ir.py
    ├── test_package.py
    ├── test_render_odt.py
    ├── test_render_odp.py
    ├── test_render_ods.py
    ├── test_validate.py
    ├── test_llm.py             # 全部 mock,不打真 API
    ├── test_check.py
    └── test_cli.py
```

**介面總覽(所有 Phase 共用的契約):**

```python
# ir.py
parse_ir(data: dict) -> TextDoc | Presentation | Spreadsheet   # discriminator="type"
TextDoc.model_json_schema() 等                                  # 給 LLM/MCP 用

# package.py
write_odf_package(path: Path, mimetype: str, parts: dict[str, str]) -> Path

# render/__init__.py
render(ir, out_path: Path) -> Path                              # 依 ir.type 分派

# validate.py
validate_odf(path: Path, *, with_soffice: bool = False) -> ValidationReport
find_soffice() -> Path | None

# llm.py — 後端抽象(定案)
class LLMBackend(Protocol):
    def generate_ir(self, prompt: str, doc_type: str) -> IR: ...

class OpenAICompatBackend(LLMBackend):        # 一個類別吃下 DeepSeek / Ollama / OpenAI / Groq...
    def __init__(self, base_url: str, api_key: str, model: str): ...

BACKENDS: dict[str, Callable[[], LLMBackend]] # "deepseek" | "ollama" | (加分) "claude"
get_backend(name: str | None = None) -> LLMBackend   # None → 讀 ODFORGE_BACKEND,預設 "deepseek"
generate_ir(prompt: str, doc_type: str, backend: str | None = None) -> IR  # 門面函式

# check.py
check_odf(path: Path) -> str                                    # markdown 報告
diff_docx_odt(docx: Path) -> str                                # markdown 報告
```

---

## Phase 0:專案鷹架 [SPEC §6]

### Task 0.1: repo + 環境 + 測試骨架

**Files:** Create `pyproject.toml`, `README.md`, `LICENSE`, `src/odforge/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: 建 repo 與 venv**

```powershell
mkdir c:\Users\User\Desktop\project\ODF\odforge; cd c:\Users\User\Desktop\project\ODF\odforge
git init
python -m venv .venv; .\.venv\Scripts\Activate.ps1
```

- [ ] **Step 2: 寫 pyproject.toml(完整內容)**

```toml
[project]
name = "odforge"
version = "0.1.0"
description = "Forge natural language into native ODF documents (.odt/.odp/.ods)"
requires-python = ">=3.10"
license = { text = "MIT" }
dependencies = [
  "odfdo>=3.22", "pydantic>=2.7", "typer>=0.12", "rich>=13",
  "openai>=1.40", "mcp>=1.2", "lxml>=5",
]
# anthropic SDK 移到 Phase 9 加分(claude 後端)才裝:optional-dependencies claude = ["anthropic>=0.40"]
[project.optional-dependencies]
dev = ["pytest>=8"]
[project.scripts]
odforge = "odforge.cli:app"
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/odforge"]
[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: `src/odforge/__init__.py` 寫 `__version__ = "0.1.0"`;安裝 `pip install -e .[dev]`**
- [ ] **Step 4: 冒煙測試 `tests/test_ir.py::test_import`:`import odforge; assert odforge.__version__`,跑 `pytest -v` 綠燈**
- [ ] **Step 5: `.gitignore`(.venv/、__pycache__/、*.egg-info、dist/)+ README 一段話 + LICENSE,commit `chore: scaffold odforge project`**

---

## Phase 1:Document IR [SPEC §2]

### Task 1.1: IR pydantic 模型

**Files:** Create `src/odforge/ir.py`, Test `tests/test_ir.py`, `tests/conftest.py`

**Interfaces — Produces(後面所有 Phase 都吃這個):**

```python
# 區塊(discriminator="kind")
HeadingBlock(kind="heading", level: int[1..6], text: str)
ParagraphBlock(kind="paragraph", text: str, style: "body"|"quote"|"note" = "body")
ListBlock(kind="list", ordered: bool = False, items: list[str])
TableBlock(kind="table", header: list[str], rows: list[list[str]])
TocBlock(kind="toc"); PageBreakBlock(kind="pagebreak")

TextDoc(type="text", title: str, lang: str = "zh-TW", blocks: list[Block])

Slide(layout: "title"|"title-content"|"two-col"|"section"|"big-fact",
      title: str = "", subtitle: str = "", bullets: list[str] = [],
      left: list[str] = [], right: list[str] = [], fact: str = "", notes: str = "")
Presentation(type="presentation", title: str,
             theme: "academic"|"minimal"|"dark" = "academic", slides: list[Slide])

FormulaSpec(cell: str, formula: str)          # formula 必須 "of:=" 開頭(validator)
Sheet(name: str, columns: list[str], rows: list[list[str|int|float]],
      formulas: list[FormulaSpec] = [])
Spreadsheet(type="spreadsheet", title: str, sheets: list[Sheet])

parse_ir(data: dict) -> TextDoc | Presentation | Spreadsheet  # TypeAdapter(discriminator="type")
```

- [ ] **Step 1: 寫失敗測試(節錄,三型別都要)**

```python
def test_parse_text_doc():
    ir = parse_ir({"type": "text", "title": "測試", "blocks": [
        {"kind": "heading", "level": 1, "text": "第一章"},
        {"kind": "paragraph", "text": "內文"}]})
    assert isinstance(ir, TextDoc) and ir.blocks[0].level == 1

def test_heading_level_out_of_range_rejected():
    with pytest.raises(ValidationError):
        parse_ir({"type": "text", "title": "x",
                  "blocks": [{"kind": "heading", "level": 9, "text": "x"}]})

def test_parse_presentation_default_theme():
    ir = parse_ir({"type": "presentation", "title": "簡報", "slides": [
        {"layout": "title", "title": "封面", "subtitle": "副標"}]})
    assert ir.theme == "academic"

def test_formula_must_use_of_namespace():
    with pytest.raises(ValidationError):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "B1", "formula": "=SUM(A1)"}])  # 缺 "of:" 前綴

def test_schema_exportable():
    assert "properties" in Presentation.model_json_schema()
```

- [ ] **Step 2: `pytest tests/test_ir.py -v` → FAIL(ImportError)**
- [ ] **Step 3: 實作 `ir.py`(依上方介面,`Annotated[Union[...], Field(discriminator=...)]` + `TypeAdapter`;FormulaSpec 用 `field_validator` 檢查 `of:=` 前綴)**
- [ ] **Step 4: `pytest tests/test_ir.py -v` → PASS**
- [ ] **Step 5: `conftest.py` 加 fixture `sample_text_doc` / `sample_presentation`(5 張投影片、五種 layout 各一、含 notes)/ `sample_spreadsheet`(含公式),commit `feat: document IR models`**

---

## Phase 2:.odt 渲染器 [SPEC §3-odt]

### Task 2.1: TextDoc → .odt

**Files:** Create `src/odforge/render/__init__.py`, `src/odforge/render/odt.py`, Test `tests/test_render_odt.py`

**Interfaces — Produces:** `render_odt(doc: TextDoc, out_path: Path) -> Path`;`render/__init__.py` 的 `render(ir, out_path)` 分派表。

**實作前置:** 先用 context7 查 odfdo 最新 API(Document/Header/Paragraph/List/Table/TOC 的正確用法),不要憑記憶寫。

- [ ] **Step 1: 失敗測試——用 zipfile+lxml 驗證,不依賴 odfdo 讀回(測試與實作解耦)**

```python
NS = {"text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
      "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0"}

def content_xml(path):
    with zipfile.ZipFile(path) as z:
        return lxml.etree.fromstring(z.read("content.xml"))

def test_mimetype(tmp_path, sample_text_doc):
    out = render_odt(sample_text_doc, tmp_path / "d.odt")
    with zipfile.ZipFile(out) as z:
        assert z.read("mimetype").decode() == "application/vnd.oasis.opendocument.text"

def test_heading_and_paragraph(tmp_path, sample_text_doc):
    root = content_xml(render_odt(sample_text_doc, tmp_path / "d.odt"))
    hs = root.findall(".//text:h", NS)
    assert hs and hs[0].get("{%s}outline-level" % NS["text"]) == "1"
    assert any("內文" in (p.text or "") for p in root.findall(".//text:p", NS))

def test_table_rendered(tmp_path):  # header 列 + 資料列
    doc = TextDoc(title="t", blocks=[TableBlock(header=["科目","分數"], rows=[["資結","95"]])])
    root = content_xml(render_odt(doc, tmp_path / "d.odt"))
    assert len(root.findall(".//table:table-row", NS)) == 2

def test_list_ordered_flag(tmp_path): ...   # text:list 存在;ordered 用編號 list style
def test_toc_present(tmp_path): ...          # text:table-of-content 節點存在
```

- [ ] **Step 2: 跑 → FAIL**
- [ ] **Step 3: 實作 `odt.py`:odfdo `Document("text")`;block 分派 dict(kind → handler);具名樣式:Heading1-3(深藍 `#1A3C6E`)、quote(左縮排+斜體)、note(灰字);`office:font-face-decls` 註冊 Noto Sans TC。`render/__init__.py`:`render()` 依 `ir.type` 查表分派**
- [ ] **Step 4: 跑 → PASS;手動抽查:生成 fixture 檔案用 LibreOffice 開一次(人工關卡,截圖存 `docs/screenshots/`)**
- [ ] **Step 5: commit `feat: odt renderer with named styles`**

---

## Phase 3:ODF package writer [SPEC §3-odp 前置]

### Task 3.1: zip 打包 + manifest

**Files:** Create `src/odforge/package.py`, Test `tests/test_package.py`

**Interfaces — Produces:** `write_odf_package(path, mimetype, parts: dict[str,str]) -> Path`(parts = {"content.xml": xml字串, "styles.xml": ..., "meta.xml": ...};manifest 自動生成)。

- [ ] **Step 1: 失敗測試**

```python
def test_mimetype_is_first_and_stored(tmp_path):
    out = write_odf_package(tmp_path / "t.odp", ODP_MIMETYPE,
                            {"content.xml": "<a/>", "styles.xml": "<b/>"})
    with zipfile.ZipFile(out) as z:
        infos = z.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype").decode() == ODP_MIMETYPE

def test_manifest_lists_all_parts(tmp_path):
    out = write_odf_package(tmp_path / "t.odp", ODP_MIMETYPE, {"content.xml": "<a/>"})
    m = zipfile.ZipFile(out).read("META-INF/manifest.xml").decode()
    assert 'manifest:full-path="/"' in m and 'manifest:full-path="content.xml"' in m
```

- [ ] **Step 2: FAIL → Step 3: 實作(`ZipInfo("mimetype")` 預設 ZIP_STORED 先寫入;其餘 ZIP_DEFLATED;manifest 模板逐字用 SPEC 的 namespace `urn:oasis:names:tc:opendocument:xmlns:manifest:1.0`、version 1.2)→ Step 4: PASS → Step 5: commit `feat: odf package writer`**

---

## Phase 4:.odp 渲染器(最大技術風險,demo 核心)[SPEC §3-odp]

### Task 4.1: 主題與版面資料(themes.py)

**Files:** Create `src/odforge/themes.py`, Test `tests/test_render_odp.py`(先放 themes 測試)

**Interfaces — Produces:**

```python
@dataclass(frozen=True)
class Frame: role: str; x: float; y: float; w: float; h: float; size_pt: int; bold: bool = False; center: bool = False
@dataclass(frozen=True)
class Theme: bg: str; title_color: str; text_color: str; accent: str; font: str = "Noto Sans TC"

LAYOUTS: dict[str, list[Frame]]   # 五種 layout,單位 cm,頁面 28×15.75(16:9)
THEMES:  dict[str, Theme]         # academic(MVP)/minimal/dark(Phase 9 補)
```

版面幾何(定案,實作照抄):

| layout | frames(role @ x,y,w,h cm / pt) |
|---|---|
| title | title @ 2,5.5,24,3 /40pt bold center;subtitle @ 2,9,24,2 /20pt center |
| title-content | title @ 1.5,0.8,25,2 /28pt bold;bullets @ 1.5,3.5,25,11 /18pt |
| two-col | title 同上;left @ 1.5,3.5,12,11 /16pt;right @ 14.5,3.5,12,11 /16pt |
| section | title @ 2,6.5,24,3 /36pt bold center |
| big-fact | fact @ 2,5,24,4 /48pt bold center;bullets @ 2,10,24,3 /16pt center |

- [ ] **Step 1: 測試:五種 layout 都存在、frame 不出界(x+w ≤ 28, y+h ≤ 15.75)、三主題色碼是合法 hex → Step 2: FAIL → Step 3: 照表實作 → Step 4: PASS → Step 5: commit `feat: odp themes and layout data`**

### Task 4.2: Presentation → .odp

**Files:** Create `src/odforge/render/odp.py`, Test `tests/test_render_odp.py`

**Interfaces — Produces:** `render_odp(p: Presentation, out_path: Path) -> Path`;內部 `build_content_xml(p) -> str`、`build_styles_xml(theme) -> str`(純函式,直接可測)。

- [ ] **Step 1: 失敗測試**

```python
NS = {"draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
      "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
      "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"}

def test_one_page_per_slide(sample_presentation, tmp_path):
    root = content_xml(render_odp(sample_presentation, tmp_path / "p.odp"))
    assert len(root.findall(".//draw:page", NS)) == len(sample_presentation.slides)

def test_title_text_present(sample_presentation, tmp_path): ...
    # 第一張 slide 的 title 文字出現在第一個 draw:page 的 text:p 裡

def test_notes_rendered(sample_presentation, tmp_path):
    # 有 notes 的 slide,其 draw:page 下存在 presentation:notes 節點且含文字
def test_two_col_has_two_content_frames(tmp_path): ...
def test_styles_xml_has_master_page_and_theme_bg(tmp_path):
    # styles.xml 含 style:master-page;drawing-page 填色 = THEMES["academic"].bg
def test_xml_escaping(tmp_path):
    # slide title 含 <>&,輸出仍是 well-formed XML(lxml 解析不炸)
```

- [ ] **Step 2: FAIL**
- [ ] **Step 3: 實作。骨架(核心邏輯,實作照此結構):**

```python
def render_odp(p, out_path):
    theme = THEMES[p.theme]
    parts = {"content.xml": build_content_xml(p, theme),
             "styles.xml": build_styles_xml(theme),
             "meta.xml": build_meta_xml(p.title)}
    return write_odf_package(out_path, ODP_MIMETYPE, parts)

def build_content_xml(p, theme):
    # 對每張 slide:LAYOUTS[slide.layout] 逐 frame 產 <draw:frame><draw:text-box>
    # frame 內容:role → getattr 對應欄位;bullets/left/right 每項一個 text:p
    # 自動樣式:每個 (size_pt, bold, center, color) 組合生成一個 style:style
    #   family="paragraph"(fo:text-align)+ text-properties(fo:font-size, fo:font-weight,
    #   fo:color, style:font-name=theme.font),名稱 P1, P2... 去重快取
    # notes:<presentation:notes><draw:frame(整頁)><draw:text-box><text:p>{notes}
    # 全部文字過 xml.sax.saxutils.escape
```

  styles.xml 要點:`office:font-face-decls`(Noto Sans TC + 微軟正黑體 fallback)、`style:page-layout`(28cm×15.75cm、margin 0)、`draw:fill="solid" draw:fill-color="{theme.bg}"` 的 drawing-page 樣式、`style:master-page name="Standard"`。
- [ ] **Step 4: PASS + 人工關卡:LibreOffice Impress 開 fixture 簡報,五種版面各截圖一張存 `docs/screenshots/`(這些截圖就是佐證素材)**
- [ ] **Step 5: commit `feat: odp renderer with data-driven layouts`**

---

## Phase 5:驗證關卡 [SPEC §3-驗證]

### Task 5.1: validate_odf 三關 + find_soffice

**Files:** Create `src/odforge/validate.py`, Test `tests/test_validate.py`

**Interfaces — Produces:**

```python
@dataclass
class ValidationReport: ok: bool; gates: dict[str, tuple[bool, str]]  # gate名 → (過/不過, 訊息)
validate_odf(path, *, with_soffice=False) -> ValidationReport
find_soffice() -> Path | None   # 查 PATH + C:\Program Files\LibreOffice\program\soffice.exe
```

- [ ] **Step 1: 失敗測試:①三種 render fixture 檔 gate1(mimetype 第一且 STORED、值對)+ gate2(content.xml well-formed、manifest 覆蓋所有 part)全過;②手工壞檔(mimetype 放第二個 entry)gate1 fail;③`@pytest.mark.skipif(find_soffice() is None)` 的 gate3:soffice `--headless --convert-to pdf` returncode 0 且 pdf 存在**
- [ ] **Step 2: FAIL → Step 3: 實作(subprocess 跑 soffice 加 `--outdir` 到 tmp;timeout 120s)→ Step 4: PASS → Step 5: commit `feat: three-gate odf validation`**

---

## Phase 6:LLM 層 + CLI [SPEC §1、§4-CLI]

### Task 6.1: LLM 後端抽象層(預設 DeepSeek)

**Files:** Create `src/odforge/llm.py`, Test `tests/test_llm.py`

**實作前置:** context7 查 DeepSeek API 現行文件(OpenAI 相容:`base_url="https://api.deepseek.com"`,function calling 的 tools/tool_choice 寫法與 json 模式限制)。

**Interfaces — Produces:**(見上方介面總覽的 llm.py 區塊)`LLMBackend` Protocol、`OpenAICompatBackend`、`BACKENDS` 註冊表、`get_backend()`、門面 `generate_ir(prompt, doc_type, backend=None)`;`SYSTEM_PROMPT` 常數(要求 zh-TW、教學場景、簡報 8–20 張、每張 bullets ≤5 條每條 ≤30 字、適時用 section/big-fact 版面、一定要寫 notes)。

**設計要點:**
- `OpenAICompatBackend` 用 openai SDK(`OpenAI(base_url=..., api_key=...)`),function calling 強制吐 IR:`tools=[{"type":"function","function":{"name":"emit_document","parameters": <IR schema>}}]` + `tool_choice={"type":"function","function":{"name":"emit_document"}}`;若模型不支援強制 tool_choice,fallback 走 `response_format={"type":"json_object"}` + schema 塞 system prompt,回來一律過 `parse_ir` 硬驗證(壞 JSON 重試 1 次)。
- 註冊表(新增後端 = 加一行,這就是「抽象接口」的證明):

```python
BACKENDS = {
  "deepseek": lambda: OpenAICompatBackend("https://api.deepseek.com",
                        os.environ["DEEPSEEK_API_KEY"],
                        os.environ.get("ODFORGE_MODEL", "deepseek-chat")),
  "ollama":   lambda: OpenAICompatBackend("http://localhost:11434/v1", "ollama",
                        os.environ.get("ODFORGE_OLLAMA_MODEL", "qwen2.5")),
  # "claude": Phase 9 加分再上(anthropic SDK,獨立類別)
}
```

- [ ] **Step 1: 失敗測試(mock,不打真 API):monkeypatch openai client──①假 tool_call(arguments=合法 Presentation JSON)→ 斷言回傳 `Presentation` 實例;②非法 dict → 拋 `ValidationError`(重試 1 次後);③斷言呼叫參數:`tool_choice` 強制 emit_document、schema == `model_json_schema()`;④`get_backend("deepseek")` 讀 `DEEPSEEK_API_KEY`(monkeypatch.setenv),沒設時報清楚的錯;⑤`get_backend(None)` + `ODFORGE_BACKEND=ollama` → 回 ollama 後端(base_url 斷言)**
- [ ] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: pluggable llm backends, deepseek default`**

### Task 6.2: CLI `odforge new`

**Files:** Create `src/odforge/cli.py`, Test `tests/test_cli.py`

**Interfaces — Produces:** typer app;`odforge new PROMPT -o FILE [--theme academic] [--backend deepseek]`,doc_type 由 `-o` 副檔名推斷(.odt→text/.odp→presentation/.ods→spreadsheet,其他副檔名報錯 exit 2);流程 = generate_ir → render → validate_odf → rich 表格印驗證結果;驗證失敗 exit 1。

- [ ] **Step 1: 失敗測試:`CliRunner` + monkeypatch `generate_ir` 回 fixture IR → `new "做簡報" -o out.odp` 後檔案存在、exit 0、輸出含「✓」;`-o out.xyz` → exit 2**
- [ ] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS;煙霧測試(真 API,人工跑一次):`odforge new "資料結構第三章:樹與二元樹,15張教學簡報" -o demo.odp` 用 Impress 開 → Step 5: commit `feat: odforge new CLI`**

---

## Phase 7:MCP server [SPEC §4-MCP]

### Task 7.1: FastMCP 工具

**Files:** Create `src/odforge/mcp_server.py`, Test `tests/test_llm.py` 追加(直接呼叫工具函式,不起 server)

**設計決策(定案):MCP 工具收 IR JSON,不收 prompt。** 呼叫端已經是 AI(Claude Desktop),內容它自己生;ODForge 在 MCP 模式下是純渲染引擎,server 不需要 API key。這就是「內容 AI 生成、格式引擎保證」的架構誠實版。

**Interfaces — Produces:** tools `forge_text_document(document: dict, out_path: str) -> str`、`forge_presentation(...)`、`forge_spreadsheet(...)`(回傳含驗證結果的訊息字串)、`inspect_odf(path: str) -> str`(= check_odf 報告;Phase 8 完成前先回 validate 摘要)。工具 docstring 要寫清楚 document 參數的 schema(MCP client 看得到)。

- [ ] **Step 1: 失敗測試:直接呼叫 `forge_presentation(sample_presentation.model_dump(), str(tmp/"x.odp"))` → 檔案存在、回傳字串含 "ok";塞非法 dict → 回傳字串含 "error"(MCP 工具不拋例外,回錯誤訊息)**
- [ ] **Step 2: FAIL → Step 3: 實作(FastMCP,`mcp.run()` 走 stdio)→ Step 4: PASS;人工關卡:寫 `claude_desktop_config.json` 設定片段進 README,在 Claude Desktop 實測「幫我做一份簡報」→ 桌面出現 .odp → Step 5: commit `feat: mcp server`**

---

## Phase 8:.ods 渲染器 + check [SPEC §3-ods、§5]

### Task 8.1: Spreadsheet → .ods

**Files:** Create `src/odforge/render/ods.py`, Test `tests/test_render_ods.py`

**Interfaces — Produces:** `render_ods(s: Spreadsheet, out_path: Path) -> Path`(odfdo `Document("spreadsheet")`;數字存數值 cell 不是字串;公式寫入 `table:formula="of:=..."`)。圖表**不做**(退階決策,見 Phase 9 加分區)。

- [ ] **Step 1: 失敗測試:mimetype 對;sheet 數對;`table:formula` 屬性出現在指定 cell;數字 cell 的 `office:value-type="float"` → Step 2: FAIL → Step 3: 實作(先 context7 查 odfdo Cell/Row API)→ Step 4: PASS + LO Calc 人工開檔確認公式會算 → Step 5: commit `feat: ods renderer with formulas`**

### Task 8.2: odforge check

**Files:** Create `src/odforge/check.py`, Modify `src/odforge/cli.py`(加 `check` 子命令), Test `tests/test_check.py`

**Interfaces — Produces:** `check_odf(path) -> str`(markdown:三關結果 + 部件清單 + 樣式引用完整性——content.xml 用到的 style name 是否都有定義);`diff_docx_odt(docx) -> str`(soffice 轉 odt,比對段落數/標題階層數/表格數/圖片數,markdown 表格輸出;無 soffice 時回傳說明文字)。

- [ ] **Step 1: 失敗測試:對 render fixture 檔 `check_odf` 回傳含 "## 驗證結果" 與 "PASS";對故意壞檔含 "FAIL";`diff_docx_odt` 在無 soffice 環境回傳含 "需要 LibreOffice" → Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: odforge check + docx/odt diff`**

> **Phase 8 完 = 競賽 MVP 完成線。** 此時跑一次全量 pytest + 三種檔案的真 API 端到端生成 + LO 開檔截圖。

---

## Phase 9:加分項(有餘裕才做,依此順序)[SPEC §7-加分]

- [ ] 9.1 minimal / dark 主題(themes.py 加兩筆 Theme + 各截圖)
- [ ] 9.2 `--outline-only`:generate_ir 先出大綱(標題清單)印給使用者,確認後才長全文(CLI `typer.confirm`)
- [ ] 9.3 本地模型實測:Ollama 後端在 Task 6.1 的抽象層已內建(OpenAI 相容端點),此項只是實測——裝 Ollama + qwen2.5,跑 `odforge new ... --backend ollama`,錄「全程離線生成」畫面(隱私訴求佐證)
- [ ] 9.4 Claude 後端(anthropic SDK 獨立類別,註冊進 BACKENDS;先跑 claude-api skill 確認寫法)
- [ ] 9.5 .ods 基本長條圖(ODF chart 子文件 Object 1/;若 2 小時內做不完就砍,簡報改講「為何 ODF 圖表是生態痛點」)

---

## Phase 10:佐證資料與交付(不是程式,是競賽交付物)[SPEC §8、§9]

- [ ] 10.1 Dogfooding:用 ODForge 生成 `ODForge使用手冊.odt`、`決賽簡報.odp`、`生成測試結果.ods`(內容先人工寫好 IR/prompt,產出後人工微調)
- [ ] 10.2 錄影(OBS):CLI 生三種檔 → LO 開啟 → Claude Desktop MCP demo,3–5 分鐘
- [ ] 10.3 GitHub repo 公開(MIT、README 含安裝/用法/架構圖/截圖)
- [ ] 10.4 附件 4 優化建議草稿(素材:深度研究報告 §4 + check 報告 + 開發踩坑紀錄)→ **使用者改寫成自己的話**
- [ ] 10.5 附件 5 心得草稿 → **使用者改寫**;自評成效表線上填寫
- [ ] 10.6 佐證打包(≤4 個 zip,檔案全部 ODF 原生格式)、7/8 中午前上傳

---

## Self-Review 紀錄(SDD 檢核)

- SPEC §1 四層架構 → Phase 1/2/4(引擎)、6(LLM+CLI)、7(MCP)✅
- SPEC §2 IR 三型別 → Task 1.1 ✅
- SPEC §3 三渲染器+驗證 → Phase 2/4/5/8.1 ✅(.ods 圖表退階至 9.4,SPEC 已允許)
- SPEC §4 CLI+MCP → 6.2/7.1 ✅(MCP 改收 IR,已回寫 SPEC 的決策)
- SPEC §5 check → 8.2 ✅
- SPEC §7 範圍切割 → Phase 順序即優先序;MVP 完成線在 Phase 8 ✅
- SPEC §8/§9 佐證與簡報 → Phase 10 ✅
- 型別一致性:`parse_ir`/`render`/`validate_odf`/`generate_ir`/`check_odf` 簽名在各 Phase 引用一致 ✅
