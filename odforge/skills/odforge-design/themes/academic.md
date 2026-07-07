# academic — 學術藍 + 暖白

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["academic"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：學術藍配暖白底、serif 標題撐場面。適合論文口試、研究報告、教學講義、
政府與學會場合——任何需要沉穩、可信、耐讀氣質的簡報。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #FBF9F4 | 頁面背景：暖白，不刺眼、久看不累 |
| surface | #EFEADD | 卡片／色塊底：比 bg 深一階的米色 |
| text | #1F2733 | 主要內文：近黑的深藍灰 |
| muted | #5B6470 | 次要文字：註解、attribution、caption |
| accent | #1A4B8C | 強調色：學術藍，用在標題底線、highlight 長條、關鍵字 |
| title_color | #1F2733 | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Serif TC** — serif 帶來學術份量。
- 內文（body）：**Noto Sans TC** — sans 保持條列清爽好讀。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：研究題目 + 作者／單位。serif 大標最能定調 |
| agenda | 大綱頁：口試／長報告開場，先給評審一張路線圖 |
| title-content | 主力內頁：研究背景、方法、每一條列即一個論點 |
| section | 章節分隔：進入「文獻回顧」「研究設計」等大段落時 |
| two-col | 並列說明：假設 vs 觀察、理論 vs 資料 |
| comparison | 對照表態：本研究 vs 既有做法，左右各一欄優劣 |
| big-fact | 關鍵數據：把核心發現放大成一個數字（如樣本數、顯著性）|
| quote | 引述：引用文獻原句或受訪者原話，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，最多 8 條長條，右欄放洞見 |
| closing | 結尾：結論一句話 + 致謝／Q&A |
