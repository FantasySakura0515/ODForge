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

每一份產出都會通過**四道品質閘**（前兩道是阻斷閘，後兩道如實回報結果，
定義見 [`docs/gates.md`](docs/gates.md)）：

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

只用 CLI／MCP 時上面就夠了。要跑 **Web 介面**（`odforge serve`）需要 web 相依：

```powershell
pip install -e ".[web]"
```

（開發時安裝測試相依：`pip install -e ".[dev]"`；三者可合併為 `pip install -e ".[dev,web]"`。）

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

- `--theme`：內建主題（僅對 `.odp` 有效），十三選一：`academic`、`minimal`、`dark`、
  `teal`、`forest`、`navy`、`violet`、`crimson`、`slate`、`gold`、`sky`、`plum`、`clay`。
  省略則尊重 LLM 自己定的美術方向。
- `--style classic | editorial | stage | corporate | zen`：版式（僅對 `.odp` 有效）；
  省略則使用該主題配對的預設版式。
- `--language zh-TW | en | bilingual`：輸出語言，預設繁體中文。
- `--byline "單位 · 講者 · 日期"`：封面署名（僅對 `.odp` 有效），原樣印上，不經 AI 改寫。
- `--logo PATH` / `--logo-placement cover | cover-closing | all`：封面校徽與它出現的頁面，
  預設封面與結尾頁。
- `--from-template PATH`：吃現有 `.otp`/`.odp` 公版，抽出樣式並鎖定設計，LLM 只寫內容。
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

需求框下方是「參考文件」與「客製化」兩塊常駐區塊，不收在任何抽屜裡。參考文件最多
6 個（PDF、PNG 或 JPEG，每個 8 MiB、合計 16 MiB）：PDF 會先抽取文字，供需求訪談與
大綱／逐頁內容使用；PNG/JPEG 則保留為可插入簡報的圖片素材。無文字層的掃描 PDF 會
要求先執行 OCR。

客製化欄位全部可留白（留白＝交給 AI 判斷）：內容密度（上台報告／閱讀文件）、頁數、
簡報場合、講述時間、聽眾對象、語氣風格、輸出語言與視覺主題。前四項與聽眾、語氣會附加
在需求尾端，訪談與大綱兩段都讀得到；視覺主題若指定，整份會鎖定該範本的配色，不再由
模型自訂 DesignSpec。

輸出語言有繁體中文（預設）、English 與中英對照三種。訪談問題一律以繁體中文提問——
要換的是簡報的語言，不是跟你對話的語言。中英對照會讓每一行大約加長一倍，模型被要求
主動縮短句子，超載的頁面仍由版面預算閘處理。

「封面署名與校徽」是使用者自己的內容，不經過 AI 改寫：署名原樣置中印在封面標題下方，
校徽（PNG／JPEG，2 MiB 以內）預設出現在封面與結尾頁，也可改成只放封面或每頁角落。
深色的分節／結尾頁會替校徽鋪一塊底色板，避免深色標誌沉進深色背景。校徽不會被列入
模型可用的圖片素材——它是版面家具，不該被排進某一頁的內容裡。

設計品質檢查（第四道閘）沒有開關：只要伺服器設定了可用的視覺來源就一定執行；沒有
設定或探測失敗時，介面會直接說明這一輪只跑前三道格式驗證，不會給出跑不成的承諾。

首頁有一個「範本庫」入口，範本庫是自己的一頁（`?templates=1`）。一個範本 = **版式 ×
配色**：

- **版式**決定構圖：封面怎麼排、內頁標題用什麼記號、分節頁怎麼處理、頁尾留多少家具。
  內建五套——`classic` 學院派、`editorial` 編輯風、`stage` 舞台、`corporate` 企業報告、
  `zen` 極簡。這一層是「兩份範本真的是兩份文件」的來源；只換顏色不換版式，十三套就只是
  同一個版面的十三種配色。
- **配色**決定顏色與字體：13 套內建（九套淺底、四套暗底），每一套都與一個版式配對好。

新增自訂範本有兩條路——選版式後手動調色（五色＋字體配對＋字級密度，對比不足會當場指出
是哪一組、差多少，過不了不給存），或直接匯入學校／公司的公版 `.otp`／`.odp`／`.ott`／
`.odt`，系統會抽出它的配色與字體。自訂範本儲存在 sessions 目錄下的 `templates.json`，
換瀏覽器或清快取都還在。

首頁會列出最近的生成 session，包含狀態、頁數、首張預覽與下載入口。session metadata
與產物預設保存在使用者的 local application-data 目錄；可用
`ODFORGE_SESSIONS_DIR` 指定其他位置。預設最多保留 100 份、90 天，超過時從最舊的
已結束工作開始清理。

```powershell
# 視窗一：API（production build 存在時也會一併提供前端）
.\.venv\Scripts\odforge.exe serve

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

## MCP server：把品質保證接給任何 AI 工具

ODForge 也是一個 MCP server。**任何支援 MCP 的 AI 工具都能直接用它產出並驗證
ODF，不需要修改任何一行程式碼。**

### 一分鐘接上

```powershell
pip install odforge
```

然後在 MCP 客戶端設定裡加一段（Claude Code、Claude Desktop、Cursor、Cline
都吃同一份格式）：

```json
{
  "mcpServers": {
    "odforge": {
      "command": "odforge-mcp"
    }
  }
}
```

沒有金鑰要設、沒有路徑要填。`pip install` 之後 `odforge-mcp` 就在 PATH 上。

> 從原始碼跑（未安裝）時用：
> `"command": "<repo>/odforge/.venv/Scripts/python.exe", "args": ["-m", "odforge.mcp_server"]`

### 分工

呼叫端的 AI 負責**內容**（自己寫 Document IR），ODForge 負責**格式與保證**：

| 工具 | 做什麼 |
|------|--------|
| `forge_text_document` / `forge_presentation` / `forge_spreadsheet` | 渲染成原生 ODF，並跑三道格式閘（zip / XML / LibreOffice 實際開檔）＋版面預算檢查 |
| `inspect_odf` | 驗證**任何**既有 ODF 檔——不必是 ODForge 產的 |
| `preview_odf` | 逐頁轉成 PNG，讓 agent 自己看、自己修（第四道閘的手動版） |

`forge_*` 不會只回「好了」。文字若會溢出版面，回傳字串會附上一段
`warning:`，逐頁逐框指出哪裡放不下——**ODForge 量測，呼叫端重寫**：

```
ok: wrote C:\...\deck.odp — structure: OK …; xml: OK …; soffice: OK …

warning: 版面預算超載——檔案已產出且格式有效,但下列內容會溢出版面。
請縮短這些頁面的文字後重新 forge:
- 第 2 頁:投影片「太多重點」的「bullets」框內容超出版面:預估 15.2cm > 可用 10.8cm(frame 高 11cm)—— 請縮短文字或改用其他版型。
```

搭配 `skills/odforge-design/SKILL.md`（教外部 agent 怎麼設計一份好簡報）
一起掛上，效果最好。

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
