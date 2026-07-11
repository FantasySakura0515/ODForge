import { useState } from "react";
import type { DocType } from "../state/types";

// odt/ods 後端尚未支援(選「試算表」實測拿回 .odp),故停用並標示「即將支援」;
// 只有 odp 可選,送出永遠 doc_type:"odp"。
const TABS: { id: DocType; label: string; enabled: boolean }[] = [
  { id: "odp", label: "簡報", enabled: true },
  { id: "odt", label: "文書", enabled: false },
  { id: "ods", label: "試算表", enabled: false },
];

export function PromptBar({ onGenerate }: { onGenerate: (prompt: string, docType: DocType) => void }) {
  const [prompt, setPrompt] = useState("");
  const [docType, setDocType] = useState<DocType>("odp");
  return (
    <div className="promptbar">
      <div className="doctabs" role="tablist" aria-label="文件型別">
        {TABS.map((t) => (
          <button
            key={t.id}
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
      <textarea aria-label="主題" placeholder="用一句話描述你要的文件…" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <button className="forge" disabled={!prompt.trim()} onClick={() => onGenerate(prompt.trim(), docType)}>
        鍛造 ▸
      </button>
    </div>
  );
}
