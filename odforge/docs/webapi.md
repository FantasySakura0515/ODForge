# ODForge Web API 契約（Task 18.1）

前端控制台照本文件設計。後端把 ODForge v2 的兩段式生成流程
（`generate_outline` → `generate_slides` → `render` → `render_pages` →
選用的 `run_qa_loop`）包成 FastAPI + SSE。此為**後端契約**，前端由團隊另行設計。

## 啟動

```bash
pip install "odforge[web]"
odforge serve --host 127.0.0.1 --port 8000
```

`odforge serve` 會以 uvicorn 跑 `odforge.webapi.create_app()`。
CORS 採「明確白名單」（**非**萬用 `*`、且不帶 credentials）：預設放行本機開發來源
`http://localhost:5173`、`http://127.0.0.1:5173`、`http://localhost:3000`、
`http://127.0.0.1:3000`，可用環境變數 `ODFORGE_CORS_ORIGINS`（逗號分隔）覆寫。
覆寫值必須是明確的 `http(s)` origin；`*`、`null`、缺 scheme、含路徑／query 的值會在
啟動時直接拒絕。非 loopback `--host` 也必須明確加上 `--allow-remote`；遠端模式仍無認證，
只應在受信任網路與防火牆內使用。
綁定 127.0.0.1 不能取代此檢查——請求來自使用者自己的瀏覽器，故以 Origin 白名單擋下
任意網站驅動本工具（此 API 無認證且會花用使用者的真實 LLM 金鑰）。所有回應與 SSE
`data:` 一律為 UTF-8 JSON。

## 資料模型速覽

以下欄位是 SSE 事件與快照回應裡會出現的巢狀結構，皆為 pydantic v2
`model_dump(mode="json")` 的輸出。

### `Outline`
```json
{
  "design": {
    "palette": {
      "bg": "#0B1020",
      "surface": "#161C2E",
      "text": "#F5F7FA",
      "muted": "#9AA6C0",
      "accent": "#5B8CFF"
    },
    "fonts": { "display": "Noto Sans TC", "body": "Noto Sans TC" },
    "scale": "standard",
    "mode": "presenter"
  },
  "mode": "presenter",
  "source_prompt": "為高中生製作一份光合作用簡報",
  "media_assets": [
    { "id": "asset-01", "description": "葉片顯微照片", "credit": "校內實驗室" }
  ],
  "image_generation_available": false,
  "pages": [
    { "role": "title",   "title": "光合作用", "gist": "全片主題與定位", "visual_intent": "簡潔封面" },
    { "role": "process", "title": "反應總覽", "gist": "光反應與暗反應兩階段", "visual_intent": "由左到右呈現能量轉換" },
    { "role": "metrics", "title": "關鍵數據", "gist": "整理題目提供的成果數字", "visual_intent": "三張數據卡並列" }
  ]
}
```
- `design` 可為 `null`（模型色盤未通過對比檢查時後端會剝除，渲染改用預設主題）。
  存在時 `palette` 五色與 `fonts` 供前端即時渲染色卡／字體預覽。
- `mode`：`"presenter"`（講者型，大字少字）或 `"detailed"`（自讀型，完整文字）。
- `source_prompt` 保存原始需求，確保逐頁生成與重新生成時不會丟失受眾、資料與限制。
- `visual_intent` 描述每頁要呈現的視覺關係。
- `media_assets` 是應用程式產生的安全素材索引；模型只能引用 `asset://<id>`，
  不會看到上傳檔案的本機路徑。
- `role` 取值同 `Slide.layout`（見下）。

### `Slide`
```json
{
  "layout": "title-content",
  "title": "反應總覽",
  "subtitle": "",
  "bullets": ["光反應：類囊體膜", "暗反應：基質（卡爾文循環）"],
  "left": [],
  "right": [],
  "fact": "",
  "quote": "",
  "attribution": "",
  "kicker": "",
  "chart": null,
  "steps": [],
  "events": [],
  "metrics": [],
  "diagram": null,
  "image": null,
  "sources": [],
  "notes": "開場帶出兩階段。"
}
```
- `layout` ∈ `title | title-content | two-col | section | big-fact | quote |
  agenda | comparison | chart | cards | process | timeline | metrics | diagram |
  image-focus | image-split |
  closing`。
- `bullets` 元素可為字串，或巢狀 `{"text": "...", "children": ["...", "..."]}`。
- `chart` 存在時為 `{"labels": [...], "values": [...], "unit": "", "highlight": null}`。
- `steps`、`events`、`metrics` 分別供流程、時間軸與指標卡頁型使用。
- `diagram` 使用 `kind: "hub" | "hierarchy"`、2–6 個 nodes 與 1–8 條 edges。
- `image` 為
  `{"src":"asset://asset-01","prompt":"","alt":"...","caption":"","credit":"","fit":"contain"}`；
  `image-focus`／`image-split` 必須提供。`prompt` 只有在圖片生成 adapter 已設定時使用。
- `sources` 最多 3 筆，每筆包含短 `label` 與可選的 HTTP(S) `url`；
  renderer 會在頁尾輸出可點擊引用。

### `Finding`
```json
{ "slide_no": 3, "issue": "標題文字溢出版面右緣", "severity": "error", "fix_hint": "縮短標題或縮小字級" }
```
`severity` ∈ `error | warn`。

### `QAReport`
```json
{
  "rounds": 2,
  "findings_by_round": [
    [ { "slide_no": 3, "issue": "標題文字溢出版面右緣", "severity": "error", "fix_hint": "縮短標題或縮小字級" } ],
    []
  ],
  "final_ok": true,
  "note": "",
  "failure": "",
  "repaired": true
}
```

- `rounds` 只計**完成**的評審輪數；`findings_by_round` 保留每一輪的發現,
  中途失敗不再整份丟棄。
- `failure` 非空表示評審迴圈未能跑完(視覺來源失效、修補階段出錯等),原因
  同步鏡射到 `note` 供顯示。此時若 `final_ok=false`,代表最後完成的一輪仍有
  error,且其後套用的修補**未經複驗**。
- `repaired=true` 表示修補實際改寫並重渲染了成品——後端會重發 `preview_ready`
  並在 URL 加上 `?v=k` cache-buster,前端照字面換圖即可。

---

## API 契約表

| Method | Path | Body → 回應 |
|---|---|---|
| POST | `/api/discovery/questions` | `{prompt, mode?, theme?, language?, backend?, doc_type?: "odp", pages?, assets?: [{description, credit, data_url?}]}` → `DiscoveryPlan`。訪談一律以繁體中文提問，`language` 只是讓讀題模型知道成品要寫成哪一種語言 |
| POST | `/api/discovery/questions/stream` | 同上 → NDJSON `progress` / `result` / `error` 事件 |
| POST | `/api/generate` | `{prompt, mode?, theme?, design?: DesignSpec, style?: "classic"\|"report"\|"academic"\|"keynote"\|"editorial"\|"zen", language?: "zh-TW"\|"en"\|"bilingual", byline?, logo?: AssetUpload, logo_placement?: "cover"\|"cover-closing"\|"all", interactive?: bool, qa?: bool, backend?, doc_type?: "odp", pages?: int, assets?: AssetUpload[]}` → `{job_id}`。`theme` 對照內建主題註冊表（THEMES）驗證；`design` 是自訂範本的設計代幣，兩者同時給時 `design` 勝出 |
| GET | `/api/templates` | 範本庫 `{templates: [{id, name, design, style, builtin, source, created_at}], languages: [{id, label}], styles: [{id, label, blurb}]}`。一個範本 = 版式 × 配色：內建（`builtin: true`）以 `theme: <id>` 套用、不可改不可刪，自訂的整包送 `design`，兩者都連同 `style` 一起送 |
| POST | `/api/templates` | `{name, design, style?, source?: "custom"\|"extracted", id?}` → 存檔後的 `Template`。給 `id` 為覆寫；調色盤過不了 WCAG 對比或範本數達上限回 `422` |
| DELETE | `/api/templates/{id}` | 刪除自訂範本 → `{ok: true}`；內建 `422`、不存在 `404` |
| POST | `/api/templates/extract` | `{data_url}`（.otp/.odp/.ott/.odt 的 base64 data URI，≤ 12 MiB）→ `{design}`。只抽出、不存檔——命名與儲存是下一步 |
| GET | `/api/sessions` | 最近 session `{sessions: [{id, title, prompt, status, page_count, preview_url?, download_url?, ...}]}` |
| DELETE | `/api/sessions/{id}` | 刪除一份工作紀錄與它在伺服器上的檔案（`.odp`、預覽圖）→ `{ok: true}`；不存在 `404`。工作仍在進行、已取消但外部呼叫尚未結束、或正在重生時回 `409` 並附中文原因——目錄被抽掉會讓還在跑的 worker 寫進不存在的路徑 |
| GET | `/api/sources` | 可用模型來源 `{text: [{name, available, reason}], vision: [...], defaults: {text, vision}}`。前端據此決定這一輪的「設計品質檢查」算不算數——第四道閘沒有開關,但沒有可用的視覺來源時不會送 `qa: true`,介面會直接說明只跑前三道 |
| GET | `/api/jobs/{id}/events` | SSE 事件流（見「SSE 事件」） |
| POST | `/api/jobs/{id}/outline` | `{action: "approve"}` 或 `{action: "edit", outline: Outline}` → `{ok, status}` |
| POST | `/api/jobs/{id}/cancel` | 無 body → `{ok, status: "cancelled"}`；停止尚未開始的後續階段 |
| POST | `/api/jobs/{id}/slides/{n}/regenerate` | `{instruction?: str}` → `{ok, n, slide, preview_url, version, gates}`（同步：新頁 + preview 直接回在回應內，**不**走 SSE）。交易式：算圖／驗證／預覽任一失敗即完整回滾，回 `500` 且工作維持原狀。`gates` 是重算後的四道閘結果，設計閘一律退回 `unknown`（視覺評審看的是被換掉的那一頁） |
| POST | `/api/jobs/{id}/units/{n}/regenerate` | 同上（`slides/{n}/regenerate` 的 F4 正名別名，同一 handler） |
| GET | `/api/jobs/{id}/preview/{n}.png` | 第 n 頁 PNG（`image/png`） |
| GET | `/api/jobs/{id}/download` | 最終 `.odp`（`Content-Disposition: attachment`）。**只有**「工作已完成且磁碟上的版本通過必要閘門」才回 `200`；生成中／驗證失敗／取消／重生中一律 `409` 並附中文原因；工作不存在為 `404` |
| GET | `/api/jobs/{id}` | 狀態快照 `{status, slides_done, outline?, gates, artifact_version, downloadable, findings?, dropped_content?, download_url?, error?}`。`download_url` 只在 `downloadable` 為真時出現——不宣傳一個點下去就 409 的連結 |

### POST `/api/discovery/questions`

這是生成工作建立前的短回合，不會建立 job。模型會根據已知 prompt 與介面設定，
只詢問尚未提供且最影響簡報品質的問題：一般 3–5 題，需求龐大時最多 8 題（`questions`
上限 8、`known_context` 上限 10，超出的部分由後端裁掉，不會讓整場訪談失敗）：

```json
{
  "summary": "向系上老師報告畢業專題進度，聚焦架構與時程。",
  "known_context": ["受眾是系上老師", "目標頁數為 8 頁"],
  "questions": [
    {
      "id": "feedback_goal",
      "question": "這次最希望老師提供哪一類回饋？",
      "why": "決定簡報最後的行動請求。",
      "options": ["確認架構可行性", "提供技術建議", "評估時程"]
    }
  ],
  "completeness": 35
}
```

前端把回答整理為可編輯 Brief，再把確認後的文字放回 `/api/generate.prompt`。
`assets` 中的圖片只傳描述與來源；PDF 可附 `data_url`，後端抽取文字供訪談使用。
圖片 bytes 僅在真正的 `/api/generate` 上傳。

需要讓使用者看到等待狀態時，使用 `/api/discovery/questions/stream`。每一行都是
一個完整 JSON 事件；`progress.data` 會提供 `stage`、`message`、`elapsed_ms` 與
`request_id`，最後以 `result.data.plan` 回傳同一份 `DiscoveryPlan`。這些是可驗證的
作業階段（送出模型、等待、驗證），不是模型的 chain-of-thought。

### POST `/api/generate`
請求：
```json
{
  "prompt": "介紹光合作用的兩階段",
  "mode": "presenter",
  "interactive": false,
  "qa": false,
  "doc_type": "odp",
  "pages": 12,
  "assets": [
    {
      "description": "研究論文",
      "credit": "校內實驗室",
      "data_url": "data:application/pdf;base64,..."
    }
  ]
}
```
回應 `200`：
```json
{ "job_id": "3f0a1c9e8b7d4a2f9c1e6b5d4a3c2b1a" }
```
`job_id` 為伺服器產生的 uuid4 hex（32 字）。生成隨即在背景 asyncio task 執行，
前端接著開 `GET /api/jobs/{id}/events` 訂閱進度。
`mode` / `theme` 若給定會覆蓋 LLM 的選擇（`theme` 會同時剝除 AI 自選 design）。
`assets` 最多 6 筆，只接受 8 MiB 以下的真實 PDF/PNG/JPEG。後端會驗證 magic
bytes；PDF 最多 120 頁，會抽取最多 60,000 字作為模型資料，無文字層、損毀或有密碼
的 PDF 會回 `422`。圖片會改用伺服器產生的 ID 並寫入該 job 的隔離目錄；模型輸出的
任意本機路徑一律不接受。

- `prompt`：去除頭尾空白後必須非空，最長 8,000 字元。
- `mode`：只接受 `presenter`／`detailed`；`theme` 只接受七個內建主題；`backend` 只接受
  `deepseek`／`ollama`／`custom`。未知值回 `422`，不會留到背景工作才失敗。

每個 job 會把可恢復的 session metadata 原子寫入自己的隔離目錄；服務重啟後會載入
既有 session。重啟時仍未完成的工作會標記為 `error`／`restore`，已完成的預覽與 ODP
仍可由首頁開啟或下載。預設保存位置可用 `ODFORGE_SESSIONS_DIR` 覆寫。

- `doc_type`（預設 `"odp"`）：Web 控制台**只**產生簡報（`odp`），這是定案而非待辦
  （理由見 `gates.md` 第五節）。傳入其他值（如 `ods`／`odt`）回 `422`，`detail` 是
  人可讀的中文字串，並指向能做這件事的介面（CLI 的 `odforge new` 與 MCP 的 `forge_*`
  三種格式都支援）。
- `pages`（選填，整數 `3..30`）：目標頁數；後端把「約 N 頁（含封面與結尾，可 ±1）」的
  軟性指示併入第一段（`generate_outline`）的提示，非硬性張數約束。超出 `3..30` 由 pydantic
  回 `422`。

### POST `/api/jobs/{id}/outline`（僅 interactive）
生成在 `awaiting_approval` 停下等待核可時使用。
- 核可：`{"action": "approve"}`
- 修改後核可：`{"action": "edit", "outline": { …完整 Outline… }}`

回應 `200`：`{"ok": true, "status": "awaiting_approval"}`（收下後 runner 隨即續跑）。
狀態不在 `awaiting_approval` 時回 `409`；`edit` 缺 `outline` 或 Outline 不合法回 `422`。
等待核可超過 30 分鐘會結束為 `error`，釋放工作名額。

### POST `/api/jobs/{id}/cancel`
將尚未結束的工作標記為 `cancelled`、喚醒互動核可等待並取消背景 runner。已經送到外部
LLM 的單次請求無法追回，但取消後不再啟動後續生成、渲染與預覽階段。已完成、失敗或
已取消的工作回 `409`。

### POST `/api/jobs/{id}/slides/{n}/regenerate`
只重生第 n 頁（以該頁 role/title/gist 組成單頁 sub-outline，把 `instruction`
併入 gist 後重跑 stage 2），其餘頁不動；接著重渲染並刷新該頁 preview。
這是**同步**操作（與初次生成不同）：SSE 串流的生命週期在 `complete` 已結束，故結果
直接回在 HTTP 回應內，前端由發出此請求的元件自行更新該頁。
請求：`{"instruction": "改用更大膽的視覺，字更少"}`（`instruction` 可省略，最長 2,000
字元）。同一工作同時只允許一個重生，全服務最多同時兩個；超額回 `409`／`429`。
回應 `200`：
```json
{
  "ok": true,
  "n": 2,
  "slide": { "…": "…新的 Slide…" },
  "preview_url": "/api/jobs/3f0a…/preview/2.png"
}
```
`preview_url` 在無 LibreOffice（preview 不可用）時為 `null`。n 超出範圍回 `404`；
job 尚未生成完成回 `409`；重生本身失敗回 `500`，body 為
`{"detail": {"message": "…", "stage": "regenerate"}}`（乾淨的結構化錯誤，非 traceback；
既有的 deck 仍有效，job 狀態維持 `complete`）。

### GET `/api/jobs/{id}/preview/{n}.png`
回第 n 頁 PNG（`image/png`）。n 從 1 起算。超出頁數或該頁 PNG 尚未產生（如無
LibreOffice）回 `404`。

### GET `/api/jobs/{id}/download`
回最終 `.odp`，`Content-Type: application/vnd.oasis.opendocument.presentation`，
`Content-Disposition: attachment`。檔名依主題命名（**非** UUID）：優先取生成簡報的標題
（`job.ir.title`），否則取 `prompt` 前 20 字；經檔名消毒（移除 `\ / : * ? " < > |` 與換行、
去首尾空白）後加 `.odp`；若消毒後為空才回退為 `{id}.odp`。中文保留（非 ASCII 檔名以
RFC 5987 `filename*` 送出）。檔案尚未算圖回 `404`。

### GET `/api/jobs/{id}`
重整頁面／重連時取狀態快照：
```json
{
  "status": "complete",
  "slides_done": 3,
  "outline": { "…": "…Outline…" },
  "findings": [ { "slide_no": 3, "issue": "…", "severity": "warn", "fix_hint": "…" } ],
  "download_url": "/api/jobs/3f0a…/download"
}
```
`status` ∈ `pending | generating_outline | awaiting_approval | generating_slides
| rendering | qa | complete | error`。`outline`/`findings`/`download_url`/`error`
視進度出現。

### 錯誤碼
- `404`：job 不存在，或 preview 頁碼超出範圍。job id 只作為記憶體 dict 的鍵，
  絕不併進檔案路徑，故不存在即 404，不會有路徑穿越。
- `409`：狀態不符（outline 未在等待核可、regenerate 時 job 未完成）。
- `429`：同時執行中的生成工作（最多 4）或重生操作（最多 2）已達上限。
- `422`：請求 body 不合法（如 edit 的 outline 不通過 pydantic 驗證）。

---

## SSE 事件

`GET /api/jobs/{id}/events` 是 `text/event-stream`。每筆事件的
`event:` 是型別、`data:` 是一行 JSON。事件會**完整回放**（晚訂閱／重連者會從
`outline` 起收到全部歷史），串流在 `complete` 或 `error` 後結束。

wire 範例：
```
event: outline
data: {"design":null,"mode":"presenter","pages":[…]}

event: slide_done
data: {"n":1,"slide":{…}}

```

非 interactive、開 preview + qa 的典型順序：
`outline` → `slide_done`×N → `gate_result{zip}` → `gate_result{xml}` →
`gate_result{libreoffice}` → `preview_ready`×N → `qa_round`×R →
`gate_result{design}` → `complete`。
（interactive 時 `outline` 後先出 `awaiting_approval`，核可後才續。無 soffice 時
`libreoffice` 閘回 `skipped` 且略過所有 `preview_ready`，其餘照舊。QA 若實際修頁
（多於一輪），會重跑 preview 階段刷新過期 PNG，故會**再發一次** `gate_result{libreoffice}`
與修好頁的 `preview_ready`；同一 `gate`／同一頁因此可能出現一次以上——前端一律**以最後
一次為準**（last-wins）。)

以下為每個事件 `data` 的完整 JSON 範例。

### `outline` — 第一段（大綱＋美術方向）完成
```json
{
  "design": {
    "palette": { "bg": "#0B1020", "surface": "#161C2E", "text": "#F5F7FA", "muted": "#9AA6C0", "accent": "#5B8CFF" },
    "fonts": { "display": "Noto Sans TC", "body": "Noto Sans TC" },
    "scale": "standard",
    "mode": "presenter"
  },
  "mode": "presenter",
  "pages": [
    { "role": "title",         "title": "光合作用",     "gist": "全片主題與定位" },
    { "role": "title-content", "title": "反應總覽",     "gist": "光反應與暗反應兩階段" },
    { "role": "big-fact",      "title": "能量轉換效率", "gist": "強調約 3–6% 的實際效率" }
  ]
}
```

### `awaiting_approval` — interactive 等待大綱核可
```json
{}
```

### `slide_done` — 第 n 頁 IR 完成（逐頁）
```json
{
  "n": 2,
  "slide": {
    "layout": "title-content",
    "title": "反應總覽",
    "subtitle": "",
    "bullets": ["光反應：類囊體膜", "暗反應：基質（卡爾文循環）"],
    "left": [],
    "right": [],
    "fact": "",
    "quote": "",
    "attribution": "",
    "kicker": "",
    "chart": null,
    "notes": "開場帶出兩階段。"
  }
}
```

### `unit_done` — `slide_done` 的 F4 正名別名

自 F4 起，「頁」的正名為 unit。每發一個 `slide_done` 會**緊接**並發一個 `unit_done`，
`data` 完全相同（`{n, slide}`）。`slide_done` 保留以向前相容；新前端可只監聽 `unit_done`，
或兩者皆聽並收斂成同一處理（本專案前端即如此）。

### `preview_ready` — 第 n 頁 PNG 就緒
```json
{ "n": 2, "url": "/api/jobs/3f0a1c9e8b7d4a2f9c1e6b5d4a3c2b1a/preview/2.png" }
```

QA 修補或單頁重生後**重發**的 `preview_ready`,其 `url` 會帶 `?v=k`
cache-buster(伺服器端忽略該 query)——同一頁的 URL 字串因此不同,前端照字面
換掉 `src` 就能避開瀏覽器快取。

### `qa_round` — QA 每輪結束
```json
{
  "round": 1,
  "findings": [
    { "slide_no": 3, "issue": "標題文字溢出版面右緣", "severity": "error", "fix_hint": "縮短標題或縮小字級" }
  ]
}
```

### `gate_result` — 四道品質閘的真實訊號

前端的四道閘勾勾以此事件點亮（**非**用 `preview_ready` 假裝）。
`data` 為 `{"gate": <str>, "status": <str>, "note"?: <str>}`：

- `gate` ∈ `zip | xml | libreoffice | design`
- `status` ∈ `pass | fail | skipped`
- `note`（選用）：這道閘為什麼是這個結果（視覺模型未設定、供應商拒絕、
  評審中途失敗…）。只在需要解釋時附上，前端照字面顯示。

```json
{ "gate": "zip", "status": "pass" }
```

發射時機（依序）：

1. render 完成後，對產出的 `.odp` 跑確定性驗證（`validate.py`）：
   - `zip`：zip 結構／mimetype-first-stored／副檔名相符／manifest 對齊。
   - `xml`：每個 `.xml` 成員皆 well-formed。
   兩閘各發一個 `gate_result`。任一 `fail` 會**中止**流程並發 `error`（`stage="validate"`）。
2. preview 階段(`libreoffice`)：`render_pages` 成功 → `pass`；無 soffice（`PreviewUnavailable`）
   → `skipped`；其他轉檔錯誤 → `fail`（preview 為 best-effort，`fail` 不中止 job）。
3. `design` 閘：未開 `qa` → `skipped`；視覺來源為 `off` → `skipped`（沒有模型看過
   圖,綠勾等於謊報）；`qa_report.rounds == 0`（無 soffice、或第一輪視覺品檢就
   失敗）→ `skipped`；其餘依 `final_ok` 給 `pass`／`fail`。`skipped`／`fail` 都
   帶 `note` 說明原因（`failure` 優先於 `note`）。QA 例外未產報告 → `skipped`
   加上 `job.qa_error` 的原因。在 `complete` 前發出。

`gate_result` 也進事件 log，故快照重連會完整回放。

### `complete` — 全部完成
```json
{
  "download_url": "/api/jobs/3f0a1c9e8b7d4a2f9c1e6b5d4a3c2b1a/download",
  "qa_report": {
    "rounds": 2,
    "findings_by_round": [
      [ { "slide_no": 3, "issue": "標題文字溢出版面右緣", "severity": "error", "fix_hint": "縮短標題或縮小字級" } ],
      []
    ],
    "final_ok": true,
    "note": ""
  }
}
```
未開 `qa` 時無 `qa_report` 欄位，只有 `download_url`。

### `error` — 任一階段失敗
```json
{ "message": "內容產生失敗：…", "stage": "slides" }
```
`stage` ∈ `outline | slides | render | validate | preview | qa`。錯誤事件後串流結束，
快照的 `status` 為 `error`、並帶同樣的 `error` 物件。

---

## 前端建議流程

1. `POST /api/generate` 取 `job_id`。
2. 開 `EventSource("/api/jobs/{id}/events")` 訂閱。
3. 收 `outline` → 渲染色卡與頁面骨架。若 interactive，收到 `awaiting_approval`
   後讓使用者確認／編修，再 `POST /outline`。
4. 逐一收 `slide_done`（更新頁面內容）與 `preview_ready`（載入 `url` 的 PNG）。
5. 收 `qa_round` 顯示評審結果；收 `complete` 後以 `download_url` 提供下載。
6. 使用者想改某頁 → `POST /slides/{n}/regenerate`，直接用回應裡的 `slide` 與
   `preview_url` 更新該頁（同步；此路徑不走 SSE）。
7. 重整／重連 → 直接重開 `EventSource("/api/jobs/{id}/events")`。事件 log 是 append-only
   且**完整回放**：晚訂閱者會從 `outline` 起依序重收全部歷史事件，reducer 天然重建整個
   狀態（`awaiting_approval` 回到確認站、`complete` 回到可下載）。故復原**走全量事件重播**，
   不需先打 `GET /api/jobs/{id}` 快照；本專案前端即如此（`?job=` 直接重訂閱）。`GET
   /api/jobs/{id}` 快照端點仍可用於不想重播事件流的輕量狀態查詢（如輪詢 `status`）。

## 閘門狀態與 `content_degraded`

`gate_result` 的 `status` 有四種**已定案**的值,語意互不重疊:

| status | 意思 |
|--------|------|
| `pass` | 跑完了,通過 |
| `fail` | 跑完了,沒通過 |
| `skipped` | **沒有人要求**跑這一道(使用者關掉 QA、或這次選擇不用視覺模型) |
| `unknown` | **要求了但跑不成**(沒有 LibreOffice、視覺來源拒絕、回應讀不懂)——這份交付的該項品質實際上沒有被驗證過 |

`skipped` 與 `unknown` 不可混用。完整定義見 [`gates.md`](gates.md)。

`content_degraded`(新增事件):版面預算被迫刪掉內容時發出,
`data = {items: [{slide_no, title, items: [被移除的文字, ...]}]}`。
使用者要求過這些字,消失了就必須看得見——只寫進備忘稿等同於沒說。

## 安全性

- **CORS**：明確 Origin 白名單、且 `allow_credentials=False`；絕不用 `*`＋credentials。
  預設只放行本機開發來源，可用 `ODFORGE_CORS_ORIGINS` 覆寫。此 API 無認證、會花用
  使用者真實 LLM 金鑰，故白名單是擋下任意網站跨源驅動的關鍵（綁定 127.0.0.1 無法取代
  它，因請求來自使用者自己的瀏覽器）。萬用、opaque 或格式不完整的 origin 會 fail closed。
- **工作治理**：最多 4 個生成工作、2 個重生操作；互動核可 30 分鐘逾時。完成／失敗／
  取消的工作保留 **90 天**（`_JOB_RETENTION_SECONDS`），建立新工作時清除過期產物，
  記憶體最多保留 100 個工作。並行上限計算的是**仍在執行的 worker**,不是 UI 狀態:
  已取消但外部呼叫尚未結束的工作照樣佔用名額,否則反覆 start/cancel 可以無限量
  堆疊同時進行的供應商呼叫。
- **路徑白名單**：`job_id` 由伺服器以 `uuid4().hex` 產生，**絕不**把呼叫端輸入寫進檔案
  路徑；它只當記憶體 dict 的鍵使用，查無即 `404`。
- 每個 job 的產物落在伺服器自建的 `%TEMP%/odforge-jobs/{id}/` 之下（`preview/`、
  `deck.odp`）。預覽採版本目錄 `preview/v{n}`：每次重算圖寫進新版本後才切換指標,
  所以 5 頁縮成 3 頁時,`page-04.png` 不可能殘留在服務中的版本裡。
- preview 的頁碼 `n` 由 FastAPI 驗證為整數並檢查落在 1..頁數，再格式化成固定的
  伺服器檔名 `page-NN.png`；不存在或超界即 `404`。任何請求都無法指涉自身 job
  目錄以外的路徑。
