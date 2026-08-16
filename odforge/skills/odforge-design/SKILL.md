---
name: odforge-design
description: 教外部 agent（Claude Code 等）驅動 ODForge MCP server 產出高品質簡報。涵蓋生成前訪談、page-role 大綱紀律、DesignSpec 設計欄位與對比度自檢、三套主題規格書，以及產出後必跑的 inspect_odf + preview_odf 視覺自查迴圈。當使用者要用 ODForge 做 .odp 簡報（或 .odt/.ods）時使用。
---

# odforge-design：用 ODForge 產出好簡報

ODForge 是一台**確定性渲染引擎**——你（agent）自己當內容作者，寫出 Document IR
（一個 JSON 物件）交給 MCP 工具，它渲染出原生 `.odp`／`.odt`／`.ods` 並驗證。
引擎不會幫你想設計，好壞取決於你交出的 IR。這份 skill 教你交出好的 IR。

## MCP 工具（共 5 個）

| 工具 | 簽名 | 用途 |
| --- | --- | --- |
| `forge_presentation` | `(document: dict, out_path: str) -> str` | 渲染簡報 → `.odp` |
| `forge_text_document` | `(document: dict, out_path: str) -> str` | 渲染文件 → `.odt` |
| `forge_spreadsheet` | `(document: dict, out_path: str) -> str` | 渲染試算表 → `.ods` |
| `inspect_odf` | `(path: str) -> str` | 驗證既有 ODF 檔（結構／XML／soffice 三閘）|
| `preview_odf` | `(path: str, out_dir: str) -> str` | 每頁渲成 PNG，回傳路徑 JSON，供你**看圖**自查 |

每個工具**只回字串、永不 raise**：失敗一律回 `"error: ..."`。呼叫後要讀回傳字串，
`"ok:"` 才算成功，`"error:"` 要修好再重來。

---

## ① 生成前訪談（先問，別急著產）

動手前先跟使用者確認三件事。**不要套罐頭模板**——針對這個主題現想。

1. **三個客製美術方向候選**：讀懂主題後，提出 3 個**為這個題目量身**的方向，
   而不是把 academic／minimal／dark 三個內建 preset 端出來。每個候選講清楚：
   氛圍一句話、主色與強調色的角色、字型個性、適合的理由。例如題目是「深海生態
   保育」，候選可以是「深海靛藍＋生物螢光青」「潮間帶砂與海泡沫的暖中性」「警訊
   珊瑚白化的漂白灰＋一點警示紅」——每個都扣題，不是通用色票。你可以拿最接近的
   內建主題當技術底（見 ③ 與主題規格書），但敘事要客製。
2. **頁數**：問清楚大概幾頁、時間多長。10 分鐘口頭簡報 ≈ 10–15 頁；別把一場演講塞
   進 40 頁。
3. **mode 二分法**：`presenter`（講者型）還是 `detailed`（自讀型）？
   - `presenter`：大字、極簡、一頁一個想法，靠講者口述補完。
   - `detailed`：文字完整，觀眾自己讀也懂（講義、寄出去的檔）。
   這會影響每頁塞多少字。

確認後再進大綱。

---

## ② page-role 大綱紀律（先排骨架，再填肉）

先用版型（layout）排出整份簡報的節奏骨架，一頁一角色，再逐頁填內容。守這些規矩：

- **一頁一個想法**：一張投影片只講一件事。塞不下就拆成兩頁，別擠。
- **`title` 開場**：第一頁一定是封面（`title` 版型）。
- **長簡報放 `agenda`**：超過約 8 頁就在封面後放一張大綱，給聽眾路線圖；短簡報可略。
- **用 `section` 分段**：進入新的大段落時插一張 section 分隔頁，像書的章名。
- **節奏穿插**：別整份都是 `title-content` 條列。用 `big-fact`（關鍵數字）、
  `quote`（引述）、`chart`（真實數據圖）製造高低起伏，讓聽眾不睡著。
- **同版型不連發 ≥3**：同一種 layout 不要連續出現 3 頁以上。連兩頁條列後，換個
  節奏頁再繼續。
- **`chart` 只配真數據**：有實際數字才用 chart，最多 8 條長條。沒有數據就別硬畫圖。
- **`closing` 收尾**：最後一頁用 closing（或 section）收束——結論／CTA／致謝／Q&A。

### 10 種版型與必填欄位

| layout | 這頁的角色 | 必填／常用欄位 |
| --- | --- | --- |
| `title` | 封面 | `title`, `subtitle` |
| `title-content` | 標題＋條列內頁（主力）| `title`, `bullets` |
| `two-col` | 左右兩欄並列 | `title`, `left`, `right` |
| `section` | 章節分隔頁 | `title` |
| `big-fact` | 放大一個關鍵數字 | `fact`,（可加 `bullets` 當註腳）|
| `quote` | 引述金句 | `quote`（必填非空）, `attribution` |
| `agenda` | 大綱／議程 | `title`, `bullets` |
| `comparison` | 對照表態 | `title`, `left`, `right` |
| `chart` | 資料長條圖 | `title`, `chart`（必填 ChartSpec）|
| `closing` | 結尾頁 | `title`（收束訊息）|

---

## ③ IR 契約：把 document 寫對

### Presentation（頂層）

```json
{
  "type": "presentation",
  "title": "簡報標題",
  "theme": "academic",
  "design": { ... 見下方 DesignSpec，可省略 ... },
  "branding": { "byline": "單位 · 講者 · 日期", "logo": "asset://logo",
                "placement": "cover-closing" },
  "slides": [ { ...Slide... }, ... ]
}
```

- `theme`：`"academic"` | `"minimal"` | `"dark"` | `"teal"` | `"forest"` | `"navy"` |
  `"violet"` | `"crimson"` | `"slate"` | `"gold"` | `"sky"` | `"plum"` | `"clay"`
  （沒給 `design` 時就用這個內建主題；十三套的定位見 ④）。
- `slides`：至少 1 張。
- `branding`：封面署名與機構標誌，可省略。`byline` 原樣印在封面標題下方；
  `logo` 只接受 `asset://<id>`（呼叫端上傳的圖），`placement` 是
  `"cover"` | `"cover-closing"`（預設）| `"all"`。這是版面家具，不是投影片內容：
  不要把單位名稱或校徽再寫進任何一頁的 title / bullets。
- `forge_presentation` 會把 `type` 強制成 `"presentation"`，你標錯也會被更正。

### Slide（每張投影片）

欄位（依 layout 取用；用不到的留空即可）：

- `layout`（必填，上表 10 選 1）
- `title`, `subtitle`, `fact`, `quote`, `attribution`, `kicker`（字串）
- `bullets`：字串陣列；也可以是巢狀 `{"text": "...", "children": ["...", ...]}`（一層）
- `left`, `right`：字串陣列（two-col／comparison 用）
- `chart`：ChartSpec（chart 版型必填）
- `notes`：講者備忘稿（任何版型都可加）

交叉規則：`layout="chart"` 必須有 `chart`；`layout="quote"` 的 `quote` 必須非空。
違反會回 `error:`。

### ChartSpec（給 chart 版型）

```json
{ "labels": ["A","B","C"], "values": [10, 25, 40], "unit": "%", "highlight": 2 }
```

- `labels` 與 `values` 等長，`values` 非負，最多 **8 條**（超過請拆成多張圖）。
- `highlight`（可省）：要用強調色點亮的那條的索引（0 起算）。

### DesignSpec（每份簡報客製設計，可省略）

把 ① 訪談定案的美術方向落成 `design`。省略則用 `theme` 內建主題。

**配色心法（60-30-10）**：五個 palette token 就是一套 60-30-10——`bg` 是 60% 的底、
`surface`+`text`+`muted` 是 30% 的結構、`accent` 是那 10%（**全簡報唯一的彩色**，只點在
一個關鍵字／標題底線／一條 highlight）。要自訂 palette、想懂配色配方與趨勢／色盲安全
的圖表用色，讀 [themes/color-system.md](themes/color-system.md)。

```json
"design": {
  "palette": { "bg":"#RRGGBB", "surface":"#RRGGBB", "text":"#RRGGBB",
               "muted":"#RRGGBB", "accent":"#RRGGBB" },
  "fonts": { "display": "Noto Serif TC", "body": "Noto Sans TC" },
  "scale": "standard",
  "mode": "presenter"
}
```

- `palette`：5 色，全部 `#RRGGBB` 六碼 hex。
- `fonts`：`display` 與 `body`，只能選 `FONT_WHITELIST` 內的
  **Noto Sans TC / Noto Serif TC / 微軟正黑體 / 標楷體**（其他字型會被拒）。
- `scale`：`"compact"` | `"standard"` | `"display"`（字級密度，見主題規格書）。
- `mode`：`"presenter"` | `"detailed"`（① 的二分法；給下游提示，不改字級）。

### 對比度自檢（palette 過不了就會被拒）

調色盤送進去前，先自己算 WCAG 對比，全部要達標，否則 `forge_presentation` 回 `error:`：

- `text` / `bg` ≥ **4.5**
- `accent` / `bg` ≥ **3.0**
- `muted` / `bg` ≥ **3.0**

暗底主題尤其容易踩雷：淺文字掉到中灰就糊。算不確定就把顏色再拉開一階。

---

## ④ 主題規格書（給 LLM 讀的鏡像）

十三套內建美術方向的完整 hex 表、字級刻度、字型與每個版型的使用時機，寫在下面。
九套淺底、四套暗底，挑最扣題的一套當 `theme`，或當自訂 `design` 的技術底：

- [themes/academic.md](themes/academic.md) — 學術藍＋暖白，serif 標題，適合論文口試／研究報告。
- [themes/minimal.md](themes/minimal.md) — 暖灰單色＋一點橘，克制留白，適合產品發表／pitch。
- [themes/teal.md](themes/teal.md) — 企業青綠（2026 年度色）＋冷白，冷靜可信，適合企業／SaaS／ESG。
- [themes/forest.md](themes/forest.md) — 森綠＋暖奶油，serif，editorial 質感，適合永續／生態／人文。
- [themes/navy.md](themes/navy.md) — 海軍藍＋冷白，最通用的商務配色，適合正式簡報／投資人場。
- [themes/dark.md](themes/dark.md) — 深靛＋亮青，暗底舞台感，適合技術分享／demo。
- [themes/violet.md](themes/violet.md) — 午夜紫＋電光紫，暗底創意戲劇感，適合創意提案／設計敘事。
- [themes/crimson.md](themes/crimson.md) — 學院紅＋暖白，serif，典禮與人文重量，適合校慶／系所評鑑。
- [themes/slate.md](themes/slate.md) — 石墨灰＋柔和冷藍，暗底不刺眼，適合工程／資安／長時間技術場。
- [themes/gold.md](themes/gold.md) — 墨金，暗底最正式的一套，適合頒獎／成果發表／年度回顧。
- [themes/sky.md](themes/sky.md) — 天青＋冷白，溫和好認，適合課堂教學／招生說明／公部門宣導。
- [themes/plum.md](themes/plum.md) — 梅紫＋米白，serif，淺底最有個性，適合藝文／設計提案／展覽。
- [themes/clay.md](themes/clay.md) — 陶土橘＋米色，土地與手作感，適合永續／地方創生／社區營造。

配色方法論（60-30-10、配色配方、趨勢與色盲安全的圖表用色）另寫在
[themes/color-system.md](themes/color-system.md)。這些數值與 `odforge.themes.THEMES` /
`SCALES` 常數 lockstep（有測試防漂移）。要客製 `design` 時，可拿最接近的一套當技術底，
再微調 palette。

---

## ⑤ 產出後必做：inspect_odf + preview_odf 自查迴圈

`forge_*` 回 `"ok:"` 只代表檔案結構有效，**不代表版面好看**。產完一定要兩步自查：

1. **`inspect_odf(path)`**：確認結構／XML（有裝 LibreOffice 時還會跑 soffice 往返）
   全部 OK。任何 FAIL 都要修。
2. **`preview_odf(path, out_dir)`**：把每頁渲成 PNG。成功回
   `{"pages": [...png 路徑...], "count": N}`（JSON 字串，`json.loads` 後拿路徑）。
   **實際把 PNG 讀進來看**：有沒有文字溢出、標題撞邊、條列爆框、對比不足、
   同版型連發太單調？看到問題就回去改 IR，重 forge、重 preview，直到畫面過關。
   （若回 `"error: preview unavailable: ..."` 表示這台沒裝 LibreOffice，跳過視覺步驟，
   但仍以 `inspect_odf` 把關結構。）

沒跑過 preview_odf 就交件，等於沒看過自己的簡報長怎樣。這一步是把 QA harness 的
「看圖」能力交到你手上——用它。
