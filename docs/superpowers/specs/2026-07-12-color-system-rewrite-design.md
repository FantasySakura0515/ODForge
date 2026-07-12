# 簡報配色 skill 重寫 + 新增預設主題

日期：2026-07-12
範圍：odforge-design skill 的配色指南重寫，並新增 4 套研究背書的預設主題（academic/minimal/dark → 共 7 套）。

## 動機

現行 skill 的配色知識分散在 `SKILL.md §③`（DesignSpec + 對比度自檢）與三份 theme
規格書，偏「契約 + 對比門檻」，缺少**如何配出好色**的方法論。上網研究主流簡報配色
（2025–2026）後，歸納出可落地的三點：

1. **60-30-10 是主流框架**：60% 中性底、30% 結構文字、10% 強調色。ODForge 的
   5-token palette（`bg / surface / text / muted / accent`）本身就是這個結構，但 skill
   沒把它講明。
2. **趨勢方向「calm, clear, credible」**：暖中性底（cream/off-white，比純白暖）+ 單一
   自然色強調（teal / forest / navy）；遠離 neon、避免第二個強調色、避免純黑配純白。
   「Transformative Teal」是 2026 年度代表色。
3. **無障礙補強**：現有 WCAG 門檻（text/bg≥4.5、accent/bg≥3.0、muted/bg≥3.0）保留；
   補上 colorblind-safe 圖表指引——強調長條要靠**亮度**而非色相分辨、不用紅綠編碼、
   相鄰資料序至少 3:1。

研究來源見文末。

## Part A — 配色指南重寫

新增 `skills/odforge-design/themes/color-system.md`（一份給 LLM 讀的配色方法論），
並把 `SKILL.md §③` 收斂為重點 + 連到這份深指南。`color-system.md` 內容：

- **把 5-token palette 明講成 60-30-10**：`bg`=60% 底；`surface`+`text`+`muted`=30%
  結構；`accent`=10%，全簡報唯一彩色，只花在一個關鍵字／標題底線／一條 highlight。
- **自訂 palette 配色配方**：選一個暖／中性底 → surface 比 bg 深/淺一階 → text 近黑/
  近白 → muted = text 調淡到**剛好過 3:1** → accent = 單一飽和色相。
- **趨勢守則**：暖 off-white/cream 勝純白；單一自然色強調（teal/forest/navy）；不要
  neon、不要第二個強調色相、不要純黑配純白硬對比。
- **無障礙升級**：保留對比門檻，補 colorblind-safe 圖表指引（亮度分辨、避免紅綠、
  相鄰序 3:1、絕不只靠顏色傳達意義）。

`SKILL.md §③` 保留 DesignSpec 契約與對比自檢，新增一句 60-30-10 心法並連到
`color-system.md`。`§④` 主題清單擴充為 7 套並補連結。

## Part B — 新增 4 套預設主題（保留現有三套）

全部已用專案自身的 `contrast_ratio` 驗過，清空三道門檻（見下方實測）：

| 主題 | 定位（研究背書） | bg | surface | text | muted | accent | display 字型 |
|---|---|---|---|---|---|---|---|
| **teal** | 2026 年度色 Transformative Teal；冷靜可信的企業場 | `#F4F7F7` | `#E4EDEC` | `#1B2A2C` | `#556463` | `#0F766E` | Noto Sans TC |
| **forest** | 2025/26 主導的專業配色：森綠＋奶油，editorial／永續／健康 | `#F6F2E9` | `#E9E1CE` | `#23291F` | `#5E5849` | `#2C6E49` | Noto Serif TC |
| **navy** | 海軍藍＋冷白，最通用的企業／高階主管場（SaaS 感，與 academic 的暖白 serif 學術藍區隔）| `#FAFBFD` | `#EAEEF5` | `#16233F` | `#55617A` | `#2563EB` | Noto Sans TC |
| **violet** | 第二套暗底：午夜紫＋電光紫，創意／技術 demo（與 dark 的亮青區隔）| `#1A1526` | `#2A2440` | `#ECEAF4` | `#9A93B8` | `#B49BF5` | Noto Sans TC |

實測對比（`contrast_ratio`，越大越好）：

```
teal    text/bg=13.78 accent/bg=5.08 muted/bg=5.75  OK
forest  text/bg=13.35 accent/bg=5.47 muted/bg=6.33  OK
navy    text/bg=15.06 accent/bg=4.99 muted/bg=6.01  OK
violet  text/bg=14.96 accent/bg=7.59 muted/bg=6.14  OK
```

最終陣容 7 套：5 淺（academic / minimal / teal / forest / navy）+ 2 深（dark / violet）。
所有字型都在 `FONT_WHITELIST`（Noto Sans TC / Noto Serif TC / 微軟正黑體 / 標楷體）內。

## Part C — 連動點（讓測試維持全綠）

新增主題會牽動下列每一處，全部要一起改：

1. `src/odforge/themes.py`：`THEMES` dict 用 `_preset(...)` 加 4 筆。
2. `src/odforge/ir.py:285`：`theme` 的 `Literal["academic","minimal","dark"]` 擴為 7 值。
3. `tests/test_render_odp.py:23`：`assert set(THEMES) == {...}` 硬編三值 → 改成 7 值集合。
   `test_all_presets_pass_contrast_bars`（迭代 `THEMES.items()`）**自動涵蓋**新 preset，
   無需改，但這也是新 palette 必須清門檻的原因。
4. `tests/test_mcp.py`：lockstep 的 `@pytest.mark.parametrize(... ["academic","minimal","dark"])`
   與 `test_theme_md_scale_equals_standard_tier`、`test_skill_md_links_theme_specs` 的硬編
   清單 → 改成**從 `THEMES.keys()` 衍生**，未來加主題自動涵蓋。
5. `skills/odforge-design/themes/<name>.md`：每套新主題一份規格書（與 `academic.md`
   同格式：調色盤兩欄表 + 字級表 + 字型 + 各版型使用時機），受 lockstep 測試綁定。
6. `skills/odforge-design/SKILL.md §④`：補上 4 個新主題的連結與一句定位。
7. `web/src/components/PromptBar.tsx:19`：`THEMES` 選單陣列補 4 個 option。確認
   `PromptBar.test.tsx` 不會因 radio 數量變動而卡死（沒有硬編數量斷言即安全）。

## 驗證

- `pytest`（odforge/）全綠，特別是 lockstep、`set(THEMES)`、`test_all_presets_pass_contrast_bars`。
- `npm test`（web/）全綠。
- 抽一套新主題（如 forest）實跑 `forge_presentation` → `preview_odf`，看圖確認對比與版面。

## 非目標（YAGNI）

- 不改動現有 academic/minimal/dark 的 hex（scope 明確為「新增」而非「換新」）。
- 不加第二個 accent token／不動 palette 結構（維持 5-token 契約與既有渲染器）。
- 不改字級刻度 `SCALES`。

## 研究來源

- SlideUpLift — Best PowerPoint Color Palettes（具名 palette + hex，3–5 色上限，4.5:1）
  <https://slideuplift.com/blog/best-powerpoint-color-palettes-you-should-use/>
- SlidesGo — Presentation Design Trends 2026 <https://slidesgo.com/slidesgo-school/ai-presentations/presentation-design-trends-2026>
- Wix / UX Planet — 60-30-10 color rule
  <https://www.wix.com/wixel/resources/60-30-10-color-rule> ·
  <https://uxplanet.org/the-60-30-10-rule-a-foolproof-way-to-choose-colors-for-your-ui-design-d15625e56d25>
- RGBlind — Color Blind Friendly PowerPoint <https://rgblind.com/blog/color-blind-friendly-powerpoint>
- Venngage — Colorblind-Friendly Palettes <https://venngage.com/blog/color-blind-friendly-palette/>
