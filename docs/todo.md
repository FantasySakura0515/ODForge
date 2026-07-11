# ODForge 開發計畫(SDD / TDD)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## 現況(2026-07-11)

- **v1 MVP 已完成**:CLI + MCP、三渲染器、三道驗證閘、113 tests 綠燈、demo 與佐證備妥(報名 7/8 截止)。v1 原計畫移至文末「附錄:v1 計畫存檔」。
- **v2 後端 Phase 11–18 已完成**(勾選對照 git log;僅 Task 11.3 重產 demo 與各人工關卡截圖未補,見 19.0):兩段式生成、design tokens、五新版型、預算閘、QA 迴圈、樣式抽取、agent skill、Web API + SSE 全數落地。
- **前端 F1 已交付併入 main**:cockpit shell + SSE + 下載,`odforge serve` 一鍵全棧(spec 與 F1 計畫在 `docs/superpowers/`)。
- **2026-07-11 全面實測評審完成**(真 API 實測 + 獨立設計審查 + 自動掃描,Nielsen 19/40):介面三處說謊(假成功/假綠勾/假分頁)、後端已就緒的能力前端未接線 → 修正項目全數列入新增的 **Phase 18.5**,P0–P1 應在 Phase 19 之前完成。
- **現行計畫 = v2**:目標決賽(8/24,入圍公布 8/10)。依據:`巨頭AI文件工具研究.md`(五大陣營研究)與 2026-07-06/07 的架構討論定案。

---

# v2:Claude Design 等級升級

**Goal:** 把 ODForge 的簡報視覺品質從 2.5/10 提升到 7.5–8/10,新增品質 harness(第四道閘:設計閘)、樣式抽取、agent skill 生態,以及供前端使用的 Web API。**前端由參賽者自行設計,本計畫只交付後端與 API 契約。**

**定位(定案):** ODForge 是 ODF 生態缺失的「AI 生成層」——巨頭管線的最後一段,在他們都不做的格式上的唯一實作。對評審的一句話:「NotebookLM 給你一張漂亮的圖,我們給你一份真正的文件。」

**架構分工(定案,v2 一切設計決策的最高原則):**

> **引擎管「能力」與「保證」;LLM 掛著 skill 管「選擇」與「變化」;harness 兜底。**
> 變化來自 per-deck design tokens(LLM 針對主題設計)+ page-role 版型多樣性 + 參數化選項,**不是自由座標**。LLM 從頭到尾不碰 cm 與 pt。

**明確不做(定案):** 線上編輯器(編輯器 = LibreOffice / MODA ODF 工具)、HTML 中間層(conversion drift 是我們要打的敵人)、像素路線(輸出不可編輯違反 ODF 存在理由)、拚模板數量(寧可 5 套打磨到位)、一步到位黑箱生成。

**里程碑:**

| 線 | Phase | 意義 |
|---|---|---|
| 決賽 demo 基本線 | 11–15 完成 | CLI 產出已是全新視覺 + 兩段式生成 |
| 決賽 demo 完整線 | 16 完成 | 四道閘故事完整,QA 迴圈可展示 |
| 決賽亮點 | 17–18 完成 | 樣式抽取 + agent skill + Web API(接前端) |
| 決賽可信線 | 18.5 P0–P1 完成 | 介面不說謊 + 單張檢視 + 貼合需求的輸入層(2026-07-11 評審定案) |

## v2 Global Constraints(v1 constraints 全部沿用,以下為新增)

- 新相依:`PyMuPDF`(PDF→PNG 預覽,純 pip 安裝、無外部程式相依,Windows 友善)。`fastapi` + `uvicorn` + `sse-starlette` 放 optional-dependencies `web`;`anthropic` 放 optional `vision`。
- Vision critic 後端環境變數:`ODFORGE_VISION_BACKEND` ∈ `claude` | `ollama` | `off`(預設 `off`,未設定時 QA 迴圈只跑確定性檢查)。`claude` 需 `ANTHROPIC_API_KEY`;模型 id **實作前先跑 claude-api skill 查最新**(候選:Haiku 級,vision 需求足夠且最省)。`ollama` 用視覺模型(tag 以 Ollama 現行為準,候選 `qwen2.5vl`)。
- 設計鐵則(渲染層 enforce,不是 prompt 勸導):
  - CJK 三軌字級必須齊:`fo:font-size` + `style:font-size-asian` + `style:font-size-complex`(v1 已修,v2 所有新樣式沿用,測試要驗)。
  - 對比度:內文 vs 背景 ≥ 4.5:1、標題 vs 背景 ≥ 3:1(WCAG 相對亮度公式),DesignSpec validator 硬擋。
  - accent 紀律:accent 色只用於裝飾元素/強調,不做大面積內文色(section/closing 反白頁除外)。
  - 版面預算:`估算文字高度 ≤ frame 高度`,超出即 fail(Phase 14 的設計閘 part 1)。
- LLM 產出的 `DesignSpec` 一律過 pydantic + 對比度驗證;驗證失敗 → 回退到內建 preset,**絕不讓壞 token 進渲染器**。
- 每個 Phase 結束:全量 `pytest` 綠燈 + LibreOffice 人工開檔關卡,截圖存 `odforge/docs/screenshots/v2/`(截圖同時是決賽佐證素材)。
- 參考資產:Claude 官方 pptx skill 的設計鐵則與 QA 協議(60-30-10 色彩、每頁要有視覺元素、內文靠左不置中、「第一次渲染幾乎必錯,QA 當抓蟲」、**禁止在標題下加裝飾短線——那是 AI 簡報的特徵**,所以 Phase 13 的 accent 元素改用「標題左側縱向短棒 / kicker 色字 / 底部細線」等形態,不用標題下橫線)。

## v2 檔案結構(新增部分)

```
odforge/
├── src/odforge/
│   ├── ir.py                   # [改] DesignSpec、新版型欄位、巢狀 bullets、ChartSpec
│   ├── themes.py               # [改] token 化 Theme、3 套 preset 翻新、resolve_design()
│   ├── textmetrics.py          # [新] 文字高度估算(版面預算閘的核心)
│   ├── render/odp.py           # [大改] shape/list/master-page/gradient/SVG builders
│   ├── package.py              # [改] binary parts + media-type manifest
│   ├── llm.py                  # [改] 兩段式:generate_outline() + generate_slides()
│   ├── preview.py              # [新] odp → PDF(soffice)→ PNG(PyMuPDF)
│   ├── critic.py               # [新] vision critic 抽象 + Finding + repair 迴圈
│   ├── extract.py              # [新] 樣式抽取:.otp/.odp → DesignSpec 約束
│   ├── webapi.py               # [新] FastAPI(前端契約見 Phase 18)
│   ├── mcp_server.py           # [改] 加 preview_odf tool
│   └── cli.py                  # [改] --interactive / --qa / --from-template / serve
├── skills/odforge-design/      # [新] SKILL.md + 3 份 theme 規格書(給外部 agent 讀)
└── tests/                      # 每個新模組對應 test_*.py
```

**v2 介面總覽(各 Phase 共用契約):**

```python
# ir.py(新增)
Palette(bg, surface, text, muted, accent)                 # hex;validator 驗格式+對比度
FontPair(display: str, body: str)                          # 必須 ∈ FONT_WHITELIST
DesignSpec(palette: Palette, fonts: FontPair,
           scale: Literal["compact","standard","display"] = "standard",
           mode: Literal["detailed","presenter"] = "presenter")
Presentation.design: DesignSpec | None = None              # None → 用 theme preset
BulletItem(text: str, children: list[str] = [])
Slide.bullets: list[str | BulletItem]                      # 巢狀(至多兩層)
Slide.layout: Literal[...v1 五種..., "quote","agenda","comparison","chart","closing"]
Slide.quote: str = ""; Slide.attribution: str = ""         # quote 版型用
Slide.chart: ChartSpec | None                              # ChartSpec(labels, values, unit="", highlight=None)
Slide.kicker: str = ""                                     # 頁面左上小標(eyebrow)

# themes.py(改)
Theme = 完整 token 集(見 Phase 12)                          # preset 就是 3 個 Theme 實例
resolve_design(p: Presentation) -> Theme                   # design 優先、驗證失敗回退 preset

# textmetrics.py
estimate_height_cm(text: str, size_pt: float, width_cm: float,
                   line_height: float = 1.45) -> float
check_budget(slide: Slide, theme: Theme) -> list[str]      # 超載訊息;空 = 過

# llm.py(改)
generate_outline(prompt, backend=None) -> Outline          # Outline(design, pages: list[PageRole])
generate_slides(outline, backend=None) -> Presentation     # 含 budget 回饋重試
generate_ir(...)                                           # 保留(v1 相容,一段式)

# preview.py
render_pages(odf_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]  # 每頁一張 PNG

# critic.py
Finding(slide_no: int, issue: str, severity: Literal["error","warn"], fix_hint: str)
critique(pngs: list[Path], ir: Presentation) -> list[Finding]
run_qa_loop(ir, out_path, max_rounds: int = 2) -> QAReport # render→critique→修被標記頁→重驗

# extract.py
extract_design(template_path: Path) -> DesignSpec          # 從 styles.xml 抽色/字體
```

---

## Phase 11:修復與地基

### Task 11.1: `--theme` 覆寫 bug 修復

**Files:** Modify `src/odforge/cli.py`, Test `tests/test_cli.py`

現況 bug:使用者明確指定 `--theme academic` 時不會覆寫 LLM 回傳的 theme(只在 `theme != academic` 時 copy)。

- [x] **Step 1: 失敗測試:monkeypatch `generate_ir` 回傳 `theme="dark"` 的 Presentation → `odforge new "x" -o t.odp --theme academic` → 渲染用的 theme 是 academic(讀 styles.xml 的 `draw:fill-color` == academic.bg 斷言)**
- [x] **Step 2: FAIL → Step 3: 修法:typer 參數改 `Optional[str] = None`,`None` = 尊重 LLM,有值 = 一律覆寫 → Step 4: PASS → Step 5: commit `fix: --theme always overrides llm choice`**

### Task 11.2: package.py 支援 binary parts 與 media-type

**Files:** Modify `src/odforge/package.py`, Test `tests/test_package.py`

圖片/SVG 嵌入的前置。現況 `build_manifest` 把所有 part 硬編碼 `text/xml`。

**Interfaces — Produces:** `write_odf_package(path, mimetype, parts: dict[str, str | bytes])`——str 視為 XML(`text/xml`),bytes 依副檔名推 media-type(`.png`→`image/png`、`.svg`→`image/svg+xml`、`.jpg`→`image/jpeg`),manifest 對應。

- [x] **Step 1: 失敗測試:parts 含 `"Pictures/a.svg": b"<svg .../>"` → zip 內存在該 entry;manifest 有 `manifest:full-path="Pictures/a.svg"` 且 `media-type="image/svg+xml"`;既有 XML parts 的 manifest 不變(回歸)**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: binary parts + media-type manifest`**

### Task 11.3: 重產 demo(人工關卡,零程式改動)

v1 的 `demo/tree.odp`/`tree.png` 是 CJK 字級 bug 修復**前**的舊產物(P1–P7 樣式缺 `style:font-size-asian`,demo 圖標題是預設小字)。

- [ ] 真 API 重跑 `odforge new "幫我做一份資料結構第三章:樹與二元樹的教學簡報" -o demo/tree.odp`,LibreOffice 開檔確認字級正確,重截 `tree.png`,README 圖同步更新,commit `docs: regenerate demo with fixed cjk sizing`

---

## Phase 12:Design Token 系統(變化的來源)

### Task 12.1: DesignSpec IR + 對比度驗證

**Files:** Modify `src/odforge/ir.py`, Test `tests/test_ir.py`

**Interfaces — Produces:**(見 v2 介面總覽)`Palette` / `FontPair` / `DesignSpec`;`Presentation.design: DesignSpec | None`。

**設計要點:**
- `FONT_WHITELIST = ["Noto Sans TC", "Noto Serif TC", "微軟正黑體", "標楷體"]`(系統可得、CJK 完整;display 與 body 可同款)。
- 對比度 validator:WCAG 相對亮度,`text/bg ≥ 4.5`、`accent/bg ≥ 3.0`;`muted/bg ≥ 3.0`。model_validator 級,錯誤訊息要寫清楚哪一對不夠(LLM 重試時看得懂)。
- `mode` 二分法(抄 NotebookLM):`detailed`(自讀型,文字完整)vs `presenter`(講者型,大字少文)——這個 enum 直接控制 Phase 15 的內容密度指示與 scale 預設。

- [x] **Step 1: 失敗測試:①合法 DesignSpec 過;②`text="#CCCCCC", bg="#FFFFFF"` → ValidationError 且訊息含 "contrast";③fonts 不在白名單 → ValidationError;④`Presentation` 不給 design → None(向後相容,v1 fixture 全部照舊過)**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: per-deck design tokens with contrast validation`**

### Task 12.2: themes.py token 化 + resolve_design

**Files:** Modify `src/odforge/themes.py`, Test `tests/test_render_odp.py`

**Interfaces — Produces:**

```python
@dataclass(frozen=True)
class Theme:                      # 渲染器唯一認得的東西(resolved)
    bg: str; surface: str; text: str; muted: str; accent: str
    title_color: str              # 預設 = text,深色主題可另指定
    font_display: str; font_body: str
    display_pt: int; h1_pt: int; body_pt: int; caption_pt: int
    bullet_char: str = "▪"

SCALES = {"compact":  (44, 24, 16, 12),
          "standard": (54, 28, 18, 13),
          "display":  (66, 32, 20, 14)}   # (display, h1, body, caption) pt

THEMES: dict[str, Theme]          # academic / minimal / dark 三套 preset 翻新
resolve_design(p: Presentation) -> Theme   # p.design 有效 → 組 Theme;否則 THEMES[p.theme]
```

**preset 翻新(定案,美術方向:academic=學術藍+暖白、minimal=暖灰單色+一點橘、dark=深靛+亮青 accent;實作時先掛 frontend-design skill 調色,完成後把 hex 寫死進程式碼——品味進引擎,這一步是「開發期用 design skill」的落點):**

- [x] **Step 1: 失敗測試:①三 preset 全過 DesignSpec 級對比度檢查(用同一個驗證函式跑);②`resolve_design` 對 design=None 回 preset;③給合法 design → Theme 的色與字體來自 design;④SCALES 三檔位遞增**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: tokenized themes + resolve_design`**

---

## Phase 13:渲染引擎能力(odp.py 大改——會畫)

> 本 Phase 全部是 ODF 1.2 標準支援的能力,純粹是 renderer 還沒寫。每個 Task 的測試都用 lxml 驗 XML 結構(與 v1 同套路),人工關卡看 LibreOffice。

### Task 13.1: 圖形基元 XML builders

**Files:** Modify `src/odforge/render/odp.py`, Test `tests/test_render_odp.py`

**Interfaces — Produces(odp.py 內部純函式,直接可測):**

```python
_rect_xml(x, y, w, h, *, fill: str, opacity: float = 1.0,
          corner_radius_cm: float = 0.0, style_name: str) -> str    # draw:rect / 圓角
_line_xml(x1, y1, x2, y2, *, color: str, width_pt: float) -> str    # draw:line
_GraphicStyles                                                      # (fill,opacity,radius,stroke) 去重 → G1,G2...(仿 _ParagraphStyles)
build_styles_xml 增加:draw:gradient 定義(雙色線性,給深色主題背景用)
```

- [x] **Step 1: 失敗測試:①`_rect_xml` 輸出含 `draw:rect` 且 graphic style 的 `draw:fill-color` 正確;②opacity < 1 時 style 有 `draw:opacity`;③圓角有 `draw:corner-radius`;④gradient:styles.xml 含 `<draw:gradient>` 且 drawing-page style `draw:fill="gradient"`(僅 dark preset)**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: shape primitives + gradients`**

### Task 13.2: 真 text:list + 段落排印

**Files:** Modify `src/odforge/render/odp.py`, Test `tests/test_render_odp.py`

現況:bullets 是逐行裸 `text:p`,無符號、無縮排、無行距。

- [x] **Step 1: 失敗測試:①bullets 渲染為 `<text:list text:style-name="L1">` 包 `<text:list-item>`;②automatic-styles 有 `<text:list-style>` + `text:bullet-char` 且 bullet 色 = accent;③巢狀 BulletItem → 兩層 `text:list` 縮排;④段落樣式含 `fo:line-height="145%"` 與 `fo:margin-bottom`;⑤kicker 段落含 `fo:letter-spacing`**
- [x] **Step 2: FAIL → Step 3: 實作(`_ParagraphStyles` 現有去重 key 為 `(size_pt, bold, center, color)`,擴充加 line_height / margin / letter_spacing 維度)→ Step 4: PASS → Step 5: commit `feat: semantic lists + paragraph typography`**

### Task 13.3: master page 元素 + section/closing 反白頁 + accent 出場

**Files:** Modify `src/odforge/render/odp.py`, `src/odforge/themes.py`, Test `tests/test_render_odp.py`

**設計(定案):**
- master page 固定元素:頁腳細線(y=14.9,x 1.5→26.5,muted 色 0.75pt)、右下頁碼框(`presentation:class="page-number"` + `<text:page-number/>`,caption_pt,muted)、左下 kicker 小字(簡報標題,caption_pt,muted)。title/section/closing 頁不顯示頁碼(用第二個 master page "Plain")。
- section 頁:**全頁 accent 底 + 反白字** + 巨型章節編號(96pt、白、`draw:opacity 15%`、右側絕對定位)。章節編號 = 該 slide 在 sections 中的序數,自動計算,LLM 不用給。
- closing 頁(新版型,幾何同 title):accent 底反白,結尾訊息。
- 內頁標題 accent 元素:**標題左側縱向短棒**(x=1.5, 標題同高, w=0.18cm, accent)——不用標題下橫線(AI 簡報特徵,見 Global Constraints)。
- big-fact:數字改 display_pt、accent 色;下方說明 muted。

- [x] **Step 1: 失敗測試:①styles.xml 有兩個 `style:master-page`(Standard/Plain)且 Standard 內含 page-number frame;②section 頁的 drawing-page style `draw:fill-color` == accent 且標題字色 == bg(反白);③內容頁 content.xml 含縱向短棒 rect(w≈0.18cm);④big-fact 的 fact 段落字級 == display_pt 且色 == accent**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS + 人工關卡:三 preset 各生成一份 8 頁 fixture 簡報開檔截圖 → Step 5: commit `feat: master pages + inverted section/closing + accent system`**

### Task 13.4: SVG 嵌入 + 卡片化 two-col/comparison

**Files:** Modify `src/odforge/render/odp.py`, Test `tests/test_render_odp.py`

- SVG 裝飾件由引擎程式生成(不是 LLM):幾何點陣/波形/圓弧,title 頁右下低調擺放,色取 accent。`_svg_decoration(theme) -> bytes`。
- two-col 與新 comparison 版型:兩欄各墊圓角 surface 色卡片(`corner_radius 0.3cm`),欄標題 accent 色。

- [x] **Step 1: 失敗測試:①title 頁 zip 內有 `Pictures/*.svg` + manifest media-type 正確 + content.xml 有 `draw:image` 引用;②two-col 頁有兩個圓角 rect(fill == surface)且 z-order 在文字 frame 之前**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: svg decorations + card columns`**

### Task 13.5: shape 長條圖(chart 版型的渲染端)

**Files:** Modify `src/odforge/render/odp.py`, Test `tests/test_render_odp.py`

**設計(定案):** 不做原生 ODF chart 子文件(工程量大),用 `draw:rect` 按 `ChartSpec.values` 比例畫水平長條 + 數值標籤;`highlight` 指定的 bar 用 accent,其餘 muted 淡色;圖表區 x 1.5–17cm,右側 7.5cm 放 insight bullets。上限 8 條(超過 validator 擋,「不支援就閉嘴」)。

- [x] **Step 1: 失敗測試:①chart slide 渲染出 len(values) 個 bar rect 且寬度與 values 成正比(±公差);②highlight bar fill == accent;③values > 8 → ir.py ValidationError**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: shape-drawn bar charts`**

---

## Phase 14:版型庫擴充 + 版面預算閘(設計閘 part 1)

### Task 14.1: 新版型幾何 + IR 欄位

**Files:** Modify `src/odforge/ir.py`, `src/odforge/themes.py`, Test `tests/test_ir.py`, `tests/test_render_odp.py`

版面幾何(定案起點,頁面 28×15.75cm;實作後看截圖允許微調,調完回寫此表):

| layout | frames(role @ x,y,w,h cm / 字級) |
|---|---|
| quote | quote @ 3,4.8,22,4.5 /h1_pt center;attribution @ 3,10,22,1.2 /caption_pt center muted;裝飾引號 shape @ 2.2,2.8(accent, opacity 25%) |
| agenda | title @ 1.5,0.8,25,2 /h1_pt;items @ 3,3.8,22,10.5 /body_pt(編號 01/02… accent 色) |
| comparison | title 同上;left-card @ 1.5,3.2,12.2,11.3;right-card @ 14.3,3.2,12.2,11.3(卡片內:欄標題 body_pt bold accent + bullets body_pt) |
| chart | title 同上;chart-area @ 1.5,3.5,15.5,11;insights @ 17.6,3.5,8.9,11 /caption_pt |
| closing | message @ 2,6,24,3 /h1_pt bold center(accent 底反白,Plain master) |

- [x] **Step 1: 失敗測試:①新 layout Literal 全部可 parse;②LAYOUTS 含五個新 key 且 frame 不出界(沿用 v1 的邊界測試,參數化跑全部 layout);③quote/chart 欄位缺漏時 validator 給明確錯誤(layout="chart" 但 chart=None → error)**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: five new page-role layouts`**

### Task 14.2: 版面預算驗證器(textmetrics.py)

**Files:** Create `src/odforge/textmetrics.py`, Modify `src/odforge/validate.py`(或獨立 gate), Test `tests/test_textmetrics.py`

**這是我們比 open-slide 強的點:它靠 prompt 自律,我們用程式硬擋。**

**Interfaces — Produces:**

```python
PT_TO_CM = 0.03528
def estimate_height_cm(text, size_pt, width_cm, line_height=1.45) -> float:
    # CJK 字寬 ≈ 1.0em、ASCII ≈ 0.55em;chars_per_line = width / (size_pt*PT_TO_CM*平均字寬)
    # lines = ceil(有效長度 / chars_per_line);height = lines * size_pt * PT_TO_CM * line_height
def check_budget(slide, theme) -> list[str]   # 逐 frame 累加(段落高+段距),超出 frame.h → 訊息
```

- [x] **Step 1: 失敗測試:①短文字 1 行高度 ≈ size_pt*PT_TO_CM*1.45(±10%);②塞 40 條 bullets 的 slide → check_budget 非空且訊息含 slide 標題與 frame role;③正常 5 條 bullets → 空;④全 ASCII 與全 CJK 的行數估算差異方向正確(CJK 行數較多)**
- [x] **Step 2: FAIL → Step 3: 實作;`generate_slides`(Phase 15)把超載訊息餵回 LLM 重試該頁,CLI `new` 在渲染前跑一次並警告 → Step 4: PASS → Step 5: commit `feat: layout budget validator`**

---

## Phase 15:LLM 兩段式生成 + 設計 skill prompt

### Task 15.1: generate_outline(大綱 + 設計一起出)

**Files:** Modify `src/odforge/llm.py`, Test `tests/test_llm.py`

**Interfaces — Produces:**

```python
PageRole(role: Literal[...同 Slide.layout...], title: str, gist: str)   # gist = 這頁要講什麼(一句話)
Outline(design: DesignSpec, mode: Literal["detailed","presenter"],
        pages: list[PageRole])                                          # pydantic,可 json 序列化(前端要用)
generate_outline(prompt, backend=None) -> Outline
```

**Prompt 設計要點(第一段,一次呼叫):** 要求 LLM (a) 從主題推導美術方向並輸出 DesignSpec(給 2–3 個候選讓它自選一個,說明理由寫進 log 即可);(b) 產 page-role 大綱——「一頁一個想法」、開場 title、agenda、section 分節、穿插 big-fact/quote/chart 調節奏、同版型不得連續 ≥3 頁、結尾 closing。DesignSpec 驗證失敗(對比度)→ 把錯誤訊息回餵重試 1 次,再失敗 → design=None(preset 兜底,不炸)。

- [x] **Step 1: 失敗測試(mock):①假 tool_call 回合法 Outline JSON → Outline 實例;②design 對比度不足 → 斷言重試 1 次(mock call count == 2),第二次也壞 → Outline.design is None 且不拋;③schema 斷言:tools 參數 == Outline.model_json_schema()**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: outline-first generation with design spec`**

### Task 15.2: generate_slides(填充 + budget 回饋)

**Files:** Modify `src/odforge/llm.py`, Test `tests/test_llm.py`

**設計要點:** 第二段可一次呼叫(pages 全給,要求逐頁填 Slide;8–20 頁在 8192 tokens 內可行,`ODFORGE_MAX_TOKENS` 提到 16384 預設)——**不做逐頁平行呼叫**(v2 範圍控制;Web API 的逐頁進度用 SSE 假流式:收到完整 IR 後逐頁 emit)。填完每頁跑 `check_budget`,超載頁收集起來一次回餵:「第 n 頁超載:訊息」,重試至多 1 輪,仍超載 → 自動降級(bullets 截到裝得下 + 加 `notes` 註明被截,誠實優於溢版)。

**System prompt 蒸餾設計鐵則(第二段):** mode=presenter → 每條 bullet ≤ 16 字、每頁 ≤ 4 條;detailed → ≤ 30 字、≤ 6 條(v1 規則保留);60-30-10 色彩紀律由引擎保證不用寫;禁止把版型寫進內容文字;chart 頁要給真數據(來源 = 使用者 prompt 內容,沒有數據就不要排 chart 頁——這條寫進第一段大綱 prompt)。

- [x] **Step 1: 失敗測試(mock):①合法回傳 → Presentation,design 從 Outline 帶入;②故意回一頁超載 → 斷言第二次呼叫的 user message 含 "超載" 與頁碼;③兩次都超載 → 回傳的該頁 bullets 被截且 notes 含省略說明**
- [x] **Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: slide filling with budget feedback`**

### Task 15.3: CLI 大綱確認站

**Files:** Modify `src/odforge/cli.py`, Test `tests/test_cli.py`

- `odforge new PROMPT -o FILE [--interactive]`:interactive 時印大綱表格(rich:頁碼/版型/標題/gist + 色盤色塊),`typer.confirm` 後才進第二段;非 interactive 直通。`--mode detailed|presenter` 可覆寫。v1 一段式保留為 `--one-shot`(Ollama 弱模型路徑)。
- [x] **Step 1: 失敗測試(CliRunner + monkeypatch 兩段函式):interactive 輸入 y → 檔案存在;輸入 n → exit 0 但無檔案且印 "已取消" → Step 2: FAIL → Step 3: 實作 → Step 4: PASS;煙霧測試(真 API):`odforge new "介紹台灣珍珠奶茶產業,12張,presenter" -o boba.odp --interactive` 開檔驗收新視覺 → Step 5: commit `feat: outline checkpoint in cli`**

> **★ Phase 15 完 = 決賽 demo 基本線。** 此時停下來:三 preset + LLM 自選 design 各生成一份真簡報,全部截圖,對照 v1 demo 圖做 before/after——這組圖就是決賽簡報的核心素材。

---

## Phase 16:品質 Harness(第四道閘:設計閘 part 2)

### Task 16.1: preview.py(渲染管線,QA 與前端共用)

**Files:** Create `src/odforge/preview.py`, Test `tests/test_preview.py`

**Interfaces — Produces:** `render_pages(odf_path, out_dir, dpi=150) -> list[Path]`——`run_soffice_convert` 轉 PDF(重用 v1 validate.py 的隔離 profile 邏輯)→ PyMuPDF 逐頁點陣化 PNG。無 soffice → 拋 `PreviewUnavailable`(呼叫端決定降級)。

- [x] **Step 1: 失敗測試:`@skipif(find_soffice() is None)`——fixture 簡報 → PNG 數 == slide 數、尺寸比例 ≈ 16:9;無 soffice(monkeypatch find_soffice=None)→ PreviewUnavailable → Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: page preview rendering`**

### Task 16.2: critic.py(vision 批判)

**Files:** Create `src/odforge/critic.py`, Test `tests/test_critic.py`

**Interfaces — Produces:**(見介面總覽)`Finding` / `critique(pngs, ir)`。

**設計要點:**
- Vision 後端抽象仿 llm.py:`VISION_BACKENDS` registry(`claude` 用 anthropic SDK、`ollama` 用 OpenAI 相容 chat + base64 image;實作前先跑 claude-api skill 查 vision 請求格式與模型 id)。
- **獨立 context**:critic 呼叫不帶生成對話史,只給圖 + 檢查清單(抄 Claude pptx skill):文字溢出/截斷、元素重疊、對比不足、對齊歪斜、版型連續重複、留白失衡。structured output 強制 `list[Finding]`。
- 一次請求塞全部頁圖(≤20 頁可行),省 round-trip。
- `ODFORGE_VISION_BACKEND=off` → `critique` 回空清單(QA 迴圈退化為只有確定性檢查,不炸)。

- [x] **Step 1: 失敗測試(mock):①假 vision 回應(兩個 finding JSON)→ list[Finding] 解析正確;②off 後端 → 空清單;③斷言請求含 len(pngs) 張圖與檢查清單關鍵詞 → Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: vision critic`**

### Task 16.3: run_qa_loop + CLI --qa

**Files:** Modify `src/odforge/critic.py`, `src/odforge/cli.py`, Test `tests/test_critic.py`

**迴圈(定案,抄 Claude pptx skill 協議):** render → critique → severity=="error" 的頁丟回 `generate_slides` 局部重生(只送那幾頁的 PageRole + fix_hint)→ 重渲染受影響頁 → 再驗一次 → 無新 error 或滿 2 輪即停。輸出 `QAReport(rounds, findings_by_round, final_ok)`,CLI 用 rich 印修訂前後對照表;`--qa` 需要 soffice + vision 後端,缺任一就印明確訊息跑 fallback(只有 budget 閘)。

- [x] **Step 1: 失敗測試(全 mock):①第一輪 2 errors → 斷言重生呼叫只含那 2 頁;②第二輪 0 findings → 迴圈停在 round 2;③永遠有 error → 停在 max_rounds 且 final_ok=False(不無限迴圈)→ Step 2: FAIL → Step 3: 實作 → Step 4: PASS;煙霧測試(真 API + 真 soffice)跑一次 `--qa` 留下 QAReport 截圖(佐證:第四道閘實錄)→ Step 5: commit `feat: render-critique-repair qa loop`**

> **★ Phase 16 完 = 決賽 demo 完整線。**「內容 AI 生成、格式三道閘保證、設計第四道閘把關」的故事完整。

---

## Phase 17:樣式抽取 + Agent 生態(決賽亮點)

### Task 17.1: extract.py(殺手功能:吃現有範本)

**Files:** Create `src/odforge/extract.py`, Modify `src/odforge/cli.py`, Test `tests/test_extract.py`

**定位:** 2026 年 OpenAI/Anthropic 增益集路線的核心洞見——設計品質最便宜的來源是使用者已有的模板。政府/學校都有公版 ODF 範本,ODF 是開放 XML,抽樣式比誰都容易。

**MVP 範圍(定案,不做 master page 完整複用):** 解析 `.otp`/`.odp`/`.ott` 的 styles.xml──抽 ①drawing-page/頁面背景色 ②最常用的標題/內文字色 ③字體宣告(font-face-decls 第一順位)④出現頻率最高的非黑白色當 accent 候選 → 組 `DesignSpec`(過同一套對比度驗證,不夠格的欄位用 preset 補)。CLI:`odforge new "..." -o x.odp --from-template 公版.otp`(等效於鎖定 design,LLM 只出內容)。

- [x] **Step 1: 失敗測試:①對 v2 自產的 dark 簡報跑 extract → DesignSpec.palette.bg == dark.bg(round-trip);②對故意缺字體宣告的檔 → fonts 回退 preset 且不拋;③CLI --from-template → generate_outline 被呼叫時 design 已鎖定(monkeypatch 斷言)→ Step 2: FAIL → Step 3: 實作 → Step 4: PASS;人工關卡:拿一份 MODA/學校公版 odt/otp 實測 → Step 5: commit `feat: style extraction from existing odf templates`**

### Task 17.2: odforge-design skill + MCP preview 工具

**Files:** Create `odforge/skills/odforge-design/SKILL.md` + `themes/academic.md`/`minimal.md`/`dark.md`, Modify `src/odforge/mcp_server.py`, Test `tests/test_mcp.py`

**SKILL.md 內容(教外部 agent 用 MCP 產好 IR,抄 open-slide 的骨架):** ①生成前訪談:3 個針對主題客製的美術方向候選(禁 preset 罐頭)+ 頁數 + mode 二分法;②page-role 大綱紀律(一頁一想法、節奏穿插、同版型 <3 連發);③DesignSpec 欄位指南 + 對比度自檢;④theme 規格書索引;⑤產完必呼叫 `inspect_odf` + `preview_odf` 自查。theme md = hex 表 + 字級刻度 + 每版型使用時機(給 LLM 讀的規格書,與 THEMES 常數 lockstep,寫測試防漂移)。

**MCP 新工具:** `preview_odf(path: str, out_dir: str) -> str`(回傳 PNG 路徑清單 JSON;外部 agent 拿去自跑視覺迴圈——**把 harness 能力輸出給整個 agent 生態**)。

- [x] **Step 1: 失敗測試:①`preview_odf` 對 fixture 檔回傳的字串可 json.loads 且路徑存在(skipif 無 soffice);②theme md 檔的 hex 與 THEMES 常數一致(解析 md 表格比對——防規格書漂移);→ Step 2: FAIL → Step 3: 實作 → Step 4: PASS;人工關卡:Claude Code 掛上 MCP + SKILL.md 實測產一份簡報 → Step 5: commit `feat: odforge-design skill + mcp preview tool`**

---

## Phase 18:Web API 後端(前端由參賽者自行設計)

> **本 Phase 只做後端。前端(頁面、互動、視覺)由參賽者自行設計實作,本計畫不含任何前端步驟;後端保證以下 API 契約穩定。**

### Task 18.1: FastAPI + SSE 事件流

**Files:** Create `src/odforge/webapi.py`, Modify `pyproject.toml`(optional `web`)、`src/odforge/cli.py`(`odforge serve`), Test `tests/test_webapi.py`(httpx TestClient)

**API 契約(定案——前端照此設計):**

| Method | Path | Body / 回應 |
|---|---|---|
| POST | `/api/generate` | `{prompt, mode?, theme?, interactive?: bool, qa?: bool}` → `{job_id}` |
| GET | `/api/jobs/{id}/events` | SSE 事件流(型別見下表) |
| POST | `/api/jobs/{id}/outline` | interactive 時:`{action: "approve"} \| {action: "edit", outline: Outline}` |
| POST | `/api/jobs/{id}/slides/{n}/regenerate` | `{instruction?: str}` → 該頁重生 + 重渲染,SSE 推新 preview |
| GET | `/api/jobs/{id}/preview/{n}.png` | 該頁 PNG(preview.py 產物) |
| GET | `/api/jobs/{id}/download` | 最終 .odp(Content-Disposition) |
| GET | `/api/jobs/{id}` | job 狀態快照(重整頁面用):`{status, outline?, slides_done, findings?}` |

**SSE 事件型別(`event:` 欄位;`data:` 一律 JSON):**

| event | data | 時機 |
|---|---|---|
| `outline` | Outline JSON(含 design 色盤——前端可即時渲染色卡) | 第一段完成 |
| `awaiting_approval` | `{}` | interactive 時等待 outline 核可 |
| `slide_done` | `{n, slide}` | 第 n 頁 IR 完成(逐頁 emit) |
| `preview_ready` | `{n, url}` | 第 n 頁 PNG 就緒 |
| `qa_round` | `{round, findings: [Finding]}` | QA 每輪結束 |
| `complete` | `{download_url, qa_report?}` | 全部完成 |
| `error` | `{message, stage}` | 任一階段失敗 |

**實作要點:** job 存記憶體 dict + 檔案落在 `%TEMP%/odforge-jobs/{id}/`(路徑白名單,絕不接受呼叫端路徑);生成跑 `asyncio` background task;SSE 用 `sse-starlette`;CORS 全開(本機工具);`odforge serve --port 8000`。

- [x] **Step 1: 失敗測試(monkeypatch 兩段生成函式,不打真 API、不需 soffice):①POST /generate → 200 + job_id;②SSE 收到 outline → slide_done×N → complete 順序;③interactive job 卡在 awaiting_approval,POST outline approve 後繼續;④download 檔案存在且 mimetype 對;⑤regenerate 只重跑該頁(mock 斷言)→ Step 2: FAIL → Step 3: 實作 → Step 4: PASS → Step 5: commit `feat: web api with sse progress`**
- [x] **Step 2(交接):寫 `odforge/docs/webapi.md`——上表 + 每個事件的完整 JSON 範例(前端設計的依據),commit `docs: web api contract`**

---

## Phase 18.5:前端 Cockpit 修正(2026-07-11 實測評審定案)

> **依據:** 2026-07-11 全面實測評審——真 API 走完「生成 → SSE → 預覽 → 下載 → 單張重生」、獨立設計審查(Nielsen 19/40)、impeccable 自動掃描(markup 0 發現)。
> **實測數據:** outline 11s → **43s 死寂** → 17 張 `slide_done` 同一瞬間爆發 → preview 一秒連發——「逐格點亮」目前只在 mock 存在;單張重生 API 實測 20s 且自然語言指令生效;`doc_type:"ods"` 實測拿回 .odp。
> **核心結論:** token 系統/狀態機/動效紀律在水準之上,但介面三處說謊(P0),且後端已就緒的能力(確認站/單張重生/快照/mode/theme/qa)前端全未接線。
> **順序:** P0 → P1-4/P1-5(使用者兩大抱怨)→ P1-6/P1-7 → P2 → P3;P0–P1 必須在 Phase 19 之前完成。檔案行號以 2026-07-11 `main`(`0f154d6`)為準。

### P0:誠實度——介面在說謊,決賽現場會爆炸

- [ ] **P0-1 移除無聲 mock fallback**(`web/src/App.tsx:39-43`):`postGenerate` 一失敗就靜默 `playMock`——不管使用者輸入什麼,畫面完整演出「樹與二元樹」假簡報、宣告四道閘全綠、下載鈕連到 404。改為 dispatch `error` 事件(「無法連上後端,請確認 odforge serve 是否在執行」+ 重試鈕);mock 僅允許 `?mock` 顯式進入,且進入時頂欄掛常駐「展示模式」chip(順便補 spec §5 缺的後端狀態 chip)
- [ ] **P0-2 四道閘接真訊號**(`web/src/state/cockpit.ts:36,44-49`):現況第一個 `preview_ready` 三閘齊 pass、`complete` 無條件全綠——包含從未執行的設計閘(UI 從不送 `qa:true`)。前端短期:qa 未啟用時第四道顯示「— 未啟用」而非 ✓、LibreOffice 閘只在真的收過 preview_ready 才 pass。正解(後端小增補):render 後跑既有驗證器,emit `gate_result{gate,status}` SSE 事件,每顆勾對回一次真實檢查,順帶實現 spec 的「依序點亮」。同步修死碼:後端 error stage 詞彙(outline/slides/render/preview/qa)與前端 GATE_IDS(zip/xml/libreoffice/design)完全不重疊,`cockpit.ts:50-53` 的閘門標紅永不命中
- [ ] **P0-3 型別分頁停止說謊**(`web/src/components/PromptBar.tsx:4-6` → `src/odforge/webapi.py:338-344`):後端 `GenerateBody` 無 `doc_type` 欄位、被 Pydantic 靜默丟棄,選「試算表」實測拿到 .odp。藏掉 odt/ods 分頁(feature flag,spec §10 本有要求),或後端收下 `doc_type`、非 odp 回 422 + 前端標「即將支援」

### P1:核心互動——使用者的兩大抱怨

- [ ] **P1-4 單張檢視 UnitDetail(抱怨①「只能看縮圖牆,無法點進去一張一張看」)**:`web/src/components/UnitCell.tsx` 無 onClick、連 cursor:pointer 都沒有,spec §8 的 `<UnitDetail>` 未實作。做 lightbox:點格放大 + ←/→ 逐張翻頁 + Esc 關閉 + 頁碼指示;放大視圖內放**單張重生輸入框**接 `POST /slides/{n}/regenerate`(後端就緒 `src/odforge/webapi.py:473-491`,實測 20s、指令生效)。動工前先用 /shape 定 UX
- [ ] **P1-5 讓 AI 貼合需求的輸入層(抱怨②「只能輸入一句話」)**:
  - [ ] a. 進階抽屜:mode(detailed/presenter)、theme 預設、QA 開關、backend 選擇——`web/src/state/api.ts:3` 早已宣告這些欄位,`App.tsx:36` 寫死只送 prompt,純接線工作
  - [ ] b. 大綱確認站:送 `interactive:true` + ConfirmBar(就地改標題/刪頁/確認)接 `POST /outline`(後端就緒 `src/odforge/webapi.py:450-471`)。注意現況:若 `awaiting_approval` 事件到來,畫面顯示「等待你確認大綱…」後**永久卡死、無任何按鈕**
  - [ ] c. prompt 引導:placeholder 別再教人「用一句話」;給 2–3 個好範例 + 受眾/頁數/語氣欄位(頁數需後端 `GenerateBody`/`generate_outline` 加欄位)
  - [ ] d. Ctrl+Enter 送出(`web/src/components/PromptBar.tsx:20` 現在 Enter 只會換行)
- [ ] **P1-6 完成即死路、錯誤即失憶**(`web/src/App.tsx:48,55`、`PromptBar.tsx:9`):complete 後 `busy` 恆真 → PromptBar 永不回來,做第二份的官方姿勢是 F5;頂欄寫死「生成中的文件」(完成後也不變、也不是使用者的 prompt);錯誤後 remount 清空辛苦打的句子。修:prompt 提升到 App state、頂欄細條顯示真實 prompt、加「再鍛一份」重置動作、錯誤時保留原文(約 20 行)
- [ ] **P1-7 生成期回饋機械全數失靈**(43 秒死寂那段):
  - [ ] a. narrator 用 `find()` 永遠報第一個 filling(`web/src/components/StatusNarrator.tsx:14`)→ 改報最新完成的 n
  - [ ] b. 計數只算 preview/done(`web/src/components/PreviewStage.tsx:5`)→ LLM 階段全程「0/N 頁」;改算已有 ir 的 unit
  - [ ] c. `slide_done`/`preview_ready` 批次到達 → 前端以 80–120ms 級距排隊播放點亮(視覺節流,不改資料;filling 態加退場條件,免得整排同時發光)
  - [ ] d. 等待 outline 的 40 秒谷:階段時間軸 + 經過秒數 + 安撫文案 + **取消鈕**——把最深的焦慮谷變成「AI 管內容、引擎管格式」的教學時刻

### P2:可信與可用

- [ ] **P2-8 重整/斷線復原**:jobId 進 URL,用 `GET /jobs/{id}` 快照重建(後端就緒 `src/odforge/webapi.py:514-529`;現況生成中 F5 = job 從 UI 消失,後端其實還在跑);主題偏好順手寫 localStorage(`web/src/theme/useTheme.ts`)
- [ ] **P2-9 QAPanel(findings 有存沒顯示)**:reducer 存了 findings(`cockpit.ts:42`),UI 只印「第 N 輪」(`GateRail.tsx:24`)——被標紅的格子零解釋。做 spec §8 的 FindingRow(頁碼 · issue · severity · fix_hint)
- [ ] **P2-10 錯誤與術語人話化**:原始英文 exception → 白話訊息 + 重試鈕;閘門副標(「mimetype 為首 · manifest」「headless 真轉 PDF」)加白話 tooltip
- [ ] **P2-11 下載檔名**:UUID → 依主題命名(`src/odforge/webapi.py:511` 的 `filename=`)
- [ ] **P2-12 空台矛盾**:下載鈕「生成中…」改「尚未生成」或隱藏(`web/src/components/DownloadDock.tsx:10`,與 narrator「準備就緒」打架);outline 未到前左欄 248px 空白直條(grid 欄寬寫死)
- [ ] **P2-13 深色投影白邊**:body 無 margin reset(`app.css` 只 reset 了 `.page`)→ 全螢幕投影時 8px 瀏覽器預設白邊框住整個深色控制室,上台必炸;順便加 `<meta name="color-scheme">`

### P3:打磨

- [ ] **P3-14 無障礙**(/audit):narrator 無 `aria-live`(讀屏器聽不到任何進度);placeholder 掛 `aria-hidden` 卻是唯一承載標題的元素(`UnitCell.tsx:12`);主輸入框焦點環被拔(`app.css:168` `outline:none` 無替代);主題鈕 ☀/☾ 無 aria-label;深色 `--muted` 對比 ~4.0:1 未達 AA 卻大量用於 10.5px 微字
- [ ] **P3-15 字體現實檢查**(/typeset):`Georgia,"Noto Serif TC"` 在台灣 Windows 多半 fallback 到新細明體——「文鍛」字標在真實機器是 90 年代公文感;自架字型檔或調整 fallback 順序(`web/src/theme/tokens.css:5`)
- [ ] **P3-16 視覺細節**(/polish):flagged 態 3px 全高左紅條(`app.css:371-380`,::before 側條紋模式)換個結構;⟳「轉檔中」符號其實不會轉;`.railfoot`/`.tag` 孤兒 CSS;無 favicon
- [ ] **P3-17 契約詞彙 slide→unit**:F1 計畫鐵則自我違反(實作仍是 `slide_done`、`/slides/{n}`)——F4 做 odt/ods 前補齊,後端保留別名向前相容(spec §10)

> **保留區(評審認證的資產,修正時別動):** tokens.css 與雙向 `data-theme` 架構、data-status 驅動的 CSS 狀態機 + `prefers-reduced-motion` 完整降級、`?mock`/`?mockStep` demo 工程(只需顯式化)、引擎產出的 deck 品質本身(實測視覺在水準上)。

---

## Phase 19:決賽交付(不是程式)

- [ ] 19.0 補齊 v2 人工關卡截圖佐證:`odforge/docs/screenshots/v2/` 目前不存在——三 preset 對照(13.3)、兩段式+確認站煙霧測試(15.3)、`--qa` QAReport 實錄(16.3)、公版樣式抽取實測(17.1)各留一組;同時是決賽簡報素材
- [ ] 19.1 Dogfooding v2:用 v2 重生成使用手冊 .odt、**決賽簡報 .odp(7 分鐘,用 ODForge 自己生,QA 迴圈實錄截圖放進去)**、測試報表 .ods
- [ ] 19.2 Before/After 對照組:v1 demo vs v2 同 prompt 產出,並排截圖(決賽簡報核心素材)
- [ ] 19.3 錄影:CLI 兩段式生成(大綱確認站)→ --qa 迴圈 → LibreOffice 開檔 → 前端 demo(前端完成後)→ MCP + skill 讓 Claude Code 產簡報
- [ ] 19.4 README 更新:v2 架構圖(四道閘)、巨頭研究定位一句話、樣式抽取用法
- [ ] 19.5 決賽 QA 準備:預想統問統答題(為何 ODF 不做 PPTX、為何不用 HTML 中間層、與 NotebookLM/Gamma 差異、TAIDE 可否接——答案全在 `巨頭AI文件工具研究.md`)

---

## v2 Self-Review 檢核

- 架構分工:引擎能力(13)/保證(12 validator、14.2 預算閘、16 設計閘)/LLM 選擇與變化(12.1 design、15 兩段式)/skill(17.2)✅
- 巨頭研究採納:大綱確認站(15.3)、mode 二分法(12.1)、模板外包(17.1)、render-critique(16)、「不支援就閉嘴」(13.5 上限、16.3 fallback)✅
- 不做清單守住:無前端實作(18 只有 API)、無 HTML 中間層(preview 只做 PNG 給人看,不進生成路徑)、無像素路線、LLM 不碰座標(全部 layout 常數)✅
- 相依風險:PyMuPDF(純 pip)、soffice(v1 已處理)、vision 後端(off 可退化)——每個外部相依都有降級路徑 ✅
- v1 相容:`generate_ir` 保留、design=None 走 preset、v1 fixture 不改仍過 ✅

---
---

# 附錄:v1 MVP 計畫存檔(已完成,勿再執行)

> 以下為 2026-07 初的 v1 原計畫,全部 Phase 已完成(113 tests、CLI、MCP、三渲染器、三道閘)。保留供追溯。

**Goal:** 打造 ODForge——自然語言 → 原生 ODF(.odt/.odp/.ods)的開發者工具,含 CLI 與 MCP server,48 小時內完成競賽 MVP。

**Architecture:** LLM 只產出 Document IR(pydantic 驗證的 JSON),確定性渲染引擎把 IR 轉成原生 ODF XML;.odt/.ods 走 odfdo,.odp 走自寫 ODF package writer(mimetype 規則 + 手寫 content.xml/styles.xml + 資料驅動的版面系統)。每個輸出檔過三道驗證關卡。

**Tech Stack:** Python ≥3.10、pydantic v2、odfdo ≥3.22、typer + rich、anthropic SDK、mcp(FastMCP)、lxml、pytest。

**Spec(SDD 規格源):** `ODForge-SPEC.md`。每個 Phase 標註 [SPEC §n]。規格與計畫衝突時以 SPEC 為準,並回頭修 SPEC。

## Global Constraints(v1,v2 沿用)

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

## v1 檔案結構與介面(存檔)

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
│   ├── llm.py                  # generate_ir(prompt, doc_type)
│   ├── check.py                # odforge check:結構報告 + docx↔odt diff
│   ├── cli.py                  # typer app:new / check
│   └── mcp_server.py           # FastMCP:forge_* 工具(收 IR,不收 prompt)
└── tests/                      # test_ir / test_package / test_render_* / test_validate / test_llm / test_check / test_cli / test_mcp
```

v1 Phase 0–10(專案鷹架 → IR → odt → package → odp → 驗證 → LLM+CLI → MCP → ods+check → 加分項 → 佐證交付)**全部完成**。原步驟細節如需追溯見 git history(本檔 2026-07-07 之前的版本)。

## v1 Self-Review 紀錄(SDD 檢核,存檔)

- SPEC §1 四層架構 → Phase 1/2/4(引擎)、6(LLM+CLI)、7(MCP)✅
- SPEC §2 IR 三型別 → Task 1.1 ✅
- SPEC §3 三渲染器+驗證 → Phase 2/4/5/8.1 ✅(.ods 圖表退階,SPEC 已允許)
- SPEC §4 CLI+MCP → 6.2/7.1 ✅(MCP 改收 IR,已回寫 SPEC 的決策)
- SPEC §5 check → 8.2 ✅
- SPEC §7 範圍切割 → Phase 順序即優先序;MVP 完成線在 Phase 8 ✅
- SPEC §8/§9 佐證與簡報 → Phase 10 ✅
