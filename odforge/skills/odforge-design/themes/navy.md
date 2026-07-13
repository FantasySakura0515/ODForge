# navy — 海軍藍 + 冷白

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["navy"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：冷白底、深海軍藍墨字、電光藍強調——最通用、最不會出錯的企業配色。
乾淨的 sans 撐出 SaaS／科技感。適合公司內外部正式簡報、投資人與董事會、產品發表、
高階主管場——任何要俐落、可信、專業的商務場合。

與 **academic** 的區隔：academic 是暖白 + serif + 學術藍，氣質偏學院／政府；navy 是
冷白 + sans + 更深的海軍藍 + 電光藍強調，氣質偏企業／科技。選題偏商務就用 navy。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #FAFBFD | 頁面背景：帶一絲藍的冷白 |
| surface | #EAEEF5 | 卡片／色塊底：淺藍灰 |
| text | #16233F | 主要內文：近黑的深海軍藍墨 |
| muted | #55617A | 次要文字：註解、attribution、caption |
| accent | #2563EB | 強調色：電光藍，用在標題底線、highlight 長條、關鍵字 |
| title_color | #16233F | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Sans TC** — 冷白底上乾淨俐落，貼合企業科技感。
- 內文（body）：**Noto Sans TC** — 同家族，靠字級與電光藍強調分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：公司／產品名大字，電光藍點一個關鍵詞 |
| agenda | 大綱頁：正式簡報開場，先給聽眾路線圖 |
| title-content | 主力內頁：一頁一個重點，條列精簡到只剩關鍵詞 |
| section | 章節分隔：切換「市場」「產品」「財務」等大段落時 |
| two-col | 並列：問題 vs 解法、功能 vs 效益 |
| comparison | 對照：我們 vs 競品，電光藍標出優勢欄 |
| big-fact | 關鍵數據：市佔、營收、成長率——一個大數字最有記憶點 |
| quote | 引述：客戶好評、媒體評語，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，最多 8 條長條，highlight 用電光藍 |
| closing | 結尾：一句 call to action + 聯絡方式 |
