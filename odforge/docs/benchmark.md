# 模型同題 benchmark

```powershell
odforge benchmark "為醫院主管整理候診流程改善成果" `
  --backend deepseek --backend ollama `
  --pages 8 --image .\after.png `
  --out-dir .\benchmark-results
```

每個 backend 都會獨立跑完 `outline → slides → render → validate → preview`。
輸出目錄保留：

```text
benchmark-results/
  report.json
  report.md
  deepseek/
    outline.json
    presentation.json
    deck.odp
    preview/
  ollama/
    ...
```

分數是 0–100 的**結構品質代理指標**，包含：

- ODF package 是否通過確定性驗證。
- 版型多樣性與視覺版型比例。
- speaker notes、引用來源覆蓋率。
- 文字預算警告與缺圖 placeholder 扣分。
- 封面／結尾敘事結構。

它不能衡量圖片美感、資訊正確性或故事說服力；請直接比較各候選的 PNG 預覽，
或再啟用 vision QA。某一候選失敗不會中止其他候選，錯誤會寫入同一份報告。

若要比較同一 OpenAI-compatible endpoint 上的不同模型，可分別使用已註冊 backend，
或用 `custom` 環境變數配置候選。大綱與逐頁內容也能分開指定：

```powershell
$env:ODFORGE_CUSTOM_BASE_URL = "https://example.com/v1"
$env:ODFORGE_CUSTOM_API_KEY = "..."
$env:ODFORGE_CUSTOM_MODEL = "fallback-model"
$env:ODFORGE_CUSTOM_OUTLINE_MODEL = "planning-model"
$env:ODFORGE_CUSTOM_SLIDES_MODEL = "writing-model"
```
