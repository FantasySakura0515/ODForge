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

需求：Python 3.11+，以及 [LibreOffice](https://www.libreoffice.org/)
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

# 簡報，提供兩張可用圖片素材
odforge new "整理醫療流程改善成果" -o result.odp --image before.jpg --image after.png

# 同一題比較兩個模型，保留 IR、ODP、預覽與報告
odforge benchmark "整理醫療流程改善成果" --backend deepseek --backend ollama --out-dir .\benchmark-results

# 文書
odforge new "寫一份專案結案報告的大綱" -o report.odt

# 試算表
odforge new "建立一張三項商品的銷售統計表，含小計公式" -o sales.ods
```

- `--theme academic | minimal | dark`：簡報主題（僅對 `.odp` 有效）。
- `--backend deepseek | ollama | custom`：選擇 LLM 後端。
- `--image PATH`：加入 PNG/JPEG 素材，可重複指定；模型只會看到伺服器產生的
  `asset://id` 與描述，不會取得本機路徑。
- `odforge benchmark` 的分數是可重現的結構代理指標；請搭配輸出的預覽與人工／
  vision QA 判斷真正的視覺品質。

使用 **DeepSeek** 後端前，先設定 API 金鑰（PowerShell）：

```powershell
$env:DEEPSEEK_API_KEY = "你的金鑰"
```

改用 **ollama** 後端則完全在本機執行、離線可用，不需要任何 API 金鑰
（請先在本機安裝並啟動 [Ollama](https://ollama.com/)）。

任何 OpenAI-compatible 服務可透過 `custom` 接入：

```powershell
$env:ODFORGE_CUSTOM_BASE_URL = "https://example.com/v1"
$env:ODFORGE_CUSTOM_API_KEY = "你的金鑰"
$env:ODFORGE_CUSTOM_MODEL = "model-name"
odforge new "做一份產品簡報" -o product.odp --backend custom
```

第四道閘（設計品質）另外用一個**視覺**模型；沒設定時是 `off`，此時它照樣會算圖，
但沒有任何模型看過那些圖，永遠回報零個問題：

```powershell
# 沿用上面的 custom 端點與金鑰，只換一個看得懂圖的模型
$env:ODFORGE_VISION_BACKEND = "custom"
$env:ODFORGE_CUSTOM_VISION_MODEL = "qwen3-vl-plus"
odforge new "做一份產品簡報" -o product.odp --qa
```

視覺模型必須支援**強制** `tool_choice`（DashScope 的 `qwen3-vl-plus` 可以，
`qwen-vl-max` 會回 400）。也可改用 `claude`（需 `ANTHROPIC_API_KEY`）或 `ollama`
（本機視覺模型）。視覺端點若與文字端點不同，再設 `ODFORGE_CUSTOM_VISION_BASE_URL`
與 `ODFORGE_CUSTOM_VISION_API_KEY`。

若快速讀題、規劃大綱與撰寫逐頁內容適合不同模型，可分別設定
`ODFORGE_*_DISCOVERY_MODEL`、`ODFORGE_*_OUTLINE_MODEL` 與
`ODFORGE_*_SLIDES_MODEL`；未設定 discovery 模型時會沿用 outline 模型。圖片生成採可插拔 HTTP
adapter（`ODFORGE_IMAGE_BACKEND=http`）；遠端圖片則必須把精確 HTTPS host 放進
`ODFORGE_REMOTE_IMAGE_HOSTS`，且內網／loopback 位址仍會被拒絕。
若 provider 需要額外的 OpenAI-compatible request 欄位，可用
`ODFORGE_CUSTOM_EXTRA_BODY` 傳 JSON；例如 Qwen 3.7 強制 function calling 時使用
`{"enable_thinking":false}`。

## Web 介面與 AI 需求訪談

Web 介面不會把需求直接送去生成。按下「繼續」後，discovery 模型會先提出 2–5 個
依題目動態產生的高價值問題；回答完成後，介面會整理成可編輯的 Brief，確認後才進入
大綱與逐頁生成。讀題期間會以 NDJSON 顯示實際工作階段與耗時，但不會暴露或捏造
模型的私密推理。

輸出設定可附加最多 6 個參考文件（PDF、PNG 或 JPEG，每個 8 MiB）。PDF 會先抽取
文字，供需求訪談與大綱／逐頁內容使用；PNG/JPEG 則保留為可插入簡報的圖片素材。
無文字層的掃描 PDF 會要求先執行 OCR。

首頁會列出最近的生成 session，包含狀態、頁數、首張預覽與下載入口。session metadata
與產物預設保存在使用者的 local application-data 目錄；可用
`ODFORGE_SESSIONS_DIR` 指定其他位置。預設最多保留 100 份、90 天，超過時從最舊的
已結束工作開始清理。

```powershell
# 視窗一：API（production build 存在時也會一併提供前端）
.\.venv312\Scripts\odforge.exe serve

# 視窗二：前端開發模式
cd web
npm.cmd run dev
```

開發模式瀏覽 `http://localhost:5173`；production build 可直接瀏覽
`http://127.0.0.1:8000`。需求訪談不會傳送圖片的 base64 bytes；PDF 則會在後端
驗證並抽取有長度上限的文字後，作為模型參考資料。

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
