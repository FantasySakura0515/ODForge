# ODForge（文鍛）

> **把一句話,鍛造成一份正式文件。**
>
> 自然語言 → 原生 ODF(`.odt` / `.odp` / `.ods`)文件生成引擎

[English](README.en.md) | 繁體中文

![tests](https://img.shields.io/badge/tests-113%20passed-brightgreen) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![format](https://img.shields.io/badge/output-native%20ODF-orange)

```powershell
odforge new "幫我做一份資料結構第三章:樹與二元樹的教學簡報" -o tree.odp
```

![ODForge 生成的簡報](odforge/demo/tree.png)

*↑ 這張投影片(以及本專案的使用手冊、決賽簡報、測試報表)都是 ODForge 自己生成的。*

---

## 為什麼需要 ODForge?

AI 時代的文件工具幾乎全部輸出 PPTX / DOCX——主流開源 AI 簡報工具連 ODP 匯出都沒有。ODF(OpenDocument Format)是 ISO/IEC 26300 國際標準、我國國家標準 CNS15251、政府公文標準格式,卻在這波 AI 浪潮中缺席。

現有的 LibreOffice AI 擴充(localwriter、LibreThinker 等)都停留在「選取文字改寫」的層級,沒有任何工具能**從一句自然語言生成一份完整、結構化的原生 ODF 文件**。ODForge 補上這塊空缺。

## 核心理念:內容由 AI 生成,格式正確性由引擎保證

直接叫 AI 吐檔案,是每次擲一次骰子;ODForge 把骰子換成了機器。

```
使用者一句話
     │
┌────▼─────────┐   LLM 只產出「Document IR」——
│  LLM 層       │   以 pydantic 嚴格驗證的結構化 JSON。
│ (可插拔後端)  │   LLM 從頭到尾不碰 XML。
└────┬─────────┘   (內建 json-repair:模型吐出壞 JSON 也能修復或擋下)
     │ Document IR
┌────▼─────────┐   確定性渲染引擎親手寫出符合
│  渲染引擎     │   OpenDocument 規範的 XML:
│              │   .odt / .ods → odfdo;.odp → 手寫 XML
└────┬─────────┘   (主題系統、五種版面、講者備忘稿、完整 CJK 支援)
     │ 原生 ODF 檔案
┌────▼─────────┐   1. zip 結構(mimetype 為首且未壓縮、manifest 齊全)
│  三道驗證閘   │   2. XML 正確性(每個部件 well-formed)
│              │   3. LibreOffice 實際转檔(headless 轉 PDF 成功)
└────┬─────────┘
     ▼
  交付檔案(CLI)或回報結果(MCP)
```

每一份產出都必須通過三道驗證閘才會交到你手上——**你拿到的檔案保證開得起來**。

## 快速開始

需求:Python 3.10+、[LibreOffice](https://www.libreoffice.org/)(第三道驗證閘使用;未安裝時其餘功能仍可運作)。

```powershell
git clone https://github.com/FantasySakura0515/ODForge.git
cd ODForge/odforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

設定 LLM 後端(擇一):

```powershell
# 方案一:DeepSeek(雲端)
$env:DEEPSEEK_API_KEY = "你的金鑰"

# 方案二:Ollama(本機、完全離線、資料不出門)—— 不需任何金鑰
# 安裝並啟動 https://ollama.com/ 後加 --backend ollama 即可
```

生成文件:

```powershell
# 簡報(三種主題:academic / minimal / dark)
odforge new "介紹光合作用的教學簡報,12張" -o photo.odp --theme academic

# 文書(含目錄、標題階層、表格)
odforge new "一份實驗報告模板:目的、方法、結果、討論" -o report.odt

# 試算表(含公式,LibreOffice 開啟即自動計算)
odforge new "全班成績表,學期成績=期中30%+期末40%+平時30%" -o grades.ods
```

檢測既有檔案:

```powershell
odforge check some.odt              # 三道驗證閘 + 樣式引用完整性,輸出 Markdown 報告
odforge check some.docx -o diff.md  # docx → odt 轉檔前後結構比對(跑版偵測)
```

## MCP:讓任何 AI 助理直接產出 ODF

ODForge 同時是一個 **MCP server**,把確定性渲染引擎開放給 AI 助理(Claude Desktop、任何支援 MCP 的客戶端)。這個模式下助理自己撰寫 Document IR,ODForge 純粹負責渲染與驗證——**不需要任何 API 金鑰**。

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

掛上之後,對助理說「幫我做一份簡報存到桌面」,桌面就會出現一份通過驗證的 `.odp`。

> **信任模型**:server 會把檔案寫到 MCP 客戶端要求的任意路徑,請只搭配你信任的助理使用(本機 stdio 工具)。

## 開發過程中實測發現的 ODF 生態問題

這些是開發 ODForge 時親手踩到、可供社群參考的一手觀察:

| 發現 | 說明 |
|------|------|
| 中文字級陷阱 | ODF 的 CJK 文字要另外設定 `style:font-size-asian` / `style:font-weight-asian`,只設 `fo:font-size` 中文會掉回預設字級——多數工具沒處理 |
| odfpy 停更 | Python 生態最知名的 ODF 庫自 2020 年起無新版本,僅支援 ODF 1.2 |
| odfdo 簡報弱項 | 活躍維護,但 `.odp` 的 master page / 版面支援陽春(官方自認),因此本專案的簡報渲染器全部手寫 XML |
| AI 工具無 ODP 匯出 | 主流開源 text-to-slides 工具(如 Presenton)僅出 PPTX/PDF,ODF feature request 懸置中 |
| LLM 結構化輸出不可信 | DeepSeek 的 function calling 會輸出未加引號的字串值(CJK 括號開頭誘發),必須有修復層與 schema 驗證兜底 |

## 專案結構

```
odforge/src/odforge/
├── ir.py           # Document IR:pydantic 模型(全系統的契約)
├── llm.py          # 可插拔 LLM 後端(DeepSeek / Ollama / 任何 OpenAI 相容端點)
├── render/
│   ├── odt.py      # 文書渲染(odfdo)
│   ├── odp.py      # 簡報渲染(手寫 XML,資料驅動版面)
│   └── ods.py      # 試算表渲染(數值儲存格 + 原生公式)
├── themes.py       # 主題與版面幾何(純資料)
├── package.py      # ODF zip 打包(mimetype 規則 + manifest)
├── validate.py     # 三道驗證閘
├── check.py        # 檢測報告 + docx↔odt 結構比對
├── cli.py          # odforge new / check
└── mcp_server.py   # MCP 工具(收 IR、不收 prompt、不需金鑰)
```

## 開發

```powershell
cd odforge
.\.venv\Scripts\python.exe -m pytest -q    # 113 passed
```

全程 TDD:每個模組先寫失敗測試再實作;渲染器測試直接用 zipfile + lxml 驗證 XML,與實作庫解耦。

## 已知事項

- `.odt` 內的目錄(TOC)剛產生時顯示空白,在 LibreOffice 中右鍵 →「更新索引/目錄」即會填入。
- `.ods` 圖表、即時協作編輯不在目前範圍(見 Roadmap)。

## Roadmap

- [ ] `.ods` 原生圖表(ODF chart 子文件)
- [ ] 更多簡報主題與版面
- [ ] `--outline-only` 大綱確認模式(人在迴圈)
- [ ] 圖片插入

## 授權

MIT,詳見 [odforge/LICENSE](odforge/LICENSE)。
