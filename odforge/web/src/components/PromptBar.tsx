import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  buildGenerateBody,
  getSources,
  getTemplates,
  isValidPages,
  PAGES_MAX,
  PAGES_MIN,
  type AssetUpload,
  type GenerateBody,
  type GenerateOptions,
  type LanguageId,
  type LanguageOption,
  type LogoPlacement,
  type SourcesReport,
} from "../state/api";

const MODES: { id: "presenter" | "detailed"; label: string; hint: string }[] = [
  { id: "presenter", label: "上台報告", hint: "大字少文，保留講者節奏" },
  { id: "detailed", label: "閱讀文件", hint: "資訊完整，離開講者也看得懂" },
];

// 下面三組不是後端欄位,而是附加在需求尾端的文字(buildGenerateBody)。它們值得
// 佔畫面,是因為大綱骨架真的會因此不同:場合決定要不要決策頁與時程頁,受眾決定
// 術語深度,語氣決定句子的長相。留白就是「交給 AI 判斷」,不會被硬塞一個預設。
const OCCASIONS = [
  "課堂教學",
  "專題提案",
  "成果報告",
  "研討會發表",
  "招生說明",
  "工作匯報",
];
const DURATIONS = ["5 分鐘", "10 分鐘", "15 分鐘", "20 分鐘", "30 分鐘以上"];
const TONES = ["親切易懂", "正式專業", "精簡直接", "熱情有感染力"];

const LOGO_PLACEMENTS: { id: LogoPlacement; label: string; hint: string }[] = [
  { id: "cover", label: "只放封面", hint: "最保守,只出現一次" },
  { id: "cover-closing", label: "封面與結尾", hint: "首尾呼應,中間頁保持乾淨" },
  { id: "all", label: "每頁角落", hint: "像企業簡報的頁尾標誌" },
];

const MAX_LOGO_BYTES = 2 * 1024 * 1024;

const MAX_ASSETS = 6;
const MAX_ASSET_BYTES = 8 * 1024 * 1024;
// 整批上限,不只是單檔上限。六個各 8 MiB 的檔案 = 48 MiB 的 base64 常駐在 React
// state 裡,送出時還要同步 JSON.stringify 一次 —— 分頁在低階手機上直接被殺掉。
const MAX_TOTAL_ASSET_BYTES = 16 * 1024 * 1024;

function mib(bytes: number): string {
  return (bytes / 1024 / 1024).toFixed(1);
}
const REFERENCE_MIME_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
};

function referenceMime(file: File): string | undefined {
  if (["application/pdf", "image/png", "image/jpeg"].includes(file.type)) {
    return file.type;
  }
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return REFERENCE_MIME_BY_EXTENSION[extension];
}

function fileAsDataUrl(file: File, mime: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const comma = result.indexOf(",");
      if (comma < 0) {
        reject(new Error("invalid data URL"));
        return;
      }
      resolve(`data:${mime};base64,${result.slice(comma + 1)}`);
    };
    reader.onerror = () => reject(reader.error ?? new Error("無法讀取檔案"));
    reader.readAsDataURL(file);
  });
}

/** 可收起的區塊:標題本身就是開關(+ / −)。
 *
 * 收起來的代價是「看不見自己設過什麼」,所以收起時一定要把已指定的值摘要在標題
 * 下方——沒有摘要的抽屜,使用者只會忘記自己上次填了 20 頁。 */
function CollapsibleBlock({
  id,
  title,
  sub,
  summary,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  sub: string;
  summary: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const bodyId = `${id}-body`;
  return (
    <section className="composer-block" aria-labelledby={id}>
      <div className="block-head">
        <h2 className="block-title">
          <button
            id={id}
            type="button"
            className="block-toggle"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={onToggle}
          >
            <span className="block-sign" aria-hidden="true">{open ? "−" : "+"}</span>
            <span>{title}</span>
          </button>
        </h2>
        {sub !== "" && <p className="block-sub">{sub}</p>}
        {!open && summary !== "" && <p className="block-set">已指定：{summary}</p>}
      </div>
      {open && (
        <div className="block-body" id={bodyId}>
          {children}
        </div>
      )}
    </section>
  );
}

/** 單選晶片組:再點一次同一顆就取消,回到「交給 AI」。`hint` 留空就不出說明列。 */
function ChipField({
  id,
  label,
  hint,
  options,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  options: string[];
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="advfield">
      <span className="advlabel" id={`${id}-label`}>{label}</span>
      <div className="chipset" role="group" aria-labelledby={`${id}-label`}>
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={value === option ? "chip on" : "chip"}
            aria-pressed={value === option}
            onClick={() => onChange(value === option ? "" : option)}
          >
            {option}
          </button>
        ))}
      </div>
      {hint && <small className="advhint">{hint}</small>}
    </div>
  );
}

export function PromptBar({
  onDiscover,
  value,
  onValueChange,
}: {
  onDiscover: (body: GenerateBody) => void;
  // Controlled prompt text: when provided, App owns it (single source of truth)
  // so the句子 survives an error or a completed run. Falls back to internal state
  // when omitted (standalone rendering in unit tests).
  value?: string;
  onValueChange?: (v: string) => void;
}) {
  const [internalPrompt, setInternalPrompt] = useState("");
  const controlled = value !== undefined;
  const prompt = controlled ? value : internalPrompt;
  const setPrompt = (v: string) => (controlled ? onValueChange?.(v) : setInternalPrompt(v));
  const [mode, setMode] = useState<"presenter" | "detailed">("presenter");
  const [pages, setPages] = useState("");
  const [occasion, setOccasion] = useState("");
  const [duration, setDuration] = useState("");
  const [tone, setTone] = useState("");
  const [audience, setAudience] = useState("");
  const [languages, setLanguages] = useState<LanguageOption[]>([]);
  const [language, setLanguage] = useState<LanguageId>("zh-TW");
  const [byline, setByline] = useState("");
  const [logo, setLogo] = useState<AssetUpload>();
  const [logoPlacement, setLogoPlacement] = useState<LogoPlacement>("cover-closing");
  const [logoError, setLogoError] = useState("");
  const [assets, setAssets] = useState<AssetUpload[]>([]);
  const [assetError, setAssetError] = useState("");
  // 讀檔是非同步的。在它完成之前,`assets` 還是空的——舊版沒有這個狀態,所以
  // 「選檔 → 立刻按繼續」會送出一份沒有任何參考文件的請求,而且畫面上完全看不
  // 出來少了什麼。讀取中一律擋住送出。
  const [assetsLoading, setAssetsLoading] = useState(false);
  const assetRunRef = useRef(0);
  // 視覺來源探測結果。三態,不是兩態:「還沒問到」和「問到了、沒有來源」必須
  // 分得開,否則第一秒的 undefined 會被當成「沒問題,照開」。
  const [sources, setSources] = useState<SourcesReport>();
  const [sourcesState, setSourcesState] =
    useState<"loading" | "ready" | "error">("loading");
  const [sourcesAttempt, setSourcesAttempt] = useState(0);
  const pagesInputRef = useRef<HTMLInputElement>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  // 兩個進階區塊預設收起:需求欄位與參考文件才是主線,客製化與封面家具是「要的人
  // 才要」。收起的狀態不會清掉任何值——值住在這個元件裡,不在那段 JSX 裡。
  const [showCustom, setShowCustom] = useState(false);
  const [showCover, setShowCover] = useState(false);
  // 頁數非法時要把焦點帶回那個欄位,但收起來時它根本不在 DOM 裡:先展開,等它掛
  // 上去之後再 focus(下面的 effect)。少了這一步,使用者按下繼續只會看到一顆
  // 沒反應的按鈕,錯誤訊息還藏在收起來的區塊裡。
  const wantPagesFocus = useRef(false);

  // 需求框跟著內容長高。固定高度的框把一整段需求塞進一條四行的捲軸裡:使用者看
  // 不到自己寫過什麼,也就校對不了——而這裡正是整個產品唯一必須寫長的地方。上限
  // 交給 CSS 的 max-height,超過才在框內捲動,繼續鈕不會被推出畫面。
  // layout effect 而非 effect:量測與改高度要在瀏覽器繪製前做完,否則每打一個字
  // 都會先閃一次舊高度。
  useLayoutEffect(() => {
    const box = promptRef.current;
    if (!box) return;
    // 先歸零再量:scrollHeight 不會小於目前的高度,不歸零就只會單向長大,刪字時
    // 框永遠縮不回去。
    box.style.height = "auto";
    // jsdom 沒有版面,scrollHeight 恆為 0;照抄會把框壓成 0 高。
    if (box.scrollHeight > 0) box.style.height = `${box.scrollHeight}px`;
  }, [prompt]);

  useEffect(() => {
    let alive = true;
    setSourcesState("loading");
    getSources()
      .then((report) => {
        if (!alive) return;
        setSources(report);
        setSourcesState("ready");
      })
      .catch(() => alive && setSourcesState("error"));
    return () => {
      alive = false;
    };
  }, [sourcesAttempt]);

  // 語言選單來自 /api/templates。失敗時靜默降級成不顯示語言選項——這是加分選項,
  // 拿不到清單不該擋住任何人生成簡報。
  useEffect(() => {
    let alive = true;
    getTemplates()
      .then((report) => alive && setLanguages(report.languages))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // 展開之後才輪到 focus:收起時 pagesInputRef 是 null,先 focus 只會靜靜落空。
  useEffect(() => {
    if (showCustom && wantPagesFocus.current) {
      wantPagesFocus.current = false;
      pagesInputRef.current?.focus();
    }
  }, [showCustom]);

  // 這一輪實際會用到的視覺來源,以及它現在能不能跑。
  const visionDefault = sources?.defaults.vision;
  const visionStatus = sources?.vision.find((v) => v.name === visionDefault);
  const visionIsOff = visionDefault === "off";
  const visionUnavailable = visionStatus != null && !visionStatus.available;
  const qaUnavailable = sources != null && (visionIsOff || visionUnavailable);
  // 第四道閘不再是選項:能跑就跑。但「能跑」仍然要問過才算數——qa:true 是一句
  // 承諾,只有「問過、而且答案是可以」才送得出去。探測中與探測失敗都算不知道,
  // 送 qa:true 只會讓報告回來寫「未啟用」,而中間沒有一處說過做不到。
  const qaKnownGood = sourcesState === "ready" && !qaUnavailable;
  const qaState = qaKnownGood
    ? "on"
    : sourcesState === "loading"
      ? "checking"
      : "off";

  const totalAssetBytes = assets.reduce(
    // base64 每 4 字元代表 3 bytes;估個近似值就夠做預算。
    (sum, a) => sum + Math.floor((a.data_url.length - a.data_url.indexOf(",") - 1) * 0.75),
    0,
  );

  // 頁數即時守門:填了但非數字/超界 → 擋住送出。舊版把非法值靜默省略成「AI 決定」,
  // 使用者輸入 100 頁、拿到 12 頁,而且沒有任何一處說明發生過什麼事。
  const pagesNum = pages.trim() ? Number(pages) : undefined;
  const pagesInvalid = pages.trim() !== "" && !isValidPages(pagesNum);
  const canSubmit = !!prompt.trim() && !pagesInvalid && !assetsLoading;

  async function selectAssets(files: FileList | null) {
    if (!files) return;
    // 這一行同時做兩件事:開新的一輪,並且讓「上一輪」失效。上一輪的 finally 因此
    // 不會再碰 assetsLoading——所以下面每一條提早 return 的路徑都必須自己把它關掉。
    // 少關一條,使用者「讀取中又選了一個非法檔」就會把送出鈕永久鎖死,而畫面上只
    // 寫著一行檔案格式錯誤,看不出繼續鈕為什麼再也按不下去。
    const run = ++assetRunRef.current;
    const abandon = (message: string) => {
      setAssetError(message);
      setAssetsLoading(false);
    };
    const selected = Array.from(files).slice(0, MAX_ASSETS);
    const invalid = selected.find(
      (file) =>
        !referenceMime(file) ||
        file.size > MAX_ASSET_BYTES,
    );
    if (invalid) {
      abandon(`請選擇 ${mib(MAX_ASSET_BYTES)} MiB 以下的 PDF、PNG 或 JPEG。`);
      return;
    }
    const total = selected.reduce((sum, file) => sum + file.size, 0);
    if (total > MAX_TOTAL_ASSET_BYTES) {
      abandon(
        `參考文件合計 ${mib(total)} MiB,超過上限 ${mib(MAX_TOTAL_ASSET_BYTES)} MiB;` +
        "請減少檔案或選較小的版本。",
      );
      return;
    }
    setAssetsLoading(true);
    setAssetError("");
    try {
      const loaded = await Promise.all(
        selected.map(async (file) => ({
          description: file.name.replace(/\.[^.]+$/, ""),
          credit: "",
          data_url: await fileAsDataUrl(file, referenceMime(file)!),
        })),
      );
      // 使用者在讀取途中又換了一批檔案 → 這一輪的結果作廢,別蓋掉新的選擇。
      if (assetRunRef.current !== run) return;
      setAssets(loaded);
      setAssetError(files.length > MAX_ASSETS ? `最多 ${MAX_ASSETS} 個檔案。` : "");
    } catch {
      if (assetRunRef.current === run) setAssetError("無法讀取檔案，請重新選擇。");
    } finally {
      if (assetRunRef.current === run) setAssetsLoading(false);
    }
  }

  function cancelAssetLoad() {
    assetRunRef.current++;
    setAssetsLoading(false);
    setAssetError("已取消讀取參考文件。");
  }

  async function selectLogo(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    const mime = referenceMime(file);
    if (!mime || mime === "application/pdf") {
      setLogoError("校徽請用 PNG 或 JPEG。");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError(`校徽請小於 ${mib(MAX_LOGO_BYTES)} MiB。`);
      return;
    }
    try {
      setLogo({
        description: file.name.replace(/\.[^.]+$/, ""),
        credit: "",
        data_url: await fileAsDataUrl(file, mime),
      });
      setLogoError("");
    } catch {
      setLogoError("無法讀取這個檔案，請重新選擇。");
    }
  }

  function requestBody(): GenerateBody {
    const opts: GenerateOptions = {
      mode,
      pages: pagesNum,
      language,
      byline,
      logo,
      logoPlacement,
      purpose: occasion,
      audience,
      tone,
      duration,
      qa: qaKnownGood,
      interactive: true,
      assets,
    };
    return buildGenerateBody(prompt.trim(), "odp", opts);
  }

  // 收起時的摘要只列真的被指定過的東西:留白就是「交給 AI」,不必報告。
  const customSummary = [
    mode === "detailed" ? "閱讀文件" : "",
    pages.trim() ? `${pages.trim()} 頁` : "",
    occasion,
    duration,
    audience,
    tone,
    language === "en" ? "English" : language === "bilingual" ? "中英對照" : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const coverSummary = [byline.trim() ? "封面署名" : "", logo ? "校徽" : ""]
    .filter(Boolean)
    .join(" · ");

  function submit() {
    // 非法頁數不再靜默丟掉:把焦點放到出問題的欄位,錯誤訊息就在旁邊。收起來的
    // 情況下先展開——否則錯誤訊息連著欄位一起被藏著,只剩一顆不動的按鈕。
    if (pagesInvalid) {
      if (showCustom) {
        pagesInputRef.current?.focus();
      } else {
        wantPagesFocus.current = true;
        setShowCustom(true);
      }
      return;
    }
    if (!canSubmit) return;
    onDiscover(requestBody());
  }

  return (
    <div className="promptbar">
      <div className="promptbar-top">
        <div className="output-format" aria-label="輸出格式：ODP 簡報">
          <span className="format-code">ODP</span>
          <span>
            <b>簡報</b>
            <small>OpenDocument</small>
          </span>
        </div>
        <span className="shortcut" aria-hidden="true">Ctrl / ⌘ + Enter</span>
      </div>

      <div className="promptbar-main">
        <span className="prompt-quote" aria-hidden="true">「</span>
        <textarea
          ref={promptRef}
          aria-label="簡報需求（主題、受眾與內容）"
          placeholder={
            "盡量詳細地寫下想呈現的內容：主題、受眾、每個段落要講的重點，" +
            "以及手上已有的資料、數據或結論。\n" +
            "可以直接貼上講稿、條列筆記或論文摘要——寫得越具體，生成的內容越貼近你要的。"
          }
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            // Ctrl/Cmd+Enter 送出;Enter 維持換行。
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="prompt-actions">
          <button
            className="forge"
            type="button"
            // 非法頁數時刻意*不* disable:按下去才能把使用者帶到出問題的欄位。
            // 一個沒有解釋、也點不下去的灰鈕,是最難排除的那種故障。
            disabled={!canSubmit && !pagesInvalid}
            aria-disabled={!canSubmit || undefined}
            onClick={submit}
          >
            <span>{assetsLoading ? "讀取參考文件…" : "繼續"}</span>
            <span className="forge-arrow" aria-hidden="true">→</span>
          </button>
        </div>
      </div>

      <p className="prompt-tip">
        內容寫得不夠也沒關係：按下繼續後，AI 會就缺少的部分逐題追問，再一起生成。
      </p>

      {/* 參考文件是「材料」,不是輸出設定——它決定 AI 有什麼可寫,所以就放在需求
          底下的第一層,不再收進任何抽屜裡。 */}
      <section className="composer-block" aria-labelledby="ref-block">
        <div className="block-head">
          <h2 className="block-title" id="ref-block">參考文件</h2>
          <p className="block-sub">
            上傳講義、論文或圖片，AI 會據此取材。
          </p>
        </div>
        <label className="advfield">
          <span className="advlabel">選擇檔案</span>
          <input
            type="file"
            accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg"
            multiple
            data-testid="asset-input"
            onChange={(e) => void selectAssets(e.target.files)}
          />
          <small className="advhint">
            PDF、PNG 或 JPEG，最多 {MAX_ASSETS} 個，每個 {mib(MAX_ASSET_BYTES)} MiB，
            合計 {mib(MAX_TOTAL_ASSET_BYTES)} MiB。PDF 的文字會進訪談，圖片會在生成時使用。
          </small>
          {assetsLoading && (
            <span className="assetloading" role="status" aria-live="polite">
              <span className="spin" aria-hidden="true">⟳</span> 正在讀取參考文件…
              <button type="button" className="assetclear" onClick={cancelAssetLoad}>
                取消
              </button>
            </span>
          )}
          {!assetsLoading && assets.length > 0 && (
            <small className="advhint" data-testid="asset-total">
              合計約 {mib(totalAssetBytes)} MiB
            </small>
          )}
          {assets.length > 0 && (
            <span className="assetlist" data-testid="asset-list">
              {assets.map((asset, index) => (
                <span className="advchip" key={`${asset.description}-${index}`}>
                  {String(index + 1).padStart(2, "0")} · {asset.description}
                </span>
              ))}
              <button type="button" className="assetclear" onClick={() => setAssets([])}>
                移除全部
              </button>
            </span>
          )}
          {assetError && <small className="advhint pageserr" role="alert">{assetError}</small>}
        </label>
      </section>

      <CollapsibleBlock
        id="custom-block"
        title="客製化"
        sub="全部可留白：留白的欄位由 AI 依你的需求判斷。"
        summary={customSummary}
        open={showCustom}
        onToggle={() => setShowCustom((v) => !v)}
      >
        <div className="settings-grid">
          <div className="advfield">
            <span className="advlabel" id="density-label">內容密度</span>
            <div className="density-options" role="radiogroup" aria-labelledby="density-label">
              {MODES.map((m) => (
                <label
                  key={m.id}
                  className={mode === m.id ? "density-option on" : "density-option"}
                >
                  <input type="radio" name="adv-mode" checked={mode === m.id} onChange={() => setMode(m.id)} />
                  <span>
                    <b>{m.label}</b>
                    <small>{m.hint}</small>
                  </span>
                </label>
              ))}
            </div>
          </div>
          <label className="advfield page-setting">
            <span className="advlabel">頁數</span>
            <input
              ref={pagesInputRef}
              type="number"
              min={PAGES_MIN}
              max={PAGES_MAX}
              inputMode="numeric"
              placeholder="AI 決定"
              aria-invalid={pagesInvalid || undefined}
              // 錯誤訊息與欄位綁起來:讀屏器聽得到「為什麼不能送出」,而不是
              // 只發現按鈕變灰了。
              aria-describedby={pagesInvalid ? "pages-error" : undefined}
              value={pages}
              onChange={(e) => setPages(e.target.value)}
            />
            {!pagesInvalid && <small className="advhint">留白即可自動安排（{PAGES_MIN}–{PAGES_MAX}）</small>}
            {pagesInvalid && (
              <small className="advhint pageserr" id="pages-error" role="alert">
                請輸入 {PAGES_MIN}–{PAGES_MAX} 頁，或留白。
              </small>
            )}
          </label>
        </div>

        <div className="settings-grid even">
          <ChipField
            id="occasion"
            label="簡報場合"
            hint="決定敘事骨架：提案會多一頁決策請求，成果報告會多一段時程。"
            options={OCCASIONS}
            value={occasion}
            onChange={setOccasion}
          />
          <ChipField
            id="duration"
            label="講述時間"
            hint="影響頁數與每頁資訊量；同時指定頁數時以頁數為準。"
            options={DURATIONS}
            value={duration}
            onChange={setDuration}
          />
        </div>

        <div className="settings-grid even">
          <label className="advfield text-setting">
            <span className="advlabel">聽眾對象</span>
            <input
              type="text"
              maxLength={60}
              placeholder="例：大一新生、系上老師、招生說明會家長"
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
            />
            <small className="advhint">決定術語深度與要不要先補背景知識。</small>
          </label>
          <ChipField id="tone" label="語氣風格" options={TONES} value={tone} onChange={setTone} />
        </div>

        {languages.length > 1 && (
          <div className="advfield">
            <span className="advlabel" id="language-label">輸出語言</span>
            <div className="chipset" role="group" aria-labelledby="language-label">
              {languages.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={language === option.id ? "chip on" : "chip"}
                  aria-pressed={language === option.id}
                  onClick={() => setLanguage(option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <small className="advhint">
              {language === "bilingual"
                ? "每一行會是「中文（English）」；字數大約加倍，AI 會主動把句子縮短。"
                : language === "en"
                  ? "投影片內容全部以英文撰寫；訪談問題仍用繁體中文問你。"
                  : "投影片與講者備忘稿都用繁體中文（台灣用語）。"}
            </small>
          </div>
        )}
      </CollapsibleBlock>

      {/* 封面署名與校徽:兩者都是使用者自己的內容,模型不參與,也不會被寫進頁面
          內容裡——它們是版面家具,由渲染器直接畫上去。 */}
      <CollapsibleBlock
        id="cover-block"
        title="封面署名與校徽"
        sub=""
        summary={coverSummary}
        open={showCover}
        onToggle={() => setShowCover((v) => !v)}
      >
        <div className="settings-grid even">
          <label className="advfield text-setting">
            <span className="advlabel">封面署名</span>
            <input
              type="text"
              maxLength={80}
              placeholder="例：XX大學資訊工程學系 · 王小明 · 2026/08"
              value={byline}
              onChange={(e) => setByline(e.target.value)}
            />
            <small className="advhint">單位、講者與日期寫成一行，置中於封面標題下方。</small>
          </label>

          <label className="advfield">
            <span className="advlabel">校徽／機構標誌</span>
            <input
              type="file"
              accept="image/png,image/jpeg,.png,.jpg,.jpeg"
              data-testid="logo-input"
              onChange={(e) => void selectLogo(e.target.files)}
            />
            <small className="advhint">
              PNG 或 JPEG，{mib(MAX_LOGO_BYTES)} MiB 以內；建議用去背 PNG。
            </small>
            {logo && (
              <span className="assetlist" data-testid="logo-chosen">
                <span className="advchip">{logo.description}</span>
                <button type="button" className="assetclear" onClick={() => setLogo(undefined)}>
                  移除
                </button>
              </span>
            )}
            {logoError && <small className="advhint pageserr" role="alert">{logoError}</small>}
          </label>
        </div>

        {/* 位置選項只在真的有校徽時出現:對一張不存在的圖做設定,只會讓人以為
            自己漏傳了什麼。 */}
        {logo && (
          <div className="advfield" data-testid="logo-placement">
            <span className="advlabel" id="placement-label">校徽出現在哪些頁</span>
            <div className="density-options three" role="radiogroup" aria-labelledby="placement-label">
              {LOGO_PLACEMENTS.map((option) => (
                <label
                  key={option.id}
                  className={logoPlacement === option.id ? "density-option on" : "density-option"}
                >
                  <input
                    type="radio"
                    name="logo-placement"
                    checked={logoPlacement === option.id}
                    onChange={() => setLogoPlacement(option.id)}
                  />
                  <span>
                    <b>{option.label}</b>
                    <small>{option.hint}</small>
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}
      </CollapsibleBlock>

      {/* 第四道閘不再是選項:能跑就跑,所以「會跑」這件事不必佔一整條版面
          (2026-08-17 使用者決定拿掉那條綠色說明)。跑不成才要說——那是使用者
          拿到的成品少了一道檢查,不講就是隱瞞。確認中也不出聲:它是暫態,
          幾百毫秒後就會有答案,先閃一條字只是噪音。 */}
      {/* role=status:探測是非同步的,從「什麼都沒有」翻成「這一輪跑不成」時,
          讀屏使用者要聽得到。 */}
      {qaState === "off" && (
        <div className="qa-status" data-state={qaState} data-testid="qa-status" role="status">
          <span className="qa-mark" aria-hidden="true">!</span>
          <span className="qa-copy">
            <b>
              {sourcesState === "error"
                ? "無法確認視覺模型，這一輪只跑前三道格式驗證"
                : "未設定視覺模型，這一輪只跑前三道格式驗證"}
            </b>
            <small>
              {sourcesState === "error"
                ? "讀取 /api/sources 失敗，因此無法保證有人看過版面。"
                : visionUnavailable
                  ? `來源「${visionDefault}」目前不可用：${visionStatus?.reason}`
                  : "伺服器的 ODFORGE_VISION_BACKEND 是 off；設定一個視覺來源後即會自動執行。"}
            </small>
          </span>
          {sourcesState === "error" && (
            // 失敗不是終局:快取已經清掉,再問一次是真的會再送一次請求。
            <button
              type="button"
              className="linkish"
              data-testid="retry-sources"
              onClick={() => setSourcesAttempt((n) => n + 1)}
            >
              重新確認
            </button>
          )}
        </div>
      )}
    </div>
  );
}
