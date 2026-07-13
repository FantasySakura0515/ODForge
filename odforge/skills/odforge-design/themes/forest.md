# forest — 森綠 + 暖奶油

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["forest"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：暖奶油底配深森綠強調、serif 標題——2025/26 最主導的專業配色之一。森綠傳達
穩定、自然、健康；奶油比純白更暖、更 editorial；serif 撐出份量。適合永續／環境／
生態題目、健康與生活風格品牌、文化與人文報告、質感型提案——任何要沉靜耐讀、帶點
編輯氣質的簡報。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #F6F2E9 | 頁面背景：暖奶油，比純白溫潤、久看不累 |
| surface | #E9E1CE | 卡片／色塊底：比 bg 深一階的砂色 |
| text | #23291F | 主要內文：近黑的深墨綠 |
| muted | #5E5849 | 次要文字：註解、attribution、caption |
| accent | #2C6E49 | 強調色：森綠，用在標題底線、highlight 長條、關鍵字 |
| title_color | #23291F | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Serif TC** — serif 帶來編輯質感與自然份量。
- 內文（body）：**Noto Sans TC** — sans 保持條列清爽好讀。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：題目大字 + 副標，奶油底大量留白，serif 定調 |
| agenda | 大綱頁：較長的報告開場，先給讀者路線圖 |
| title-content | 主力內頁：一頁一個論點，條列精簡 |
| section | 章節分隔：進入「背景」「發現」「行動」等大段落時 |
| two-col | 並列：問題 vs 對策、現況 vs 願景 |
| comparison | 對照：永續做法 vs 傳統做法，森綠標出優勢欄 |
| big-fact | 關鍵數據：減碳量、覆蓋率、參與人數——一個大數字最有記憶點 |
| quote | 引述：受訪者原話、報告原句，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，最多 8 條長條，highlight 用森綠 |
| closing | 結尾：一句收束 + 行動呼籲／致謝 |
