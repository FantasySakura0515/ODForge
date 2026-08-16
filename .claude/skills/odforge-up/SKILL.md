---
name: odforge-up
description: 啟動／停止 ODForge 本機 Web 控制台(FastAPI + React)供人工測試。處理 venv 選擇、埠位衝突、前端 dist 新鮮度、啟動後健康檢查與後端可用性回報。當使用者說「開啟系統」「啟動 ODForge」「跑起來讓我測」「開 web 介面」「關掉伺服器」時使用。
---

# odforge-up：把 ODForge 控制台開起來

一句話:跑 `scripts/start.ps1`,它會印出可以直接點的 URL。

```powershell
powershell -ExecutionPolicy Bypass -File .claude/skills/odforge-up/scripts/start.ps1
```

腳本做完所有雜事(選 venv、避開被佔用的埠、檢查 dist、等伺服器就緒、回報後端可
用性),並把 PID／埠位／log 路徑寫進 `%TEMP%\odforge-dev-serve.json`,供之後停止或
看 log。**用背景模式跑腳本沒有意義**——它自己已經把伺服器丟到背景,腳本本身幾秒
內就回來了。

## 常用旗標

| 指令 | 用途 |
| --- | --- |
| `start.ps1` | 啟動(prod 模式:API 直接掛載已 build 的前端,單一 URL) |
| `start.ps1 -Port 8005` | 指定埠位 |
| `start.ps1 -Rebuild` | 先 `npm run build` 再啟動(改過 `web/src/` 時用) |
| `start.ps1 -Status` | 只回報目前狀態,不啟動任何東西 |
| `start.ps1 -Stop` | 停掉本腳本啟動的伺服器 |
| `start.ps1 -Dev` | 額外開 vite dev server(`http://localhost:5173`,HMR) |

## 回報給使用者時

- 給**可點的 URL**(prod 模式是一個;`-Dev` 是 5173 給前端、API 那個備用)。
- 若腳本回報某個後端不可用(例如 `deepseek` 缺金鑰、`vision=off`),照實說明那會
  影響什麼:`vision` 為 `off` 時第四道設計閘照樣跑圖但沒有模型看圖,永遠回報零問題。
- 不要宣稱「已驗證可以生成」,除非真的跑過一次生成。要驗證後端連得上 LLM,打一次
  discovery 就夠(見下)。

## 已知地雷

**8000 埠常被別的專案佔住。** 這台機器上 `C:\Users\User\Desktop\project\FJU AITA`
的 ai-core 服務就綁 8000。腳本會自動往上找空埠,**不要去 kill 那個行程**——它不屬於
本專案。

**venv 有兩個,只有 `.venv312` 裝了 web 相依**(fastapi)。`.venv` 沒有,用它跑 `serve`
會直接失敗。腳本會挑對的那個,但手動下指令時要記得。

**API 金鑰在 `odforge/.env`,只有 `serve` 指令會載入它**(`cli.py` 裡延遲 import
python-dotenv)。所以直接跑 `python -m uvicorn` 繞過 CLI 的話金鑰不會進環境變數。

**`-Dev` 模式綁死 8000。** `web/vite.config.ts` 的 proxy 目標寫死 `localhost:8000`,
所以 dev 模式下 API 必須在 8000。8000 被佔住時腳本會說清楚,選項是:改用 prod 模式
(預設)、把佔用者關掉,或把 proxy 目標改掉。

## 手動驗證後端真的通(會花一次 API 呼叫)

curl 在 Windows 上直接用 `-d` 帶中文會被編碼弄壞(伺服器回
`There was an error parsing the body`)。把 JSON 寫成 UTF-8 檔案再送:

```powershell
'{"prompt": "介紹二元樹的教學簡報", "doc_type": "odp", "pages": 8}' |
  Set-Content -Path "$env:TEMP\d.json" -Encoding utf8
curl.exe -s -X POST http://127.0.0.1:8001/api/discovery/questions `
  -H "Content-Type: application/json" --data-binary "@$env:TEMP\d.json" --max-time 180
```

回傳含 `questions` 陣列就代表金鑰、端點、模型三者都通。

## 主要端點(除錯用)

| 端點 | 用途 |
| --- | --- |
| `GET /api/sources` | 各文字／視覺後端可用性與預設值(健康檢查就看這個) |
| `GET /api/sessions` | 歷史 session 列表 |
| `POST /api/discovery/questions` | 需求訪談(第一次真正呼叫 LLM 的地方) |
| `POST /api/generate` | 建立 job,之後用 `GET /api/jobs/{id}/events` 收 SSE |
| `GET /api/jobs/{id}/preview/{n}.png` | 單頁預覽圖 |
| `GET /api/jobs/{id}/download` | 下載產出檔 |

沒有 `/api/health`——用 `/api/sources` 當健康檢查。

## 伺服器 log

路徑在狀態檔裡,或直接看 `%TEMP%\odforge-serve-<port>.log`(與 `.err`)。
生成失敗、金鑰錯誤、502 的真正原因都在 `.err` 那份(uvicorn 的 traceback 走 stderr)。
