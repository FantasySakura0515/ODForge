# sky — 天青 + 冷白

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["sky"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：冷白底、深藍墨字、中藍強調——教學與說明場的安全牌。字重與對比都溫和，投影在老舊投影機上也不容易糊。適合課堂教學、招生說明會、家長座談、公部門宣導。

與 **navy** 的區隔：navy 的電光藍偏企業科技感，銳利；sky 的中藍更平實、更親切，是講給非專業聽眾聽的顏色。商務場用 navy，教學場用 sky。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #F7FAFD | 頁面背景：帶一絲藍的冷白 |
| surface | #E6EEF7 | 卡片／色塊底：淺天藍灰 |
| text | #152230 | 主要內文：近黑的深藍墨 |
| muted | #526375 | 次要文字：註解、attribution、caption |
| accent | #0B6BA8 | 強調色：中藍，用在標題底線、highlight 長條、關鍵字 |
| title_color | #152230 | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Sans TC** — 教學場要好認，不要裝飾。
- 內文（body）：**Noto Sans TC** — 同家族，靠字級分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：課程或說明會名稱大字 |
| agenda | 大綱頁：先講今天會學到什麼 |
| title-content | 主力內頁：一頁一個觀念，條列不超過四條 |
| section | 章節分隔：切換單元或主題 |
| two-col | 並列：概念 vs 例子 |
| comparison | 對照：常見誤解 vs 正確做法 |
| big-fact | 關鍵數據：錄取率、班級人數、學習時數 |
| quote | 引述：學生或家長回饋，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，highlight 用中藍 |
| closing | 結尾：作業、報名方式或聯絡窗口 |
