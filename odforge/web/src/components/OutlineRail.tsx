import type { Outline } from "../state/types";

const ROLE_ZH: Record<string, string> = {
  title: "封面", agenda: "議程", section: "分節", "two-col": "雙欄", "big-fact": "關鍵數字",
  "title-content": "標題內文", comparison: "對比", chart: "圖表", closing: "結語",
};

export function OutlineRail({ outline }: { outline?: Outline }) {
  if (!outline) return null;
  const p = outline.design?.palette;
  return (
    <aside className="rail left">
      <div className="railhead"><span className="t">大綱 Outline</span><span>{outline.pages.length}</span></div>
      {outline.design && p && (
        <div className="palette">
          <div className="cap">🎨 AI 為此主題設計的配色</div>
          <div className="swatches">
            {[p.bg, p.surface, p.accent, p.text, p.muted].map((c, i) => <i key={i} style={{ background: c }} />)}
          </div>
        </div>
      )}
      <div className="outline">
        {outline.pages.map((u, i) => (
          <div className="orow" key={i}>
            <span className="num">{String(i + 1).padStart(2, "0")}</span>
            <span><span className="ti">{u.title}</span><span className="role">{ROLE_ZH[u.role] ?? u.role}</span></span>
          </div>
        ))}
      </div>
    </aside>
  );
}
