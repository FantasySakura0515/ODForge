import { useEffect, useState } from "react";
import type { OutlineActionBody } from "../state/api";
import type { Outline, OutlineRow, Phase } from "../state/types";
import { ConfirmBar } from "./ConfirmBar";

const ROLE_ZH: Record<string, string> = {
  title: "封面", agenda: "議程", section: "分節", "two-col": "雙欄", "big-fact": "關鍵數字",
  "title-content": "標題內文", comparison: "對比", chart: "圖表", closing: "結語",
};

export function OutlineRail({
  outline,
  phase,
  onConfirm,
}: {
  outline?: Outline;
  phase?: Phase;
  onConfirm?: (action: OutlineActionBody) => Promise<void>;
}) {
  const [draft, setDraft] = useState<OutlineRow[]>([]);
  const [submitted, setSubmitted] = useState(false);

  // Snapshot a fresh draft each time we (re-)enter the approval gate.
  useEffect(() => {
    if (phase === "await" && outline) {
      setDraft(outline.pages.map((r) => ({ ...r })));
      setSubmitted(false);
    }
  }, [phase, outline]);

  if (!outline) return null;
  const p = outline.design?.palette;
  const editing = phase === "await" && !!onConfirm && !submitted;
  // While in the approval gate, show the working draft — even after a successful
  // submit (submitted=true) — so the rail reflects the titles the user just sent,
  // not the stale pre-edit outline still held in props until the backend resumes.
  const rows = phase === "await" && !!onConfirm && draft.length ? draft : outline.pages;

  const changed =
    editing &&
    (draft.length !== outline.pages.length ||
      draft.some((r, i) => r.title !== outline.pages[i]?.title));

  function setTitle(i: number, title: string) {
    setDraft((d) => d.map((r, j) => (j === i ? { ...r, title } : r)));
  }
  function removeRow(i: number) {
    setDraft((d) => (d.length <= 1 ? d : d.filter((_, j) => j !== i)));
  }
  async function confirm() {
    const body: OutlineActionBody = changed
      ? { action: "edit", outline: { ...outline!, pages: draft } }
      : { action: "approve" };
    await onConfirm!(body);
    setSubmitted(true); // only on success → collapse the ConfirmBar
  }

  return (
    <aside className="rail left">
      <div className="railhead"><span className="t">大綱 Outline</span><span>{rows.length}</span></div>
      {outline.design && p && (
        <div className="palette">
          <div className="cap">🎨 AI 為此主題設計的配色</div>
          <div className="swatches">
            {[p.bg, p.surface, p.accent, p.text, p.muted].map((c, i) => <i key={i} style={{ background: c }} />)}
          </div>
        </div>
      )}
      <div className="outline">
        {rows.map((u, i) => (
          <div className={editing ? "orow editing" : "orow"} key={i}>
            <span className="num">{String(i + 1).padStart(2, "0")}</span>
            {editing ? (
              <>
                <input
                  className="oedit"
                  aria-label={`第 ${i + 1} 頁標題`}
                  value={u.title}
                  onChange={(e) => setTitle(i, e.target.value)}
                />
                <button
                  type="button"
                  className="odel"
                  aria-label={`刪除第 ${i + 1} 頁`}
                  disabled={draft.length <= 1}
                  onClick={() => removeRow(i)}
                >
                  ✕
                </button>
              </>
            ) : (
              <span><span className="ti">{u.title}</span><span className="role">{ROLE_ZH[u.role] ?? u.role}</span></span>
            )}
          </div>
        ))}
      </div>
      {editing && <ConfirmBar onConfirm={confirm} />}
    </aside>
  );
}
