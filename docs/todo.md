# ODForge 開發計畫（SDD / TDD）

> **給 agentic worker：** 本檔是**現況與待辦**，不是歷史紀錄。已完成的 Phase 只留一行結論；
> 用不到的計畫直接標為「不做」並寫明理由。要追溯細節請看 git log 與 `odforge/docs/`。

## 現況（2026-08-17，版式層／範本庫之後）

| 項目 | 狀態 |
| ------ | ------ |
| Python 測試 | **784 passed**（含真 LibreOffice 整合測試 5 項） |
| Frontend 測試 | **268 passed**（26 檔） |
| Ruff | 明確規則集（E/F/W/I/B/UP/C4/SIM/RUF）**All checks passed**（實跑 CI 釘的 0.15.4） |
| Coverage | **87.87%**（branch，門檻 85%，`pyproject.toml`） |
| TypeScript | src 與 **測試** 皆通過（`tsconfig.test.json`） |
| wheel | 乾淨安裝實測：`/`＋JS＋CSS 皆 200、MCP import 乾淨、ODT/ODP/ODS 三閘全綠 |
| 相依漂移 | 乾淨環境解析到 **mcp 2.0.0 / PyMuPDF 1.28.2 / openai 3.0.0 / pytest 9.1.1** 下全數通過 |
| 真 LibreOffice | 三格式三道閘全過；ODS **每一張工作表**都實算並掃描錯誤值 |
| CI | Python 3.11/3.12 × Linux/Windows、**locked lane（constraints.txt）**、Node 20/22/24、ruff、wheel smoke、LO lane（含跨層 pipeline，零 skip）、**tags 觸發**的 release gate |

**v1 MVP、v2 Phase 11–18、前端 F1 與 Phase 18.5 皆已完成並併入 main。**

2026-08-13 對抗式審查第一輪（P1×10、P2×13、P3×6）結果見下方。
**同日第二輪複驗判定 FAIL**，在「全部完成」的基線上又找出 R0×3、R1×10、R2×8、R3×3
共 24 項可重現缺陷（其中 R0 三項為發布阻斷）。這 24 項已全數修復，逐項結果與
證據見根目錄 `todo.md` 的「第二輪修復結果」。

> 這一輪最該記住的一件事：**測試全綠不等於做完。** 第一輪結束時 657 passed、
> 86.6% coverage、三道閘全綠——而 tag release 從來沒有可能執行過（workflow 不
> 監聽 tags）、`ci.yml` 與 `hatch_build.py` 根本沒進 Git、ODS 品質閘只看第一張
> 工作表、乾淨安裝會因為 mcp 2.0 直接 crash。綠燈量的是「跑過的東西」，不是
> 「該跑的東西」。

2026-08-17 補記：版式層／範本庫那三個 commit 讓 **CI 連三次紅燈**（本機全綠）。
兩個原因，都已修：ruff 7 項（import 排序、E501、未使用 import／noqa、`raises(Exception)`），
以及 `test_serve_rejects_remote_bind_without_explicit_acknowledgement` ——
**Rich 把 GitHub Actions 當終端機**，於是上色，而它的 highlighter 把旗標拆成三段
各自加碼，`--allow-remote` 不再是連續子字串。開發機色彩關閉所以永遠看不到。
兩個 `_out()` helper 現在都先剝 ANSI；本機要複現 CI 的算繪模式就設 `GITHUB_ACTIONS=true`。
**推 main 之後要看一眼 `gh run list`——本機綠燈不代表 CI 綠燈。**

---

## 一、對抗式審查結果（2026-08-13）

### P1：交付可信度與發布阻斷 — **全部完成**

| 項目 | 做法 | 驗證 |
| ------ | ------ | ------ |
| **P1-01** 單張重生交易化 | 候選 IR → 側寫 `deck.candidate.odp` → 驗證 → 算圖到未使用的預覽版本 → **全部成功才原子交換**（IR／檔案／預覽／版本號／閘門）。失敗完整回滾。重生後回寫 `outline.pages[n-1]`，QA 升級過的版型不會被過期契約打回去 | `test_webapi.py` 8 項回滾／版本／契約測試＋整合測試 |
| **P1-02** 下載只交付已驗證版本 | `Job.artifact_version` / `validated_version` / `downloadable`；生成中、驗證失敗、取消、重生中一律 **409＋中文原因**；快照不宣傳點下去會 409 的連結 | 5 項狀態別測試 |
| **P1-03** 收緊 IR／API 模型 | 全部 IR 與 request body 改 `extra="forbid"`；`bullet`／`page` 等拼錯欄位回可操作錯誤；補齊 title-content／two-col／comparison／big-fact／title／section／closing 的最低內容 invariant；加入 abuse 上限；`MAX_OUTLINE_PAGES` 由 IR、API、前端共用 | `test_ir.py` 11 項＋`test_webapi.py` 3 項 |
| **P1-04** ODS 數值與公式語意 | 拒絕 NaN/±Inf；驗證 cell ref、重複 target、越界引用、括號平衡；**函式白名單**（不支援就拒絕，不寫出 `#NAME?`）；`soffice --convert-to csv` 強制計算並掃描 `#NAME?`/`#VALUE!`/`Err:` | 8 項單元＋真 LibreOffice 數值一致性測試（120+300+80=500） |
| **P1-05** QA 解析失敗不 fail-open | `_findings_from_payload` 三分：可用信封／不可用信封（raise）／全部 finding 不合格（raise）；四個後端共用同一判準；`QAReport.verdict ∈ pass/fail/unknown`，`final_ok` 改為 computed | 6 項新測試，並修掉三個鎖定舊行為的舊測試 |
| **P1-06** QA 旗標與修復後同步 | `UnitStatus`（生成）與 `UnitQa`（品檢）拆成兩個維度；每輪 QA 整批重算 unresolved set；`complete` 不再洗掉 flagged；後端在 QA 修補後重新 emit 變更頁的 IR／title／role | reducer 3 項＋webapi |
| **P1-07** 前端打包進 wheel | `hatch_build.py`：`npm ci`（lockfile）＋`npm run build` → `odforge/webui` package data；`frontend_dist()` 改用 `importlib.resources`；**缺前端就 build 失敗**（要 API-only 得顯式 `ODFORGE_API_ONLY=1`）；`serve` 錯誤文案不再假設 cwd 是 repo root | `scripts/wheel_smoke.py`，乾淨 venv 實測通過 |
| **P1-08** CI 與發布閘門 | Python 3.11/3.12 × ubuntu/windows matrix、Node 20.19/22/24、ruff（版本固定）、wheel build＋clean-install smoke、LibreOffice integration lane（並斷言沒有整批 skip）、tag release 只發布 CI 產出的 wheel、env 清空所有金鑰 | `.github/workflows/ci.yml` |
| **P1-09** 行動版主要動作 | `data-phase` 驅動欄位重排（await 時大綱欄排到縮圖牆之前，用 `grid-template-areas` 而非 `order`，DOM／鍵盤順序一致）；確認 CTA sticky；未確認前收合骨架牆；`.foot`／`.workhead` 在窄螢幕 sticky；桌機 cockpit 綁 `100dvh`、三欄各自捲動 | `styles/layout.test.ts` 6 項 |
| **P1-10** WCAG 對比／live region／焦點 | 依實測比值重算 token（`--quiet`、`--muted`、`--warn`、`--pass`、`--crit`）；新增 `--line-interactive`（互動元件邊界 ≥3:1）並套用到 19 個控制項；skipped 閘不再整列淡化原因文字；narrator／WaitingCard／DiscoveryPanel 的 live region 縮到**離散階段**，秒數計時器退出無障礙樹；換題與進 Brief 後移動焦點；修正 h1→h3→h4 跳級 | `theme/contrast.test.ts` 7 項（含三個 dark 區塊一致性） |

### P2：可靠性、UX 與測試缺口 — **12/13 完成，1 項改為明確產品決策**

| 項目 | 結果 |
| ------ | ------ |
| **P2-01** QA 開關反映真實能力 | ✅ 前端讀取並快取 `/api/sources`；無可用視覺來源時 checkbox **disabled** 並顯示供應商給的真正原因，送出 body 不帶 `qa:true` |
| **P2-02** 取消後的外部呼叫 | ✅ 並行上限改數「仍在執行的 worker」而非 UI 狀態；`run_job` 在 slides／render／qa／complete 前各設取消檢查點，取消後不再算圖、不跑 QA、不發 complete |
| **P2-03** QA 修復後重新驗證 | ✅ CLI `--qa` 修補後重跑完整驗證並印第二張表；Web QA 修補後重跑 zip／xml／LibreOffice 並遞增 artifact version |
| **P2-04** 不再靜默刪除內容 | ✅ `DroppedContent` 逐條回報：CLI 印表格、Web 發 `content_degraded` SSE、GateRail 顯示、備忘稿列出**被刪的原文**（不再只寫「部分要點省略」）；含內容守恆測試 |
| **P2-05** 參考檔競態與大 payload | ✅ 讀取中禁止送出＋可取消；**整批**容量上限 16 MiB（不只單檔）；顯示合計大小。**未做 multipart upload**：現行 data-URI 路徑在新的上限下不會壓垮分頁，改協定的收益不足以justify 動到 API 契約 |
| **P2-06** 非法頁數阻止送出 | ✅ 3–30 以外／非整數一律擋下；按下 CTA 會展開設定、聚焦頁數欄、`aria-describedby` 指向錯誤訊息；上限與 IR 共用常數 |
| **P2-07** 清理過期預覽 | ✅ 預覽改版本目錄 `preview/v{n}`，發佈＝切指標；5 頁縮 3 頁後 `page-04.png` 不可能殘留 |
| **P2-08** 跨層整合測試 | ✅ `tests/test_integration_pipeline.py`：只 fake 模型，真渲染／真三道閘／真 LibreOffice／真下載，並把回應 bytes 當 ZIP 開來驗。**未導入 Playwright**（見「不做」） |
| **P2-09** 隔離真實 provider | ✅ autouse fixture 清空 19 個 provider 環境變數並鎖 `ODFORGE_VISION_BACKEND=off`；**任何連往非 localhost 的連線直接讓測試失敗**；「接受 backend 名稱」改純 schema 驗證，不啟動背景 pipeline |
| **P2-10** 測試工具鏈 | ✅ 新增 `test` extra（乾淨環境可收集全部測試）；`tsconfig.test.json`＋`npm run typecheck:tests`（當場抓出 5 個 `regen_start` 型別錯誤與 1 個 GateStatus 錯誤）；coverage baseline 85% |
| **P2-11** QA 圖像批次與持久化成本 | ✅ 視覺 QA 依**張數＋位元組**雙預算分批，跨批 findings 重新編號；session 落盤改 `asyncio.to_thread`，不再阻塞 event loop |
| **P2-12** 格式可信 vs 內容已查證 | ✅ 新增 `odforge/docs/gates.md` 第四節；GateRail 常駐範圍聲明；README／README.en 加 Scope 區塊；`.odp` 來源列標為**「來源（未查證）」** |
| **P2-13** Web 對 ODT/ODS 策略 | ✅ **定案：Web 永久專注 ODP**（理由見 `gates.md` §5：控制台的資訊架構建立在「一頁＝一個可預覽可重生的單元」上，文件與試算表沒有這個天然單元）。`doc_type != "odp"` 回 422，前端不擺會失望的分頁 |

### P3：一致性、文件與打磨 — **5/6 完成，1 項延後**

| 項目 | 結果 |
| ------ | ------ |
| **P3-01** 統一品質閘承諾與命名 | ✅ `odforge/docs/gates.md` 為唯一權威來源：四道閘各自保證什麼／不保證什麼、六種狀態語意（**`skipped`＝沒人要求；`unknown`＝要求了但跑不成**）、阻斷閘 vs 觀察訊號的定案。README／README.en／webapi.md 全數對齊 |
| **P3-02** 文件與程式漂移 | ✅ 測試數字（649＋225）、Python 3.11、session retention 90 天、`/api/sources` 補進 API 表、regenerate／download／snapshot 契約更新、wheel 安裝與驗證指令。`docs/audits/quality-final.md` **不存在**，無需標記 |
| **P3-03** 固定工具版本 | ✅ `engines`（node >=20.19 <25）＋`.node-version`；CI 固定 `ruff==0.15.4`；`test` extra 明列相依。**`httpx2` 已從 dev extra 移除**（確認無任何引用） |
| **P3-04** 拆分大型模組 | ⏸ **延後**（見下方「刻意不做／延後」） |
| **P3-05** 生成簡報的視覺多樣性 | ✅ 已在 2026-08 的 `feat(critic)` 系列完成（content-sized layouts、視覺敘事版型 process/timeline/metrics/cards/diagram、critic 檢查清單第 7 項「視覺敘事不足」）。本輪未再改動 |
| **P3-06** 清理 UI 設計反模式 | ✅ 移除四處重複的側邊色條語彙：ConfirmBar 改 accent 底色＋上下框、finding 改狀態底色＋整圈邊框、ErrorPanel 改完整邊框、session 卡改 hover 邊框；縮圖品檢徽章改成**真元素**（`::before` 的 content 讀屏器讀不到） |

---

## 一之二、MCP 整合面（2026-08-13 補）

**決定：不改別人的工具，讓別人呼叫我們。** 上游 patch（open-slide / Presenton 之類）
不做——它們是 HTML 中間層架構，加上 ODF 輸出等於把 conversion drift 裝回去，而且
掛的是它們的品牌。ODForge 的資產是四道閘，不是「會產 ODF」。

補齊三件事，讓「任何 AI 工具都能接上我們的品質保證」這句話成立：

| # | 做了什麼 | 為什麼 |
| --- | ------ | ------ |
| 1 | `forge_*` 補上第三道閘 | 原本硬寫 `with_soffice=False`，MCP 的「ok」比 CLI 的「ok」弱一級，讀起來卻一模一樣 |
| 2 | `forge_*` 補上版面預算檢查 | `check_budget` 原本只活在 `llm.py` / `cli.py`；外部 agent 塞 12 條 bullet 進來，引擎照畫、回一個開朗的 `ok:`。現在回傳字串附 `warning:` 逐頁逐框指出溢出——**ODForge 量測，呼叫端重寫** |
| 3 | `odforge-mcp` console script | `pip install odforge` 之後沒有任何指令能啟動 MCP server，想接的人得自己猜模組路徑與直譯器 |

**過程中抓到兩個只會發生在使用者身上的 bug：**

- **`mcp` 2.0 移除了 `mcp.server.fastmcp`。** 依賴寫 `mcp>=1.2`（無上界），開發機
  剛好停在 1.28.1 所以一切正常；今天任何人 `pip install odforge` 解析到 2.0.0，
  `odforge-mcp` 第一行就 crash。已加相容 import（兩個 major 的 `.tool()` /
  `.run()` 完全相同，shim 就是整個遷移）。
- **PyMuPDF 1.28.2 的 `fitz` 別名把棄用警告印到 stdout。** 對 stdio MCP server
  來說 stdout 全部都是協定，一行雜訊就毀掉整條串流。改用 `pymupdf` 正名 import，
  並加了「開新直譯器 import server 後 stdout 必須為空」的回歸測試
  （1.28.0 不印、1.28.2 印——正是開發機看不到的那種差異）。

驗證方式：乾淨 venv 裝 wheel（解析到 mcp 2.0.0），用**真的 MCP client** 走 stdio
連線 → `tools/list` 回 5 個工具 → `forge_presentation` 產出真 .odp，三道閘全過
（含 `soffice: OK — converted to pdf`），超載頁面確實回報 `warning:` 並指名第 2 頁。

---

## 二、刻意不做／延後（附理由）

| 項目 | 決定 | 理由 |
| ------ | ------ | ------ |
| Playwright 端到端瀏覽器測試 | **不做（本輪）** | 需下載瀏覽器二進位並維護一套獨立 runner。目前以 `styles/layout.test.ts`（斷言產生行為的 CSS 規則）＋`test_integration_pipeline.py`（真渲染／真 LibreOffice／真下載）覆蓋同一批風險。**未覆蓋的是「504×692 實際捲動位置」這類需要真實佈局引擎的斷言** —— 這一項仍是人工驗收 |
| 參考文件改 multipart upload | **不做** | 加上整批 16 MiB 上限與讀取中禁止送出之後，原本的記憶體與競態風險已解除。改協定要動 API 契約、前端與測試，收益不成比例 |
| P3-04 拆分 `render/odp.py`（3.2k 行）、`webapi.py`（2k 行）、`llm.py`（1.2k 行）、`app.css`（3.2k 行） | **延後** | 純結構重構，不修任何缺陷。在 649＋225 測試綠燈、決賽在即的時點動這種改動半徑，風險遠大於收益。**前置條件已備妥**：行為測試與 ODF fixture 齊全，隨時可安全進行 |
| Web 支援 ODT/ODS | **永久不做** | 見 `gates.md` §5 |
| 內容真偽查證（citation verification） | **不在範圍** | 需要 evidence provenance 流程。目前的處置是**誠實標示**：介面、README 與 `.odp` 來源列都寫明未查證 |
| ODF RELAX NG schema 驗證 | **不做** | 第二道閘只保證 well-formed，`gates.md` 已明說不保證 ODF 語意 |

---

## 三、Phase 19：決賽交付（人工，非程式）

- [ ] 19.0 補齊 v2 人工關卡截圖佐證：`odforge/docs/screenshots/v2/`（三 preset 對照、兩段式＋確認站、`--qa` QAReport 實錄、公版樣式抽取實測）
- [ ] 19.1 Dogfooding v2：用 v2 重生成使用手冊 `.odt`、**決賽簡報 `.odp`（7 分鐘，用 ODForge 自己生）**、測試報表 `.ods`
- [ ] 19.2 Before/After 對照組：v1 demo vs v2 同 prompt 並排截圖
- [ ] 19.3 錄影：CLI 兩段式（大綱確認站）→ `--qa` 迴圈 → LibreOffice 開檔 → 前端 demo → MCP + skill
- [ ] 19.4 README 更新：v2 架構圖、巨頭研究定位一句話、樣式抽取用法（**閘門敘述已於本輪對齊 `gates.md`**）
- [ ] 19.5 決賽 QA 準備：統問統答（為何不做 PPTX、為何不用 HTML 中間層、與 NotebookLM/Gamma 差異、TAIDE 可否接）
- [ ] 人工驗收：504×692 手機實機走一次「生成 → 確認 → 下載」，確認 CTA 與下載列不需捲過預覽牆

---

## 四、驗證指令（現行唯一一套）

```powershell
cd odforge

# Python
.\.venv\Scripts\python.exe -m pytest -q --cov --cov-report=term   # 711 passed, 87.19%
.\.venv\Scripts\ruff.exe check .                                  # All checks passed

# Frontend
npm.cmd --prefix web ci                    # 依 lockfile 安裝
npm.cmd --prefix web test                  # 244 passed
npm.cmd --prefix web run typecheck         # src
npm.cmd --prefix web run typecheck:tests   # 含測試
npm.cmd --prefix web run build

# Wheel（前端會被 hatch_build.py 編進去；缺前端則 build 失敗）
uv build --wheel -o dist
uv venv .venv-smoke
$env:VIRTUAL_ENV = ".venv-smoke"; uv pip install "dist\odforge-0.1.0-py3-none-any.whl[web]" httpx
.\.venv-smoke\Scripts\python.exe scripts\wheel_smoke.py           # / + JS + CSS 皆 200
```

真 LibreOffice 驗證已內建於測試（`test_validate.py`、`test_preview.py`、
`test_integration_pipeline.py`），安裝 LibreOffice 後 `pytest` 會自動執行；
CI 的 integration lane 另外斷言這些測試沒有整批被 skip，並**單獨**跑
`test_integration_pipeline.py` 且不容許任何一項 skip。

**開發機的 venv 不是驗證環境。** 它停在 mcp 1.28／PyMuPDF 1.28.0，而乾淨解析
會拿到 mcp 2.0／PyMuPDF 1.28.2——兩個分別移除了我們 import 的模組、和開始往
stdout 印字。要複驗相依漂移：

```powershell
python -m venv $env:TEMP\odforge-clean
& $env:TEMP\odforge-clean\Scripts\python.exe -m pip install -e ".[test,vision,web]"
& $env:TEMP\odforge-clean\Scripts\python.exe -m pytest -q      # 必須也是 711 passed
```

CI 兩條線並存：不釘版本的 `python` job 是上游變動的早期警報，
`locked` job 用 `constraints.txt` 保證可重現，release 需要兩條都綠。

---

## 五、架構決策（v2 定案，仍然有效）

> **引擎管「能力」與「保證」；LLM 掛著 skill 管「選擇」與「變化」；harness 兜底。**
> 變化來自 per-deck design tokens ＋ page-role 版型多樣性 ＋ 參數化選項，
> **不是自由座標**。LLM 從頭到尾不碰 cm 與 pt。

**明確不做：** 線上編輯器（編輯器＝LibreOffice / MODA ODF 工具）、HTML 中間層
（conversion drift 是我們要打的敵人）、像素路線（輸出不可編輯違反 ODF 存在理由）、
拚模板數量、一步到位黑箱生成。

**設計鐵則（渲染層 enforce，不是 prompt 勸導）：** CJK 三軌字級齊備；內文對背景
≥4.5:1、標題 ≥3:1（DesignSpec validator 硬擋）；accent 只用於裝飾與強調；版面預算
`估算文字高度 ≤ frame 高度`；禁止在標題下加裝飾短線（AI 簡報特徵）。

**相依降級路徑：** PyMuPDF（純 pip）、soffice（缺席 → 第三道閘 skipped）、
視覺後端（`off` → 第四道閘 skipped）。每個外部相依都有降級路徑，且降級一律**誠實回報**。

---

## 附錄：歷史

v1 MVP 計畫（Phase 0–10）與 v2 Phase 11–18.5 的逐步細節已全數完成，
原始步驟見 2026-08-13 之前的本檔版本與 git history。本輪對抗式審查的原始清單
保留於根目錄 `todo.md`。
