import { useState } from "react";
import { buildGenerateBody, isValidPages, PAGES_MAX, PAGES_MIN, type GenerateBody, type GenerateOptions } from "../state/api";
import type { DocType } from "../state/types";

// odt/ods 後端尚未支援(選「試算表」實測拿回 .odp),故停用並標示「即將支援」;
// 只有 odp 可選,送出永遠 doc_type:"odp"。
const TABS: { id: DocType; label: string; enabled: boolean }[] = [
  { id: "odp", label: "簡報", enabled: true },
  { id: "odt", label: "文書", enabled: false },
  { id: "ods", label: "試算表", enabled: false },
];

const MODES: { id: "presenter" | "detailed"; label: string; hint: string }[] = [
  { id: "presenter", label: "講者型", hint: "大字少文,適合上台講" },
  { id: "detailed", label: "自讀型", hint: "資訊完整,適合自己讀" },
];

// "" = 自動(不送 theme,交給 AI 依主題設計)
const THEMES: { id: string; label: string }[] = [
  { id: "", label: "自動" }, { id: "academic", label: "academic" },
  { id: "minimal", label: "minimal" }, { id: "dark", label: "dark" },
];

// 展示「好 prompt」:含受眾/頁數/語氣線索,主題台灣在地、評審看得懂。
const EXAMPLES: string[] = [
  "給大一新生的資料結構第三章教學簡報,12 頁,語氣親切",
  "向系上老師提案的畢業專題進度報告,重點放系統架構與時程",
  "面向招生說明會家長的科系介紹,8 頁,語氣正式又溫暖",
];

export function PromptBar({ onGenerate }: { onGenerate: (body: GenerateBody) => void }) {
  const [prompt, setPrompt] = useState("");
  const [docType, setDocType] = useState<DocType>("odp");
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"presenter" | "detailed">("presenter");
  const [theme, setTheme] = useState("");
  const [pages, setPages] = useState("");
  const [audience, setAudience] = useState("");
  const [tone, setTone] = useState("");
  const [qa, setQa] = useState(false);
  const [interactive, setInteractive] = useState(true);

  const canSubmit = !!prompt.trim();
  // 頁數即時守門:填了但非數字/超界 → 提示,且送出時不帶 pages(交給 AI)。
  const pagesNum = pages.trim() ? Number(pages) : undefined;
  const pagesInvalid = pages.trim() !== "" && !isValidPages(pagesNum);

  function submit() {
    if (!canSubmit) return;
    const opts: GenerateOptions = {
      mode,
      theme: theme || undefined,
      pages: pagesNum,
      audience: audience.trim() || undefined,
      tone: tone.trim() || undefined,
      qa,
      interactive,
    };
    onGenerate(buildGenerateBody(prompt.trim(), docType, opts));
  }

  const summary: string[] = [];
  if (mode === "detailed") summary.push("自讀型");
  if (theme) summary.push(theme);
  if (isValidPages(pagesNum)) summary.push(`${pagesNum} 頁`);
  if (audience.trim()) summary.push(`受眾:${audience.trim()}`);
  if (tone.trim()) summary.push(`語氣:${tone.trim()}`);
  if (qa) summary.push("QA");
  if (!interactive) summary.push("略過確認");

  return (
    <div className="promptbar">
      <div className="promptbar-main">
        <div className="doctabs" role="tablist" aria-label="文件型別">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={docType === t.id}
              aria-disabled={!t.enabled}
              disabled={!t.enabled}
              className={docType === t.id ? "on" : t.enabled ? "" : "soon"}
              title={t.enabled ? undefined : "即將支援"}
              onClick={() => t.enabled && setDocType(t.id)}
            >
              {t.label}
              {!t.enabled && <span className="soonbadge">即將支援</span>}
            </button>
          ))}
        </div>
        <textarea
          aria-label="主題"
          placeholder="描述主題、重點、場合……愈具體,成品愈貼合"
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
        <button className="forge" type="button" disabled={!canSubmit} onClick={submit}>
          鍛造 ▸
        </button>
      </div>

      {!prompt.trim() && (
        <div className="examples" aria-label="範例提示">
          <span className="exlead">試試:</span>
          {EXAMPLES.map((ex, i) => (
            <button key={i} type="button" className="exchip" data-testid="example-chip" onClick={() => setPrompt(ex)}>
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="advbar">
        <button type="button" className="advtoggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          進階選項 {open ? "▴" : "▾"}
        </button>
        {!open && summary.length > 0 && (
          <span className="advsummary" data-testid="adv-summary">
            {summary.map((s, i) => <span className="advchip" key={i}>{s}</span>)}
          </span>
        )}
      </div>

      {open && (
        <div className="advdrawer" role="group" aria-label="進階選項">
          <div className="advfield">
            <span className="advlabel">模式</span>
            <div className="segmented" role="radiogroup" aria-label="模式">
              {MODES.map((m) => (
                <label key={m.id} className={mode === m.id ? "seg on" : "seg"}>
                  <input type="radio" name="adv-mode" checked={mode === m.id} onChange={() => setMode(m.id)} />
                  {m.label}
                </label>
              ))}
            </div>
            <small className="advhint">{MODES.find((m) => m.id === mode)?.hint}</small>
          </div>

          <div className="advfield">
            <span className="advlabel">主題</span>
            <div className="segmented" role="radiogroup" aria-label="主題配色">
              {THEMES.map((t) => (
                <label key={t.id || "auto"} className={theme === t.id ? "seg on" : "seg"}>
                  <input type="radio" name="adv-theme" checked={theme === t.id} onChange={() => setTheme(t.id)} />
                  {t.label}
                </label>
              ))}
            </div>
            <small className="advhint">「自動」讓 AI 依主題設計配色</small>
          </div>

          <div className="advrow">
            <label className="advfield sm">
              <span className="advlabel">頁數</span>
              <input type="number" min={PAGES_MIN} max={PAGES_MAX} inputMode="numeric" placeholder="留空=AI 決定"
                aria-invalid={pagesInvalid || undefined} value={pages} onChange={(e) => setPages(e.target.value)} />
              {pagesInvalid && (
                <small className="advhint pageserr" role="alert">頁數需在 {PAGES_MIN}–{PAGES_MAX} 之間,否則交給 AI 決定</small>
              )}
            </label>
            <label className="advfield sm">
              <span className="advlabel">受眾</span>
              <input type="text" placeholder="選填,例:大一新生"
                value={audience} onChange={(e) => setAudience(e.target.value)} />
            </label>
            <label className="advfield sm">
              <span className="advlabel">語氣</span>
              <input type="text" placeholder="選填,例:親切"
                value={tone} onChange={(e) => setTone(e.target.value)} />
            </label>
          </div>

          <div className="advchecks">
            <label className="advcheck">
              <input type="checkbox" checked={qa} onChange={(e) => setQa(e.target.checked)} />
              <span>設計 QA</span>
            </label>
            <small className="advhint">需要 LibreOffice 與 vision 後端</small>
            <label className="advcheck">
              <input type="checkbox" checked={interactive} onChange={(e) => setInteractive(e.target.checked)} />
              <span>大綱確認</span>
            </label>
            <small className="advhint">AI 先給大綱,你確認後再生成</small>
          </div>
        </div>
      )}
    </div>
  );
}
