# clay — 陶土橘 + 米色

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["clay"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：暖米底、深棕墨字、陶土橘強調——土地感、手作感、人的溫度。適合永續與 ESG、地方創生、社區營造、food／農業與非營利組織的報告。

與 **minimal** 的區隔：minimal 是暖灰單色＋一點橘，克制、產品感；clay 的底色更暖、accent 更土，整體更接近手作與地方的語氣。產品發表用 minimal，社區與永續題目用 clay。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #FAF6F1 | 頁面背景：暖米色紙感 |
| surface | #EDE3D8 | 卡片／色塊底：淺陶土米 |
| text | #2C241C | 主要內文：近黑的暖棕墨 |
| muted | #6B5C4C | 次要文字：註解、attribution、caption |
| accent | #B4541F | 強調色：陶土橘，用在標題底線、highlight 長條、關鍵字 |
| title_color | #2C241C | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Sans TC** — 暖底上 sans 顯得踏實不做作。
- 內文（body）：**Noto Sans TC** — 同家族，靠字級與陶土橘分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：計畫或社區名大字，陶土橘點一個關鍵詞 |
| agenda | 大綱頁：成果報告的閱讀順序 |
| title-content | 主力內頁：一頁一個做法或發現 |
| section | 章節分隔：切換「背景」「行動」「成效」等段落 |
| two-col | 並列：問題 vs 在地解方 |
| comparison | 對照：計畫前 vs 計畫後 |
| big-fact | 關鍵數據：參與人數、減碳量、產值 |
| quote | 引述：居民或夥伴的話，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，highlight 用陶土橘 |
| closing | 結尾：後續行動與合作邀請 |
