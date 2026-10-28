<picture>
  <source media="(prefers-color-scheme: dark)" srcset="odforge/brand/logo-dark.svg">
  <img src="odforge/brand/logo.svg" alt="ODForge" width="96">
</picture>

# ODForge（文鍛）

讓 AI 助理做出能直接編輯的簡報，然後在瀏覽器裡跟它一起改。

[English](README.en.md) | 繁體中文

![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![pypi](https://img.shields.io/pypi/v/odforge)

![ODForge 做出來的簡報](odforge/demo/deck.png)

你跟 Claude Code、Codex 這類 AI 助理說「幫我做一份資料結構第三章的教學簡報」，它用 ODForge
產出原生的 `.odp`：每個字都是可以改的文字框，不是一張截圖。做完後瀏覽器會自動打開編輯器，
你可以直接拖、改字，看到不滿意的地方就留言，助理會照著改，你在網頁上即時看到結果。

也能做文書 `.odt` 和試算表 `.ods`。

## 安裝

先裝 [uv](https://docs.astral.sh/uv/)。建議也裝 [LibreOffice](https://www.libreoffice.org/)，
ODForge 在背景用它檢查排版和產生預覽圖，你不需要打開它。

**用 Claude Code**：在對話框輸入

```text
/plugin marketplace add FantasySakura0515/ODForge
/plugin install odforge@odforge
```

**用 Codex、Cursor 或其他助理**：在終端機執行

```bash
uv tool install "odforge[web,cjk]"
odforge setup             # 接上這台電腦的 Claude Code 與 Codex
```

只想讓某個資料夾裡的助理會用，或要把設定連同專案交給別人，改用
`odforge init 資料夾名稱`。它會在資料夾裡放 `AGENTS.md`、`CLAUDE.md`、skill 和 `.mcp.json`，
助理一打開那個資料夾就知道怎麼用 ODForge。原本就有 `AGENTS.md` 的話，只會在後面加一段。

## 怎麼用

開一個新對話，說你要什麼簡報就好，例如：

> 幫我做一份給大一新生的資料結構簡報，講樹與二元樹，大約 12 頁，要 .odp

接下來：

1. 助理會先問幾個問題（聽眾、場合、頁數），排好大綱再產出簡報。
2. 簡報做好後，瀏覽器自動打開編輯器。
3. 你可以直接動手改；想讓助理改的地方，按「留言」（快捷鍵 C）點在那個物件上寫下來，
   寫完按「交給 AI 修改」。
4. 助理收到留言就改，改好的留言會自動刪掉。可以一批接一批來回。
5. 滿意了按「完成」。

留言存在 `.odp` 檔案裡，傳給別人也還在。

## 網頁編輯器

![網頁編輯器：留言點在物件上，按「交給 AI 修改」](odforge/demo/editor.png)

任何 `.odp` 都能開，不限 ODForge 做的。自己開的話：

```bash
odforge edit 簡報.odp
```

- 拖曳移動，靠近其他物件或頁面中線會吸附，並顯示和鄰居的距離；按住 ⌥ 暫時不吸附。
- 拉八個把手縮放，按住 Shift 等比例；方向鍵微調 0.5 mm，加 Shift 5 mm。
- 雙擊改字，原本的字型、字級、清單符號都保留。
- 框選多個物件、群組、複製（⌘D）、刪除、復原與重做（⌘Z／⇧⌘Z）。
- 左側頁面列可以拖曳排序、複製、刪除、新增頁面。

每一步都直接存回檔案，編輯器畫不出來的東西（備忘稿、特殊物件）原封不動。
助理同時在改同一份檔案時，畫面一秒內就會更新。

## 其他用法

### 不透過助理，直接用指令產生

ODForge 也能自己呼叫語言模型，副檔名決定文件類型：

```bash
odforge new "介紹光合作用的教學簡報，12 頁" -o photo.odp
odforge new "實驗報告模板：目的、方法、結果、討論" -o report.odt
odforge new "全班成績表，學期成績 = 期中 30% + 期末 40% + 平時 30%" -o grades.ods
```

模型用 `--backend` 或環境變數 `ODFORGE_BACKEND` 指定：

| 後端 | 需要什麼 |
| --- | --- |
| `codex`（預設） | 本機裝好 [Codex CLI](https://github.com/openai/codex) 並 `codex login`，用自己的 ChatGPT 帳號，不用 API 金鑰 |
| `ollama` | 本機跑 [Ollama](https://ollama.com/)，完全離線 |
| `custom` | 任何 OpenAI API 格式的服務（DeepSeek、OpenAI、OpenRouter、vLLM…），設定 `ODFORGE_CUSTOM_BASE_URL`、`ODFORGE_CUSTOM_MODEL`、`ODFORGE_CUSTOM_API_KEY` |

簡報常用選項：

| 選項 | 說明 |
| --- | --- |
| `--theme` | 配色：`academic` `minimal` `dark` `teal` `forest` `navy` `violet` `crimson` `slate` `gold` `sky` `plum` `clay` |
| `--style` | 版式：`classic` `report` `academic` `keynote` `editorial` `passport` `zen` |
| `--mode` | `presenter`（字少，給講者用）或 `detailed`（字多，給人自己讀） |
| `--from-template 檔案` | 沿用學校或公司公版（`.otp`／`.odp`）的配色與字型 |
| `--image 檔案`、`--logo 檔案`、`--byline` | 放入圖片、校徽、封面署名 |
| `--interactive` | 大綱產完先給你看，確認後才繼續 |

其他設定（生圖、視覺審稿、遠端圖片白名單）見 [`odforge/.env.example`](odforge/.env.example)。

### 檢查既有的檔案

```bash
odforge check 簡報.odp             # 格式驗證與跑版檢查，輸出 Markdown 報告
odforge check 文件.docx -o diff.md  # 轉成 .odt，比對前後結構並檢查跑版
```

跑版檢查會實際把檔案排版一次，找出超出文字框、超出頁面或被裁掉的文字，並標出是哪一頁的哪段字。

### 即時預覽

讓助理寫一份簡報的 JSON（`deck.json`），每次存檔，瀏覽器就重新排版並標出跑版的頁面：

```bash
odforge preview deck.json
```

適合從頭反覆調整整份結構的時候。結構定案後再改用上面的編輯器流程。

### 網頁控制台

```bash
odforge serve        # http://127.0.0.1:8000
```

在網頁上輸入需求（可附 PDF 或圖片），模型會先追問幾題、讓你確認大綱，再逐頁產出。
只綁本機位址；要對外開放得加 `--allow-remote`。

## 接到其他 MCP 客戶端

ODForge 是一個 MCP server，助理自己寫內容，ODForge 負責排版、產檔和檢查，所以不需要 API 金鑰。
上面的安裝方式已經幫你設定好；其他客戶端可以手動加入：

```json
{
  "mcpServers": {
    "odforge": {
      "command": "uvx",
      "args": ["--from", "odforge[web,cjk]", "odforge-mcp"]
    }
  }
}
```

| 工具 | 用途 |
| --- | --- |
| `forge_presentation`／`forge_text_document`／`forge_spreadsheet` | 產出 `.odp`／`.odt`／`.ods` |
| `inspect_odf` | 驗證既有檔案並檢查跑版 |
| `preview_odf` | 每頁轉成圖片，讓助理看圖自查 |
| `outline_odp` | 列出既有 `.odp` 每頁的物件、位置與文字 |
| `edit_odp` | 修改既有 `.odp`：改字、搬位置、增刪頁、處理留言 |
| `open_editor` | 在瀏覽器打開網頁編輯器 |
| `wait_for_feedback` | 等使用者在網頁上按「交給 AI 修改」，取回那批留言 |

這個 server 會把檔案寫到助理指定的路徑，請只搭配你信任的助理使用。

## 運作方式

模型從頭到尾不碰檔案格式。它只寫一份結構化的內容（Document IR），通過驗證後才交給渲染引擎寫出 ODF，
寫完再經過幾道檢查：

| 檢查 | 內容 | 沒過時 |
| --- | --- | --- |
| 結構 | zip 結構、`mimetype`、manifest | 不交付 |
| XML | 每個部件格式正確 | 不交付 |
| 轉檔 | 實際轉成 PDF；試算表逐格檢查公式錯誤 | 回報，照常交付 |
| 版面 | 視覺模型逐頁看圖、修補後重算（選用） | 回報，照常交付 |

沒跑的檢查會照實標成「未啟用」或「無法判定」，不會顯示成通過。細節見
[`odforge/docs/gates.md`](odforge/docs/gates.md)。

這些檢查只保證檔案正確、打得開，**不保證內容正確**。數字、日期、引言和出處都可能是模型編的，請自行查證。

## 已知限制

- `.odt` 的目錄剛產生時是空的，要在文書軟體裡更新索引才會填入。
- `.ods` 還沒有原生圖表；圖片目前只能放進簡報。
- `--from-template` 只沿用配色和字型，不沿用公版的版面配置。

## 開發

```bash
git clone https://github.com/FantasySakura0515/ODForge.git
cd ODForge/odforge
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,web,cjk]"

pytest -q && ruff check .
npm --prefix web ci && npm --prefix web test
npm --prefix web run build      # odforge edit／serve 需要建置好的前端
```

`git push` 一個 `v*` tag 會在所有測試通過後自動發佈到 PyPI。

## 授權

MIT，見 [odforge/LICENSE](odforge/LICENSE)。
