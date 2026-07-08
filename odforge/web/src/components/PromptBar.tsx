import { useState } from "react";
import type { DocType } from "../state/types";

const TABS: { id: DocType; label: string }[] = [
  { id: "odp", label: "簡報" }, { id: "odt", label: "文書" }, { id: "ods", label: "試算表" },
];

export function PromptBar({ onGenerate }: { onGenerate: (prompt: string, docType: DocType) => void }) {
  const [prompt, setPrompt] = useState("");
  const [docType, setDocType] = useState<DocType>("odp");
  return (
    <div className="promptbar">
      <div className="doctabs" role="tablist" aria-label="文件型別">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={docType === t.id} className={docType === t.id ? "on" : ""} onClick={() => setDocType(t.id)}>
            {t.label}
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
