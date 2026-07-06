# ODForge(文鍛)開發規格 v1

> 一句話:把自然語言鍛造成**原生** ODF 文件(.odt / .odp / .ods)的開發者工具。
> 競賽對應:學生組評分項 1「自行開發實用性 ODF 應用工具」(60%)。

## 1. 系統架構(四層)

```
使用者指令(自然語言)
        │
┌───────▼────────┐
│  LLM 層         │  LLMBackend 抽象介面(可插拔):
│  structured     │  預設 DeepSeek;同一類別吃所有 OpenAI 相容端點
│  output         │  (Ollama 本地/OpenAI/Groq...);Claude 為加分後端
│                │  → 輸出「文件中介表示 Document IR」(JSON)
│                │  LLM 絕不直接寫 XML,只產出結構化內容
└───────┬────────┘
        │ Document IR (JSON Schema 驗證)
┌───────▼────────┐
│  渲染引擎       │  IR → 原生 ODF XML
│  (odfdo +      │  .odt:樣式階層、目錄、表格、圖片
│   自寫 XML)    │  .odp:自寫 master page / 版面 / 主題系統
│                │  .ods:資料表 + 公式 + 統計圖表
└───────┬────────┘
        │ 原生 .odt / .odp / .ods
┌───────▼────────┐
│  介面層         │  ① CLI:odforge new/check
│                │  ② MCP server:任何 AI 助手可呼叫
└────────────────┘
```

**關鍵設計決策:LLM 只生成 IR,不生成 XML。**
- 原因:LLM 直接寫 ODF XML 錯誤率高(命名空間、schema 驗證、樣式引用),而 IR 是我們定義的乾淨 JSON Schema,可用 structured output 強制合規,渲染的正確性由我們的程式碼保證。
- 這也是簡報上的技術亮點:「內容由 AI 生成,格式正確性由引擎保證」。

## 2. Document IR(中介表示)

JSON Schema,三種文件型別:

```jsonc
// TextDoc (.odt)
{ "type": "text", "title": "...", "lang": "zh-TW",
  "blocks": [
    {"kind": "heading", "level": 1, "text": "..."},
    {"kind": "paragraph", "text": "...", "style": "body|quote|note"},
    {"kind": "list", "ordered": false, "items": ["..."]},
    {"kind": "table", "header": ["..."], "rows": [["..."]]},
    {"kind": "toc"}, {"kind": "pagebreak"}
  ] }

// Presentation (.odp)
{ "type": "presentation", "title": "...", "theme": "academic|minimal|dark",
  "slides": [
    {"layout": "title|title-content|two-col|section|big-fact",
     "title": "...", "bullets": ["..."], "notes": "講者備忘稿",
     "table": {...}, "chart": {...}}   // 可選
  ] }

// Spreadsheet (.ods)
{ "type": "spreadsheet", "title": "...",
  "sheets": [
    {"name": "...", "columns": ["..."], "rows": [[...]],
     "formulas": [{"cell": "D2", "formula": "of:=SUM([.B2:.C2])"}],
     "chart": {"kind": "bar|line|pie", "range": "A1:C5", "title": "..."}}
  ] }
```

## 3. 渲染引擎細節

### .odt(odfdo 主力,風險低)
- 樣式系統:預先定義好的具名樣式(標題階層、內文、引用、表格樣式),中文字型指定(Noto Sans TC / 標楷體 fallback)。
- 支援:目錄(TOC)、頁首頁尾、頁碼、表格、清單。

### .odp(最大技術挑戰 = 最大亮點)
- odfdo 的簡報支援陽春(官方自認),master page / layout 直接手寫 `styles.xml` 與 `content.xml` 的 XML。
- **主題系統**:內建 3 個主題(academic 學術白、minimal 極簡灰、dark 深色),每個主題 = 一組 master page + 配色 + 字型定義。
- 版面 5 種:標題頁、標題+內容、雙欄、章節隔頁、大字重點頁。
- 講者備忘稿(presenter notes)寫入 — 競品都沒做,評審是老師,會有感。

### .ods
- 資料 + ODF 公式(`of:` namespace)+ 原生圖表(`<office:chart>` 嵌入物件)。
- 圖表若太深,退階方案:先渲染表格 + 基本長條/圓餅圖。

### 驗證關卡(每個生成檔案自動過)
1. odfdo 重新解析無錯誤;
2. zip 結構 + mimetype 正確;
3. headless LibreOffice `--convert-to pdf` 能轉檔成功(= LO 開得起來的鐵證)。

## 4. 介面層

### CLI
```
odforge new "幫我做一份資料結構第三章(樹與二元樹)的15頁教學簡報" -o tree.odp --theme academic
odforge new "產生一份實驗報告模板,含目的、方法、結果、討論" -o report.odt
odforge new "做一份全班成績統計表,含平均、標準差和長條圖" -o grades.ods
odforge check some.odt        # 驗證 ODF 結構 + 相容性報告
```
- `--backend deepseek|ollama|claude`:後端切換(預設 deepseek;ollama = 本地隱私訴求,呼應全部競品的共同賣點;claude 為加分後端)。API key 一律走環境變數(`DEEPSEEK_API_KEY`),絕不落入 repo。
- `--outline-only`:先出大綱給使用者確認再生成(人在迴圈)。

### MCP Server
- tools:`forge_text_document`、`forge_presentation`、`forge_spreadsheet`、`inspect_odf`。
- Demo 劇本:在 Claude Desktop 掛上 ODForge MCP → 對話說「幫我做簡報」→ 桌面直接出現 .odp → LibreOffice 打開完美顯示。**這是決賽 demo 的殺手鏡頭。**

## 5. `odforge check`(餵給 30% 優化建議的子功能)

- ODF 結構驗證(schema、mimetype、樣式引用完整性)。
- docx → odt 轉檔比對:headless LO 轉檔前後結構 diff(標題階層/樣式/圖表數量),輸出 Markdown 報告。
- 報告輸出 = 附件 4「ODF 優化建議」的實例與截圖來源。

## 6. 技術棧

| 元件 | 選擇 | 理由 |
|------|------|------|
| 語言 | Python 3.12 | odfdo 生態 |
| ODF 庫 | odfdo(+ 自寫 XML 補 .odp) | 活躍維護,見研究報告 |
| LLM | LLMBackend 抽象介面:DeepSeek(預設,openai SDK 相容端點)/ Ollama 本地 / Claude(加分) | 可插拔換源 + 隱私雙軌 |
| Schema | pydantic v2 | IR 驗證 + JSON Schema 匯出 |
| CLI | typer + rich | 快、漂亮的終端輸出 |
| MCP | mcp python SDK(FastMCP) | 標準作法 |
| 測試 | pytest + LO headless smoke test | 驗證關卡自動化 |

## 7. 範圍切割(48 小時)

### MVP(7/6 晚上 — 必須完成)
- [ ] IR schema(三型別)+ pydantic 模型
- [ ] .odt 渲染器(樣式、表格、清單、TOC)
- [ ] .odp 渲染器(academic 主題 × 5 版面 + notes)
- [ ] LLM 層(Claude structured output)+ CLI `odforge new`
- [ ] 驗證關卡(parse + LO convert-to pdf)

### V1(7/7 上午)
- [ ] .ods 渲染器(公式 + 基本圖表)
- [ ] Ollama 後端
- [ ] MCP server
- [ ] `odforge check` 基本版(結構驗證 + docx↔odt diff)

### 加分(7/7 下午,有餘裕才做)
- [ ] minimal / dark 主題
- [ ] `--outline-only` 人在迴圈
- [ ] 圖片插入(佔位圖/本地圖檔)

### 明確不做(講清楚,簡報反而加分)
- 網頁 UI(CLI + MCP 已足)、即時協作、.odg/.odb、修改既有文件(只生成新檔)。

## 8. 佐證資料產出計畫(dogfooding:全部用 ODForge 自己生)

| 佐證 | 檔案 | 生成方式 |
|------|------|----------|
| 工具說明文件 | ODForge使用手冊.odt | ODForge 自己生成 |
| 決賽簡報 | ODForge決賽簡報.odp | ODForge 自己生成(demo 即證據) |
| 測試數據表 | 生成測試結果.ods | ODForge 自己生成 |
| 操作影片 | 螢幕錄影:CLI 生成 3 種檔案 + Claude Desktop MCP demo + LibreOffice 開啟 | OBS 錄製 |
| 原始碼 | GitHub repo(MIT license) | — |
| 附件 4 優化建議 | odforge check 報告 + 開發踩坑(odfdo .odp 陽春、odfpy 停滯、Presenton 無 ODP) | 深度研究報告 §4 |

## 9. 決賽簡報 7 分鐘節奏(預排)

1. 痛點 30 秒:AI 時代所有工具都出 PPTX,沒人出 ODF(Presenton issue 截圖)。
2. Demo 3 分鐘:一句話 → CLI 生成 .odp → LO 打開;Claude Desktop MCP 生成 .odt。
3. 架構 90 秒:IR 設計 + 「內容 AI 生成、格式引擎保證」。
4. 優化建議 60 秒:check 報告 + 生態觀察。
5. 推廣藍圖 60 秒:開源、MCP 生態、校園工作坊。
```
