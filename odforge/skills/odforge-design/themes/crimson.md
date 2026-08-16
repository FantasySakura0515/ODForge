# crimson — 學院紅 + 暖白

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["crimson"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：暖白底、近黑棕墨字、學院紅強調，serif 標題撐出典禮與人文的重量。適合校慶、系所評鑑、人文社會領域的研究發表、榮譽榜與畢業成果展——任何要莊重、有歷史感，又不能顯得冰冷的場合。

與 **academic** 的區隔：academic 是學術藍，氣質偏理性、學院官方；crimson 換成飽和的學院紅，情緒更暖、更有儀式感。理工題目用 academic，人文題目與典禮場用 crimson。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #FCF8F7 | 暖白：帶一點紅的米白紙感 |
| surface | #F1E5E3 | 卡片／色塊底：淺陶紅灰 |
| text | #2A1D1C | 主要內文：近黑的暖棕墨 |
| muted | #6B5651 | 次要文字：註解、attribution、caption |
| accent | #A3212F | 強調色：學院紅，用在標題底線、highlight 長條、關鍵字 |
| title_color | #2A1D1C | 標題色：與 text 同（此主題不另外覆寫）|

對比：text/bg、accent/bg、muted/bg 皆已通過 WCAG 門檻
（text/bg ≥ 4.5、accent/bg ≥ 3.0、muted/bg ≥ 3.0），surface 當底時同樣過關。
若你另外覆寫 DesignSpec 調色盤，務必自行重算對比。

## 字級刻度（pt，standard 密度）

| role | pt | 用途 |
| --- | --- | --- |
| display | 54 | 封面主標、big-fact 數字 |
| h1 | 28 | 內頁標題、section 分隔標題 |
| body | 18 | 條列與內文 |
| caption | 13 | 註解、資料來源、attribution |

## 字型

- 標題（display）：**Noto Serif TC** — 襯線筆畫在暖白底上有印刷質感，撐得住典禮語氣。
- 內文（body）：**Noto Sans TC** — 內文回到 sans，長段落仍好讀。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：校名／系所名大字，學院紅點一個關鍵詞 |
| agenda | 大綱頁：正式場合開場，先給聽眾路線圖 |
| title-content | 主力內頁：一頁一個重點，條列克制 |
| section | 章節分隔：切換「沿革」「成果」「展望」等大段落 |
| two-col | 並列：理念 vs 實踐、現況 vs 目標 |
| comparison | 對照：改制前 vs 改制後，紅色標出關鍵欄 |
| big-fact | 關鍵數據：招生人數、獲獎數、就業率——一個大數字最有記憶點 |
| quote | 引述：師長題詞、校友回饋，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，highlight 用學院紅 |
| closing | 結尾：一句期許或行動邀請 + 聯絡方式 |
