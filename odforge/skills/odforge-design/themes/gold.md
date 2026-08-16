# gold — 墨金

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["gold"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：近黑底、暖白字、克制的金色強調，serif 標題——深色系裡最正式的一套。適合頒獎、成果發表會、年度回顧、募款說明與任何需要「隆重」而不是「炫技」的場合。

與 **violet** 的區隔：violet 是創意戲劇感，適合設計提案；gold 是典禮的隆重感，金色只點在關鍵字與線條上。金色面積一大就俗氣，這套的 accent 只用在 10% 的位置。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #12100C | 頁面背景：近黑的暖墨 |
| surface | #221E17 | 卡片／色塊底：深棕墨 |
| text | #F3EEE2 | 主要內文：暖白，不用純白以免刺眼 |
| muted | #B5A88E | 次要文字：註解、attribution、caption |
| accent | #D9A441 | 強調色：克制的金，用在標題底線、關鍵字、細分隔線 |
| title_color | #F3EEE2 | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Serif TC** — 襯線在深色底上最有典禮感。
- 內文（body）：**Noto Sans TC** — 內文用 sans 維持可讀性。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：活動或獎項名大字，金色只點一個詞 |
| agenda | 大綱頁：典禮流程一覽 |
| title-content | 主力內頁：一頁一個成果，條列極簡 |
| section | 章節分隔：切換年度、階段或獎項類別 |
| two-col | 並列：目標 vs 達成 |
| comparison | 對照：去年 vs 今年，金色標出成長欄 |
| big-fact | 關鍵數據：得獎數、募款額、參與人次 |
| quote | 引述：得獎感言或評審評語，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，highlight 用金色 |
| closing | 結尾：致謝 + 明確的下一步，不要只寫「謝謝聆聽」 |
