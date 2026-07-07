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
CORS 全開（本機工具）。所有回應與 SSE `data:` 一律為 UTF-8 JSON。

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
  "pages": [
    { "role": "title",         "title": "光合作用",       "gist": "全片主題與定位" },
    { "role": "title-content", "title": "反應總覽",       "gist": "光反應與暗反應兩階段" },
    { "role": "big-fact",      "title": "能量轉換效率",   "gist": "強調約 3–6% 的實際效率" }
  ]
}
```
- `design` 可為 `null`（模型色盤未通過對比檢查時後端會剝除，渲染改用預設主題）。
  存在時 `palette` 五色與 `fonts` 供前端即時渲染色卡／字體預覽。
- `mode`：`"presenter"`（講者型，大字少字）或 `"detailed"`（自讀型，完整文字）。
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
  "notes": "開場帶出兩階段。"
}
```
- `layout` ∈ `title | title-content | two-col | section | big-fact | quote |
  agenda | comparison | chart | closing`。
- `bullets` 元素可為字串，或巢狀 `{"text": "...", "children": ["...", "..."]}`。
- `chart` 存在時為 `{"labels": [...], "values": [...], "unit": "", "highlight": null}`。

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
  "note": ""
}
```

---

## API 契約表

| Method | Path | Body → 回應 |
|---|---|---|
| POST | `/api/generate` | `{prompt, mode?, theme?, interactive?: bool, qa?: bool, backend?}` → `{job_id}` |
| GET | `/api/jobs/{id}/events` | SSE 事件流（見「SSE 事件」） |
| POST | `/api/jobs/{id}/outline` | `{action: "approve"}` 或 `{action: "edit", outline: Outline}` → `{ok, status}` |
| POST | `/api/jobs/{id}/slides/{n}/regenerate` | `{instruction?: str}` → `{ok, n}`；重生該頁並在 SSE 推新 `slide_done` + `preview_ready` |
| GET | `/api/jobs/{id}/preview/{n}.png` | 第 n 頁 PNG（`image/png`） |
| GET | `/api/jobs/{id}/download` | 最終 `.odp`（`Content-Disposition: attachment`） |
| GET | `/api/jobs/{id}` | 狀態快照 `{status, slides_done, outline?, findings?, download_url?, error?}` |

### POST `/api/generate`
請求：
```json
{ "prompt": "介紹光合作用的兩階段", "mode": "presenter", "theme": null, "interactive": false, "qa": false }
```
回應 `200`：
```json
{ "job_id": "3f0a1c9e8b7d4a2f9c1e6b5d4a3c2b1a" }
```
`job_id` 為伺服器產生的 uuid4 hex（32 字）。生成隨即在背景 asyncio task 執行，
前端接著開 `GET /api/jobs/{id}/events` 訂閱進度。
`mode` / `theme` 若給定會覆蓋 LLM 的選擇（`theme` 會同時剝除 AI 自選 design）。

### POST `/api/jobs/{id}/outline`（僅 interactive）
生成在 `awaiting_approval` 停下等待核可時使用。
- 核可：`{"action": "approve"}`
- 修改後核可：`{"action": "edit", "outline": { …完整 Outline… }}`

回應 `200`：`{"ok": true, "status": "awaiting_approval"}`（收下後 runner 隨即續跑）。
狀態不在 `awaiting_approval` 時回 `409`；`edit` 缺 `outline` 或 Outline 不合法回 `422`。

### POST `/api/jobs/{id}/slides/{n}/regenerate`
只重生第 n 頁（以該頁 role/title/gist 組成單頁 sub-outline，把 `instruction`
併入 gist 後重跑 stage 2），其餘頁不動；接著重渲染並刷新該頁 preview。
請求：`{"instruction": "改用更大膽的視覺，字更少"}`（`instruction` 可省略）。
回應 `200`：`{"ok": true, "n": 2}`。n 超出範圍回 `404`；job 尚未生成完成回 `409`。

### GET `/api/jobs/{id}/preview/{n}.png`
回第 n 頁 PNG（`image/png`）。n 從 1 起算。超出頁數或該頁 PNG 尚未產生（如無
LibreOffice）回 `404`。

### GET `/api/jobs/{id}/download`
回最終 `.odp`，`Content-Type: application/vnd.oasis.opendocument.presentation`，
`Content-Disposition: attachment; filename="{id}.odp"`。檔案尚未算圖回 `404`。

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
`outline` → `slide_done`×N → `preview_ready`×N → `qa_round`×R → `complete`。
（interactive 時 `outline` 後先出 `awaiting_approval`，核可後才續。無 soffice 時
略過所有 `preview_ready`，其餘照舊。）

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

### `preview_ready` — 第 n 頁 PNG 就緒
```json
{ "n": 2, "url": "/api/jobs/3f0a1c9e8b7d4a2f9c1e6b5d4a3c2b1a/preview/2.png" }
```

### `qa_round` — QA 每輪結束
```json
{
  "round": 1,
  "findings": [
    { "slide_no": 3, "issue": "標題文字溢出版面右緣", "severity": "error", "fix_hint": "縮短標題或縮小字級" }
  ]
}
```

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
`stage` ∈ `outline | slides | render | preview | qa`。錯誤事件後串流結束，
快照的 `status` 為 `error`、並帶同樣的 `error` 物件。

---

## 前端建議流程

1. `POST /api/generate` 取 `job_id`。
2. 開 `EventSource("/api/jobs/{id}/events")` 訂閱。
3. 收 `outline` → 渲染色卡與頁面骨架。若 interactive，收到 `awaiting_approval`
   後讓使用者確認／編修，再 `POST /outline`。
4. 逐一收 `slide_done`（更新頁面內容）與 `preview_ready`（載入 `url` 的 PNG）。
5. 收 `qa_round` 顯示評審結果；收 `complete` 後以 `download_url` 提供下載。
6. 使用者想改某頁 → `POST /slides/{n}/regenerate`，等 SSE 推新的
   `slide_done` + `preview_ready`。
7. 重整／重連 → `GET /api/jobs/{id}` 取快照重建畫面，再重開 SSE 補收後續。

## 安全性（路徑白名單）

- `job_id` 由伺服器以 `uuid4().hex` 產生，**絕不**把呼叫端輸入寫進檔案路徑；它只當
  記憶體 dict 的鍵使用，查無即 `404`。
- 每個 job 的產物落在伺服器自建的 `%TEMP%/odforge-jobs/{id}/` 之下（`preview/`、
  `deck.odp`）。
- preview 的頁碼 `n` 由 FastAPI 驗證為整數並檢查落在 1..頁數，再格式化成固定的
  伺服器檔名 `page-NN.png`；不存在或超界即 `404`。任何請求都無法指涉自身 job
  目錄以外的路徑。
