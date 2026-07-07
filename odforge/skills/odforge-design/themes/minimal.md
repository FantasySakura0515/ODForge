# minimal — 暖灰單色 + 一點橘

給 LLM 讀的規格書。這些數值是 `odforge.themes.THEMES["minimal"]` 與
`SCALES["standard"]` 的鏡像，兩者以測試 lockstep 綁定，改一邊必須改另一邊。

**定位**：暖灰單色系，只用一抹燒橘（burnt orange）點睛。適合產品發表、
新創 pitch、設計提案、品牌敘事——任何想要克制、現代、留白呼吸感的簡報。
節制是這個主題的美學：一頁只讓一件事發光。

## 調色盤（hex）

| token | hex | 說明 |
| --- | --- | --- |
| bg | #FAF8F5 | 頁面背景：幾乎純白的暖灰 |
| surface | #ECE8E2 | 卡片／色塊底：淺暖灰 |
| text | #2B2926 | 主要內文：近黑的暖灰 |
| muted | #6E6A63 | 次要文字：註解、attribution、caption |
| accent | #C0560E | 強調色：燒橘，全簡報唯一的彩色，省著用才有力 |
| title_color | #2B2926 | 標題色：與 text 同（此主題不另外覆寫）|

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

- 標題（display）：**Noto Sans TC** — 單一 sans 家族撐全場，乾淨無雜訊。
- 內文（body）：**Noto Sans TC** — 與標題同家族，靠字級與粗細分層次。
- 皆在 `FONT_WHITELIST` 內，CJK 覆蓋完整。

## 各版型使用時機

| 版型 | 使用時機 |
| --- | --- |
| title | 封面：產品名／提案名，大量留白，橘色只點一個字 |
| agenda | 大綱頁：較長的 pitch 才需要，短簡報可略 |
| title-content | 主力內頁：一頁一個賣點，條列精簡到只剩關鍵詞 |
| section | 章節分隔：從「問題」切到「解法」切到「市場」時 |
| two-col | 並列：before / after、功能 / 效益 |
| comparison | 對照：我們 vs 競品，用橘色標出自己的優勢欄 |
| big-fact | 關鍵數據：成長率、市佔、節省成本——一個大數字最有記憶點 |
| quote | 引述：用戶好評、媒體評語，attribution 標出處 |
| chart | 圖表：**只有真實數據**才用，最多 8 條長條，highlight 用橘色 |
| closing | 結尾：一句 call to action + 聯絡方式 |
