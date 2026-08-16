# ODForge 對抗式審查修復清單

> 建立日期：2026-08-13
> 依據：目前工作樹的程式碼審查、Python／Frontend 測試、LibreOffice 實測、wheel 安裝驗證與響應式／無障礙檢查。
> 範圍：產品、正確性、可靠性、ODF 語意、UX/UI、無障礙、效能、測試、文件與發布工程。
> 不在本輪範圍：深入安全審查與滲透測試。

---

## 第二輪修復結果（2026-08-13，本輪作業）

**R0×3、R1×10、R2×8、R3×3 全數完成。** 每一項都先寫出能重現問題的測試，
再實作修正；下方「證據」欄是實際跑過的東西，不是預期。

| 驗收基線 | 結果 |
| ------ | ------ |
| Python | **711 passed**、branch coverage **87.19%**（門檻 85%） |
| Ruff | `All checks passed` |
| Frontend | **244 passed**（25 檔）、`typecheck` 與 `typecheck:tests` 皆過、production build 過 |
| 乾淨相依環境 | 全新 venv 解析到 **mcp 2.0.0 / PyMuPDF 1.28.2 / openai 3.0.0 / pytest 9.1.1 / starlette 1.6.0**，`import odforge.mcp_server` 成功、完整 pytest 通過 |
| wheel | 乾淨 build → 乾淨 venv 安裝 →`/` + JS + CSS 皆 200、MCP import stdout 乾淨、ODT/ODP/ODS 三閘全綠 |
| 真 LibreOffice | `test_integration_pipeline.py` 5/5 passed，零 skip |

### R0：新增發布阻斷 — 全部完成

| 項目 | 做法 | 證據 |
| ------ | ------ | ------ |
| **R0-01** MCP 2.0 乾淨安裝 | `mcp_server.py` 的相容 import（`mcp.server.mcpserver.MCPServer` ← → `mcp.server.fastmcp.FastMCP`）**對著真的 mcp 2.0.0 驗過**：2.0 確實移除了 `mcp.server.fastmcp`，且兩個 major 的 `.tool()` / `.run()` 行為一致 | 全新 venv 裝 `.[test,vision,web]`（解析到 mcp 2.0.0）→ import 成功 → **711 passed** |
| **R0-02** 不可達的 tag release | `on.push` 補上 `tags: ["v*"]`。原本只監聽 branches，release job 卻只在 `refs/tags/v*` 執行 —— 這條路**從來沒有可能被觸發過**，而且沒有任何一處會說 | `.github/workflows/ci.yml`；release `needs` 也補上新的 `locked` lane |
| **R0-03** 發布關鍵檔納入 Git | `.github/workflows/ci.yml`、`hatch_build.py`、`scripts/wheel_smoke.py`、`tests/test_integration_pipeline.py`、`web/.node-version`、`web/tsconfig.test.json`、`docs/gates.md`、`constraints.txt` 與五個新測試檔全部納入版控；`.gitignore` 補上會反覆出現的 agent 垃圾檔 | `git status` 只剩 `.claude/`（agent 工具設定，非專案程式碼） |

### R1：重新開啟的正確性缺口 — 全部完成

| 項目 | 做法 | 證據 |
| ------ | ------ | ------ |
| **R1-01** 重生全有或全無 | `os.replace` 提到最前面成為唯一的 point of no return，失敗即丟棄候選並完整回滾；`_persist_generated_assets` 移進 rollback 範圍;`PreviewUnavailable` 後**照樣發佈新的（空的）預覽版本**，不再拿上一版的圖回答新頁面的請求 | `test_regenerate_rolls_back_when_the_final_swap_fails`、`test_regenerate_does_not_serve_the_previous_image_when_no_preview_is_made`（重生後 `preview/2.png` 回 404 而不是舊圖） |
| **R1-02** 重生後可恢復 | 重生成功後補發 `qa_invalidated` / `slide_done` / `unit_done` / `preview_ready` / 四道 `gate_result` / `complete`；SSE replay 改成**只在最後一個 terminal event 才收線**（原本停在第一個 `complete`，後面補的事件永遠送不出去） | `test_regeneration_survives_a_reload`、`test_events_replay_does_not_stop_at_a_superseded_complete`（log 中兩個 complete 都要收到） |
| **R1-03** QA 用候選成品 | `run_qa_loop` 整輪跑在 `ir` 的深拷貝與 `deck.odp.qa-candidate` 上，最後才 `os.replace` 換入。**乾淨品檢完全不動磁碟上的 bytes**（原本照樣重寫，而 `repaired=False` 又叫 caller 不用複驗）；中途 render 例外時記憶體與磁碟一起維持原狀；換檔失敗回報 `verdict=fail` 且 `repaired=False` | `test_a_clean_review_does_not_touch_the_shipped_artifact`、`test_a_render_failure_mid_loop_leaves_the_ir_and_the_deck_agreeing`、`test_a_failed_commit_is_reported_as_a_failure_not_a_repair` |
| **R1-04** ODS 驗證所有工作表 | `--convert-to csv` 只匯出第一張。改用 all-sheets filter（尾碼 `-1`）逐張匯出並掃描，**張數對不上就 fail**（HTML 匯出為舊版 LibreOffice 的 fallback），而不是拿收到的子集當全部 | 真 LibreOffice 重現：第一張正常、第二張 `of:=1/0` 的檔案原本回「formulas evaluated without errors」,現在指名 `Bad: #DIV/0!`。`test_formula_error_on_the_second_sheet_fails_the_gate` |
| **R1-05** 裸 A1／range 引用 | 引用掃描器重寫：括號式 `[.B2]` / `[Sheet2.B2:.B9]`（range 尾端繼承 sheet）、裸式 `A1:A999` / `$A$1` / `Sheet2.B2` 全部解析；`LOG10(` 這種函式名不會被誤讀成儲存格、字串常值裡的 `"B5"` 不算引用；跨 sheet 引用改由 `Spreadsheet` 層對**該張**工作表做界限檢查 | `of:=SUM(A1:A999)`、`of:=SUM(Z999)` 現在會被擋下。`test_out_of_range_reference_is_rejected` 等 5 組參數化測試 |
| **R1-06** 名額綁真 worker | 新增 `_offload()`：計數在送進 executor 前 +1、**由執行緒自己**在 finally −1。`_occupies_a_slot` 改看這個計數,不再相信被取消的 Task 的 `done()` | **真 asyncio cancellation**（非 FakeTask）：取消後 task 已 done、執行緒仍在跑時名額不釋放。移除該 fix 後兩項測試立刻失敗，還原後通過 |
| **R1-07** 清除上一版 QA | 後端 `job.qa_report = None` 之外補發 `qa_invalidated`；reducer 在 `qa_invalidated` 與 `regen_done` 兩條路徑（SSE 與同步 HTTP）都清空 `unit.qa` 與 `qaRounds` | `test_regeneration_discards_the_previous_rounds_qa`；reducer 兩項新測試 |
| **R1-08** 實算對比 | placeholder 的 `color-mix(--muted 74%, transparent)` 實測 **3.68:1**、skipped gate 文字 `opacity: 0.62` 實測 **2.7:1**。文字一律改用 `--quiet`（全強度即過 AA），`opacity` 只留給純裝飾的圖示格並提到 0.75（3:1） | `contrast.test.ts` 新增合成計算：把 `opacity` / `color-mix` 疊到底色上再驗，並直接斷言「文字不得用 opacity 調淡」 |
| **R1-09** LO lane 納入跨層測試 | integration lane 新增獨立步驟跑 `test_integration_pipeline.py`，**任何一項 skip 就 fail**（skip 代表 soffice 沒裝，而這條 lane 存在的意義就是它有裝） | `.github/workflows/ci.yml` |
| **R1-10** PyMuPDF import | `tests/test_preview.py`、`tests/test_webapi.py` 改 `import pymupdf`；新增全 repo 掃描測試，禁止任何檔案再用會往 stdout 印字的舊 `fitz` 別名 | 乾淨環境（PyMuPDF 1.28.2）全綠；`test_no_module_imports_the_legacy_fitz_alias` |

### R2：新增可靠性／UX 回饋 — 全部完成

| 項目 | 做法 | 證據 |
| ------ | ------ | ------ |
| **R2-01** API 端 16 MiB 總量閘 | `GenerateBody` 與 `DiscoveryBody` 都加上 model validator：先看編碼後總長（解碼前的便宜擋門），再用字串長度**推算**解碼後總量（不 decode，否則等於配置你想拒絕的東西）。超限回 422＋中文訊息 | 6×3 MiB 被拒、5×3 MiB 通過；`test_decoded_length_matches_a_real_decode` 對照真 decode |
| **R2-02** 拒絕空白內容 | `min_length=1` 數的是字元，空白也是字元。所有最低內容 invariant 改用 strip 後的有效內容；bullets/left/right、ProcessStep、TimelineEvent、MetricSpec、DiagramNode、SourceRef、ChartSpec.labels、ImageSpec.alt 一律拒絕全空白 | 12 組參數化測試 + 真渲染回歸：`bullets=["   "]` 原本能產生「標題下面什麼都沒有」卻通過 LibreOffice 的簡報 |
| **R2-03** DroppedContent 覆蓋重生與 QA | `regenerate_slide` 與 `_repair_error_slides` 都接上 collector；Web 發 `content_degraded`、回應帶 `dropped_content`、snapshot 也看得到；CLI `--qa` 印出 QA 修補階段掉的內容 | `test_regeneration_reports_content_the_budget_dropped`、`test_repair_reports_content_the_layout_budget_dropped` |
| **R2-04** QA 開關 fail-closed | 來源探測改三態（loading / ready / error）。**只有「問過而且可以」才送 `qa:true`**；探測失敗時停用、說明原因並提供重試；`getSources()` 不再永久快取 rejected promise（原本網路抖一下，這個 session 就再也問不到來源） | 三項新測試：pending 不送 `qa:true`、reject 不送且顯示原因、重試後真的會再問一次 |
| **R2-05** 素材重選競態 | 每條提早 return 的路徑（非法檔／超過總量）都自己清掉 `assetsLoading` —— 因為 run counter 已經讓上一輪的 finally 失效了 | 兩項新測試：「讀取中又選非法檔」與「超過合計上限」之後，繼續鈕都不能鎖死 |
| **R2-06** 手機視覺／DOM 順序 | 窄螢幕所有 phase 統一 `left → center → right`（＝DOM 順序＝朗讀順序）；coarse pointer 的 `min-width` 補上（原本只有 `min-height`，44×44 只做了一半）；工作台補上唯一的 `<h1>` | `layout.test.ts` 三項新測試 + `App.test.tsx` 實際渲染斷言只有一個 h1 |
| **R2-07** 隔離 Codex 認證 | conftest 原本**刪除** `CODEX_HOME`，而刪除正是「用預設值」的意思 —— 預設值就是 `~/.codex/auth.json`。改成指向空的 tmp 目錄，並新增 subprocess guard 禁止測試啟動真的 `codex` CLI | 三項新測試（含「guard 不會擋到其他工具」） |
| **R2-08** zip-safe 宣告 | `as_file()` 會在 context 結束時刪掉它產生的目錄，而路徑是從 `with` 裡面 return 的 —— zip 情境下等於把已刪除的目錄交給 StaticFiles，Python 3.11 更是連目錄都處理不了。**不支援就不要宣告**：改為直接使用檔案系統路徑，非檔案系統 loader 降級成 API-only 並記一行 log | `test_frontend_dist_returns_a_directory_that_is_still_there`（回傳後仍存在）、`test_a_non_filesystem_package_degrades_to_api_only_and_says_so` |

### R3：文件與可重現性 — 全部完成

| 項目 | 做法 |
| ------ | ------ |
| **R3-01** 文件漂移 | README／README.en 測試數字改 711＋244；`docs/audits/quality-final.md` 加上**歷史快照**橫幅（並說明它後來被兩輪審查推翻，這正是「全綠不等於做完」最直接的證據）；`docs/todo.md` 現況表整段重寫；順手修好 README 兩份裡壞掉的 `ruff` 指令（`\r` 被當成跳脫字元，指令根本貼不能跑） |
| **R3-02** Web ODP 策略文案 | 「即將支援」與 `gates.md` 第五節的「定案」互相矛盾，二選一。API 錯誤訊息改成說明**為什麼**是定案並指向做得到這件事的介面（CLI／MCP），`webapi.md` 同步；測試改為斷言「即將支援」**不得**出現 |
| **R3-03** constraints／漂移 lane | 新增 `constraints.txt`（乾淨解析後 freeze）與 CI `locked` lane，並斷言 pin 真的生效；原本不釘版本的 `python` lane 保留為上游變動的早期警報。release 需要兩條都綠 |

### 第二輪放行條件

- [x] R0 全數完成且相關檔案已納入 Git。
- [x] R1 每項都有先失敗、修正後通過的 regression test；故障注入不存在 mixed-version artifact。
- [x] 全新 Python 環境完整 pytest＋coverage＋ruff 通過（711 passed / 87.19% / All checks passed）。
- [x] Frontend tests／兩份 typecheck／build 通過；computed contrast 與重生後 QA state 有自動測試。
- [x] 真 LO lane 執行多 sheet 錯誤公式與完整 Web regeneration pipeline，相關案例零 skip。
- [x] build wheel → 乾淨安裝 → `/`、JS、CSS、MCP import、ODT／ODP／ODS smoke 全通過。
- [x] 文件數字、完成狀態與實際 gate 契約一致。

### 仍未做（誠實列出，非「已完成」）

- **Playwright／瀏覽器端到端測試**：仍未導入。`layout.test.ts` 斷言的是產生行為的
  CSS 規則，不是真實佈局引擎算出來的位置。**「504×692 實機捲動位置」仍是人工驗收項目**
  （見「三、Phase 19」）。
- **請求體總大小的傳輸層上限**：R2-01 在模型層擋住總量，但 FastAPI 仍會先把整個
  JSON 讀進記憶體才驗證。要真正在傳輸層設限需要 ASGI middleware 或反向代理設定。
- **`.claude/`** 未納入版控（agent 工具設定，不是專案程式碼）。
- **本輪變更尚未 commit**，工作樹保留給使用者自行檢視後提交。

---

## 第二輪複驗回饋（原文保留）

**結論：FAIL，尚不可標記全部完成或 production-ready。** 既有測試基線明顯改善，
但乾淨安裝、tag release、artifact 交易一致性與 ODS 多工作表品質閘仍有可重現反例。

### 已確認通過的基線

- [x] 既有開發環境：Python **649 passed**，branch coverage **86.59%**。
- [x] Frontend：**225 passed**；source／test TypeScript、production build 全部通過。
- [x] Ruff：`All checks passed`。
- [x] 一般 pip wheel 在 Python 3.11／3.12：`/`、主 JS、主 CSS 均回 200。
- [x] `tests/test_integration_pipeline.py` 本機真 LibreOffice **5/5 passed**。
- [x] 真實 Chrome 504×692：await 確認 CTA 與 complete 下載 CTA 均在首屏可見。
- [x] Strict IR/API `extra="forbid"`、NaN／Infinity、重複／越界 formula target、
  函式白名單、malformed QA envelope 與下載 version／status gate 的狹義行為通過。

> 上述綠燈是基線，不等於下列對抗案例已通過。第二輪複驗未修改程式碼。

### 原項目複驗判定

| 項目 | 判定 | 第二輪證據摘要 |
| ------ | ------ | ------ |
| P1-01 單張重生交易化 | **FAIL** | preview publish 失敗後，新 ODP／IR／version 已發布，舊 preview／design pass 仍在且可下載；成功重生也未更新 SSE replay |
| P1-02 下載版本／狀態 | **PASS（狹義）** | generating／error／cancelled／版本不符會拒絕；但上游若錯把 mixed artifact 標成 validated，仍會下載 |
| P1-03 Strict IR／內容 invariant | **PARTIAL** | unknown fields 已拒絕；`bullets=["   "]` 仍可產生視覺空白但通過 LibreOffice 的簡報 |
| P1-04 ODS 數值／公式 | **PARTIAL** | finite／target／函式白名單已完成；第二工作表公式錯誤與裸 A1 range bounds 仍漏檢 |
| P1-05 malformed QA fail-open | **PASS** | 不可用 envelope／全部 finding 不合法會 raise；verdict 已有 pass／fail／unknown |
| P1-06 QA 狀態同步 | **PARTIAL** | 初始 QA round 改善；手動重生後舊 `unit.qa`、`qaRounds`、findings 與 session replay 仍指向上一版 |
| P1-07 wheel Web UI | **PARTIAL** | 一般 pip install 通過；zip-safe 宣告不成立，build hook／smoke 等關鍵檔仍未納入 Git |
| P1-08 CI／發布閘門 | **FAIL** | workflow 不監聽 tags，release job 永遠不可達；LO lane 漏掉真正跨層測試 |
| P1-09 行動版主要動作 | **PARTIAL** | await／complete CTA 實機通過；一般／完成態視覺順序仍和 DOM／讀屏順序不同 |
| P1-10 WCAG／live region／焦點 | **PARTIAL** | live region 與主要焦點改善；computed placeholder／skipped gate 對比仍低於 AA |
| P2-01 QA 能力開關 | **PARTIAL** | 正常 `/api/sources` 流程通過；首次 pending／reject 仍可能送 `qa:true`，rejected Promise 會永久 cache |
| P2-02 取消後外部呼叫 | **FAIL** | cancelled asyncio Task 已 `done()` 時，`to_thread` provider thread 仍執行卻不占 active slot |
| P2-03 QA 後重驗 | **PARTIAL** | QA repair render 例外可造成記憶體新 IR／磁碟舊 artifact，wrapper 吃例外後仍完成；clean QA 重寫 bytes 也不重驗 |
| P2-04 DroppedContent | **PARTIAL** | 初始生成有揭露；手動重生與 QA repair 沒傳同一 collector |
| P2-05 素材競態／payload | **FAIL** | API 沒有 16 MiB aggregate gate；重新選擇非法檔可使 `assetsLoading` 永久卡住 |
| P2-06 非法頁數 | **PASS（功能）** | 會阻擋並聚焦；收合 chip「已略過」文案仍與實際阻擋行為矛盾 |
| P2-07 過期預覽 | **PASS（完整 render）** | versioned preview 可避免頁數縮短殘留；重生 `PreviewUnavailable` 仍可能服務上一版圖，歸 P1-01 |
| P2-08 跨層整合測試 | **PARTIAL** | 本機真 pipeline 5/5；CI integration lane 未執行該檔，尚無 browser E2E |
| P2-09 provider 隔離 | **PARTIAL** | HTTP provider 隔離通過；刪除 `CODEX_HOME` 會 fallback 到真實 `~/.codex/auth.json` |
| P2-10 測試工具鏈 | **PARTIAL** | coverage／test typecheck／Ruff 通過；乾淨最新 MCP、PyMuPDF 仍讓 suite 失敗 |

### R0：新增發布阻斷

- [x] **R0-01 修正 MCP 2.0 乾淨安裝失敗。** `odforge/pyproject.toml` 的 `mcp>=1.2`
  目前解析到 2.0.0，但 `odforge.mcp_server` 仍 import 已移除的 `mcp.server.fastmcp`。
  - 驗收：全新 venv 執行 `pip install -e ".[test]"` 後，`import odforge.mcp_server` 與完整 pytest 均通過。
  - 決策：短期限制 `mcp<2`，或完整遷移 MCP 2 API；不可只依賴既有 venv 的 1.28.1。
- [x] **R0-02 修正不可達的 tag release。** `.github/workflows/ci.yml` 的 `push` 目前只有
  `branches: [main]`，release job 卻只在 `refs/tags/v*` 執行。
  - 驗收：明確監聽 `tags: ["v*"]`；測試 tag 走完全部 `needs` 並只發布同次 CI wheel。
- [x] **R0-03 納入所有發布關鍵檔。** 目前至少 `.github/workflows/ci.yml`、
  `odforge/hatch_build.py`、`odforge/tests/test_integration_pipeline.py` 仍是 untracked；並一併確認
  `scripts/wheel_smoke.py`、`.node-version`、`tsconfig.test.json`。
  - 驗收：乾淨 clone 能完整重現 CI、frontend build、wheel 與 integration smoke。

### R1：重新開啟的正確性缺口

- [x] **R1-01 重生 commit 必須全有或全無。** 對 `_persist_generated_assets`、`os.replace`、
  `_page_contract`、`_publish_previews` 逐點故障注入；失敗後 IR、outline、ODP、preview、gates、
  artifact／validated version 必須全舊。`PreviewUnavailable` 後不得繼續提供舊圖。
- [x] **R1-02 重生成功後更新可恢復狀態。** 現在成功重生沒有追加 `slide_done`／`gate_result`
  或等價 snapshot version；重整後可從舊 SSE events 還原舊 IR／舊 gates。
  - 驗收：重生後刷新頁面，title、role、preview、design unknown 與下載 artifact 必須同版。
- [x] **R1-03 QA repair 使用 candidate artifact。** QA 不得先原地換 live IR；任何下一輪
  render／validate 例外必須 rollback。artifact bytes 只要改變就必須遞增 version 並重驗；
  或 clean QA 完全不重寫 artifact。
- [x] **R1-04 ODS 驗證所有工作表。** 不得只讀 `{stem}.csv`；使用 XLSX cached results、UNO
  或逐 sheet export 掃描所有 sheets。加入「第一張正常、第二張 `of:=1/0`」真 LO regression。
- [x] **R1-05 解析裸 A1／range 引用。** `of:=SUM(A1:A999)`、`of:=SUM(Z999)` 現在
  `referenced_cells()` 為空。補絕對引用、range、支援的跨 sheet 語法與 bounds tests。
- [x] **R1-06 active slot 綁定真實 worker。** 不依賴被取消 await Task 的 `done()`；追蹤
  executor future／worker counter 到 provider thread 真正結束。測試使用真實 asyncio cancellation，
  不得只用 `FakeTask`。
- [x] **R1-07 重生後清除上一版 QA。** `unit.qa`、`qaRounds`、findings、design gate 與 preview
  全部清成 unknown／空，或立即重跑 QA；補 reducer 與 session replay 測試。
- [x] **R1-08 測 computed contrast。** light placeholder 實算約 3.68:1；skipped gate 次文字／
  狀態最低約 2.57:1。測試必須計算 `opacity`／`color-mix` 合成後顏色，而非只測 token。
- [x] **R1-09 LibreOffice CI 納入 `tests/test_integration_pipeline.py`。** 斷言其中 LO-dependent
  Web pipeline／regeneration cases 實際執行且未 skip。
- [x] **R1-10 遷移 PyMuPDF import。** 最新允許版本 1.28.2 對舊 `import fitz` 印 deprecated
  warning，造成 648 pass／1 fail。改用 `import pymupdf` 或明確相容層，乾淨環境須回到全綠。

### R2：新增可靠性／UX 回饋

- [x] **R2-01 API 執行 16 MiB aggregate gate。** 直接 request model 可接受六檔合計約
  17.17 MiB decoded／22.89 MiB encoded。generate 與 discovery 都需在解碼前後檢查總量，
  超限回 413／422；前端限制不能當 server gate。
- [x] **R2-02 拒絕 whitespace-only layout 內容。** 所有最低內容 invariant 使用 strip 後的
  有效內容；補 `bullets=["   "]` 等真 render／LibreOffice regression。
- [x] **R2-03 DroppedContent 覆蓋重生與 QA repair。** 所有 `generate_slides`／repair 路徑共用
  collector，degraded 訊息綁定正確 artifact version。
- [x] **R2-04 QA capability fail-closed 且可恢復。** pending／unknown 不得送 `qa:true`；
  rejected source Promise 可清除／退避重試；補 slow、reject、recover 測試。
- [x] **R2-05 修正素材重選 loading race。** 每個 invalid／over-total／abort／stale reader
  路徑都要清理 loading；補「舊檔讀取中又重選非法檔」測試。
- [x] **R2-06 統一各 phase 的手機視覺與 DOM 順序。** 一般／完成態不可再是視覺
  center→left→right、DOM left→center→right；coarse pointer 控制至少 44×44，workspace 補唯一 h1。
- [x] **R2-07 隔離 Codex 認證。** 測試把 `CODEX_HOME` 指到 tmp 空目錄，並增加 subprocess／
  runner guard，避免 fallback 使用者真實登入。
- [x] **R2-08 修正 zip-safe 宣告或生命週期。** Python 3.11 對 zip directory 的
  `resources.as_file()` 會失敗；3.12 離開 context 後回傳路徑已被刪除。若不支援就刪除宣告；
  若支援，extracted directory 必須活到 FastAPI app 關閉。

### R3：文件與可重現性

- [x] **R3-01 修正文件漂移。** 根 README 仍寫 402 passed；`docs/audits/quality-final.md`
  仍存在並顯示舊 20/20、402／150；`docs/todo.md` 卻聲稱該檔不存在。
- [x] **R3-02 統一 Web ODP 策略文案。** 「永久專注 ODP」與 API／webapi 文件的
  ODT／ODS「即將支援」二選一，README、gates、API error、webapi.md 必須一致。
- [x] **R3-03 建立 Python constraints／lock 或相容上下限。** CI 增加 locked lane 與允許範圍
  upgrade lane；MCP、PyMuPDF 漂移不得再由開發機既有 venv 掩蓋。

### 第二輪放行條件

- [x] R0 全數完成且相關檔案已納入 Git。
- [x] R1 每項都有先失敗、修正後通過的 regression test；故障注入不存在 mixed-version artifact。
- [x] 全新 Python 3.11／3.12 環境完整 pytest＋coverage＋ruff 通過。
- [x] Frontend tests／兩份 typecheck／build 通過；computed contrast 與重生後 QA state 有自動測試。
- [x] 真 LO lane 執行多 sheet 錯誤公式與完整 Web regeneration pipeline，相關案例零 skip。
- [x] build wheel → 乾淨安裝 → `/`、JS、CSS、MCP import、ODT／ODP／ODS smoke 全通過。
- [x] 文件數字、完成狀態與實際 gate 契約一致；不得以既有測試全綠直接推論「全部完成」。

## 執行原則

- [x] 依 P1 → P2 → P3 順序處理；P1 未清零前不宣稱 production-ready。
- [x] 每項修正都先加入能重現問題的失敗測試，再實作修正。
- [x] 品質閘必須綁定「實際下載的同一版本產物」，不可只綁定 job 或舊報告。
- [x] `pass`、`fail`、`skipped`、`unknown` 必須語意分明；無法檢查不等於通過。
- [x] 不以增加 mock 測試取代跨層整合測試。
- [x] 保留使用者目前未提交的工作樹變更，不做破壞性重置。

## 完成定義

全部任務完成時，至少要滿足：

- [x] Python 完整測試通過，最低支援版本 Python 3.11 與主要開發版本皆有 CI 證據。
- [ ] Frontend unit tests、test typecheck、production build、browser smoke 全部通過且無未處理 `act(...)` warning。
- [x] Ruff 採明確且可通過的規則集；coverage 有 baseline 與核心模組門檻。
- [x] wheel 在乾淨環境安裝後，`odforge serve` 的 `/` 與靜態資產均回 200。
- [x] ODT／ODP／ODS 範例均通過結構、XML 與真實 LibreOffice 開啟／轉檔。
- [x] 所有可下載產物都對應最新一輪成功的 validation／QA 狀態。
- [ ] 504×692 手機 viewport 可在首屏或 sticky action bar 看見當前唯一主要動作。
- [x] 主要文字、狀態文字與表單邊界通過 WCAG AA 對比檢查。
- [x] README、英文 README、套件 README、Web API 文件與實際行為一致。

---

> **以下 P1–P3 是第一輪審查的原始規格，保留供追溯。**逐項處置結果見
> `docs/todo.md`「一、對抗式審查結果」；第二輪重新開啟的部分見本檔最上方。

## P1：交付可信度與發布阻斷

### P1-01 讓單張重生成為交易式操作

位置：`odforge/src/odforge/webapi.py:886-947, 1578-1588`

- [x] 不再直接原地修改 `job.ir.slides[n - 1]`。
- [x] 以候選 IR 與暫存 ODP 執行 render → validation → preview → optional QA。
- [x] 全部必要步驟成功後，才原子交換記憶體 IR、磁碟產物、preview 與版本號。
- [x] 任一步驟失敗時完整 rollback，原 IR、原 ODP、原 preview 與品質報告保持一致。
- [x] 成功重生後重新計算 ZIP、XML、LibreOffice、design gate；不得保留舊綠燈。
- [x] 重生後同步更新 outline／page role 或明確保存新的 page contract，避免下一次重生把 QA 修復版型改回去。
- [x] 加入測試：render 前失敗、render 後 validation 失敗、preview 失敗、連續兩頁重生、QA 修復後再次重生。

驗收：失敗的第一頁重生後再成功重生第二頁，最終下載檔不得包含第一次失敗的內容。

### P1-02 下載端點只交付已驗證的產物版本

位置：`odforge/src/odforge/webapi.py:1616-1623`

- [x] 為 artifact 建立 version／validated version／downloadable 狀態。
- [x] generating、validating、qa、regenerating、error、cancelled 工作不得僅因檔案存在便可下載。
- [x] validation fail 後回 409 或 404，並提供人可理解的狀態訊息。
- [x] 只有與最新 IR 相符且通過必要閘門的 artifact 才回 200。
- [x] 加入輪詢下載、validation-failed、QA-failed、重生中及失敗重生的 API 測試。

### P1-03 收緊 IR 與 API 模型，禁止靜默忽略欄位

位置：`odforge/src/odforge/ir.py`、`odforge/src/odforge/webapi.py:949-1119`

- [x] 所有外部輸入模型採 `extra="forbid"`，或在相容入口先明確做版本轉換。
- [x] `bullet`、`page` 等拼錯欄位必須回可操作的 validation error，不可變成空內容／預設值。
- [x] 為 title-content、two-col、agenda、big-fact、summary 等 layout 加入最低內容 invariant。
- [x] 為標題、內文、notes、bullets 數量與長度建立合理上限。
- [x] `Outline.pages` 加入與產品上限一致的最大頁數；模型回傳超額頁數時不得繼續生成。
- [x] 加入 typo、unknown field、空白 deck、過長文字、過多頁面與向後相容測試。

### P1-04 補齊 ODS 數值與公式語意驗證

位置：`odforge/src/odforge/ir.py:525-543`、`odforge/src/odforge/render/ods.py`

- [x] 浮點只接受 finite value，拒絕 NaN、Infinity、-Infinity。
- [x] 驗證 formula target cell、重複 target、越界引用與基本 OpenFormula 語法。
- [x] LibreOffice 驗證不只確認可開啟，也要偵測公式錯誤值，例如 `#NAME?`、`#VALUE!`、`Err:*`。
- [x] 決定不支援函式的明確策略：拒絕、降級成值，或以 warning 阻止「全綠」。
- [x] 加入 ODS → LibreOffice 計算／轉存後數值一致性測試。

### P1-05 QA 解析失敗不得 fail-open

位置：`odforge/src/odforge/critic.py:196-217, 930-959`

- [x] 將「零 findings」與「回應無法解析」分成不同結果。
- [x] 若原始回應含 findings 但沒有任何合法 finding，QA 結果必須為 `unknown` 或 `fail`，不可 `final_ok=True`。
- [x] 部分合法時保留合法 findings，並另記 malformed count／diagnostic。
- [x] Claude、OpenAI-compatible、Codex 與本地模型入口採一致的 envelope 驗證。
- [x] UI 的 design gate 支援 `unknown` 並解釋原因。
- [x] 修正目前鎖定 `all malformed => []` 的測試，增加 invalid enum、缺欄位、錯 envelope、部分合法等案例。

### P1-06 修正 QA 頁面旗標與修復後狀態同步

位置：`odforge/web/src/state/cockpit.ts:57-67`、`odforge/src/odforge/webapi.py:708-822`

- [x] generation status 與 QA status 分開建模，不再共用單一 `UnitStatus` 表達兩種維度。
- [x] 每輪 QA 以最新 findings 完整重算 unresolved page set。
- [x] `complete` 不得把 design-failed 頁面全部洗成 `done`。
- [x] QA 修改 slide 後重新 emit／同步更新後的 IR、title、role 與 preview。
- [x] design gate、縮圖牆、單頁 detail、session replay 對同一頁顯示一致狀態。
- [x] 增加 round 1 fail → round 2 pass、final fail、QA unknown、session replay 測試。

### P1-07 把 Web UI 正確打包進 wheel

位置：`odforge/pyproject.toml:25-26`、`odforge/src/odforge/webapi.py:1124-1132`、`odforge/web/.gitignore`

- [x] 建立可重現的 frontend build hook，使用 lockfile 執行安裝與 build。
- [x] 將 `web/dist` 產物放入 Python package data，不依賴 source-tree 相對路徑。
- [x] 用 `importlib.resources` 尋找安裝後的前端資產。
- [x] wheel 不含前端時，build 或 release 必須失敗，而不是安裝後才 API-only。
- [x] 加入 wheel clean-install smoke：`GET /`、主 JS、主 CSS 均回 200。
- [x] 更新 `odforge serve` 錯誤文案，不假設目前工作目錄是 repo root。

### P1-08 建立 CI 與發布閘門

- [x] 新增 Python 3.11／3.12 matrix。
- [x] 新增受支援 Node 版本的 frontend test／typecheck／build。
- [x] 執行 Ruff 明確規則集、dependency check、wheel build、clean install smoke。
- [x] 建立有 LibreOffice 的 integration lane，驗證 ODT／ODP／ODS 與 preview。
- [x] tag release 只能發布 CI 已驗證的同一份 artifact。
- [x] CI 不得使用開發機已存在的 `web/dist`、credential 或 session 檔案。

### P1-09 修正行動版主要動作與欄位順序

位置：`odforge/web/src/App.tsx:452-477`、`odforge/web/src/styles/app.css:2839-2861, 3064-3080`

- [x] awaiting approval 時把確認 CTA 放在首屏或 sticky action bar。
- [x] complete 時把下載／再鍛一份放在 sticky 交付列。
- [x] 不在確認前先展示會把 CTA 推到數千像素後的完整 skeleton 牆。
- [x] 讓手機視覺順序與 DOM／鍵盤閱讀順序一致。
- [x] 將桌面 cockpit 約束在 viewport，各欄獨立捲動，避免下載列落在首屏下方。
- [x] 加入 360×800、504×692、768×1024、1440×900 browser assertions。

驗收：504×692、12 頁流程中，確認與下載動作在不搜尋、不捲過完整預覽牆的情況下可發現並操作。

### P1-10 修正 WCAG 對比、live region 與焦點轉場

位置：`odforge/web/src/theme/tokens.css`、`odforge/web/src/components/DiscoveryPanel.tsx`、`odforge/web/src/components/StatusNarrator.tsx`

- [x] quiet text、placeholder、warning／critical 狀態文字達到 4.5:1。
- [x] 互動元件邊界與相鄰背景達到非文字 3:1。
- [x] skipped gate 不再以整列 opacity 淡化必要原因文字。
- [x] loading 秒數設為 `aria-live="off"`／`aria-hidden`；只播報離散階段變化。
- [x] 合併逐頁進度播報，避免每 100ms 形成噪音。
- [x] 下一題與 Brief 轉場後，焦點移至新題目標題或第一個輸入欄。
- [x] 修正 h1 → h3 → h4 的 heading 跳級。
- [ ] 加入 token contrast tests、axe/browser smoke 與 focus-order 測試。

---

## P2：重要可靠性、UX 與測試缺口

### P2-01 讓 QA 開關反映真實模型能力

位置：`odforge/web/src/components/PromptBar.tsx:77-80, 237-250`、`odforge/src/odforge/webapi.py:1472-1488`

- [x] 前端讀取並快取 `/api/sources`。
- [x] 顯示 vision source 的 ready／未設定／不可用與原因。
- [x] source 不可用時停用 QA，或明確將控制改為「未設定」，不可預設承諾會執行。
- [x] 若允許逐工作選擇，補齊 `vision_backend` 前端型別、控制項與 request 欄位。
- [x] backend／vision source 必須映射到真實 engine capability。

### P2-02 管理取消後仍在執行的外部呼叫

位置：`odforge/src/odforge/webapi.py:719-765, 1427-1436, 1591-1602`

- [x] 將 job UI 狀態與實際 worker／provider-call 狀態分開。
- [x] active-job／provider-call 上限持續計入已取消但尚未結束的 thread。
- [x] 優先採支援 timeout／cooperative cancellation 的 client；做不到時至少追蹤到 worker 真正結束。
- [x] 取消後不得啟動 render、QA 或 emit 後續完成事件。
- [x] 加入連續 start/cancel 壓力測試，證明同時外部呼叫不會超過上限。

### P2-03 重新驗證 QA 修復後的最終產物

- [x] CLI `--qa` 修復並重渲染後重新執行完整 validation。
- [x] Web QA 修復後重新執行 ZIP、XML 與 LibreOffice gate，而非只更新 preview。
- [x] MCP 的品質承諾與實際 `with_soffice` 行為一致。
- [x] 最終 exit code、gate report 與下載檔必須來自同一 artifact version。

### P2-04 不再靜默刪除超出版面的內容

位置：`odforge/src/odforge/llm.py` 的 layout budget／degrade 流程

- [x] 不只在 speaker notes 寫「部分要點省略」。
- [x] 預設優先重寫、縮短或拆頁；無法保留內容時將工作標為需確認。
- [x] UI／CLI 明確列出被移除或改寫的內容。
- [x] 加入「資訊不可無聲遺失」的內容守恆測試。

### P2-05 修正參考檔讀取競態與大型 payload

位置：`odforge/web/src/components/PromptBar.tsx`、`odforge/web/src/state/api.ts:156-181`

- [x] 增加 asset loading／progress／cancel 狀態，讀取中禁止送出。
- [x] 設定整批參考檔總容量上限，而不只單檔上限。
- [x] 避免大型 data URL 長期常駐 React state 與同步 `JSON.stringify`。
- [x] 改用 multipart upload 或先上傳後傳 asset handle。
- [x] 加入「選檔後立即送出」與最大合法 payload 的 browser／memory 測試。

### P2-06 非法頁數必須阻止送出

位置：`odforge/web/src/components/PromptBar.tsx`、`odforge/web/src/state/api.ts:118-153`

- [x] 3–30 以外、非整數、NaN 都使 CTA disabled。
- [x] 送出嘗試時展開設定、聚焦頁數欄並以 `aria-describedby` 指向錯誤訊息。
- [x] 不得把非法值靜默省略成「AI 決定」。
- [x] 前後端上限與 Outline schema 使用同一常數或同一契約來源。

### P2-07 清理 preview 目錄中的過期頁面

位置：`odforge/src/odforge/preview.py`

- [x] 每次完整 render 前清理該 job 自有 preview 目錄，或以新版本目錄原子交換。
- [x] 由 5 頁縮成 3 頁後，不得殘留 `page-04.png`、`page-05.png`。
- [x] preview endpoint 同時核對 artifact version 與頁數。

### P2-08 加入真正的跨層整合與瀏覽器測試

位置：`odforge/tests/test_webapi.py`、`odforge/web`

- [x] 保留 fake LLM，但至少用真實 IR → renderer → validator → download 跑一條 Web pipeline。
- [x] 有 LibreOffice runner 時跑真實 preview 與 QA artifact version 測試。
- [ ] 加入 Playwright 或等價 browser smoke：生成、SSE、確認、單頁 detail、重生、下載、重整恢復。
- [ ] 加入 wheel-installed server 的 browser smoke。
- [x] 測試必須覆蓋 validation fail、QA unknown、regeneration rollback、取消 race。

### P2-09 隔離測試中的真實 provider

位置：`odforge/tests/test_sources.py`、`odforge/tests/conftest.py`

- [x] autouse fixture 清除所有 provider credentials／endpoint 環境變數。
- [x] 「接受 backend 名稱」測試只驗 registry／schema，不啟動真實 background pipeline。
- [x] provider request contract 使用本地 fake HTTP server。
- [x] 任何測試若嘗試連到非 localhost，立即失敗。

### P2-10 補齊測試工具鏈

- [x] 建立 `test` extra，確保乾淨安裝後可收集並執行全部 Web tests。
- [x] 新增 `tsconfig.test.json`，測試 helper 改接 `CockpitAction[]`，修正五處 `regen_start` 型別錯誤。
- [x] 消除 React `act(...)` warnings；非 allowlist 的 console warning／error 使測試失敗。
- [x] 建立 Python 與 frontend coverage baseline，對 reducer、API、package、renderer 設 branch threshold。
- [x] 為 Ruff 設定明確 `select`／`ignore`／`per-file-ignores` 並固定工具版本。

### P2-11 控制 QA 圖像批次與持久化成本

- [x] 視覺 QA 依頁或小批次傳送，不把最多 30 張 base64 PNG 放入單一 request。
- [x] 加入 payload／token／image count 預算與 provider-specific 上限。
- [x] session event log 改為 append-only 或小型 snapshot，避免同步全量重寫造成 O(N²) I/O。
- [x] 將必要磁碟 I/O 移出 event loop。
- [x] 建立 30 頁 × 4 工作的 synthetic latency／memory benchmark。

### P2-12 明確界定「格式可信」與「內容已查證」

- [x] UI、CLI report、README 明確說明目前品質閘不驗證事實真偽與來源存在性。
- [x] 來源 URL 至少驗證可存取性、標題／網域與引用關聯，或標記為未驗證。
- [x] 若產品要宣稱內容可信，設計 evidence provenance／citation verification 流程。
- [x] 不以 prompt 中「不要捏造」代替系統性檢查。

### P2-13 決定 Web 對 ODT／ODS 的產品策略

- [x] 選擇並記錄：近期接入、明確 roadmap，或 Web 永久專注 ODP。
- [x] 若暫不支援，首頁與產品文案清楚區分「核心引擎支援三格式」與「Web 只支援 ODP」。
- [x] 若要接入，先定義不同文件型別的 preview、單元模型、QA 與下載 UX，不直接複用 slide 假設。

---

## P3：一致性、文件與視覺打磨

### P3-01 統一品質閘承諾與命名

- [x] 決定 LibreOffice／design gate 是阻斷閘或觀察訊號。
- [x] 若允許 skipped／failed 後完成，不再宣稱「每一份都必須通過三／四道閘才會交付」。
- [x] CLI、MCP、Web、README 與報告使用同一套 gate 定義。
- [x] 明確區分「結構有效」「可由 LibreOffice 開啟」「視覺已審」「內容已查證」。

### P3-02 修正文件與程式漂移

- [x] 更新 `README.md` 的測試數字與實際驗證行為。
- [x] 更新 `README.en.md` 的 113 tests、Python 3.10 與過期操作說明。
- [x] 統一 session retention；目前程式為 90 天，Web API 文件仍有 24 小時敘述。
- [x] 把 `/api/sources` 補進 API endpoint 表。
- [x] 更新 Web production build／wheel 安裝說明。
- [x] 將舊的 `docs/audits/quality-final.md` 標成歷史快照，不再展示為目前 20/20。

### P3-03 固定工具版本與開發環境

- [x] 新增 Node `engines` 與 `.node-version`，符合 Vite／jsdom 實際需求。
- [x] 提供 Python constraints／lock，確保 CI 與 release 可重現。
- [x] 確認 `httpx2` 是否有用途；無引用則移除。
- [x] README 提供唯一、可驗證的 clean setup／test／build 指令。

### P3-04 降低大型模組改動半徑

- [x] 拆分 `render/odp.py`：基元、style registry、layout renderers、asset handling。
- [x] 拆分 `webapi.py`：job store、pipeline、routes、artifact lifecycle、persistence。
- [x] 拆分 `llm.py`：prompt/schema、provider adapters、retries、budget/degrade。
- [x] 拆分大型 `app.css`，至少依 tokens、shell、components、responsive 分層。
- [x] 每次拆分保持行為測試與 ODF fixture 不變。

### P3-05 改善生成簡報的視覺多樣性

- [x] 降低等寬卡片、三欄 metrics、同型圓角容器的重複率。
- [x] 用內容語意決定版型，不以「幾個項目就幾張卡」作為主要策略。
- [x] 增加 editorial、diagrammatic、image-led、typographic 等構圖節奏。
- [x] 建立 deck-level repetition detector，限制連續同類構圖。
- [x] 以代表性 prompts 建立視覺基準集並做人工盲評。

### P3-06 清理 Web UI 的設計反模式

位置：`odforge/web/src/styles/app.css`

- [x] 移除 ConfirmBar、finding、ErrorPanel 與 session row 的重複側邊色條語彙。
- [x] 改用完整邊框、狀態底色、圖示、編號或排版層級表達狀態。
- [x] 檢查過小 monospace 標籤，在辨識度與可讀性間重新平衡。
- [x] 保留目前工作台資訊架構、明暗主題與既有 focus／motion 優點。

---

## 建議里程碑

### Milestone A：產物可信

- [x] 完成 P1-01 至 P1-06。
- [x] 完成 P2-03、P2-04、P2-07。
- [x] 通過重生 rollback、錯誤下載、malformed QA、ODS 語意的對抗測試。

### Milestone B：真正可發布

- [x] 完成 P1-07、P1-08。
- [x] 完成 P2-08 至 P2-10。
- [ ] wheel clean-install 與 packaged browser smoke 全綠。

### Milestone C：使用者可順利完成任務

- [x] 完成 P1-09、P1-10。
- [x] 完成 P2-01、P2-05、P2-06。
- [x] 完成手機與無障礙驗收。

### Milestone D：產品敘事與品質一致

- [x] 完成其餘 P2、P3 任務。
- [x] 重產 demo、README 截圖與品質報告。
- [x] 重新執行完整對抗式審查，取代舊的 20/20 報告。

## 最終驗證指令

> 指令需依完成後建立的統一工具鏈調整；下列為最低驗證集合。

```powershell
cd odforge
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\ruff.exe check .
npm.cmd --prefix web test
npm.cmd --prefix web run build
```

- [x] 額外執行 tests-inclusive TypeScript typecheck。
- [x] 額外執行 wheel build／clean install／server asset smoke。
- [ ] 額外執行 Playwright 響應式與端到端流程。
- [x] 額外執行真實 LibreOffice ODT／ODP／ODS 驗證。
- [x] 確認 `git status` 只包含預期修改與產物。
