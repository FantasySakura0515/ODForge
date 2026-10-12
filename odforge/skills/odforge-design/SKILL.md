---
name: odforge-design
description: 教外部 agent（Claude Code 等）用 ODForge 產出高品質簡報。涵蓋即時預覽工作流（寫 deck.json + odforge preview，使用者在瀏覽器看）、生成前訪談、page-role 大綱紀律、DesignSpec 設計欄位與對比度自檢、主題規格書，以及 MCP 的 inspect_odf + preview_odf 自查迴圈。當使用者要用 ODForge 做 .odp 簡報（或 .odt/.ods）時使用。
---

# odforge-design：用 ODForge 產出好簡報

ODForge 是一台**確定性渲染引擎**——你（agent）自己當內容作者，寫出 Document IR
（一個 JSON 物件）交給 MCP 工具，它渲染出原生 `.odp`／`.odt`／`.ods` 並驗證。
引擎不會幫你想設計，好壞取決於你交出的 IR。這份 skill 教你交出好的 IR。

## 首選工作流：即時預覽（簡報）

使用者要一份簡報、而且人在旁邊時，用這條路。你寫內容，ODForge 排版，使用者在
瀏覽器裡看。**絕對不要叫使用者打開 LibreOffice**——它只在背景當渲染器。

1. 先照 ① 訪談、② 排大綱，然後把整份 Presentation IR 寫成一個 JSON 檔，例如
   `deck.json`（格式見 ③；`type` 可省略，預設就是簡報）。
2. 在**背景**啟動預覽，輸出導到檔案：

   ```bash
   odforge preview deck.json > preview.log 2>&1
   ```

   它會自動用預設瀏覽器打開預覽頁（第一行印出網址；不想自動開就加 `--no-open`，
   再把網址給使用者）。成品 `.odp` 寫在 JSON 旁邊，同名。
3. 之後每次修改就直接**編輯 `deck.json` 存檔**。預覽頁幾秒內自動更新；同時
   `preview.log` 會多一行摘要，例如：

   ```text
   [13:05:56] 第 1 版:4 頁,發現 2 處跑版
     - 第 3 頁「這一段文字很長…」文字超出框的下緣 1.77 cm
   [13:06:02] 第 2 版:4 頁,沒有文字超出框或頁面
   ```

   讀 log 就知道哪頁跑版、超出多少；縮短文字或拆頁後再存檔，直到沒有跑版。
   JSON 寫壞時 log 會說「讀不進來,畫面維持上一版」並附原因，修好再存即可。
4. 使用者在預覽頁說哪裡要改，你就改 `deck.json`。不要另外重產一份新檔。

沒有人在看（批次產檔、使用者只要檔案）時，改用下面的 MCP 工具。

## 修改已經存在的 .odp（使用者可能手動改過）

簡報產出之後，使用者常會在網頁編輯器裡自己拖、自己改字。這時**不要重新 forge 整份**
——那會蓋掉他們的修改。改用：

1. 背景開編輯器給使用者看：`odforge edit deck.odp --no-open > edit.log 2>&1`，把第一行的
   網址給使用者（不加 `--no-open` 會自動開瀏覽器）。
2. `outline_odp(path)` 讀出每頁元素的代號（`"7/2"` = 第 8 頁第 3 個元素）、位置與文字。
3. `edit_odp(path, ops)` 只改要改的地方：改字、搬位置、加減頁。使用者開著的編輯器一秒內
   就會顯示你的修改；他們同時也能繼續手動調整。
4. 改完照樣用 `inspect_odf` 檢查跑版。

### 使用者說「照留言修改」

使用者會在編輯器裡用「留言」工具點在要改的地方寫意見。`outline_odp` 的開頭會寫
「待處理留言 N 則」，每則像 `留言 [c7/0] 關於 [7/2]:這句太長`（關於 = 那則留言針對的元素；
「整頁」表示針對整張投影片）。

1. 逐則照意見修改：改字用 `text`、搬動用 `geom`、拆頁用 `page_duplicate` + `text`。
2. **在同一次 `edit_odp` 裡**把處理好的留言用 `{"op": "comment_delete", "comment": "c7/0"}`
   刪掉。同一頁刪多則時，代號由大到小刪（刪掉前面的，後面的代號會往前移）。
3. 做不到或需要使用者決定的，不要刪那則留言；在回覆裡說明，或用 `comment_add` 回一則。

## MCP 工具（共 7 個）

| 工具 | 簽名 | 用途 |
| --- | --- | --- |
| `forge_presentation` | `(document: dict, out_path: str) -> str` | 渲染簡報 → `.odp` |
| `forge_text_document` | `(document: dict, out_path: str) -> str` | 渲染文件 → `.odt` |
| `forge_spreadsheet` | `(document: dict, out_path: str) -> str` | 渲染試算表 → `.ods` |
| `inspect_odf` | `(path: str) -> str` | 驗證既有 ODF 檔（結構／XML／soffice 三閘）|
| `preview_odf` | `(path: str, out_dir: str) -> str` | 每頁渲成 PNG，回傳路徑 JSON，供你**看圖**自查 |
| `outline_odp` | `(path: str) -> str` | 列出既有 .odp 每頁的元素代號、位置、文字 |
| `edit_odp` | `(path: str, ops: list) -> str` | 原地修改既有 .odp（改字、搬位置、增刪頁），不動其他內容 |

每個工具**只回字串、永不 raise**：失敗一律回 `"error: ..."`。呼叫後要讀回傳字串，
`"ok:"` 才算成功，`"error:"` 要修好再重來。

---

## ① 生成前訪談（先問，別急著產）

動手前先跟使用者確認三件事。**不要套罐頭模板**——針對這個主題現想。
使用者不在（或只丟一句話就要成品）時不要卡住：依下列預設直接做，交件時一句話說明你的假設。
預設：10 分鐘 ≈ 10–13 頁；審查／報告類 → `report` 版式、`detailed` 密度；其他 → 依場合挑版式。

1. **場合 → 版式（style）＋配色（theme）**：先判斷場合，再挑一組構圖與配色。
   審查／計畫書／成果報告 → `passport`（白卡＋右側深色重點面板，最接近正式審查簡報）；
   顧問式匯報／議會 → `report`；口試／課堂 → `academic`；人文／設計 → `editorial`；
   演講／發表 → `keynote`（配暗底）；品牌／作品集 → `zen`；一般教學 → `classic`。
   配色從 13 套內建主題挑最扣題的一套（④）。自訂 `design` 只在使用者有品牌色時才做，
   而且只換 accent——自調五色是生成簡報最常見的「看起來很業餘」來源。
2. **頁數**：問清楚大概幾頁、時間多長。10 分鐘口頭簡報 ≈ 10–15 頁；別把一場演講塞
   進 40 頁。
3. **mode 二分法**：`presenter`（講者型）還是 `detailed`（自讀型）？
   - `presenter`：字少，但每條仍是完整觀點（≤20 字、每頁 ≤4 條）。
   - `detailed`：文字完整，觀眾自己讀也懂（每條 15–40 字、每頁 3–6 條）。
   審查、提案、計畫書、報告這類「會被拿來讀、會被追問」的簡報一律用 `detailed`。
   注意：mode 是**你寫內容時的密度決定**，照上面的字數寫進 bullets／detail 即可，
   **不必也不要寫進 IR**——`design` 一旦出現就必須帶完整的 palette 與 fonts。

確認後再進大綱。

---

## ② page-role 大綱紀律（先排骨架，再填肉）

先用版型（layout）排出整份簡報的節奏骨架，一頁一角色，再逐頁填內容。守這些規矩：

- **一頁一個想法，標題就是那個想法**：每頁 title 寫成一句完整結論（10–22 字），
  不是主題名詞。只讀標題就要能讀懂整份簡報（顧問業的 horizontal logic）。
- **`title` 開場**：第一頁一定是封面（`title` 版型）。
- **結構頁要省**：`agenda` 只在 ≥ 15 頁時放（項目不要自己編號，頁面會自動編）；
  `section` 分隔頁只在 ≥ 20 頁時用、最多 2 張（≥ 30 頁才可 3 張），並在 `kicker`
  寫「02 章節名」（classic／report／keynote／editorial／passport 的分隔頁大編號取自這裡；
  academic 用上下夾線、zen 用小圓點，刻意不畫編號）。短簡報把章節名寫進該段第一頁的
  `kicker`（「02 研究方法」）。目錄＋分節＋引言＋大數字合計不超過全份約 18%；
  big-fact 每 10 頁最多 1 張，quote 全份最多 1 張。
- **版面不要重複**：cards／process／timeline／metrics 任一種不超過全份約 1/5，
  其餘改用 table、chart、diagram、comparison。標題裡的數量（「三項」「五個建議」）
  必須等於該頁列出的項目數。以上工具都會以警告回報。
  一份 13 頁卻有 6 頁封面／目錄／分隔頁，是生成簡報看起來空洞的頭號原因。
- **優先用視覺版型**：步驟→`process`、時間→`timeline`、2–4 個數字→`metrics`、
  2–4 個並列觀點→`cards`、項目×屬性→`table`、數字＋觀點→`dashboard`、
  架構／因果→`diagram`。`title-content` 整份最多 2 頁。
- **相鄰不同版型**：相鄰兩頁不用同一種 layout；同一 layout 不超過內容頁的三分之一。

### 每張內容頁的四件套

內容頁（title／section／closing／quote 以外）都要有：

- `kicker`：章節標籤，「兩位數編號＋章節名」。
- `title`：一句結論。內頁一行約可放 24 字、封面約 17 字；超過會換行，
  多出一兩個字時引擎會自動微縮字級避免孤字。
- `subtitle`：標題下一行說明（15–40 字），交代脈絡或證據來源。
- `takeaway`：這頁的「所以呢」（≤30 字），畫成頁底結論帶（`passport` 是右側深色面板）。
  **只放在真的有結論的頁**，約三分之一內容頁即可——每頁都放就不再有強調效果；
  而且要帶出標題沒說的新資訊（數字、影響、下一步），換句話重述標題的不如不放。

### 實用字數（超過會縮字，再不行就以「…」截斷）

- cards(3 張並排):小標 ≤ 10 字,每句 children ≤ 22 字;4 張(2×2)可再長一些。
- process:4 步時每步 title ≤ 7 字、detail ≤ 18 字;5 步時 5 字 / 14 字。
- timeline:5 個事件時 title ≤ 6 字、detail ≤ 18 字;4 個時 8 字 / 24 字。
- metrics:value 含單位 ≤ 7 字(如「12,000」「≥ 90%」),label ≤ 10 字。
- chart:每個 label ≤ 5 字。diagram:節點 title ≤ 6 字、detail ≤ 10 字。table:≤ 5 欄,每格 ≤ 12 字。
- closing:每項 text ≤ 10 字(4 項時 ≤ 6 字),children 一句 ≤ 20 字。

內容寫洞見而不是主題：「誤判率從 32% 降到 8%」勝過「降低誤判」。卡片、步驟、事件、
指標的 `detail`／`children` 一律要填——只有小標的卡片就是「簡陋」的樣子。
- **`chart` 只配真數據**：有實際數字才用 chart，最多 8 條長條。沒有數據就別硬畫圖。
- **`closing` 收尾**：最後一頁用 closing（或 section）收束——結論／CTA／致謝／Q&A。

### 19 種版型與必填欄位

| layout | 這頁的角色 | 必填／常用欄位 |
| --- | --- | --- |
| `title` | 封面 | `title`, `subtitle` |
| `cards` | 2–4 個並列觀點 | `bullets`：`{"text": 小標, "children": [1–2 句說明]}` |
| `process` | 步驟／方法 | `steps`：2–5 個 `{"title", "detail"}` |
| `timeline` | 時程／里程碑 | `events`：2–5 個 `{"label", "title", "detail"}` |
| `metrics` | 2–4 個關鍵數字 | `metrics`：`{"value", "label", "detail"}` |
| `dashboard` | 數字列＋觀點卡 | `metrics`（2–4，detail 不會畫出）＋ `bullets`（2–3 張卡，每張只放 1 條 ≤ 12 字的 children）|
| `table` | 項目 × 屬性比較 | `table`：`{"columns", "rows", "highlight"}`（≤6 列）|
| `comparison` | 對照（舊 vs 新）| `left`, `right`（各自第一項是欄標題）|
| `two-col` | 左右兩欄並列 | `left`, `right` |
| `diagram` | 架構／因果／層級 | `diagram`：`{"kind": "hub"\|"hierarchy", "nodes", "edges"}` |
| `chart` | 資料長條圖 | `chart`（必填 ChartSpec，真數據才用）|
| `big-fact` | 放大一個關鍵數字 | `title`（數字的意義）, `fact`, `bullets`（一行註腳）|
| `quote` | 引述金句 | `quote`（必填非空）, `attribution` |
| `image-focus` / `image-split` | 圖片為證據 | `image`（+ `bullets`）|
| `title-content` | 條列（整份最多 2 頁）| `bullets` |
| `agenda` | 議程（≥ 12 頁才用）| `bullets` |
| `section` | 章節分隔（≥ 20 頁才用）| `title`, `kicker`（「02 章節名」）|
| `closing` | 結尾 | `title`, `subtitle`, `bullets`（3–4 個下一步，含 children）, `kicker`（選填，「結語」）|

---

## ③ IR 契約：把 document 寫對

### Presentation（頂層）

```json
{
  "type": "presentation",
  "title": "簡報標題",
  "theme": "academic",
  "design": { ... 見下方 DesignSpec，可省略 ... },
  "style": "classic",
  "branding": { "byline": "單位 · 講者 · 日期", "logo": "asset://logo",
                "placement": "cover-closing" },
  "slides": [ { ...Slide... }, ... ]
}
```

- `theme`：`"academic"` | `"minimal"` | `"dark"` | `"teal"` | `"forest"` | `"navy"` |
  `"violet"` | `"crimson"` | `"slate"` | `"gold"` | `"sky"` | `"plum"` | `"clay"`
  （沒給 `design` 時就用這個內建主題；十三套的定位見 ④）。
- `style`：版式（構圖），與 `theme`／`design`（顏色）正交，可省略。每一套都照著一個
  公開慣例做：
  `"classic"` 學院派（ODForge 原版：置中封面＋點陣、標題左側色條、滿版分節頁）、
  `"report"` 顧問報告（MBB 結構：左上 action title、標題下細線、頁尾出處與頁碼）、
  `"academic"` 學術簡潔（Beamer metropolis：標題下 0.4pt 分隔線、底部進度條、
  分節頁上下夾線）、
  `"keynote"` 舞台（滿版出血封面、文字錨在下三分之一、內頁只有大字）、
  `"editorial"` 編輯風（Swiss：頂部通欄細線、粗標題橫貫中段、超大淡編號分節頁）、
  `"zen"` 極簡（無線、無頁尾；標題落在上三分之一）。
  省略時使用該 `theme` 配對的預設版式。
- `slides`：至少 1 張。
- `branding`：封面署名與機構標誌，可省略。`byline` 原樣印在封面標題下方；
  `logo` 只接受 `asset://<id>`（呼叫端上傳的圖），`placement` 是
  `"cover"` | `"cover-closing"`（預設）| `"all"`。這是版面家具，不是投影片內容：
  不要把單位名稱或校徽再寫進任何一頁的 title / bullets。
- `forge_presentation` 會把 `type` 強制成 `"presentation"`，你標錯也會被更正。

### Slide（每張投影片）

欄位（依 layout 取用；用不到的留空即可）：

- `layout`（必填，上表 19 選 1）
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
