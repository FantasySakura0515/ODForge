# teal — 企業青綠 + 冷白

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["teal"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：冷白底配「Transformative Teal」青綠強調——2026 年度代表色。青綠承襲藍的
沉穩可信、又帶綠的清新，冷靜而不冷漠。適合企業內部簡報、SaaS／產品說明、顧問提案、
健康醫療、永續與 ESG——任何要「calm, clear, credible」、給主管或客戶看的場合。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #F4F7F7 | 頁面背景：帶一絲青的冷白，比純白柔和 |
| surface | #E4EDEC | 卡片／色塊底：淺青灰 |
| text | #1B2A2C | 主要內文：近黑的深青灰 |
| muted | #556463 | 次要文字：註解、attribution、caption |
| accent | #0F766E | 強調色：青綠，用在標題底線、highlight 長條、關鍵字 |
| title_color | #1B2A2C | 標題色：與 text 同（此主題不另外覆寫）|

對比：text/bg、accent/bg、muted/bg 皆已通過 WCAG 門檻
（text/bg ≥ 4.5、accent/bg ≥ 3.0、muted/bg ≥ 3.0）。若你另外覆寫 DesignSpec
調色盤，務必自行重算對比。

## 字級刻度（pt，standard 密度）

| role | pt | 用途 |
| --- | --- | --- |
| display | 54 | 封面主標、big-fact 數字 |
| h1 | 28 | 內頁標題、section 分隔標題 |
| body | 18 | 條列與內文 |
| caption | 13 | 註解、資料來源、attribution |

## 字型

- 標題（display）：**Noto Sans TC** — 冷白底上乾淨俐落，貼合企業現代感。
- 內文（body）：**Noto Sans TC** — 同家族，靠字級與青綠強調分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：主題大字，青綠只點一個關鍵詞，冷白留白顯專業 |
| agenda | 大綱頁：企業簡報／顧問提案開場，先給客戶路線圖 |
| title-content | 主力內頁：一頁一個重點，條列精簡 |
| section | 章節分隔：切換「現況」「方案」「效益」等大段落時 |
| two-col | 並列：現況 vs 目標、成本 vs 效益 |
| comparison | 對照：我們 vs 現行做法，青綠標出優勢欄 |
| big-fact | 關鍵數據：ROI、節省成本、成長率——一個大數字最有記憶點 |
| quote | 引述：客戶好評、專家背書，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，最多 8 條長條，highlight 用青綠 |
| closing | 結尾：一句 call to action + 聯絡方式 |
