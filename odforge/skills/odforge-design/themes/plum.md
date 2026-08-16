# plum — 梅紫 + 米白

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["plum"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：淺梅紫底、深紫墨字、洋紅偏向的紫強調，serif 標題——淺底裡最有個性的一套。適合藝文活動、設計提案、品牌敘事、文創與展覽說明。

與 **violet** 的區隔：violet 是暗底的午夜紫，戲劇感強；plum 是淺底的梅紫，明亮、柔和、印刷感。白天的展場用 plum，暗場的舞台用 violet。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #FBF7FB | 頁面背景：帶紫的米白 |
| surface | #F0E6F2 | 卡片／色塊底：淺梅紫灰 |
| text | #291D2C | 主要內文：近黑的深紫墨 |
| muted | #655A6B | 次要文字：註解、attribution、caption |
| accent | #8A2A86 | 強調色：洋紅偏向的紫，用在標題底線、關鍵字 |
| title_color | #291D2C | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Serif TC** — 襯線讓藝文題目更有編輯感。
- 內文（body）：**Noto Sans TC** — 內文回到 sans，展場遠看也清楚。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：展覽或專案名大字，紫色點一個關鍵詞 |
| agenda | 大綱頁：策展或提案的閱讀順序 |
| title-content | 主力內頁：一頁一個概念 |
| section | 章節分隔：切換展區、系列或設計階段 |
| two-col | 並列：靈感 vs 成品 |
| comparison | 對照：改版前 vs 改版後 |
| big-fact | 關鍵數據：參觀人次、作品數、觸及數 |
| quote | 引述：創作自述或觀眾回饋，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，highlight 用梅紫 |
| closing | 結尾：邀請行動——看展、合作或聯絡方式 |
