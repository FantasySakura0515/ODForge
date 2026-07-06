# ODForge（文鍛）

> 把一句話，鍛造成一份正式文件。
>
> *Natural language → native ODF (`.odt` / `.odp` / `.ods`).*

完整專案介紹請見 repo 根目錄:[繁體中文](../README.md) | [English](../README.en.md)。本檔為套件層技術說明。

ODForge 讓你用一句自然語言的需求描述，直接產出一份**原生 ODF 文件**：
文書（`.odt`）、簡報（`.odp`）與試算表（`.ods`）。輸出的是真正的
OpenDocument 檔案，可用 LibreOffice、Microsoft Office、Google Docs 等直接開啟編輯。

## 狀態

可運作的 MVP。核心流程（IR 模型、三種 ODF 渲染器、CLI、MCP server、三道驗證閘）
皆已完成並具測試覆蓋。

## 核心理念

ODForge 把「內容」與「格式」拆成兩個互不干擾的階段：

- **內容由 AI 生成**：LLM 只負責產生一份結構化的 **Document IR**（中介表示，
  以 pydantic 嚴格驗證的 JSON）。它決定章節、條列、表格、公式等語意內容。
- **格式正確性由引擎保證**：一個確定性（deterministic）的渲染引擎讀取 IR，
  親手寫出符合 OpenDocument 規範的 XML。格式不經過 LLM，因此不會有「幻覺樣式」
  或壞掉的檔案結構。

每一份產出都會通過**三道驗證閘**：

1. **zip 結構**：`mimetype` 為首且未壓縮、副檔名相符、`manifest.xml` 所列部件齊全。
2. **XML 正確性**：每個 `.xml` 部件皆為 well-formed。
3. **LibreOffice 轉檔**：以 headless 的 `soffice` 實際轉檔，確認檔案能被真實應用程式開啟。

## 安裝

需求：Python 3.10+，以及 [LibreOffice](https://www.libreoffice.org/)
（第三道驗證閘與 `.docx` 結構比對會用到 `soffice`；沒有安裝時其餘功能仍可運作）。

```powershell
git clone <path-to-repo>
cd ODF/odforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

（開發時安裝測試相依：`pip install -e .[dev]`。）

## CLI 使用方式

### 產生文件

```powershell
# 簡報，指定主題與後端
odforge new "幫我做一份介紹光合作用的教學簡報" -o photosynthesis.odp --theme academic --backend deepseek

# 文書
odforge new "寫一份專案結案報告的大綱" -o report.odt

# 試算表
odforge new "建立一張三項商品的銷售統計表，含小計公式" -o sales.ods
```

- `--theme academic | minimal | dark`：簡報主題（僅對 `.odp` 有效）。
- `--backend deepseek | ollama`：選擇 LLM 後端。

使用 **DeepSeek** 後端前，先設定 API 金鑰（PowerShell）：

```powershell
$env:DEEPSEEK_API_KEY = "你的金鑰"
```

改用 **ollama** 後端則完全在本機執行、離線可用，不需要任何 API 金鑰
（請先在本機安裝並啟動 [Ollama](https://ollama.com/)）。

### 檢測文件

```powershell
# 對 .odt/.odp/.ods 跑三道驗證閘並輸出 Markdown 報告
odforge check report.odt

# 對 .docx 做「轉成 .odt 後的結構比對」
odforge check some.docx -o diff.md
```

## MCP server

ODForge 也以 MCP server 形式，把它的**確定性渲染引擎**開放給 AI 助理使用。
此時助理自己撰寫 Document IR 並呼叫工具（`forge_text_document`、
`forge_presentation`、`forge_spreadsheet`、`inspect_odf`），ODForge 純粹負責
渲染與驗證——不送出任何提示、也不需要 API 金鑰。

在 MCP 客戶端設定中註冊（把 `<path-to-repo>` 換成你 clone 的實際路徑）：

```json
{
  "mcpServers": {
    "odforge": {
      "command": "<path-to-repo>/odforge/.venv/Scripts/python.exe",
      "args": ["-m", "odforge.mcp_server"]
    }
  }
}
```

**信任模型**：此 server 會把檔案寫到 MCP 客戶端所要求的任意路徑，因此請只搭配
你信任的助理使用（它是一個本機 stdio 工具）。

## 示範

`demo/` 內附幾份由 ODForge 產生的範例輸出。

**提醒**：`.odt` 內的目錄（TOC）在剛產生時會顯示為空白，需在 LibreOffice 中
以右鍵 →「更新索引／目錄」（Update Index）重新整理後才會列出項目。

> 截圖待補。

## 開發

```powershell
pytest -q
```

## 授權

MIT。詳見 [LICENSE](LICENSE)。
