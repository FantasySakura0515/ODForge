# dark — 深靛 + 亮青

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["dark"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：深靛藍底配亮青強調、淺色文字。適合技術分享、產品 demo、駭客松、
投影在暗場的舞台簡報——任何想要科技感、聚焦、舞台戲劇性的場合。深底讓亮青
與大數字像會發光一樣跳出來。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #171826 | 頁面背景：深靛藍，接近夜色 |
| surface | #252842 | 卡片／色塊底：比 bg 亮一階的靛藍 |
| text | #E8EAF2 | 主要內文：近白的冷灰，暗底上清楚可讀 |
| muted | #9BA0C4 | 次要文字：註解、attribution、caption |
| accent | #3DD6E6 | 強調色：亮青，用在標題、highlight 長條、關鍵字 |
| title_color | #E8EAF2 | 標題色：與 text 同（此主題不另外覆寫）|

對比：text/bg、accent/bg、muted/bg 皆已通過 WCAG 門檻
（text/bg ≥ 4.5、accent/bg ≥ 3.0、muted/bg ≥ 3.0）。暗底主題尤其要守住這條，
淺文字掉到中灰就會糊。若你另外覆寫 DesignSpec 調色盤，務必自行重算對比。

## 字級刻度（pt，standard 密度）

| role | pt | 用途 |
| --- | --- | --- |
| display | 54 | 封面主標、big-fact 數字 |
| h1 | 28 | 內頁標題、section 分隔標題 |
| body | 18 | 條列與內文 |
| caption | 13 | 註解、資料來源、attribution |

## 字型

- 標題（display）：**Noto Sans TC** — sans 在暗底最俐落，避免 serif 細筆糊掉。
- 內文（body）：**Noto Sans TC** — 同家族，靠字級與亮青強調分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：講題大字，亮青點在關鍵詞，暗場開場最有戲 |
| agenda | 大綱頁：技術長講的路線圖，先讓聽眾知道會走到哪 |
| title-content | 主力內頁：一頁一個概念，條列別塞滿，留黑呼吸 |
| section | 章節分隔：切換 demo 段落、切換主題時的過場 |
| two-col | 並列：架構前後、程式碼 vs 結果 |
| comparison | 對照：舊架構 vs 新架構，亮青標出改良的一欄 |
| big-fact | 關鍵數據：效能提升倍數、延遲毫秒——大數字在暗底最發光 |
| quote | 引述：使用者回饋、名言，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，最多 8 條長條，highlight 用亮青 |
| closing | 結尾：一句收束 + repo／聯絡資訊 |
