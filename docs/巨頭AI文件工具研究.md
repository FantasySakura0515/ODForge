# 各大巨頭 AI 文件/簡報生成工具研究

> 研究日期：2026-07-06。由五個平行研究 agent 對官方文件、工程部落格、原始碼與第三方評測交叉比對產出，供 ODForge 產品定位與架構決策參考。


---

# 第1部：Microsoft（Copilot / PowerPoint Designer）

# Microsoft AI 簡報/文件生成研究報告

---

## 一、Microsoft 365 Copilot in PowerPoint

### 1. 管線

**經典 Create 流程（2023–2024）：**
```
使用者 prompt（上限 2,000 字元）
  → Grounding：透過 Microsoft Graph + 語意索引取用租戶內檔案（權限範圍內）
  → LLM 生成大綱與各頁文字
  → 交給 Designer 管線套版面、配圖（庫存圖 + DALL·E 3）
  → 原生 .pptx，直接出現在 PowerPoint 內
```

**Narrative Builder（2024/11 起）：** 在 prompt 與成品之間插入一個「敘事大綱」中間層——Copilot 先產生 topic 列表，使用者可增刪、重排、對每個 topic 掛上 grounding 檔案（Word/PDF/加密 Word，[支援約 4 萬字或 150 頁投影片](https://windowsforum.com/threads/powerpoint-copilot-fast-branded-decks-from-prompts-with-narrative-builder.394504/)，早期僅 1.5 萬字），確認後才生成初稿。這是 Microsoft 對「一步到位品質太差」的直接修正：把人拉進大綱階段。

**Office Agent / Agent Mode（2025/9 起，[官方公告](https://www.microsoft.com/en-us/microsoft-365/blog/2025/09/29/vibe-working-introducing-agent-mode-and-office-agent-in-microsoft-365-copilot/)，部分由 Anthropic 模型驅動）：** 三階段——(1) 澄清意圖（長度、視覺主題、受眾）；(2) 深度研究與推理，顯示思考鏈與投影片即時預覽；(3) **用「程式碼生成」執行請求並沿途跑品質檢查**，產出可分享的簡報。官方原話承認：「過去兩年 AI 在做投影片這件事上經常不及格（AI has often fallen short when creating slides），Office Agent 改變了這一點。」

**輸出格式的真相：** 表面上都是原生 .pptx，但[第三方拆解](https://www.perspectives.plus/p/ai-still-cant-figure-out-powerpoint)指出新一代 agent 其實是**先生成 HTML 再轉換成 PPTX**，Copilot Cowork 則用開源的 PptxGenJS 產檔——結果是「看起來現代但結構空洞」，不保留佈景主題、版面配置、placeholder、SmartArt，等於是「偽裝成文件的圖片」。

### 2. 設計品質的來源

- **組織模板優先**：Copilot [優先使用模板中的「範例投影片」（sample slides），其次才是版面結構](https://support.microsoft.com/en-us/powerpoint/copilot/keep-your-presentation-on-brand-with-copilot)；只有範例不足時才退回 Slide Master。這個設計哲學值得注意：**真實範例 > 抽象規則**。
- **Organization Asset Library（OAL）**：IT 管理員把公司模板放進 SharePoint 資產庫，Copilot 生成時可選用。
- **Brand Kits（2025–2026）**：品牌管理員在 M365 Copilot app 集中管理官方 logo、色彩、字型、模板、圖庫、icon，可直接引用 OAL 資產；還有 Brand Checker 檢查偏離（[指南](https://www.aguidetocloud.com/blog/microsoft-365-copilot-brand-kit-complete-guide/)）。這本質上是「企業級 design token 治理」。
- **底層仍是 Designer 的人工設計版面庫** + DALL·E 3 生圖。**沒有 LLM 自由排版**——版面是從既有 layout 挑的。

### 3. 變化 vs 一致性

一致性極強、變化極弱——這是最大罵點。[Alai 的實測](https://getalai.com/blog/microsoft-copilot-review)：「所有內容頁套同一個版型配一張隨機庫存圖」。使用者控制美術方向的手段 = 換模板 / 換 Brand Kit / 在 prompt 描述主題風格，僅此而已。Microsoft 的取捨很明確：**企業場景中「不出格」比「好看」值錢**，寧可重複也不能違反品牌規範。

### 4. 品質保證機制

- 經典流程：**沒有 render-critique 迴圈**，靠「模板天生不會壞」的保證（版面是人做的、placeholder 是固定的）。但實務上文字溢出、圖壓 logo 仍頻繁發生（見弱點一節）。
- Office Agent / Agent Mode：首次引入「生成→評估→修復→重複直到驗證通過」迴圈；Excel Agent Mode 在 SpreadsheetBench 自評 57.2%——Microsoft 自己公布這種不到六成的數字，說明 agentic 驗證是誠實但仍不成熟的方向。
- 所有生成一律掛「AI 產出需人工審閱」免責聲明；AI 生成圖依 C2PA 標準在演講者備忘稿中標註。

### 5. 編輯/迭代 UX

生成後回到 PowerPoint 本體手動編輯，或用 Copilot 窗格下指令（摘要、重寫某頁、加頁、分節）。**歷史上最被詬病的就是不能迭代**：Create 流程一次性輸出，不滿意只能整份重來（[Microsoft Q&A 上的抱怨](https://learn.microsoft.com/en-us/answers/questions/5516746/is-copilot-integration-with-powerpoint-really-this)）。2026 年才補上「Edit with Copilot」逐頁對話編修。

### 6. 商業定位與護城河

- 定位：M365 Copilot 加購授權（約 $30/人/月），賣給已被 Office 綁定的企業。
- 護城河：**(a) Microsoft Graph**——你的信件、會議、文件都在租戶裡，grounding 帶權限控管（Conditional Access、MFA、Purview），任何第三方工具做不到（[架構文件](https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-architecture)）；**(b) 合規邊界**——資料不出 Microsoft 365 service boundary；**(c) 通路**——數億既有使用者；**(d) 模型中立**——同時接 OpenAI 與 Anthropic 模型。護城河是資料與通路，**不是生成品質本身**。

---

## 二、PowerPoint Designer（前 Copilot 時代的 ML 功能）

### 1. 管線

```
使用者在投影片上放內容（打字、貼圖）
  → 內容即時送雲端 Designer 後端（微服務架構）
  → 多個 ML 模型平行分析：
      影像多標籤分類（決定圖片該用什麼「處理方式」）
      智慧裁切（物體置中、人眼視線對齊）
      NLP 文字分析（辨識列表結構、可轉 SmartArt 的語意）
      投影片結構分析
  → 與「目前 Slide Master 裡的版面」做匹配，產生候選設計
  → 建議排序模型（suggestion ranking）排出 Design Ideas 面板的順序
  → 使用者點選 → 原生套用
```
規模：[每日最多 410 萬張 Designer 投影片、累計 17 億張被使用者保留](https://learn.microsoft.com/en-us/shows/ai-show/powered-by-azure-machine-learning-service-create-stunning-presentations-with-powerpoint-designer)；後端跑在 Azure ML 上，用 Azure Data Lake 訓練資料、分散式 GPU、MLOps 定期重訓（[Azure blog](https://azure.microsoft.com/en-us/blog/how-azure-machine-learning-enables-powerpoint-designer/)）。

### 2. 設計品質的來源

**100% 人類設計師預製版面 + ML 只做「匹配與排序」。** 沒有任何生成式排版。[官方模板規範](https://support.microsoft.com/en-us/office/creating-custom-templates-that-work-well-with-designer-in-powerpoint-21521084-0c21-4471-bec1-a286a2f70b9f)洩露了機制：自訂模板要吃到 Designer，必須「每個 Slide Master 至少 15 個 layout」、定義好主題色與字型、常見情境至少湊出 3 個候選建議、placeholder 一律視為「溢出時縮字」。也就是說 Designer 的「智慧」= 在一個保證不會壞的離散版面空間裡做分類與檢索。

### 3. 變化 vs 一致性

變化被刻意限制在版面庫內。同樣內容每次建議大致相同（排序模型有隨機性但候選集固定）。使用者的控制 = 換模板。安全、無聊、不會出錯。

### 4. 品質保證機制

不需要 render-critique——**版面本身就是驗證**。人做的 layout 不可能文字壓圖。排序模型用「使用者保留了哪個建議」的海量遙測做隱式訓練訊號。另外用「不支援就閉嘴」策略保底：有表格/圖表的投影片、母片含動畫或 OLE 物件時，Designer 直接不給建議，寧缺勿濫。

### 5. 編輯/迭代 UX

套用後就是普通原生物件，隨便改；不喜歡就換下一個建議。零學習成本，這是它成功的核心。

### 6. 商業定位與護城河

M365 訂閱的留客功能（免費內建）。護城河 = 17 億張投影片的行為遙測 + 內部設計師團隊持續產 layout。**Designer 是「AI 輔助、人類設計、系統保證」的教科書案例。**

---

## 三、Copilot in Word

### 1. 管線

```
文件開頭/新行出現 Draft with Copilot 框（或左邊界 Copilot icon）
  → prompt + 用 "/" 引用最多 20 個檔案（Word/PPT/PDF/TXT，
     來自 OneDrive/SharePoint，走 Graph 權限）
  → grounded prompt 送 LLM
  → 文字以 Word 樣式直接插入文件內（原生 docx 流）
  → Keep it / Regenerate / Discard / 追加指令微調（「更精簡」等）
```
（[官方文件](https://support.microsoft.com/en-us/office/draft-and-add-content-with-copilot-in-word-069c91f0-9e42-4c9a-bbce-fddf5d581541)）Word 的 Agent Mode（2025/9）把它變成對話式：Copilot 起草、建議修改、主動問澄清問題。

### 2. 設計品質的來源

文件不像簡報有排版問題——輸出掛在 **Word 既有樣式體系**（標題階層、清單、表格）上，等於用文件本身的 design token 保證外觀。內容品質純靠 LLM + grounding 檔案。

### 3. 變化 vs 一致性

文字生成天然多樣；格式一致性由樣式系統扛。使用者可指定語氣、長度、格式。

### 4. 品質保證機制

幾乎沒有自動驗證，官方直接把責任丟給人：「Copilot 生成的是草稿，你需要驗證與修改細節」。Agent Mode 才有評估-修復迴圈。事實準確性是已知弱點（幻覺數據）。

### 5. 編輯/迭代 UX

三工具中最順：inline 選字重寫、語氣調整、visualize as table、Keep/Regenerate 迴圈——因為**文字的迭代單位小、可局部替換**，不像投影片牽一髮動全身。

### 6. 商業定位與護城河

同 M365 Copilot 整體：Graph grounding + 合規。Word 場景相對沒有獨特設計護城河，競品（Google Docs Gemini、Notion AI）功能同質。

---

## 四、已知弱點與使用者抱怨（跨產品彙整）

- **版面重複且醜**：「所有內容頁同一版型 + 隨機庫存圖」、「AI 生成圖是平庸的 GenAI 剪貼畫，裝飾大於資訊」（[Alai 實測](https://getalai.com/blog/microsoft-copilot-review)）。
- **模板被忽略、版面損壞**：內容生成不理會 slide master，文字壓圖表、logo 位移；[Octigen 稱](https://octigen.com/blog/posts/2026-04-01-copilot-for-powerpoint-reasons-it-fails/)某歐洲銀行部署後品牌違規反增三倍、42 人簡報團隊六週後採用率僅 8%，使用者評語「useless」。
- **捏造數據**：Octigen 測試宣稱僅 12% 統計數字可驗證、41% 純屬虛構（單一來源，數字需保留）。
- **無法迭代**：生成後不能用 AI 追加修改（2026 年前）；要求象限比較圖時，Copilot 在聊天區吐 markdown 而不是改投影片。
- **2,000 字元 prompt 上限**：企業場景的脈絡塞不進去。
- **靜默失敗**：「Something went wrong」無診斷資訊，是官方論壇最常見的故障模式。
- **結構 vs 外觀的根本兩難**（[perspectives.plus 深度拆解](https://www.perspectives.plus/p/ai-still-cant-figure-out-powerpoint)）：LLM 擅長 HTML、**對 OOXML 極其笨拙**（訓練語料太少）。Microsoft 自己的繞路方案（HTML→PPTX 轉換、PptxGenJS）產出「視覺現代但結構空洞」的檔案；該文並指出**社群開源函式庫的表現與 Microsoft 企業方案相當**。
- Microsoft 官方在 Office Agent 公告中自承：「過去兩年 AI 做投影片經常不及格。」

---

## 五、對「自然語言 → 原生 ODF」開源工具（Python、兩人學生團隊）的啟示

### 可借鏡

1. **Designer 模式是小團隊最該抄的架構**：LLM 只負責「內容與語意結構」，排版交給**確定性的、人工設計的版面庫**（哪怕只有 15 個 layout）。品質保證不靠 critique 迴圈，靠「版面空間裡沒有壞的點」。這正好對應 ODF 的 `styles.xml`/master page 機制——把設計 token 寫死在樣式層，LLM 永遠不碰座標。
2. **Narrative Builder 的中間層**：prompt → 可編輯的大綱/IR → 渲染。把人插在大綱階段，比生成後再改便宜十倍。你們的工具應該暴露這個中間表示（JSON/YAML 大綱），這也是 Microsoft 藏起來而使用者一直想要的東西。
3. **「範例投影片 > Slide Master」的洞見**：Copilot 從真實範例頁學品牌風格，效果好過抽象規則。few-shot 真實 ODP 範例可能比寫一堆風格 prompt 有效。
4. **Office Agent 的「程式碼生成 + 沿途品質檢查」**：Microsoft 最新架構收斂到「LLM 寫程式碼產檔 + 驗證迴圈」——這正是 Python 工具的天然形態。加上便宜的機器驗證：XML schema 驗證、文字溢出檢測（字數 × 字級 vs placeholder 尺寸）、LibreOffice headless 渲染截圖抽查。
5. **「不支援就閉嘴」**：Designer 遇到表格/圖表直接不出建議。小工具與其硬撐所有情境出爛結果，不如明確界定支援範圍。

### 巨頭做不到、你們反而有機會的

- **原生格式保真**：Microsoft 自己的 agent 都在走 HTML→轉檔的邪路，產出結構空洞的檔案；一個用 odfpy/直接寫 ODF XML 的工具，反而能產出「可繼續編輯、樣式乾淨、placeholder 完整」的真檔案。**這是拆解文章實證過的空隙**——社群函式庫已能打平企業方案。
- **無上限的 prompt、透明的管線、可迭代的中間層**：這三項全是 Copilot 被罵最兇的點，而且是產品決策造成的，不是技術極限。
- **ODF 本身**：Microsoft 永遠不會認真做 ODF 輸出；政府/教育的 ODF 政策市場（正是你們競賽的場景）對它是死角。

### 根本不該學的

- **一步到位的黑箱生成**：最大教訓。不要做「一個 prompt 吐完整簡報」的 demo 型產品。
- **靠海量遙測訓練排序模型**：Designer 的路需要 17 億張投影片的數據，兩人團隊零機會，別往 ML 排版方向想。
- **通吃所有文件類型與情境**：Copilot 的「generic garbage」問題來自它必須服務所有人。垂直聚焦（例如公文、教學講義、競賽場景的標準文件）能讓小版面庫就覆蓋 90% 需求。
- **把品質責任推給「請人工審閱」的免責聲明**：那是有既有用戶基數才敢做的事；小工具第一印象壞了就沒有第二次。

**Sources:**
- [Vibe working: Agent Mode and Office Agent — Microsoft 365 Blog](https://www.microsoft.com/en-us/microsoft-365/blog/2025/09/29/vibe-working-introducing-agent-mode-and-office-agent-in-microsoft-365-copilot/)
- [How Azure Machine Learning enables PowerPoint Designer — Azure Blog](https://azure.microsoft.com/en-us/blog/how-azure-machine-learning-enables-powerpoint-designer/)
- [AI Show: PowerPoint Designer — Microsoft Learn](https://learn.microsoft.com/en-us/shows/ai-show/powered-by-azure-machine-learning-service-create-stunning-presentations-with-powerpoint-designer)
- [Creating custom templates that work well with Designer — Microsoft Support](https://support.microsoft.com/en-us/office/creating-custom-templates-that-work-well-with-designer-in-powerpoint-21521084-0c21-4471-bec1-a286a2f70b9f)
- [How does Microsoft 365 Copilot work? — Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-architecture)
- [Create a new presentation with Copilot in PowerPoint — Microsoft Support](https://support.microsoft.com/en-us/office/create-a-new-presentation-with-copilot-in-powerpoint-3222ee03-f5a4-4d27-8642-9c387ab4854d)
- [Keep your presentation on-brand with Copilot — Microsoft Support](https://support.microsoft.com/en-us/powerpoint/copilot/keep-your-presentation-on-brand-with-copilot)
- [Create and manage official Brand kits — Microsoft Support](https://support.microsoft.com/en-us/microsoft-365-copilot/create-and-manage-official-brand-kits-in-the-microsoft-365-copilot-app)
- [PowerPoint Copilot: Narrative Builder teardown — Windows Forum](https://windowsforum.com/threads/powerpoint-copilot-fast-branded-decks-from-prompts-with-narrative-builder.394504/)
- [Draft and add content with Copilot in Word — Microsoft Support](https://support.microsoft.com/en-us/office/draft-and-add-content-with-copilot-in-word-069c91f0-9e42-4c9a-bbce-fddf5d581541)
- [Copilot for PowerPoint: Five Reasons It Fails — Octigen](https://octigen.com/blog/posts/2026-04-01-copilot-for-powerpoint-reasons-it-fails/)
- [I Tested Microsoft Copilot with Dozens of Presentations — Alai](https://getalai.com/blog/microsoft-copilot-review)
- [AI still can't figure out PowerPoint — perspectives.plus](https://www.perspectives.plus/p/ai-still-cant-figure-out-powerpoint)
- [Is Copilot Integration with PowerPoint really this Limited — Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5516746/is-copilot-integration-with-powerpoint-really-this)
- [M365 Copilot Brand Kit guide — aguidetocloud.com](https://www.aguidetocloud.com/blog/microsoft-365-copilot-brand-kit-complete-guide/)

---

# 第2部：Google（Gemini / NotebookLM / Vids）

# Google AI 簡報/文件生成研究報告

## 總覽：Google 內部其實有兩條互相矛盾的技術路線

研究四個產品後，最重要的發現是 Google 同時押注兩條路線：

| 路線 | 代表 | 輸出 | 設計品質 | 可編輯性 |
|---|---|---|---|---|
| **結構化路線**：LLM 生成結構化內容，填入原生檔案格式的版型/主題 | Slides「Help me create a slide」、Docs「Help me create」、Gemini Canvas 匯出 Slides、Vids 大綱+模板 | 原生檔案（可編輯） | 中等，受版型限制 | 完整 |
| **像素路線**：讓推理型影像模型直接把整頁「畫」出來 | NotebookLM Slide Decks/Infographics、Slides「Beautify this slide」、Video Overviews | 圖片（PDF/PNG/影片） | 極高 | 幾乎為零 |

2025 年 11 月 Nano Banana Pro（Gemini 3 Pro Image）出現後，像素路線的設計品質暴衝，這就是 NotebookLM 品質跳升的核心答案。兩條路線的取捨（設計自由度 vs 可編輯性）至今 Google 自己也沒解決。

---

## 1. Gemini in Google Slides

### 1.1 管線
有三條並行子管線：

- **單頁生成（Help me create a slide）**：使用者輸入 prompt（可用 `@檔名` 引用 Drive 文件）→ Gemini 生成一張套用「目前簡報主題」的投影片 → 側欄預覽 → Insert / Retry / 關閉後重新下 prompt。輸出是**原生可編輯的 Slides 物件**（文字框、圖片各自獨立）。
- **整份簡報生成（走 Gemini app 的 Canvas）**：prompt 或上傳來源 → Gemini 拆解成邏輯章節 → 生成各頁標題與要點 → 挑選主題、配圖、基本圖表 → Canvas 內預覽與對話式修改 → 「Export to Google Slides」轉成**原生可編輯 Slides 檔**存入 Drive。
- **像素子管線（2025/11 起，Nano Banana Pro）**：「Help me visualize」生成資訊圖表、「Beautify this slide」一鍵把整頁內容重新設計——但輸出是**一張高解析度圖片**貼在投影片上，無法個別修改其中的文字或圖表。

### 1.2 設計品質的來源
- 結構化路線：來自 **Slides 既有的主題（theme）系統**——人類設計師預製的配色、字型、版面配置。Gemini 只負責「填內容」，不負責「做設計」。
- 像素路線：來自 **Nano Banana Pro 模型本身**——它把版面引擎、字型排印、圖表、風格全部內化在單一模型裡，且會參照 Google Search 的知識正確描繪真實物件。

### 1.3 變化 vs 一致性
- 新投影片強制套用目前主題，所以一致性由主題保證；變化來自 Retry（每次不同版本）。
- 「Beautify this slide」宣稱會「模仿整份簡報的整體風格」來維持一致性。
- 使用者的美術控制有限：換主題、下 prompt 描述，僅此而已。

### 1.4 品質保證機制
沒有公開的 render-critique 迴圈。品質靠**主題系統天生不會壞**（版型是人設計的，LLM 塞文字進去很難毀掉）。像素路線靠 Nano Banana Pro 內建的「Thinking」機制：模型會先生成中間草稿圖規劃構圖，再輸出最終圖。介面上僅以「AI 可能不準確」免責聲明兜底。

### 1.5 編輯/迭代 UX
結構化路線產物就是普通投影片，隨便改；側欄可 Retry 或重下 prompt。像素路線只能整張重新生成，這是使用者抱怨最多的點。

### 1.6 商業定位與護城河
綁在 Workspace Business/Enterprise 與 Google AI Pro/Ultra 訂閱裡，本體是「留住 30 億 Workspace 使用者」而非獨立產品。護城河：Drive 檔案的 `@` 引用（生成內容 ground 在你自己的資料上）、企業資料合規、以及 Nano Banana Pro 的獨佔。

---

## 2. Gemini in Google Docs

### 2.1 管線
- **Help me write**：選取文字或空白處 → 生成/改寫段落，純文字操作。
- **Help me create**（2024/12 起）：新文件輸入 prompt（可 `@檔名`）→ 一次生成**完整排版的文件**：封面圖、內文插圖、樣式化標題、表格、從 Drive 檔案抽取的內容。僅支援 **pageless 模式**（封面圖要滿版）。輸出是原生 Google Docs，逐字可編輯。
- 影像由 Gemini 影像模型生成（封面圖、內文圖），不能生成人物圖。

### 2.2 設計品質的來源
沒有模板庫——使用者用 prompt 描述想要的文件，系統套用「適當的格式與樣式」；也能模仿參考文件的結構（標題層級、章節、間距）。本質上是 **LLM 在 Docs 有限的排版詞彙（標題階層、表格、smart chips、圖片）內自由發揮**。文件的設計空間本來就小，所以品質風險低。

### 2.3 變化 vs 一致性
每次生成的結構與風格會隨 prompt 變化；官方提供 starter prompts（產品路線圖、活動清單等）引導常見場景。美術方向控制粗糙：只能靠 prompt 措辭。

### 2.4 品質保證機制
無公開驗證迴圈。靠 **Docs 文件模型天生的約束**——輸出只能是合法的 Docs 結構，不存在「版面壞掉」的問題。已知缺陷：不會保留 `@` 引用檔案的結構與樣式、不接網路搜尋、僅英文。

### 2.5 編輯/迭代 UX
定位明確是「first draft」：生成後就是普通文件，用一般編輯器修改，或再叫 Help me write 改寫局部。這是四個產品中迭代體驗最順的，因為輸出格式與編輯格式完全同一。

### 2.6 商業定位與護城河
同 Slides，是 Workspace 訂閱的黏著劑。護城河在於 Drive 上下文與企業通路，功能本身（生成格式化文件）競品早就能做。

---

## 3. NotebookLM（重點：2025 年設計品質跳升的原因）

### 3.1 管線
- **Slide Decks / Infographics（2025/11）**：使用者上傳來源（文件、筆記、照片、Deep Research 結果）→ Studio 面板選擇格式（**Detailed Deck** 完整文字自讀型 vs **Presenter Slides** 講者輔助型）、長度（短/預設/長）、語言、自由風格 prompt → NotebookLM 的「creative agents」（Gemini 3）從來源提煉敘事結構、決定每頁內容與視覺方向 → **Nano Banana Pro 把每一頁整張渲染成圖片** → 打包成 PDF 或 PPTX 下載（PPTX 內每頁仍是一張圖）。Infographic 則是單張大圖。生成需數分鐘。
- **Video Overviews**：初版（2025/7）是「有旁白的投影片影片」，會從來源抽取現成圖片配上文字片段；**Cinematic 版（2025/12）**改為三模型管線：**Gemini 3 當創意總監**（決定敘事、視覺風格、分鏡等數百個決策）→ **Nano Banana Pro/Imagen 生成靜態畫面**（與旁白對時）→ **Veo 3 合成動態與轉場** → 輸出 1–3 分鐘的 mp4。

### 3.2 設計品質的來源——「跳升」是怎麼發生的
這是本次研究最關鍵的一題。答案是三件事疊加，**而且完全沒有模板庫**：

1. **換掉渲染引擎**：放棄「LLM 填 HTML/版型」的做法，改讓 Nano Banana Pro 直接生成整頁像素。這個模型是第一個能在圖片內正確渲染長段多語文字的影像模型，等於把字型排印、版面、圖表、風格引擎全部內化成模型能力。設計品質從「工程師寫的版型上限」變成「模型見過的所有人類設計的上限」。
2. **推理式生成**：Nano Banana Pro 建立在 Gemini 3 Pro 上，預設有「Thinking」流程——先產出中間草稿圖規劃構圖與版面，再 commit 最終渲染。相當於把 render-critique 迴圈**內建在模型的一次前向生成裡**。
3. **創意代理人（creative agents）做美術指導**：與 Audio/Video Overviews 同源的 agent 架構，由 Gemini 對來源做資訊層級、字圖比例、敘事節奏的決策，再下指令給影像模型。也就是「LLM 當 art director、影像模型當 production designer」的分工。

代價非常明確：**輸出不可編輯**。這是 Google 刻意的取捨——NotebookLM 的定位是「從你的資料快速產出可消費的成品」，不是編輯器。

### 3.3 變化 vs 一致性
- 每次生成都不同（Retry 即得新版本）。
- 使用者透過自由風格 prompt 控制美術方向，官方示範包括「聖誕主題」「漫畫風」「黑板風」，甚至**上傳品牌手冊（brandbook）讓輸出遵循品牌規範**。
- 同一份 deck 內的跨頁一致性由模型 conditioning 維持（Nano Banana Pro 可跨 14 張輸入圖維持一致性），不是靠 design tokens。

### 3.4 品質保證機制
- 模型內建 Thinking（草稿圖→終稿）是主要機制。
- 沒有公開的外部驗證迴圈；官方直接承認「Slide Decks 可能含視覺或事實錯誤」。
- 生成時間長達數分鐘，換取單次高良率，而非快速多輪修補。

### 3.5 編輯/迭代 UX
最弱的一環，也是演進最快的一環：
- 匯出的 PDF/PPTX 是圖片，Google Slides 無法直接匯入編輯；社群發展出 Codia、Canva Grab Text 等 OCR 重建工具鏈。
- 2026/2 新增了 revision 介面：可修改單頁或多頁的文字、版面、視覺（本質是局部重生成），但**不能增刪頁**，且修訂時不再參照來源。

### 3.6 商業定位與護城河
免費層+AI Pro/Ultra 加量，是 Google 拉新的旗艦 AI 產品。護城河有三層：(1) **source-grounding**——所有輸出錨定在你上傳的資料上，幻覺風險與通用聊天機器人不同級；(2) **三模型獨佔組合**（Gemini 3 + Nano Banana Pro + Veo 3），競品拿不到同級的像素引擎；(3) Audio Overviews 建立的品牌心智。

---

## 4. Google Vids

### 4.1 管線
Prompt（可 `@` Drive 的 PDF/Slides）→ Gemini 生成**大綱**（分場景、每場景有草稿文字）→ 使用者在大綱層編輯（改寫/增刪場景）→ 從**約 50 種人類設計的模板風格**中挑一種（可預覽）→ 生成草稿影片：**storyboard（分鏡板，非 timeline）**上排好各場景，配上腳本、TTS 旁白、來自**圖庫或你檔案的媒體**、背景音樂 → 使用者逐場景編輯 → 輸出成品影片。上限 50 個視訊物件+50 個音訊物件。另有 Veo 3 文字/圖片生成影片片段、AI 虛擬主播（avatar）唸稿、Nano Banana Pro 多輪修圖。

### 4.2 設計品質的來源
四個產品中**最傳統**：人類設計師預製的 50 套模板（含圖形、版面、動態），加上授權圖庫。AI 只負責內容規劃、腳本、配媒體與旁白；設計本身幾乎不交給模型。

### 4.3 變化 vs 一致性
一致性由「整套模板套用到所有場景」保證；變化來自模板選擇與媒體替換。美術方向=挑模板，控制粒度粗但不會失敗。

### 4.4 品質保證機制
靠**模板天生不會壞**。AI 沒把握的地方會顯式標示「Add your media」佔位符，把不確定性推給使用者，而不是硬生成。這是很聰明的 UX 誠實設計。

### 4.5 編輯/迭代 UX
分鏡板逐場景編輯：改腳本、換媒體（上傳/AI 生成/圖庫）、重生成或自錄旁白、自動剪逐字稿。因為結構是場景物件而非渲染後影片，編輯體驗完整。

### 4.6 商業定位與護城河
基本編輯器 2025/8 起對消費者免費（無 AI），AI 功能綁 Workspace 付費方案與 AI Pro/Ultra。定位是「企業內部溝通影片」（培訓、公告），吃的是 Loom/Canva 市場。護城河：Workspace 通路、圖庫授權、Veo 3 與 avatar 技術。

---

## 5. 對「自然語言 → 原生 ODF」開源工具（Python、兩人學生團隊）的啟示

### 5.1 可借鏡的

1. **Google 的原生檔案路線跟你們的架構是同構的，而且 Google 證明它可行**。Slides「套用目前主題填內容」、Docs「在文件排版詞彙內生成」、Gemini Canvas「先生成結構化內容再匯出原生檔」——這正是「LLM 產生結構化中間表示 → 序列化成 ODF」。你們不是在做巨頭的殘缺版，而是在做巨頭四條產線中**唯一保留可編輯性的那條**，且 ODF 版沒有人做。
2. **主題/版型系統是小團隊品質保證的最佳槓桿**。Google Slides 的品質下限由主題保證，Vids 由 50 套模板保證。兩人團隊做 5–10 套高品質 ODF 樣式（styles.xml 層級的配色、字型、master page），讓 LLM 只做「選版型+填內容」，比讓 LLM 自由排版可靠一個數量級。這等於把 render-critique 的成本歸零：版型是人驗證過的，天生不會壞。
3. **NotebookLM 的「格式二分法」值得直接抄**：Detailed Deck（自讀型，文字完整）vs Presenter Slides（講者型，大字少文）。這一個選項就解決了「簡報要多少字」這個 LLM 最容易做錯的判斷，成本只是一個 enum。
4. **大綱先行、可在大綱層修改**（Vids 的做法）：先給使用者看章節/頁面大綱，確認後才生成完整檔案。這在 token 成本與返工成本上都划算，而且是純 prompt 工程，零額外技術。
5. **`@` 引用來源檔案的 grounding**：生成內容錨定在使用者提供的文件上，是 Google 全線產品的共同支柱，也是降低幻覺最便宜的方法。Python 讀 odt/docx/pdf 塞進 context 就能做。
6. **對不確定性誠實**（Vids 的「Add your media」佔位符）：模型沒把握的圖片位置就放明確的佔位框，比生成一張錯的圖好。

### 5.2 做不到的（別浪費時間）

- **Nano Banana Pro 級的像素設計引擎**：那是 Gemini 3 規模的訓練成果，是 NotebookLM 品質跳升的全部來源，無法用開源模型+prompt 復刻。接受「你們的設計上限=版型的上限」。
- **Veo 級影片、AI avatar、授權圖庫**：資本與授權問題，不是工程問題。
- **Workspace 通路護城河**：Google 的功能再平庸也有 30 億人順手用到。開源工具的對應打法是相反的：CLI/API 可腳本化、可自架、格式自主——這些恰好是 Google 全都不給的。

### 5.3 根本不該學的

- **像素路線本身**。NotebookLM 把投影片渲染成圖片，漂亮但不可編輯、不可及格於無障礙檢測、鎖死使用者——這與 ODF 開放文件格式的存在理由正面衝突。你們參加的是 ODF 競賽，「輸出永遠是語意完整、逐字可編輯的原生檔」不是技術妥協，是相對於 Google 最強產品的**差異化主張**，評審面前可以直接打「NotebookLM 給你一張漂亮的圖，我們給你一份真正的文件」。
- **無約束的 LLM 自由排版**。Google 有 Nano Banana Pro 的推理能力兜底才敢放手；開源 LLM 直接生成座標與版面，產出會不穩定且醜。約束在版型內生成。
- **把迭代做成「整份重生成」**（NotebookLM 的 Retry 模式）。因為你們輸出原生 ODF，使用者用 LibreOffice 就能改，天然擁有 Google 像素路線給不了的編輯體驗——不要用「重骰」交互糟蹋這個優勢；局部修改（改某頁、換版型、調語氣）應該操作在中間表示上再重新序列化。

---

## 資料來源

- [Collaborate with Gemini in Google Slides（官方說明）](https://support.google.com/docs/answer/14355071?hl=en)
- [Generate presentations in the Gemini app / Canvas 匯出 Slides](https://workspaceupdates.googleblog.com/2025/10/generate-presentations-in-gemini-app.html)
- [Introducing Nano Banana Pro in Slides, Vids, Gemini app, and NotebookLM（Workspace Updates）](https://workspaceupdates.googleblog.com/2025/11/workspace-nano-banana-pro.html)
- [Nano Banana Pro: Gemini 3 Pro Image（Google 官方）](https://blog.google/innovation-and-ai/products/nano-banana-pro/)
- [Create fully stylized documents using Gemini in Google Docs（Workspace Updates）](https://workspaceupdates.googleblog.com/2024/12/help-me-create-in-google-docs.html)
- [8 ways to make the most out of Slide Decks in NotebookLM（Google 官方）](https://blog.google/innovation-and-ai/models-and-research/google-labs/8-ways-to-make-the-most-out-of-slide-decks-in-notebooklm/)
- [Generate a Slide Deck in NotebookLM（官方說明）](https://support.google.com/notebooklm/answer/16757456?hl=en)
- [NotebookLM Video Overviews 與 Studio 升級（Google 官方）](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-video-overviews-studio-upgrades/)
- [NotebookLM Cinematic Video Overviews 三模型架構（MindStudio 分析）](https://www.mindstudio.ai/blog/what-is-notebooklm-cinematic-video-overviews)
- [How to Edit NotebookLM Slides（Alai 分析，圖片式輸出限制）](https://getalai.com/blog/how-to-edit-notebooklm-slides)
- [NotebookLM app adds slide and infographic customization（9to5Google）](https://9to5google.com/2026/02/06/notebooklm-slide-customization/)
- [Plan your video with AI in Google Vids（官方說明）](https://support.google.com/docs/answer/15067819?hl=en)
- [Google Vids 產品頁](https://workspace.google.com/products/vids/)
- [Google Vids adds AI avatars, launches consumer version（TechCrunch）](https://techcrunch.com/2025/08/27/google-vids-adds-ai-avatars-to-its-video-editor-and-launches-a-consumer-version/)
- [Nano-Banana Pro Prompting Guide（Google AI on DEV，Thinking 機制）](https://dev.to/googleai/nano-banana-pro-prompting-guide-strategies-1h9n)
- [NotebookLM Slide Decks and Infographics 深度分析（LaoZhang AI）](https://blog.laozhang.ai/en/posts/notebooklm-slide-decks-infographics)

---

# 第3部：模板巨頭（Canva / Adobe / Beautiful.ai / Pitch / Gamma）

# 模板優先設計巨頭的 AI 生成機制研究報告

> 資料來源以官方文件、官方部落格、工程部落格與創辦人訪談為主；部分為廠商行銷內容或第三方評測，已於文中標註可信度。完整來源清單見文末。

---

## 1. Canva Magic Design / Magic Studio

### 1.1 管線
- **輸入**：文字 prompt，或上傳圖片/logo/媒體檔；簡報則是「主題描述」或貼上的長文。
- **中間階段**（一般設計）：prompt → OpenAI API + Canva 自研設計引擎 → 在 **1 億+ 模板與素材庫**中做語意過濾/檢索 → 對命中的模板做內容替換與風格調整 → 一次回傳 **8~12 個可編輯的候選設計**。官方教學明確描述為「讓 AI 依 prompt 過濾選項」而非從零繪製——本質是 **retrieve-and-adapt（檢索後改編）**，不是自由生成。
- **中間階段**（Magic Design for Presentations）：prompt → LLM 產生**大綱**（使用者可審閱、重排、增刪）→ 按下 Generate design → 依內容型態挑選版面模板並填入 → 產出整副投影片。
- **輸出格式**：Canva 專有的雲端文件（web 編輯器內的原生設計物件），可再匯出 PPTX/PDF/PNG。不是開放格式。
- **生成 vs 檢索的分界**：版面與排版 = 檢索模板；文案 = LLM 生成（Magic Write）；圖像 = 需要時才生成（Dream Lab，源自收購的 Leonardo.ai）。有第三方資料（求職面試整理站，可信度中等）稱其用 CLIP 式多模態 embedding + 向量資料庫做模板檢索。

### 1.2 設計品質的來源
- 核心是**人類設計師預製的巨量模板庫**，AI 只負責「找對的模板 + 填對的內容」。
- 官方明言生成式 AI 是「由內部設計師小組訓練並評估」的。
- **Brand Kit**：品牌色、字型、logo、Brand Voice（文案語氣指引）、**Brand Templates**（加入 Brand Kit 的模板會被 Canva AI 優先取用，實現 on-brand 生成；管理員可用權限開關控制「on-brand 生成用哪個模型」）。
- ML 個人化：依使用者編輯歷史、地區、季節、語言、裝置做推薦與 fallback（Canva 工程部落格）。

### 1.3 變化 vs 一致性
- 每次給 8+ 個候選，變化來自「命中不同模板」而非同一版面的隨機擾動。
- 一致性靠 Brand Kit 一鍵套用品牌色與字型；缺點是產出有明顯「Canva 味」——因為底層就是公共模板庫，非 Pro 用戶容易與別人撞款。
- 使用者控美術方向的方式：挑候選、選風格、套 Brand Kit，而不是用文字描述美學。

### 1.4 品質保證機制
- **模板天生不會壞**是第一道防線：版面是人排好的，AI 只換內容。
- 生成模型上線前經內部設計師評估。
- 推薦系統有「視覺化模型報告」制度：工程團隊發現純數值指標會漏掉「整排推薦長得一模一樣」這類體驗災難，所以人工看渲染結果（Canva 工程部落格,是公開資料中最接近 render-critique 的做法,但用於離線模型評估,不是每次生成的迴圈）。
- 推薦無資料時有地區/季節/平台 fallback,寧可退回保守結果。

### 1.5 編輯/迭代 UX
- 生成物直接落在完整的 Canva 編輯器裡,所有元素可拖拉改動;可再叫 Magic Write 改文案、Magic Edit 改圖、一鍵換模板重排。
- 「生成只是起點,編輯器才是產品」——AI 是把人送進編輯器的漏斗。

### 1.6 商業定位與護城河
- 護城河 = 模板/素材庫（1 億+）+ 品牌管理（Teams/Brand Kit）+ 免費增值分發 + 印刷/排程/發佈整合。AI 功能被用超過 50 億次，但定位始終是「加速既有工作流」，不是取代模板庫。

---

## 2. Adobe Express / Firefly 生成式模板

### 2.1 管線
- **輸入**：文字 prompt（Text to Template，在 Express 首頁 Quick actions → Generative AI → Generate template）。
- **中間階段**：prompt → **Firefly Design Model**（專門的「設計生成模型」，以**數十萬個高品質 Adobe Express 人工模板**訓練）→ 結合「專業版面技術 + Firefly Image Model（生圖）+ Adobe Stock（素材）+ Adobe Fonts（字型）」合成模板 → 回傳多個候選。
- **輸出格式**：**完全可編輯、有圖層結構的 Express 模板**——文字、圖片、形狀都是獨立圖層，支援印刷/社群/網頁各種長寬比。這是與 Canva 最大的差異：Adobe 真的訓練了一個「生成版面結構」的模型，而不是檢索現成模板。
- 姊妹技術 Firefly Vector Model：生成的向量圖會**自動邏輯分組**（一棵樹的所有路徑自動群組），維持可編輯性。

### 2.2 設計品質的來源
- **以人類模板為訓練資料的專用生成模型**（Design Model）——品質上限由訓練集（人工策展的 Express 模板）決定。
- 生態系資產：Adobe Fonts、Adobe Stock、Firefly 生圖，全部商業安全授權（訓練資料為 Adobe Stock 授權圖 + 公版內容）。
- 內建排版啟發式：文字自動放置與縮放，**文字顏色自動選擇以最大化可讀性**。

### 2.3 變化 vs 一致性
- 每次生成多個候選；因為是模型生成而非檢索,理論上版面組合是新的,變化度高於 Canva。
- 一致性靠企業端 Firefly Custom Models（用自家品牌資產微調）與 Creative Cloud 品牌資產庫。
- 使用者控制：prompt + 長寬比 + 事後逐圖層編輯。

### 2.4 品質保證機制
- 訓練資料策展（只用高品質人工模板）是主要 QA。
- 硬編碼可讀性規則（文字對比）。
- 輸出是可編輯圖層 = 錯了人可以修，Adobe 把「最後一哩品質」交給使用者與編輯器。
- 公開資料中**沒有** render-critique 迴圈的證據。

### 2.5 編輯/迭代 UX
- 生成後選一個候選 → Edit template → 進 Express 編輯器逐圖層改；可接續用 Generative Fill、Text Effects 等 Firefly 功能。
- 企業可走 Firefly Services REST API 做批量管線（Generate、Translate、data merge 節點等）。

### 2.6 商業定位與護城河
- 護城河 = 商業安全的訓練資料（可對企業做侵權賠償承諾）+ Creative Cloud 生態（PS/AI/PR 互通）+ 字型與圖庫資產 + 企業 API。定位是「專業創意管線的入口」，Express 對標 Canva 搶輕量用戶。

---

## 3. Beautiful.ai（Smart Templates / 自適應版面規則）

### 3.1 管線
- **輸入**：DesignerBot（2023 推出，OpenAI 技術）接受文字 prompt，後續版本可上傳 PDF/Word/網頁作為 context。
- **中間階段**：prompt/文件 → LLM 產生大綱與內容 → 內容被填入 **Smart Slides**（300+ 種人工設計的智慧版型：數據圖表、比較、時間軸、大數字、團隊頁……）→ 每一頁都經過**規則式設計引擎**即時排版 → 30-60 秒產出整副簡報。圖片可由 DALL-E 生成。
- **輸出格式**：Beautiful.ai 雲端專有格式,可匯出 PPTX/PDF。

### 3.2 設計品質的來源
- **規則引擎 + 人工版型**,不是 ML 版面推薦,更不是 LLM 自由排版。每個 Smart Slide 內建設計邏輯:加內容時自動調整間距、字級、對齊、視覺比例;字排在深色圖上會自動加遮罩或轉白字;全簡報維持一致的字級層級。
- 這套「design best practices 寫成程式碼」是 2018 年就有的核心技術,生成式 AI(DesignerBot)只是 2023 年疊上去的內容層。

### 3.3 變化 vs 一致性
- **刻意犧牲變化換一致性**。版型只有 300+ 種,同類內容長得類似;第三方評測稱其輸出「保守但穩定專業,不會出現競品偶爾的 AI 詭異頁」。
- 使用者可換版型、改主題色與字型,但**不能覆寫底層版面規則**——平台故意限制自由編輯,讓使用者「不可能把設計改壞」。

### 3.4 品質保證機制
- **結構性保證**:每一頁生成內容都必須通過規則引擎,壞版面在架構上不可能出現。不需要 render-critique,因為輸出空間被規則封死。這是「模板天生不會壞」的最極致版本。

### 3.5 編輯/迭代 UX
- 受限編輯器:改內容、換版型、調主題,系統即時重排;不能自由拖拉像素。
- 有 slide 級 AI 輔助(改寫、單頁重生成),評測認為這比整副重生成更實用。

### 3.6 商業定位與護城河
- 定位「不會設計的商務人士」與需要品牌一致性的團隊。護城河 = 300+ 版型各自的自適應規則(多年設計工程累積,難以快速複製)+ 團隊品牌控管。弱點:天花板低,進階用戶會覺得被綁死。

---

## 4. Pitch(含 Gamma 對照:生成式版面 vs 模板填充)

### 4.1 Pitch 管線
- **舊版 AI 生成器**:prompt(≤450 字元)→ 生成幾頁草稿 → 圖片來自 Unsplash → 使用者切換字型組合/色盤(可 shuffle)→ 進編輯器。定位明確是「草稿產生器」。
- **Pitch Agent(2025)**:prompt + **挑選模板** + 附加檔案 → Agent 以**你的模板作為設計標準**(design standard)建整副簡報——官方說法:「你的模板不只是 hex 色碼和字型堆疊,而是你們團隊已經同意屬於你們的版面、模式與設計決策」。模板沒涵蓋的頁型,從 150+ 補充版面中挑選以維持一致。新團隊沒模板?Agent 會**爬你的官網自動生成品牌模板**(色彩、字型、logo、圖像風格)。
- **輸出格式**:Pitch 雲端專有格式,可匯出 PPTX/PDF。

### 4.2 設計品質來源
- 150+ **專家手工模板** + 模板即約束的 Agent 架構。生成圖像會「配合模板的藝術方向」生成。設計品質 = 人類模板 × LLM 服從模板。

### 4.3 變化 vs 一致性
- 一致性是賣點:同一模板下所有產出同風格。變化靠換模板、換字型組合/色盤。美術方向控制權在「模板選擇」這一層,不在 prompt。

### 4.4 品質保證
- 模板約束 + 版面庫 fallback,無公開的 render-critique 證據。內容層由聊天迭代修正。

### 4.5 編輯/迭代 UX
- **聊天式編輯**是主打:「把這頁拆成兩頁」「合併這三頁成摘要」「換一張符合模板藝術方向的圖」;另有完整拖拉編輯器。Agent 還能當「思考夥伴」(找出簡報沒回答的客戶異議、起草跟進郵件)。

### 4.6 商業定位與護城河
- 定位銷售/募資團隊的協作工作區:品牌庫、共享模板、簡報分析(誰看了哪頁)。護城河在工作流與品牌治理,不在生成技術。

### 4.7 Gamma 對照:設計引擎與模板填充的根本差異
(注意:以下部分來源為 Gamma 自家 SEO 內容與第三方部落格,技術聲稱難以獨立驗證)
- **基本單位不同**:Gamma 放棄固定 16:9 slide,改用**可變長度的 card**(創辦人 Grant Lee:「card 可以任意長寬,你解除了固定尺寸的約束」——Sacra 訪談)。輸出是 **web 原生、響應式**的文件,可嵌入影片、Airtable 等活內容;PPTX/PDF 匯出是降級品(互動元素、折疊區塊會消失,複雜卡片版面需手動修)。
- **版面來源不同**:模板填充工具(Canva/Pitch/Beautiful.ai)把內容塞進人排好的版面;Gamma 宣稱用**生成式版面引擎**——依內容語意(時間軸?比較?流程?)即時組合版面,2024 年還是 template matching,2026 年改為 generative layout(flowith 分析,廠商周邊來源)。行銷材料稱同時動用 20+ 模型分管文字、選圖、版面、視覺一致性,並用「parallel narrative graph」平行生成所有卡片(30-55 秒)。
- **一致性機制**:靠 **Theme(= design tokens:色彩、字型、logo)**而非模板;可從 PPTX 匯入自動抽取品牌色/字型/logo 建 theme。設計約束比模板鬆,所以偶爾會出怪版面——這正是模板派避掉的風險。
- **編輯**:Edit with AI(改寫、視覺化成時間軸/表格、拆欄)+ 卡片編輯器,**沒有像素級控制**。
- **商業**:約 50 人做到 $100M ARR、估值 $2B+,靠「產出本身可分享」的口碑機制與 1000+ 微型網紅;Grant Lee 自認在打造「durable GPT wrapper」。

---

## 5. 對「自然語言 → 原生 ODF 檔案」開源工具的啟示
(前提:Python、兩人學生團隊、輸出 .odt/.odp/.ods 原生檔)

### 5.1 可借鏡(而且做得到)
1. **全部巨頭收斂到同一個架構:LLM 管內容與結構,確定性系統管外觀。** 沒有任何一家讓 LLM 直接畫版面到最終像素。你們的對應做法:LLM 產出結構化中間表示(大綱/JSON),由確定性的 Python 產生器套 ODF 樣式輸出——這個分層本身就是業界共識,堅持住。
2. **Beautiful.ai 的規則引擎是兩人團隊最該抄的**:它不需要模型、不需要模板庫,只需要把「間距、字級層級、對比度、深色圖上轉白字」等規則寫成程式碼。ODF 的 style 機制(`office:styles`、段落/字元樣式繼承)天生適合承載這套規則。
3. **Pitch 的「模板即設計標準」**:讓使用者丟一個現有 .odt/.odp,抽取其樣式定義當作生成約束——ODF 是開放 XML,抽樣式比 Pitch 爬網站容易得多。這可以成為殺手級功能。
4. **Canva/Gamma 的大綱檢查點**:先給大綱讓使用者確認再生成全文,便宜、有效、降低重生成成本。
5. **Brand Kit 的極簡版**:一個 YAML/JSON 檔定義色彩、字型、logo 路徑,映射到 ODF 樣式——design tokens 概念,兩人一週能做完。
6. **render-critique 迴圈是你們的不對稱優勢**:巨頭因為有模板保底,大多不做逐次渲染驗證;你們可以用 `soffice --headless` 轉 PNG → 丟給 VLM 或跑對比度/溢出檢查 → 自動修正。加上 ODF schema 驗證與 LibreOffice round-trip 測試,這是小團隊能做出的真 QA,也是競賽答辯的好故事。
7. **Adobe 的可讀性啟發式**(文字自動選色保對比)——幾十行程式碼的事,直接實作。
8. **多候選輸出**(Canva 的 8 個):同一內容套 3~5 組預製樣式主題,讓使用者挑,比單一輸出的體感好非常多。

### 5.2 做不到(不要嘗試)
- **億級模板庫與 CLIP 向量檢索**(Canva)——沒有資產也沒有流量回饋迴路。
- **訓練專用設計生成模型**(Firefly Design Model 用數十萬人工模板訓練)——沒有資料、沒有算力、沒有商業安全授權。
- **內部設計師評估小組、20+ 模型平行編排**——用 3~5 套自己精心打磨的樣式主題代替:寧可 5 套完美,不要 50 套平庸。

### 5.3 根本不該學
- **Gamma 的 web 原生輸出**:它放棄固定版面換取響應式,匯出 PPTX 就降級。你們的價值主張恰恰相反——**原生開放格式、離線可用、政府文書可交付**。ODF 的固定版面不是包袱,是產品定位。
- **自建編輯器**:巨頭全靠自家編輯器鎖住用戶;你們的「編輯器」就是 LibreOffice,生成後可編輯性由 ODF 格式本身保證(對應 Adobe「可編輯圖層」的哲學,但用開放標準實現)。這也是護城河差異:他們鎖格式,你們開放格式——開源工具的正確姿勢。
- **追求無限版面變化**(Gamma 的生成式版面):文件格式(尤其公文、報告)的使用者要的是穩定合規,不是驚喜。Beautiful.ai 證明「保守但永不出錯」是可行的產品定位,對 ODF 場景更是如此。
- **freemium 成長機制、點數制、雲端協作**——與兩人開源專案的資源和目標無關。

### 5.4 一句話總結
巨頭的共同秘密是「AI 不做設計,AI 只是把內容送進人類預先做好的設計系統」;對你們而言,那個「設計系統」就是一小組打磨到位的 ODF 樣式主題 + 一個規則引擎 + 一條 LibreOffice 渲染驗證迴圈——這三件事全在兩人團隊的能力半徑內,而且加起來就是巨頭架構的完整縮影。

---

## 來源

**Canva**:[Magic Design 官方頁](https://www.canva.com/magic-design/)、[Magic Design 使用說明](https://www.canva.com/help/use-magic-design/)、[Brand Kit 與 on-brand 生成](https://www.canva.com/help/create-on-brand-designs/)、[Brand Voice](https://www.canva.com/help/brand-voice/)、[Magic Studio 發佈新聞稿](https://www.canva.com/newsroom/news/magic-studio/)、[OpenAI × Canva 案例](https://openai.com/index/canva/)、[Canva 工程部落格:推薦系統失效處理](https://www.canva.dev/blog/engineering/recommender-systems-when-they-fail-who-are-you-gonna-call/)、[Shopify:Canva AI 十大功能](https://www.shopify.com/blog/how-to-use-canva-ai)、[MakeUseOf 教學](https://www.makeuseof.com/canva-how-to-use-magic-design/)、[Canva Design School:Magic Design](https://www.canva.com/designschool/tutorials/new-features/magic-design/)、[Canva AI 簡報說明](https://www.canva.com/help/using-magic-presentations/)

**Adobe**:[Text to Template 官方文件](https://helpx.adobe.com/express/web/create-with-templates/text-to-template.html)、[Adobe MAX 2023:The future is Firefly](https://blog.adobe.com/en/publish/2023/10/10/future-is-firefly-adobe-max)、[次世代 Firefly 模型新聞稿](https://news.adobe.com/news/news-details/2023/adobe-releases-next-generation-of-firefly-models)、[Firefly Services](https://business.adobe.com/products/firefly-business/firefly-services.html)、[Firefly 總覽](https://helpx.adobe.com/firefly/web/get-started/learn-the-basics/adobe-firefly-overview.html)

**Beautiful.ai**:[Smart Slides](https://www.beautiful.ai/smart-slides)、[DesignerBot 發佈](https://www.beautiful.ai/blog/introducing-designerbot-ai-presentations)、[DesignerBot PR](https://www.prnewswire.com/news-releases/beautifulai-launches-designerbot-to-automatically-create-rich-presentations-about-anything-and-everything-using-generative-ai-301718769.html)、[Contextual AI PR](https://www.prnewswire.com/news-releases/beautifulai-adds-contextual-ai-capabilities-to-designerbot-the-next-evolution-of-generative-ai-301906933.html)、[Aumiqx 評測](https://aumiqx.com/ai-tools/beautiful-ai-review-presentation-maker-2026/)

**Pitch**:[Pitch Agent 發佈](https://pitch.com/blog/introducing-pitch-agent)、[AI 簡報生成說明](https://help.pitch.com/en/articles/8541722-start-a-new-presentation-with-ai)、[pitch.com](https://pitch.com/)

**Gamma**:[Sacra:Grant Lee 訪談](https://sacra.com/research/grant-lee-gamma-presentation-primitives/)、[Lenny's Newsletter:Gamma 專訪](https://www.lennysnewsletter.com/p/how-50-people-built-a-profitable-ai-unicorn)、[flowith:Gamma 2026 管線分析](https://flowith.io/blog/gamma-app-2026-one-sentence-prompt-polished-deck-60-seconds/)(廠商周邊,存疑)、[Gamma 卡片版面指南](https://gamma.app/explore/content/guides/gamma-flexible-card-layout-presentations)(官方 SEO 內容)、[Gamma 主題/字型說明](https://help.gamma.app/en/articles/11029150-can-i-add-my-own-colors-and-fonts-to-gamma)、[Gamma 匯出說明](https://help.gamma.app/en/articles/8022861-what-s-the-easiest-way-to-export-my-gamma)、[SketchBubble 深度評測](https://www.sketchbubble.com/blog/gamma-explained-a-comprehensive-deep-dive-into-the-ai-powered-presentation-platform/)

---

# 第4部：AI 原生玩家（ChatGPT / Claude / Manus / Genspark）

# AI 原生文件/簡報生成玩家研究報告

*(研究日期: 2026-07-06; 方法: 官方文件、工程部落格、GitHub 原始碼、第三方實測評論交叉比對)*

## 總覽對照表

| 玩家 | 設計媒介 | 輸出格式 | 設計品質來源 | 視覺 QA 迴圈 |
|---|---|---|---|---|
| ChatGPT (code interpreter/agent) | python-pptx / python-docx 程式碼 | 原生 .pptx/.docx | 無 (LLM 裸寫座標) | 無 |
| ChatGPT for PowerPoint (2026/5) | 現有簡報的 template/placeholder | 原生物件寫入現有檔 | 使用者自己的模板 | 無 (人在迴圈中) |
| Claude skills (pptx/docx) | PptxGenJS / docx-js 程式碼 | 原生 .pptx/.docx | prompt 內嵌設計鐵則 | **有** (render→subagent 視覺檢查→修) |
| Claude in PowerPoint (2026/2) | 現有 slide master | 原生物件寫入現有檔 | 使用者的 slide master | 無 |
| Manus Slides | 每頁一份 HTML/CSS | HTML/pptx/Slides/PDF | HTML 自由生成 + Nano Banana Pro 圖像 | 有 review/refine 步驟 |
| Genspark AI Slides | 每頁 HTML/CSS | web/pptx/PDF/Slides | 模板庫 + HTML 自由生成 | 事實查核有, 視覺 QA 不明 |

---

## 1. OpenAI ChatGPT

### 管線
目前並存三條路:
- **聊天內建 (免費也有)**: prompt → LLM 撰寫 python-pptx / python-docx 程式碼 → code interpreter 沙盒執行 → 原生 .pptx/.docx 下載。沒有中間視覺層。
- **Agent mode (2025/7 起)**: 虛擬電腦環境, 可研究再產檔, 但產檔本體仍多走 python-pptx 路線。Leon Furze 的實測評為 "in no way successful enough to deploy in the real world"。
- **ChatGPT for PowerPoint 增益集 (2026/5/21 上市)**: 側欄常駐 PowerPoint 內, 直接寫入現有簡報的 text placeholder、建立原生 shapes、套用當前模板樣式, 全免費層可用, 且支援 Skills (可重用的簡報工作流 playbook) — 明顯對標 Anthropic 的做法。

### 設計品質來源
沙盒路線幾乎沒有: python-pptx「沒有設計模板、圖庫、排版演算法、視覺構圖邏輯」, 每個座標都靠 LLM 憑空計算。增益集路線則把設計問題**外包給使用者現有的模板** — 這是最聰明的一步棋。

### 變化 vs 一致性
沙盒輸出千篇一律: 「無品牌的白底純文字投影片」「about as beginner as a presentation gets」。增益集的一致性直接繼承使用者的 slide master, 使用者對美術方向的控制 = 換模板。

### 品質保證
無 render-critique 迴圈。程式碼跑得動就交件, 溢版、重疊、對比不足一律不檢查。這是它品質聲譽差的直接原因。

### 編輯/迭代
原生檔的優點在此顯現: 輸出全是真 text box, 使用者拿到就能改。增益集內可對話式迭代。

### 商業定位與護城河
護城河是 8 億使用者的分發管道, 不是文件品質。2026 年的策略轉向說明 OpenAI 自己也承認: 與其教 LLM 做設計, 不如寄生在 Microsoft 的模板生態裡。

---

## 2. Anthropic Claude

### 管線 (2026 年中已是五條並行)
1. **Artifacts**: prompt → HTML/CSS/JS 單檔 → 瀏覽器即時渲染。純 web, 不出原生檔。
2. **官方 pptx skill (重要更新)**: 公開 repo (`anthropics/skills`, source-available) 顯示架構**已從早期的 HTML→render→html2pptx 轉換, 演進為 PptxGenJS 直接生成原生檔**。現行 skill 目錄只剩 `SKILL.md`、`editing.md`、`pptxgenjs.md`, 沒有 html2pptx script。設計靠 prompt 鐵則, 品質靠事後渲染檢查 (見下)。
3. **官方 docx skill**: 新檔用 docx-js 生成; 改舊檔走 `unpack.py` 解壓 XML → 直接編輯 → `pack.py` 重壓 → `validate.py` schema 驗證 + 自動修復。支援 tracked changes (`<w:ins>`/`<w:del>`, 作者署名 "Claude")。
4. **Claude Cowork (2026)**: 桌面代理, 操作檔案系統直接交付 .pptx。SlideSpeak 實測觀察到它「寫了一支約 4,930 字元的 python-pptx build script」— 產出「大多是標題+條列+基本圖表」, 弱項是版面變化、客製圖形、品牌一致性。
5. **Claude in PowerPoint 增益集 (2026/2) + Claude Design (2026/4, Labs)**: 前者「先讀取開啟中簡報的 slide master 與格式規則再生成」, 圖表是原生 PowerPoint 物件非圖片; 後者以 Opus 4.7 做視覺創作, 可讀 codebase 套用團隊 design system, 匯出 PDF/URL/PPTX 或送進 Canva 繼續編輯。

### 設計品質的來源
不是模板庫, 是**寫死在 prompt 裡的設計法則** (pptx skill 原文): 60-30-10 色彩權重、「每張投影片都要有視覺元素」、內文靠左不置中、0.5" 最小邊距、標題 36-44pt / 內文 14-16pt, 以及一條很有自覺的禁令: 「**NEVER use accent lines under titles — these are a hallmark of AI-generated slides**」。

### 品質保證機制 (業界唯一明文化的 render-critique 迴圈)
skill 規定: 生成 → 轉圖片 → **派 subagent 視覺檢查** (重疊、對齊、對比、留白) → 修 → 重驗受影響頁 → 直到整輪無新問題。原文: 「**Your first render is almost never correct. Approach QA as a bug hunt.**」

### 變化 vs 一致性 / 編輯 UX
skill 明令禁止整份簡報重複同一版型 (追求變化); 一致性靠色彩策略與字體配對規則。編輯走 unpack→edit XML→pack, 是所有玩家中唯一把「精修既有原生檔」當一等公民的。

### 商業定位與護城河
護城河 = 模型本身 + skills 開放策略 (公開原始碼讓生態複用, 換取 agent 標準話語權)。文件技能是 Claude Code / Cowork / API 的通用能力層, 不是獨立產品。

---

## 3. Manus AI Slides

### 管線
prompt → 研究模式 (Wide Research 平行子代理) → 大綱與每節要點 → **逐頁生成 HTML/CSS 程式碼** (實測 8 頁簡報中, 程式碼生成佔近 6 分鐘, 是最長步驟) → 「review and refine content and layout」 → 交付。輸出 web 預覽, 可匯出 .pptx / Google Slides / PDF。全程 5-15 分鐘。

### 設計品質的來源 — 為什麼比 python-pptx 輸出好看
三個疊加因素:
1. **HTML/CSS 是 LLM 訓練語料裡最豐富的視覺語言**, 而 OOXML 語料稀少 — 模型「會寫」漂亮的 CSS, 卻「不會寫」漂亮的 OOXML。
2. **瀏覽器是免費的排版引擎**: flexbox/grid 自動處理對齊與溢版, web 字體、漸層、陰影、圓角全部即取即用; python-pptx 世界裡每一個座標都要 LLM 自己當排版引擎硬算, 算錯就重疊溢版。
3. **Nano Banana Pro 圖像模型**生成視覺素材 — 實測指出開關此功能是「有設計感」與「傳統 PowerPoint 感」的分水嶺。

### 變化 vs 一致性
可自選模板或讓系統自動決定, 也可**上傳自己的 .pptx 模板**與資料檔 (CSV/Excel)。不指定時每次生成風格漂移, 一致性偏弱。

### 品質保證
管線內建 review/refine 步驟 (細節未公開); Wide Research 用平行子代理「確保每一頁都和第一頁一樣準確」— 重心在內容正確性多於像素級視覺。

### 編輯/迭代 UX
三軌: 對話式改稿 (適合大改版面/加頁)、點選元素級 AI 調整、手動編輯。匯出 .pptx 後就脫離 Manus 環境, 產生工作流斷點。宣稱匯出「fully editable」, 但 HTML→pptx 的 conversion drift (字體替換、位移、複雜元素點陣化) 是這條路線的結構性通病。

### 商業定位與護城河
泛用代理 (簡報只是其中一個 playbook), credits 計費 ($34–167/月, 一份 10 頁簡報約 200 credits), 被評為「成本不可預測」。護城河是自主研究能力, 不是設計 — 評測明言它「犧牲了專門工具的設計打磨與品牌集中管理」。

---

## 4. Genspark AI Slides

### 管線
Super Agent 架構: 任務分析 → 多代理分工 (研究 / 內容生成 / **事實查核** / 外部動作), 號稱 mixture-of-agents 調度 80+ 模型 (GPT-5.1, Claude Opus 4.6, Gemini 3 Pro, Grok4)。**投影片底層是 HTML/CSS, 不是畫布上的向量物件**; 匯出時以 PptxGenJS 類轉換產生 .pptx (需付費方案)。

### 設計品質的來源
模板庫 + HTML 自由生成的混合。但競品評測毒舌: 「模板是功能性的而非精緻的 — 給 AI 一個可填充的結構, 但不會隨內容變化智慧調整」;「generic layouts and limited visual polish」是被一致點名的弱項。

### 變化 vs 一致性
**無 brand kit、無 URL 抽取品牌、無自動樣式強制** — 品牌一致性全靠手動, 是它對上 Gamma/Beautiful.ai 類專門工具最明顯的短板。使用者控制美術方向的手段是 Guide Mode 五階段諮詢 (Strategy → Substance → Structure → Design → Build), 適合高風險簡報。

### 品質保證
有專職事實查核代理 (內容層); 視覺層 QA 未見公開機制, 而是靠事後修 bug — changelog 顯示曾修復「匯出字體樣式不一致、圖片顯示問題」並「重寫 PPTX 匯出的圖層排序演算法」, 反面印證 HTML→pptx 轉換有多脆。

### 編輯/迭代 UX
Creative Mode 的 **Mark and Edit**: 點選投影片任意區域、描述修改, AI 只重生該區域、保留其餘設計 — 這是區域級 inpainting 式編輯, 體驗領先。(注: 競品 presentations.ai 的評測稱其「無對話式迭代」, 與官方文件衝突, 應以官方為準但可見功能是近期才補上。)

### 商業定位與護城河
全平台共用 credits ($19.99–199.99/月; 一份簡報約消耗 250-450 credits, 與其他任務競爭額度)。護城河是多代理編排與一站式工作區, **不是**簡報品質 — 匯出保真度「未被獨立評測確認達到 production-grade」。

---

## 5. 共同模式: 為什麼 agent 產品全部收斂到 HTML, 又犧牲了什麼

**收斂原因 (三個結構性因素):**
1. **訓練資料不對稱**: LLM 看過的 HTML/CSS 是天文數字, OOXML/ODF XML 極少。perspectives.plus 的分析一針見血: 模型缺乏 OOXML 語料, 「迫使開發者以 HTML 為中間層再轉換 — 產出視覺上精緻、結構上空洞的文件」。
2. **排版引擎外包**: .pptx/.odp 是絕對座標格式, 沒有 reflow — LLM 必須自己當排版引擎; HTML 把這件事交給瀏覽器。
3. **QA 可行性**: HTML 一行指令就能截圖餵回 vision 模型形成 render-critique 迴圈; 這是 agent 時代品質控制的基礎設施。

**犧牲了什麼 (匯出原生格式時):**
- **Conversion drift**: 字體替換、位置漂移、複雜表格/圖表被點陣化成圖片 (Genspark 官方承認、Slidev/Marp 匯出同病)。
- **結構空洞化**: 沒有 theme、slide layout、placeholder、SmartArt、原生 chart 物件 — 「本質上是圖片而非可編輯文件」, 後續維護者無法換模板一鍵改版。
- **編輯斷鏈**: 交付後客戶要逐行改字時, HTML 出身的 pptx 遠不如原生生成的好改。

**2026 年的新收斂 — 鐘擺回擺**: OpenAI 與 Anthropic 在 2026 上半年不約而同推出 Office 增益集, 改為「**先讀使用者現有的 slide master, 再以原生物件寫入**」。這等於承認: 設計品質最便宜的來源不是 AI, 是使用者已經有的模板。同時 Anthropic 的 pptx skill 也從 HTML 中間層退回 PptxGenJS 直寫原生 + 視覺 QA 補強。純 HTML 路線 (Manus/Genspark) 則繼續守住「一條 prompt 到成品」的體驗端。

---

## 6. 對「自然語言 → 原生 ODF」開源工具 (Python, 兩人學生團隊) 的啟示

### 可借鏡 (而且成本極低)
1. **把 Claude pptx skill 的設計鐵則整套移植進你的 system prompt**: 60-30-10 色彩、每頁必有視覺元素、內文靠左、字級區間、禁止「標題下加裝飾線」這類 AI 特徵。這些是 Anthropic 花大量迭代淬鍊出的 prompt 資產, 公開可讀, 零成本可抄, 對 ODF 同樣成立。
2. **複製 render-critique 迴圈 — 這是你和「python-pptx 級輸出」拉開差距的關鍵**: `soffice --headless --convert-to png/pdf` 就能渲染 .odp/.odt, 截圖餵 vision 模型檢查重疊/溢版/對比, 修完重驗。Anthropic 證明了「第一次渲染幾乎必錯, QA 當抓蟲」是通用真理; 全場只有它把這個迴圈明文化, 而你的技術棧完全做得到。
3. **照抄 docx skill 的 unpack → edit XML → pack → validate 工作流**: ODF 同樣是 zip+XML, 而且比 OOXML 更乾淨 — 有 RelaxNG schema 和現成的 `odfvalidator` 可做機器驗證。「schema 驗證 + 自動修復」正是你 repo 裡 retry-on-malformed 邏輯的正統化。
4. **學 2026 年增益集路線的核心洞見 — 模板外包**: 支援使用者提供 .otp/.ott 模板, 工具只讀取 master/styles 並填入內容。設計品質問題瞬間從「AI 會不會設計」變成「AI 會不會遵守樣式」, 後者是小團隊贏得了的仗, 而且政府/學校場景本來就有公版模板。
5. **Progressive disclosure 的 skill 檔案架構**: SKILL.md + references/ + scripts/ 的組織方式, 讓你的 prompt 與工具資產可維護、可開源、可被其他 agent 生態複用。

### 做不到的 (不必焦慮)
- 自研圖像模型 (Nano Banana Pro)、80+ 模型 mixture-of-agents、Wide Research 平行子代理集群 — 全是燒錢的規模遊戲。
- 進 Microsoft AppSource 的增益集分發、8 億使用者管道。
- Genspark「Mark and Edit」等級的即時 web 編輯器 — 前端工程量遠超兩人團隊。

### 根本不該學的
1. **HTML 中間層再轉檔**。巨頭走 HTML 是為了 web 預覽體驗與跨格式匯出的通用性, 而 conversion drift 是他們被評測反覆點名的痛處。你的產品定義就是「原生 ODF」— **轉換漂移正是你要打的敵人, 不是你的架構**。perspectives.plus 的結論是強力背書: 直寫原生格式的開源社群工具, 設計品質「已可匹敵 Microsoft 自家 Office 團隊的產出」。直寫 ODF XML (有 schema、有 odfpy) 不是土法煉鋼, 是差異化。
2. **credits 黑箱計費與不可預測成本** — 這是所有評測共同的抱怨點, 開源工具的透明性反而是賣點。
3. **無約束的自由排版**。Manus/Genspark 的每頁自由生成靠大量算力和事後修補撐住; 沒有那個資源時, 自由生成 = 不穩定。小團隊更優解是「受限但正確」: 少數精心設計的版型 (layout functions) + design tokens, 變化來自參數而非 LLM 即興 — 模板天生不會壞, 把 LLM 的自由度花在內容而非座標上。

### 定位縫隙
全部六個巨頭產品線都在優化 .pptx/.docx, **沒有任何一家碰 ODF**。政府採購、教育體系、開源辦公 (LibreOffice 生態) 是巨頭「sacrifice」掉、也不會回頭做的市場 — 而「原生格式正確性 + schema 驗證 + 渲染 QA」恰好是 HTML-first 玩家全體交不出來的東西。這就是這個工具該講的故事。

---

## 主要來源
- [Anthropic — Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) / [anthropics/skills repo (pptx/docx SKILL.md)](https://github.com/anthropics/skills) / [Claude Agent Skills 深度解析 (Hanchung Lee)](https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/)
- [gHacks — Claude in PowerPoint](https://www.ghacks.net/2026/02/23/anthropic-launches-claude-inside-powerpoint-for-ai-powered-slide-creation-and-editing/) / [TechCrunch — Claude Design](https://techcrunch.com/2026/04/17/anthropic-launches-claude-design-a-new-product-for-creating-quick-visuals/) / [SlideSpeak — Claude Cowork 實測](https://slidespeak.co/blog/how-to-create-presentations-claude-cowork)
- [OpenAI Help — ChatGPT for PowerPoint](https://help.openai.com/en/articles/20001242-chatgpt-for-powerpoint) / [MindStudio — ChatGPT in PowerPoint 教學](https://www.mindstudio.ai/blog/chatgpt-in-powerpoint-free-add-in-tutorial) / [Leon Furze — OpenAI Agents 初測](https://leonfurze.com/2025/07/19/initial-impressions-of-openais-agents-unfinished-unsuccessful-and-unsafe/) / [Plus AI — ChatGPT 做簡報](https://plusai.com/blog/how-to-use-chatgpt-to-create-powerpoint-presentations)
- [Manus 官方 — Can Manus Create Slides](https://manus.im/blog/can-manus-create-slides) / [Manus Slides 文件](https://manus.im/docs/features/slides) / [Plus AI — Manus 評測](https://plusai.com/blog/manus-ai-slide-generator-review)
- [Genspark AI Slides FAQ/Changelog](https://www.genspark.ai/docs/ai_slides_faq) / [presentations.ai — Genspark 評測](https://www.presentations.ai/blog/genspark-review) / [Composio — open-genspark 復刻](https://composio.dev/content/i-vibe-coded-genspark-in-a-weekend) / [Alai — Genspark 實測](https://getalai.com/blog/genspark-alternatives)
- [perspectives.plus — AI still can't figure out PowerPoint](https://www.perspectives.plus/p/ai-still-cant-figure-out-powerpoint) / [Tosea — HTML vs Image 路線指南](https://tosea.ai/blog/ai-slides-html-vs-image-generation-guide-2026) / [knightli — AI PPT 工具路線調查](https://knightli.com/en/2026/05/18/ai-ppt-skills-selection-guide/) / [Deckary — HTML to PowerPoint 轉換比較](https://deckary.com/blog/html-to-powerpoint)

---

# 第5部：CJK 市場（WPS / Kimi / 百度 / 騰訊 / 台灣 ODF 生態）

# CJK 市場 AI 簡報/文件生成產品研究報告

**研究日期:2026-07-06|針對「自然語言 → 原生 ODF」開源工具(Python、兩人學生團隊、繁中教育/政府市場)的競品分析**

---

## 1. WPS AI / WPS AIPPT(金山辦公)

### 1.1 管線
```
一句話主題 or 上傳文檔(論文/報告)
  → LLM 生成大綱(逐頁標題+要點,可增刪頁、調順序、單頁重生成)
  → 使用者確認大綱 → 選擇模板(人工挑選或 AI 推薦)
  → AI 將內容填入模板槽位,自動調整字體/配色/佈局/動畫
  → 原生 PPTX(在 WPS 演示中直接開啟編輯)→ 可另存 PDF/圖片/影片
```
輸出是**真正的原生簡報檔**,不是 web 渲染——這是 WPS 與多數 web-first 工具(Gamma 等)最大的差異。另有「單頁美化」:AI 識別當前頁是封面頁/章節頁/目錄頁/正文頁,依頁面類型推薦匹配模板重排版。([WPS 365 部落格](https://plus.wps.cn/blog/p109383.html)、[WPS AI 官方介紹](https://ai.wps.cn/introduction/assistanceWpp)、[China Daily 報導](https://ex.chinadaily.com.cn/exchange/partners/82/rss/channel/cn/columns/sz8srm/stories/WS6752c6b4a310b59111da77c3.html))

### 1.2 設計品質的來源
**幾乎全部來自人類設計師預製模板**。核心資產是稻殼兒(Docer)內容平台:800 萬+ 模板素材、3000+ 簽約設計師、500+ 授權字體([Docer 官網](https://www.docer.com/)、[中國日報](https://tech.chinadaily.com.cn/a/202103/10/WS60487907a3101e7ce974353d.html))。LLM 只負責文字內容;版面、配色、字體全由模板決定。新一代設計助手加入 AI 配圖(依頁面標題與內容生成風格匹配的圖像)與「套內風格統一」的成套模板。中文排版(字體搭配、行距、標點避頭尾)是設計師在模板層解決的,不是演算法解決的。

### 1.3 變化 vs 一致性
模板驅動 = 高一致性、低變化。同一模板生成的簡報結構幾乎相同;變化來自模板庫的廣度(場景×風格矩陣)而非生成的隨機性。使用者控制美術方向的方式就是「換模板」+ 單頁美化,無法用自然語言微調視覺(如「藍色再深一點、標題更大」這種指令層級的控制很弱)。

### 1.4 品質保證機制
**靠模板天生不會壞**:內容填進固定槽位,溢出時縮字或截斷,版面不可能崩。沒有公開證據顯示存在 render-critique 迴圈。代價是內容品質不受保證——社群實測普遍抱怨「生成的內容很多是胡編亂造、硬湊字數」,框架能用、文字要重寫([WPS 社群實測](https://bbs.wps.cn/topic/3917))。

### 1.5 編輯/迭代 UX
最強項:生成結果就在 WPS 演示裡,和手工做的 PPT 無差別,任意二次編輯。支援單頁重新生成、單頁美化、大綱回頭改。這種「AI 生成 → 原生編輯器無縫接手」是 CJK 使用者最買單的模式。

### 1.6 商業定位與護城河
綁進 WPS 會員/WPS 365 訂閱,AI 是留存與升級會員的鉤子。護城河 = 稻殼設計師生態(20 年累積)+ 中國數億裝機量 + 原生編輯器。這三樣都不是技術,是資產。

---

## 2. Kimi(Moonshot)PPT 助手 → Kimi Slides

### 2.1 管線(兩個世代)
**第一代(2024,與 AiPPT.cn 合作)**:Kimi 長上下文模型(20 萬字)解析上傳文檔/指令 → 抽取大綱 → 丟給 AiPPT 的模板引擎 → 按場景(商務/學術/路演)×視覺風格(極簡/科技感/中國風)匹配千餘套聯合設計模板 → 線上編輯器 → 導出 PPTX/PDF。本質是「LLM 做大腦、AiPPT 做手」的管線拼接([AI 工具集](https://ai-bot.cn/kimi-aippt/)、[知乎教學](https://zhuanlan.zhihu.com/p/11455434521))。AiPPT 自身的技術核心是 **PPT↔JSON 雙向轉換引擎**(原生圖表/動畫/3D 解析)+ 10 萬套設計師模板,並以 API 形式開放([AiPPT 開放平台](https://open.aippt.cn/))。

**第二代(2025,自研 Kimi Slides,K2.5/K2.6 模型)**:代理式(agentic)管線——自動上網調研主題 → 撰寫內容 → 匹配配色/字體/圖標/圖表樣式 → **先把 PPT 元素與效果編譯成 JSON 中間表示,再逆向渲染生成 PPTX 檔**;分塊渲染,15 頁約 50 秒出初稿;支援「圖片復刻」與使用者上傳自定義模板([Kimi 官方幫助中心](https://www.kimi.com/zh-cn/help/ppt/ppt-overview)、[Kimi Slides](https://www.kimi.com/en/slides)、[CSDN 實測](https://blog.csdn.net/m0_74837192/article/details/155381008))。

### 2.2 設計品質的來源
第一代:純設計師模板(AiPPT 的資產)。第二代:**模板約束 + LLM 在約束內自由生成**的混合——模型決定每頁的視覺敘事(用時間軸、數據看板還是對比卡片),但配色/字體體系來自預定義的 design token 集合。垂直領域規範內建於模板引擎(學術答辯自動加參考文獻頁腳、路演模板強化融資章節)。

### 2.3 變化 vs 一致性
比 WPS 有更多頁內變化(模型會為不同內容選不同版式組件),但整份簡報靠主題 token 保持一致。使用者可透過自然語言多輪優化 + 上傳參考模板控制美術方向——這是它比 WPS 先進的地方。

### 2.4 品質保證機制
JSON 中間表示本身就是一層驗證(schema 合法 = 版面不崩);「一鍵生成 + 多輪優化」暗示有某種生成-檢查循環,但官方未披露是否有真正的 render-critique(渲染成圖 → 視覺模型評分 → 修正)。已知弱點:長文字溢出、中文標點斷行等排版細節仍會出錯,由線上編輯器兜底。

### 2.5 編輯/迭代 UX
線上編輯器:文字/圖片/圖表模組化調整、智能對齊、15 種基礎動畫;導出 PPTX 後進 PowerPoint/WPS 深加工。另有「PPT 拼圖」把 20 頁壓成一張長圖發社群。

### 2.6 商業定位與護城河
免費積分制獲客,PPT 是 Kimi 通用助手的引流功能之一。護城河 = 模型自研能力(長上下文 + 聯網調研)——內容深度是它贏 WPS 的點,設計資產反而是弱項(所以先租 AiPPT 的,後來才自建)。

---

## 3. 百度文庫 AI / GenFlow(文心大模型)

### 3.1 管線
一句話/語音/上傳文檔 → 文心模型生成兩級大綱 → 自動配圖、配圖表 → 模板適配 → 線上編輯 → 導出 PPTX/PDF(**下載要 VIP**)。2025 年升級為 **GenFlow 多智能體架構**:「滄舟 OS」內容作業系統 + MCP 調度,內置 100+ 專業 Agent(內容、排版、圖表、翻譯各司其職、並行工作),號稱 3 分鐘並行產出 30 頁 PPT + 萬字報告等 12 種格式;「自由畫布」支援拖曳素材+塗鴉+指令的多模態編輯;「記憶中心」記住使用者偏好並做內容校驗([量子位](https://www.qbitai.com/2025/11/352188.html)、[GenFlow 介紹](https://gongke.net/tools/genflow)、[知乎評測](https://zhuanlan.zhihu.com/p/16344587477))。

### 3.2 設計品質的來源
模板適配 + 文庫十億級文檔語料(內容側優勢遠大於設計側)。排版有專責 Agent,但視覺基底仍是模板庫。它的差異化是「知識找得準」而不是「長得好看」。

### 3.3 變化 vs 一致性
模板決定一致性;使用者可線上改主題色與配圖,美術控制粒度粗。多 Agent 並行生成帶來的是速度,不是視覺多樣性。

### 3.4 品質保證機制
「記憶中心」宣稱做記憶回溯與**內容校驗**(事實層),版面層仍靠模板保底。無公開的視覺評審迴圈。

### 3.5 編輯/迭代 UX
線上編輯器 + 自由畫布(拖曳、塗鴉、指令改稿、多人協同)。編輯自由度介於 WPS(原生)與純 web 工具之間;最終交付仍走 PPTX/PDF 導出。

### 3.6 商業定位與護城河
文庫會員付費牆(生成免費、下載收費)。護城河 = 文檔語料飛輪(文庫餵文心訓練、文心強化文庫產品)+ 百度搜索分發。GenFlow 宣稱活躍使用者 2000 萬+。

---

## 4. 騰訊文檔 AI / 智能文檔

### 4.1 管線
一句話 → 生成 Word/Excel/PPT/思維導圖/收集表五類;支援**跨品類轉換**(思維導圖一鍵轉 PPT、大綱擴寫成幻燈片)。「智能文檔」本體是 **web 原生的塊狀(block-based)格式**(類 Notion),但簡報線輸出相容 .pptx 可導出([量子位](https://www.qbitai.com/2024/01/116911.html)、[AI 工具集](https://ai-bot.cn/sites/8079.html))。2025 年接入 DeepSeek 強化內容生成([53AI 實測](https://www.53ai.com/news/LargeLanguageModel/2025021927341.html))。

### 4.2 設計品質的來源
「AI 單頁美化」依主題做配圖與配色;「一鍵版式美化」全簡報統一字體與配色方案。模板庫規模明顯小於 WPS/AiPPT,設計是規則化美化而非設計師資產堆疊。

### 4.3 變化 vs 一致性
一鍵全局換膚保證一致性;變化少。它的定位本來就偏「協作文檔順手做個簡報」,不是設計工具。

### 4.4 品質保證機制
規則化美化(字體/配色批量替換)不會弄壞版面;自動生成演講備註是內容側輔助。無 render-critique。

### 4.5 編輯/迭代 UX
最貼近「手工編輯 + AI 局部輔助」:使用者逐頁做,AI 單頁美化、智能寫作嵌在編輯流程裡,而不是一次性端到端生成。多人即時協同是原生能力。

### 4.6 商業定位與護城河
AI 是騰訊文檔(協作 SaaS)的功能,不是產品。護城河 = 微信/企業微信生態的分發與協同關係鏈,和設計品質無關。

---

## 5. 台灣脈絡:ODF 政策生態 × AI 的空白

### 5.1 政策面
- **ODF-CNS 15251 實施計畫(113–116 年,2024–2027)**由數位發展部主導,延續國發會 110–112 年計畫;各級機關應安裝以 ODF 為預設存檔格式的文書軟體;文書優先 .odt、試算表 .ods、簡報 .odp([moda ODF 專區](https://moda.gov.tw/digital-affairs/digital-service/app-services/248))。
- **MODA ODF 文件應用工具**:LibreOffice 的分支(MPL 2.0 開源),優化重點是公務文件範本集與跨機關流通;另有 **ODF 雲端編輯工具**(網頁版,行動裝置可用)([odf.moda.gov.tw](https://odf.moda.gov.tw/QA/public/))。**兩者皆無任何 AI 功能**。
- **TAIDE / 數發部 TryAI**:政府機關生成式 AI 熱門應用明列「公文撰擬、簡報製作」,可用 TAIDE/Llama/Gemma 及商用模型([moda TryAI](https://moda.gov.tw/major-policies/ai/gov/19270)、[行政院 TAIDE](https://www.ey.gov.tw/Page/5A8A0CB5B41DA11E/582206fe-26fc-4184-b911-aa6e4569ff3e))——但這些都是**聊天介面產文字**,公務員仍要手動貼進 ODF 工具排版。

### 5.2 關鍵發現:AI→原生 ODF 是真空地帶
遍查中日韓市場:
- 中國四大玩家(WPS/Kimi/百度/騰訊)**全部只出 PPTX/PDF,無一支援 .odp/.odt 輸出**。
- 全球唯一接近者是 Zoho Show(可導出 ODP),但它是 web-first 產品、非 CJK 導向、非 AI 原生([相關比較](https://www.sms-datatech.co.jp/column/consulting_powerpoint-generation-ai/))。
- 開源側只有「非 AI」的積木:[odfdo](https://github.com/jdum/odfdo)/[odfpy](https://pypi.org/project/odfpy/)(ODF 讀寫庫)、[odpdown](https://github.com/thorstenb/odpdown)(Markdown→ODP 轉換器,已停維護)、[Docling](https://github.com/docling-project/docling)(只做 ODF **解析給** AI,不生成)。
- **結論:「自然語言 → 原生 ODF」在整個 CJK 市場(乃至全球)沒有直接競品**。台灣有政策強制的 ODF 需求方(全國公務機關+學校)、有政府自建的 LLM(TAIDE)、有 ODF 編輯器,唯獨中間的 AI 生成層是空的。

---

## 6. 對「自然語言 → 原生 ODF」開源工具的啟示

### 6.1 可借鏡的
1. **大綱確認是必要的中間站**。四家全部採「先出大綱、使用者確認、再排版」——這一步同時解決了內容可控性與生成成本。你的管線應該讓大綱(或內容 JSON)成為一個使用者可讀、可改的一級公民,而不是黑箱直出 .odp。
2. **JSON 中間表示 → 逆向渲染**(Kimi 第二代的做法)完美對應 ODF 的本質:ODF 就是 XML,你可以定義一個「簡報語意 JSON schema」(頁型、版式、design token),讓 LLM 產 JSON,再用 odfdo/odfpy 確定性地編譯成 ODF。**LLM 永遠不要直接寫 XML**——schema 驗證那層就是你免費的品質保證。
3. **頁型分類**(WPS 單頁美化的核心):封面/目錄/章節/正文/圖表/結尾各自對應固定版式族。這是小團隊可以窮舉的有限集合,比通用版面引擎務實一百倍。
4. **模板 = design token + 槽位,不是整份檔案**。把「風格」(配色、字體對、間距尺度)與「版式」(元素擺放)分離,少量版式 × 少量風格就能組合出足夠變化,又天生一致。政府市場甚至可以做「機關品牌套件」(局處 logo + 公文標準字體 + CNS 規範)當賣點。
5. **編輯兜底策略**:所有人的最後一哩都是「導出到原生編輯器讓人手修」。你輸出的原生 ODF 直接在 MODA ODF 工具/LibreOffice 裡開——這件事 web-first 工具做不到(它們導出 PPTX 常跑版),而你是**原生生成**,格式保真是你的天然優勢,行銷上要大聲講。

### 6.2 巨頭做得到、你做不到的
- **設計師資產**:稻殼 3000 名設計師、AiPPT 10 萬套模板——不要嘗試比模板數量。你要比「精」:10 套針對台灣教育/公務場景、遵守政府視覺規範、中文排版正確(標點避頭尾、中西文間距、思源字體)的模板,價值高於 1000 套泛用模板。
- **自研大模型與聯網調研**:Kimi 的內容深度來自模型。你就用 API(或讓工具 model-agnostic,支援 TAIDE——這對政府採購是加分項),把力氣放在 ODF 編譯層,那才是你的獨佔領域。
- **分發**:他們有裝機量與生態。你的分發是政策:ODF 實施計畫就是你的市場預算,開源 + MPL 相容授權讓公部門可以直接採用。

### 6.3 根本不該學的
- **不要做線上編輯器**。四家全都養著龐大前端團隊做編輯器;你的編輯器叫 LibreOffice/MODA ODF 工具,已經存在、政府已經裝了。你只要保證生成的檔案在裡面開起來是對的。
- **不要學「一句話 30 秒出 30 頁」的展示性速度**。實測共識是快速生成的內容「胡編亂造、硬湊字數」,公務/教育場景寧可慢而準。兩人團隊拚快是死路,拚「生出來的東西可以直接交」才有活路。
- **不要學會員付費牆/積分制**(百度的生成免費、下載收費模式在開源工具上是自毀)。你的模式是開源核心 + 可能的機關導入服務。
- **不要追多 Agent 架構的軍備競賽**(GenFlow 100+ Agent 是行銷語言)。單一 LLM + 良好 schema + 確定性編譯器,在你的問題規模下更可靠、更可測試、更好 debug。

### 6.4 一句話總結競爭態勢
中國四巨頭證明了「LLM 出內容 + 模板保設計 + 原生格式落地」是 CJK 使用者要的形態,但他們的落地格式全是 PPTX;台灣政策強制了 ODF 卻沒有任何 AI 生成層——**你們做的不是巨頭的縮小版,而是他們管線的最後一段在一個他們不做的格式上的唯一實作**。

---

**主要來源**:[WPS AI 官方](https://ai.wps.cn/introduction/assistanceWpp)|[WPS 365 部落格](https://plus.wps.cn/blog/p109383.html)|[稻殼兒官網](https://www.docer.com/)|[Kimi 幫助中心](https://www.kimi.com/zh-cn/help/ppt/ppt-overview)|[Kimi Slides](https://www.kimi.com/en/slides)|[AiPPT 開放平台](https://open.aippt.cn/)|[量子位:GenFlow 3.0](https://www.qbitai.com/2025/11/352188.html)|[量子位:騰訊文檔智能助手](https://www.qbitai.com/2024/01/116911.html)|[moda ODF 文件應用工具](https://moda.gov.tw/digital-affairs/digital-service/app-services/248)|[moda TryAI](https://moda.gov.tw/major-policies/ai/gov/19270)|[TAIDE](https://taide.tw/)|[odfdo](https://github.com/jdum/odfdo)|[odpdown](https://github.com/thorstenb/odpdown)|[Docling](https://github.com/docling-project/docling)