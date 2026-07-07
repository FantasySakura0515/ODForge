# ODForge 前端設計 Spec:漸進式鍛造控制台

> **狀態:** 設計定案(brainstorming 產出),待實作計畫(writing-plans)。
> **日期:** 2026-07-07 · **對應後端:** v2 Phase 18 Web API(`docs/todo.md`)
> **視覺真理來源(可互動):** Artifact「ODForge 前端 · 一個產品兩種主題」<https://claude.ai/code/artifact/5c2e1028-3996-449e-a3ef-03b1887c8f03>

---

## 1. 背景與定位

ODForge v2 的核心是**一個確定性引擎、四張臉**(CLI / MCP / Web 前端 / agent skill),共用同一顆心臟(IR → 渲染 → 四道閘 → 原生 ODF)。`docs/todo.md` Phase 18 只交付**後端與 API 契約**,並明訂「前端由參賽者自行設計」。本 spec 補上這塊留白。

**前端的角色:** 它不新增任何引擎能力,而是把管線**變得看得見、可操控**。CLI 給開發者、MCP 給 AI 助理、skill 給外部 agent——**Web 前端是唯一給「不會用終端機的人類」(老師、公務員)的那張臉**。

**主要定位(已與使用者確認):** 工具優先、也要能 demo。設計語言走乾淨、可信、低摩擦;骨架要能在決賽現場把「四道閘 / AI 管內容·引擎管格式 / 唯一做原生 ODF」的故事講清楚。

**部署前提:** 本機執行的單人工具(`odforge serve` → localhost、CORS 全開、job 存 `%TEMP%`、**無帳號、無登入、無多租戶**)。前端因此省掉整層 auth/權限。

---

## 2. 設計原則(產品哲學決定的「反設計」)

前端性格被系統哲學鎖死,這是刻意的差異化:

| 系統定案(來自 todo.md) | 前端的直接後果 |
|---|---|
| 不做線上編輯器(編輯器 = LibreOffice / MODA 工具) | **沒有畫布、沒有拖拉、沒有屬性面板。** 這是「控制台」不是「編輯器」 |
| LLM 不碰座標,變化來自 design token | 使用者能碰的只有:**大綱、配色候選、mode、單元重生指令**——不是位置與字級 |
| 引擎保證格式 | 四道閘**做成主視覺**,不藏起來 |
| 巨頭全輸出 PPTX | 每個角落都標示**原生 ODF**——與 Gamma/Canva 一眼的差別 |

一句話:**別人有的它故意沒有(畫布);別人沒有的它大方秀(四道閘、逐張點亮、原生 ODF)。** 它是一間「鍛造控制室」,呼應品牌名「文鍛」。

---

## 3. 互動模型:漸進式控制台(已確認)

單一畫面,三區(左大綱 / 中預覽舞台 / 右四道閘)+ 頂欄 + 底部敘事條。**它們是一個一個長出來的,不是一開始就全在**——新手被漸進揭露引導,老手一眼看盡全局,demo 時最有戲劇張力。

(捨棄的替代方案:純線性 wizard——無法一眼看盡、demo 弱;對話式 chat——流程其實是固定管線,強套對話反而模糊四道閘的教事。)

---

## 4. 視覺語言:一個產品,兩種主題(已確認)

**不是三選一,而是一個產品兩種心情**:淺色 = 學術淨白(可信、工具優先)、深色 = 控制室(工程儀表感)。共用同一套骨架、字級、版型與動畫,只換光線與重點色。跟隨檢視器/系統的 light/dark;`data-theme` 覆寫 media query 兩個方向。

**統一細節(兩主題共通):**
- 字標一律**襯線**(文件感);頁碼 / 閘門代號 / 進度 / 讀數一律**等寬字**;UI 內文用系統無襯線(CJK:微軟正黑體 / Noto Sans TC)。
- 藍圖網格只在**深色**出現(控制室特徵);淺色收斂為細線紙感。
- 重點色只用於裝飾/強調與互動態,不做大面積內文色(呼應引擎的 accent 紀律)。

### 4.1 Design tokens(CSS 變數,實作直接搬)

```
                        淺色 · 學術淨白        深色 · 控制室
--page-bg   頁底         #eceae4              #070a0e
--bg        控制台底      #f6f5f2              #0b0e14
--surface   面           #ffffff              #12161f
--surface2  次面         #eef1f7              #171d29
--text      主文         #191d24              #d6dee8
--muted     次文          #6b7079              #6d7787
--line      分隔線        #e3e0d9              #212a38
--accent    重點色        #234e9e(學術藍)     #35d0ba(亮青)
--accent2   次重點        #8a6d1f              #6aa9ff
--pass      語意·通過     #2e8b57              #4ade80
--warn      語意·警告     #b6841c              #fbbf44
--crit      語意·錯誤     #c0453f              #f87171
--grid-op   藍圖網格透明度  0                    .5
```

語意色(pass/warn/crit)與 accent 分離——嚴重度靠形(色條、脈動、spinner)也靠色,一眼可掃。

### 4.2 字體角色

| 角色 | 變數 | 淺色 | 深色 |
|---|---|---|---|
| 字標/標題 | `--f-brand` `--f-title` | Georgia / Noto Serif TC(襯線) | 標題轉無襯線(儀表感),字標仍襯線 |
| UI 內文 | `--f-ui` | 微軟正黑體 / Noto Sans TC | 同 |
| 資料/讀數 | `--f-mono` | Consolas / ui-monospace | 同 |

> 註:Artifact CSP 禁外部字型,mockup 用系統字族靠 weight/字距/尺度做出個性。實際 React 專案可自架字型檔(Noto Serif/Sans TC),但仍以系統 CJK 為 fallback(使用者機器有微軟正黑體)。

### 4.3 版型縮圖(預覽格內)

mini-slide/mini-page 用**真的 deck 配色**渲染(不是灰盒),順便把引擎版型多樣性秀出來:標題左側縱向短棒、分節反白頁、big-fact 大數字、chart 長條、closing 反白。deck 配色跨主題**不變**(生成的文件本身長一樣;變的是工具的光)。深色控制台裡白色縮圖像「深色編輯器上的白投影片」,對比極佳。

---

## 5. 版面:四區 shell

```
grid-areas:  "top  top    top"
             "left center right"
             "foot foot   foot"
columns: 248px 1fr 262px ; rows: auto 1fr auto
```

- **top 頂欄:** 字標「文鍛 ODForge」· 縮起的 prompt 條(生成後細條化)· 後端/離線 chip。
- **left 大綱軌:** 型別自適應大綱 · 🎨 色盤色卡 · mode/theme tag。確認站時整條可編輯。
- **center 預覽舞台:** 主角。單元網格,逐格點亮;點一格放大 + 單元重生。
- **right 四道閘:** 三道格式閘依序打勾;第四道設計閘展開成 QA 面板。
- **foot 敘事條 + 下載塢:** 一行敘事(承載「鍛造」語氣)+ 完成後浮出下載。

**響應式:** ≤840px 單欄堆疊(top → center → left → right → foot),預覽網格降為 2 欄。桌面優先(本機工具)。寬內容各自 `overflow` 不撐破頁面。

**無障礙:** 鍵盤焦點可見態;`prefers-reduced-motion` 時跳過點亮動畫直接到完成態;色 + 形雙編碼嚴重度。

---

## 6. 三型別適配(.odp / .odt / .ods 都收)

同一骨架,單元語意隨型別變。**統一抽象:預覽一律走 `odf→PDF→PNG`(後端 `preview.py` 對三型別都通),所以「逐單元點亮」對三型別都成立——單元只是從 slide 變 page/sheet。**

| 區域 | 簡報 .odp | 文書 .odt | 試算表 .ods |
|---|---|---|---|
| 空台 | 輸入框下三個型別分頁,預設簡報;可由 AI 從 prompt 推斷(「成績表」→ ods) | | |
| 大綱軌 | 版型大綱(封面/議程/分節/圖表) | 章節大綱(H1/H2 階層) | 工作表 ＋ 欄位/公式意圖 |
| 預覽舞台 | 16:10 slide 網格 | A4 直式頁縮圖 | 工作表分頁(寬版) |
| 單元 | slide | page(渲染頁) | sheet |
| design token | 完整(色盤＋字體) | 完整(封面/標題色＋字體) | 輕量(表頭色＋字體) |
| 單元重生 | 單張重生 | 單章/單頁重生 | 單工作表重生 |
| 四道閘 | 全部適用 | 全部適用 | ①②③適用;④設計閘對表格較輕 |

型別以一個 `DocType = "odp" | "odt" | "ods"` 貫穿狀態機與元件 props,決定大綱渲染器、預覽格長寬比、單元名詞(張/頁/表)。

---

## 7. 狀態集(6 狀態)與 SSE 對映

前端本質是**被 SSE 驅動的狀態機**,與 Phase 18 契約 1:1 對齊。

| 狀態 | 觸發(SSE / 動作) | 畫面 |
|---|---|---|
| **0 空台 Empty** | 初始 | hero + 輸入框 + 型別分頁 + 進階抽屜(mode / theme / 用我的範本→樣式抽取 / 後端+離線)。其餘全隱藏 |
| **1 大綱浮現 Outline** | `outline` | 左軌滑入(大綱 + 🎨色卡即時渲染);中間長出 N 個空骨架格 |
| **· 確認站 Await** | `awaiting_approval`(interactive) | 浮出確認條:`[就地編輯] [換配色] [確認,開始生成 →]` |
| **2 逐單元點亮 Generating** | `unit_done{n}` → `preview_ready{n}` | 骨架填字 → 換真 PNG,一格一格亮;三道格式閘打勾 |
| **3 設計閘 QA** | `qa_round{round,findings}` | 右側列 findings;被標記單元脈動→重生;前後對照視後端是否保留修正前 PNG(見 §14.3) |
| **4 完成 Complete** | `complete{download_url}` | 收斂成安靜完成態:大下載鈕(ODF 徽章)+ QA 報告摘要 |
| **錯誤 Error** | `error{message,stage}` | 就地標在對應區塊(非彈窗),寫明哪一閘/哪一段掛了 + 重試 |

核心動作對映 REST:輸入框 = `POST /generate`;確認條 = `POST /outline`;單元重生指令 = `POST /units/{n}/regenerate`;下載 = `GET /download`。**沒有任何按鈕是憑空發明——全部對得回契約。**

---

## 8. 元件樹

```
<App>                          // 型別 + 主題 + reducer 根
├─ <TopBar>                    // 字標 · PromptBar(縮) · 後端/離線 chip
│  └─ <PromptBar>              // textarea · 型別分頁 · 進階抽屜(mode/theme/範本/後端)
├─ <OutlineRail>               // 型別自適應
│  ├─ <PaletteSwatches>        // DesignSpec 五色卡
│  ├─ <OutlineList>            // OutlineRow[](確認站時可編輯/排序)
│  └─ <OutlineFoot>            // mode/theme/單元數 tag
├─ <PreviewStage>
│  ├─ <StageHead>              // 進度 n/N
│  ├─ <UnitGrid>               // UnitCell[](5 態:骨架/填充/點亮中/被標記/重生中)
│  │  └─ <UnitThumb>           // 真 deck 配色縮圖(型別決定長寬比)
│  └─ <UnitDetail>             // 放大 + <RegenerateControl>(指令→單元重生)
├─ <GateRail>
│  ├─ <GateRow>×4              // zip/xml/LibreOffice/設計閘(pending/pass/fail/active)
│  └─ <QAPanel>                // FindingRow[](頁碼·issue·severity·fix_hint)+ 前後對照 + 輪次
├─ <FootBar>
│  ├─ <StatusNarrator>         // 一行敘事,承載「鍛造」語氣
│  └─ <DownloadDock>           // 下載 .od{p,t,s} + 用 LibreOffice 開 + QA 摘要
└─ <ConfirmBar>                // 確認站浮條(狀態 1· 才出現)
```

---

## 9. 前端狀態機(reducer)

單一 `useReducer` 吃 SSE 事件、吐狀態;每個事件是純轉移。型別草案:

```ts
type DocType = "odp" | "odt" | "ods";
type UnitStatus = "skeleton" | "filling" | "preview" | "flagged" | "regen" | "done";
type GateId = "zip" | "xml" | "libreoffice" | "design";
type GateStatus = "pending" | "active" | "pass" | "fail";

interface Unit { n: number; role: string; title: string; ir?: unknown; previewUrl?: string; status: UnitStatus; }
interface Finding { unit_no: number; issue: string; severity: "error" | "warn"; fix_hint: string; }
interface CockpitState {
  phase: "empty" | "outline" | "await" | "generating" | "qa" | "complete" | "error";
  docType: DocType;
  jobId?: string;
  outline?: { design: DesignSpec; mode: "detailed" | "presenter"; units: OutlineRow[] };
  units: Unit[];
  gates: Record<GateId, GateStatus>;
  qaRounds: { round: number; findings: Finding[] }[];
  downloadUrl?: string;
  error?: { message: string; stage: string };
}
```

事件轉移(節錄):`outline` → phase=outline + 建 units 骨架;`awaiting_approval` → phase=await;`unit_done` → units[n].status=filling/ir;`preview_ready` → status=preview/previewUrl;三道閘於全單元完成後依序 pass;`qa_round` → 推 findings + 標記 units;`complete` → phase=complete + downloadUrl;`error` → phase=error。SSE 用原生 `EventSource`;斷線重連後打 `GET /jobs/{id}` 取狀態快照重建。

---

## 10. API 契約 + 需後端配合的一般化

前端照 Phase 18 契約設計。因**三型別都收**,需要一個小幅一般化(回饋給後端 spec):

- 詞彙 `slide` → `unit`(涵蓋 slide/page/sheet):
  - SSE `slide_done` → **`unit_done`**;`preview_ready` 不變(已是 `{n,url}`)。
  - REST `POST /jobs/{id}/slides/{n}/regenerate` → **`/units/{n}/regenerate`**。
- 其餘契約不變:`/generate`(加 `doc_type?` 欄位,預設 odp 或由後端從 prompt 推斷)、`/outline`、`/preview/{n}.png`、`/download`、`/jobs/{id}` 快照、SSE `outline`/`awaiting_approval`/`qa_round`/`complete`/`error`。
- `Outline`/`Finding` 的 `slide_no`/`pages` 欄位一併改為 `unit_no`/`units`,或後端保留別名。

> 這是唯一需要後端配合的變更;若後端先只實作 odp,前端可用 feature flag 隱藏 odt/ods 分頁,契約向前相容。

---

## 11. 技術棧與專案結構(已確認)

**React + Vite + TypeScript + 原生 CSS 變數(design tokens)。** 刻意不用 Tailwind/shadcn——客製辨識度(學術淨白/控制室)會被元件庫拉回通用 AI 味;§4.1 的 token 已是 CSS 變數可直接搬。SSE 用原生 `EventSource`;狀態用 `useReducer`(對映後端狀態機);無需路由(單屏)。

```
odforge/web/                    # 前端獨立子專案(不污染 Python 套件)
├── index.html
├── vite.config.ts
├── package.json
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── state/            # reducer + SSE 客戶端 + 型別(對映 pydantic 契約)
│   │   ├── cockpit.ts
│   │   ├── sse.ts
│   │   └── api.ts
│   ├── components/       # §8 元件樹,一元件一檔
│   ├── theme/tokens.css  # §4.1 兩主題 token
│   └── styles/           # 各元件 CSS(或 CSS Modules)
└── dist/                 # build 產物,由 `odforge serve` 掛靜態檔一起吐出
```

`odforge serve` 啟動 FastAPI 時掛載 `web/dist` 為靜態根,demo 一行指令(前後端同起)。開發期前端 `vite dev` proxy 到 `:8000`。

---

## 12. MVP 分階(交付切分)

**三型別(.odp/.odt/.ods)是承諾範圍,不是選配**(已與使用者確認)。分階是**技術排序**不是範圍取捨:骨架與狀態機從第一天就型別無關(`DocType` 貫穿),F1 先做 .odp 這個垂直切片(逐單元點亮的戲劇性最強、後端兩段式生成也最成熟),.odt/.ods 之後**重用同一個 shell**,只補大綱渲染器、預覽長寬比、單元名詞——增量而非重寫。

| 階段 | 內容 | 決賽意義 |
|---|---|---|
| **F1 核心(型別無關 shell,先接 .odp)** | 空台輸入 → SSE 逐單元點亮 → 三道格式閘 → 下載 | **決賽 wow 的最小可展示**——一句話變一份保證合規的文件,眼睛看得到管線 |
| **F2 人在迴圈** | 大綱確認站(就地編輯 / 換配色)+ 單元重生 | 「人在迴圈」故事;可控可信 |
| **F3 設計閘 QA** | 設計閘啟用 + QA 面板 findings(前後對照見 §14.3) | 第四道閘完整實錄——最強差異化 |
| **F4 全型別 + 進階** | .odt/.ods 型別適配(補渲染器/長寬比)· 樣式抽取入口(用我的範本)· 後端/離線設定 · 錯誤態 | 三型別到齊;樣式抽取殺手功能;TAIDE/離線加分 |

依賴:F1 需後端 Phase 18.1(SSE)可用;F3 需 Phase 16(設計閘);F4 需 Phase 17(樣式抽取)。前端可在後端對應 Phase 完成後接續,或先以 mock SSE(固定 fixture 事件流)獨立開發 F1–F3 的 UI。

---

## 13. 明確不做

- **線上編輯器 / 畫布 / 拖拉排版**——違反產品哲學(編輯交給 LibreOffice/MODA)。
- **HTML 中間層預覽**——預覽只走 `odf→PDF→PNG`,不另建 HTML 渲染器(conversion drift 是敵人)。
- **帳號 / 登入 / 多租戶 / 雲端儲存**——本機單人工具。
- **像素級手調字級與座標**——使用者只碰大綱/配色/mode/重生指令。
- **歷史紀錄 / 專案管理**——MVP 外(可列 later)。

---

## 14. 開放問題(待實作前或與後端敲定)

1. **型別推斷責任:** 空台預設簡報 + 手動分頁是基準;「AI 從 prompt 推斷 doc_type」放後端 `/generate` 或前端規則?建議後端(集中一處)。
2. **確認站編輯粒度:** 大綱可改「標題/gist/版型/順序」到什麼程度?建議 F2 先做「改標題 + 刪頁 + 換配色候選」,排序與版型改列 later。
3. **QA 前後對照素材:** 需要後端在重生時保留「修正前 PNG」嗎?若否,前端只顯示「修正後 + finding 文字」。建議 F3 先不留前圖,降後端負擔。
4. **樣式抽取上傳:** `--from-template` 的 Web 版是 `POST /generate` 帶檔案(multipart),需後端補一個上傳端點——列 F4 與後端協調。

---

## 15. 驗收(對應原則)

- 反設計守住:無畫布/拖拉/屬性面板 ✓ 使用者只碰大綱/配色/mode/重生 ✓
- 四道閘為主視覺、逐單元點亮為中央戲劇 ✓
- 一產品兩主題:light/dark 皆完整、`data-theme` 覆寫 media 兩向、對比合規 ✓
- 三型別共用骨架、預覽統一走 PNG ✓
- 每個 UI 動作對得回 Phase 18 契約(含 slide→unit 一般化)✓
- 技術棧:React+Vite+TS+原生 CSS token,可由 `odforge serve` 一起吐出 ✓
