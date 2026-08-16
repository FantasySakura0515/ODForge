# slate — 石墨灰 + 冷藍

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["slate"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：中性石墨底、亮冷灰字、柔和冷藍強調——暗底但不刺眼，長時間投影或深夜場也不疲勞。適合工程、資安、系統維運、技術審查與需要大量程式碼或終端畫面的簡報。

與 **dark** 的區隔：dark 是深靛底＋亮青，飽和、舞台感強；slate 是中性灰底＋低飽和藍，安靜、專業、更接近工程工具的介面色。要有記憶點用 dark，要看一小時不累用 slate。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #14181D | 頁面背景：中性石墨，不帶色偏 |
| surface | #212832 | 卡片／色塊底：稍亮的藍灰 |
| text | #E8EDF4 | 主要內文：偏冷的亮灰白 |
| muted | #9CA9B8 | 次要文字：註解、attribution、caption |
| accent | #7FB2FF | 強調色：柔和冷藍，用在標題底線、highlight 長條、關鍵字 |
| title_color | #E8EDF4 | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Sans TC** — 暗底上 sans 的筆畫最穩定，不糊邊。
- 內文（body）：**Noto Sans TC** — 同家族，靠字級與冷藍強調分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：系統或專案名大字，冷藍點一個關鍵詞 |
| agenda | 大綱頁：技術審查開場，先講清楚今天要決定什麼 |
| title-content | 主力內頁：一頁一個技術重點 |
| section | 章節分隔：切換「架構」「風險」「維運」等大段落 |
| two-col | 並列：現行做法 vs 建議做法 |
| comparison | 對照：方案 A vs 方案 B，冷藍標出建議欄 |
| big-fact | 關鍵數據：延遲、可用度、事故數——一個大數字最有說服力 |
| quote | 引述：事故報告或稽核結論，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，highlight 用冷藍 |
| closing | 結尾：決策請求 + 負責人與下一步 |
