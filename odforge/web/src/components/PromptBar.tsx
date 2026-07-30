import { useState } from "react";
import {
  buildGenerateBody,
  isValidPages,
  PAGES_MAX,
  PAGES_MIN,
  type AssetUpload,
  type GenerateBody,
  type GenerateOptions,
} from "../state/api";

const MODES: { id: "presenter" | "detailed"; label: string; hint: string }[] = [
  { id: "presenter", label: "上台報告", hint: "大字少文，保留講者節奏" },
  { id: "detailed", label: "閱讀文件", hint: "資訊完整，離開講者也看得懂" },
];

// 展示「好 prompt」:含受眾/頁數/語氣線索,主題台灣在地、評審看得懂。
const EXAMPLES: string[] = [
  "給大一新生的資料結構第三章教學簡報，12 頁，語氣親切",
  "向系上老師提案的畢業專題進度報告，重點放系統架構與時程",
  "面向招生說明會家長的科系介紹，8 頁，語氣正式又溫暖",
];

const MAX_ASSETS = 6;
const MAX_ASSET_BYTES = 8 * 1024 * 1024;
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
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"presenter" | "detailed">("presenter");
  const [pages, setPages] = useState("");
  // 第四道閘預設開:評審看到的就是完整四道閘。關掉是使用者的明示選擇,
  // 收合後會以 chip 說明「已跳過」,不讓它悄悄消失。
  const [qa, setQa] = useState(true);
  const [assets, setAssets] = useState<AssetUpload[]>([]);
  const [assetError, setAssetError] = useState("");

  const canSubmit = !!prompt.trim();
  // 頁數即時守門:填了但非數字/超界 → 提示,且送出時不帶 pages(交給 AI)。
  const pagesNum = pages.trim() ? Number(pages) : undefined;
  const pagesInvalid = pages.trim() !== "" && !isValidPages(pagesNum);

  async function selectAssets(files: FileList | null) {
    if (!files) return;
    const selected = Array.from(files).slice(0, MAX_ASSETS);
    const invalid = selected.find(
      (file) =>
        !referenceMime(file) ||
        file.size > MAX_ASSET_BYTES,
    );
    if (invalid) {
      setAssetError("請選擇 8 MiB 以下的 PDF、PNG 或 JPEG。");
      return;
    }
    try {
      setAssets(
        await Promise.all(
          selected.map(async (file) => ({
            description: file.name.replace(/\.[^.]+$/, ""),
            credit: "",
            data_url: await fileAsDataUrl(file, referenceMime(file)!),
          })),
        ),
      );
      setAssetError(files.length > MAX_ASSETS ? `最多 ${MAX_ASSETS} 個檔案。` : "");
    } catch {
      setAssetError("無法讀取檔案，請重新選擇。");
    }
  }

  function requestBody(): GenerateBody {
    const opts: GenerateOptions = {
      mode,
      pages: pagesNum,
      qa,
      interactive: true,
      assets,
    };
    return buildGenerateBody(prompt.trim(), "odp", opts);
  }

  function submit() {
    if (!canSubmit) return;
    onDiscover(requestBody());
  }

  const summary: string[] = [];
  if (mode === "detailed") summary.push("閱讀文件");
  if (isValidPages(pagesNum)) summary.push(`${pagesNum} 頁`);
  if (assets.length) summary.push(`${assets.length} 份參考`);
  if (!qa) summary.push("略過設計品質檢查");

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
          aria-label="主題"
          placeholder="描述主題、受眾與重點。例如：給大一新生的 12 頁資料結構教學簡報。"
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
            disabled={!canSubmit}
            onClick={submit}
          >
            <span>繼續</span>
            <span className="forge-arrow" aria-hidden="true">→</span>
          </button>
        </div>
      </div>

      {!prompt.trim() && (
        <div className="examples" aria-label="範例提示">
          <span className="exlead">範例</span>
          {EXAMPLES.map((ex, i) => (
            <button key={i} type="button" className="exchip" data-index={`0${i + 1}`} data-testid="example-chip" onClick={() => setPrompt(ex)}>
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="advbar">
        <button type="button" className="advtoggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <span className="advtoggle-icon" aria-hidden="true">{open ? "−" : "+"}</span>
          輸出設定
        </button>
        {!open && (summary.length > 0 || pagesInvalid) && (
          <span className="advsummary" data-testid="adv-summary">
            {summary.map((s, i) => <span className="advchip" key={i}>{s}</span>)}
            {/* 抽屜收合時,非法頁數看不到欄位提示 → 補一個警示 chip,說明已略過。 */}
            {pagesInvalid && (
              <span className="advchip warn" data-testid="adv-warn" role="status">頁數無效，已略過</span>
            )}
          </span>
        )}
      </div>

      {open && (
        <div className="advdrawer" role="group" aria-label="輸出設定">
          <div className="settings-grid">
            <div className="advfield">
              <span className="advlabel">內容密度</span>
              <div className="density-options" role="radiogroup" aria-label="內容密度">
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
              <input type="number" min={PAGES_MIN} max={PAGES_MAX} inputMode="numeric" placeholder="AI 決定"
                aria-invalid={pagesInvalid || undefined} value={pages} onChange={(e) => setPages(e.target.value)} />
              {!pagesInvalid && <small className="advhint">留白即可自動安排</small>}
              {pagesInvalid && (
                <small className="advhint pageserr" role="alert">請輸入 {PAGES_MIN}–{PAGES_MAX} 頁，或留白。</small>
              )}
            </label>
          </div>

          <div className="advfield">
            <span className="advlabel">設計品質檢查</span>
            <label className={qa ? "qa-option on" : "qa-option"}>
              <input
                type="checkbox"
                aria-label="設計品質檢查"
                checked={qa}
                onChange={(e) => setQa(e.target.checked)}
              />
              <span>
                <b>{qa ? "生成後檢查版面，必要時重做該頁" : "已關閉，只跑格式驗證"}</b>
                <small>
                  視覺模型逐頁看溢出、重疊、對比與版型節奏，最多重生一輪；會多花約 10–30 秒。
                </small>
              </span>
            </label>
          </div>

          <label className="advfield">
            <span className="advlabel">參考文件</span>
            <input
              type="file"
              accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg"
              multiple
              data-testid="asset-input"
              onChange={(e) => void selectAssets(e.target.files)}
            />
            <small className="advhint">
              PDF、PNG 或 JPEG，最多 {MAX_ASSETS} 個，每個 8 MiB。
            </small>
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
        </div>
      )}
    </div>
  );
}
