import type { DragEvent } from "react";

/** The parts of the 元素 panel: what ODForge decks are built from. Sizes in cm. */
export const COMPONENTS: { kind: string; label: string; w: number; h: number }[] = [
  { kind: "heading", label: "標題", w: 16, h: 1.4 },
  { kind: "body", label: "內文", w: 12, h: 1.2 },
  { kind: "kicker", label: "章節標籤", w: 8, h: 0.7 },
  { kind: "card", label: "卡片", w: 6, h: 4.4 },
  { kind: "step", label: "編號步驟", w: 7.2, h: 2.2 },
  { kind: "metric", label: "數字指標", w: 6, h: 2.7 },
  { kind: "quote", label: "引言", w: 11.5, h: 2.2 },
  { kind: "takeaway", label: "結論帶", w: 20, h: 1.35 },
  { kind: "timeline", label: "時間軸節點", w: 5.8, h: 1.75 },
  { kind: "divider", label: "分隔線", w: 10, h: 0 },
  { kind: "rect", label: "方塊", w: 6, h: 3.5 },
  { kind: "ellipse", label: "圓形", w: 3, h: 3 },
];

export const COMPONENT_MIME = "application/x-odforge-component";

/** A tiny drawing of each part, in the deck's accent colour. */
function Thumb({ kind }: { kind: string }) {
  const a = "var(--ne-thumb-accent)";
  const ink = "#1F2733";
  const muted = "#B9BEC8";
  switch (kind) {
    case "heading":
      return <svg viewBox="0 0 80 48"><rect x="10" y="18" width="58" height="10" rx="2" fill={ink} /></svg>;
    case "body":
      return <svg viewBox="0 0 80 48"><rect x="10" y="16" width="60" height="4" rx="2" fill={muted} /><rect x="10" y="24" width="48" height="4" rx="2" fill={muted} /></svg>;
    case "kicker":
      return <svg viewBox="0 0 80 48"><rect x="10" y="21" width="40" height="5" rx="2" fill={a} /></svg>;
    case "card":
      return (
        <svg viewBox="0 0 80 48">
          <rect x="18" y="5" width="44" height="38" rx="3" fill="#fff" stroke="#C9CDD6" />
          <rect x="23" y="10" width="14" height="3" rx="1.5" fill={a} />
          <rect x="23" y="17" width="28" height="5" rx="2" fill={ink} />
          <rect x="23" y="27" width="32" height="3" rx="1.5" fill={muted} />
          <rect x="23" y="33" width="24" height="3" rx="1.5" fill={muted} />
        </svg>
      );
    case "step":
      return (
        <svg viewBox="0 0 80 48">
          <circle cx="18" cy="18" r="7" fill={a} />
          <text x="18" y="21" fontSize="8" fill="#fff" textAnchor="middle" fontWeight="700">1</text>
          <rect x="30" y="14" width="34" height="6" rx="2" fill={ink} />
          <rect x="30" y="26" width="30" height="3" rx="1.5" fill={muted} />
        </svg>
      );
    case "metric":
      return <svg viewBox="0 0 80 48"><text x="12" y="30" fontSize="20" fontWeight="700" fill={a}>87%</text><rect x="12" y="36" width="30" height="3" rx="1.5" fill={muted} /></svg>;
    case "quote":
      return <svg viewBox="0 0 80 48"><rect x="12" y="10" width="3" height="28" fill={a} /><rect x="20" y="14" width="48" height="6" rx="2" fill={ink} /><rect x="20" y="28" width="20" height="3" rx="1.5" fill={muted} /></svg>;
    case "takeaway":
      return <svg viewBox="0 0 80 48"><rect x="6" y="16" width="68" height="16" rx="2" fill="#fff" stroke={a} /><rect x="6" y="16" width="3" height="16" fill={a} /><rect x="14" y="22" width="40" height="4" rx="2" fill={ink} /></svg>;
    case "timeline":
      return <svg viewBox="0 0 80 48"><line x1="8" y1="24" x2="72" y2="24" stroke="#C9CDD6" /><circle cx="24" cy="24" r="5" fill={a} /><rect x="34" y="12" width="22" height="4" rx="2" fill={a} /><rect x="34" y="30" width="28" height="3" rx="1.5" fill={muted} /></svg>;
    case "divider":
      return <svg viewBox="0 0 80 48"><line x1="10" y1="24" x2="70" y2="24" stroke="#8A8F9C" strokeWidth="1.5" /></svg>;
    case "rect":
      return <svg viewBox="0 0 80 48"><rect x="18" y="10" width="44" height="28" fill="#E8ECF4" /></svg>;
    default:
      return <svg viewBox="0 0 80 48"><circle cx="40" cy="24" r="14" fill={a} /></svg>;
  }
}

export function ElementsPanel({ accent, onInsert }: { accent: string; onInsert: (kind: string) => void }) {
  return (
    <div className="ne-elements" style={{ ["--ne-thumb-accent" as string]: accent }}>
      <p className="ne-elements-hint">點一下放到投影片中央,或直接拖到想放的位置。顏色與字型會跟著這份簡報。</p>
      <div className="ne-elements-grid">
        {COMPONENTS.map((c) => (
          <button
            key={c.kind}
            type="button"
            className="ne-element"
            draggable
            onDragStart={(e: DragEvent) => {
              e.dataTransfer.setData(COMPONENT_MIME, c.kind);
              e.dataTransfer.effectAllowed = "copy";
            }}
            onClick={() => onInsert(c.kind)}
          >
            <Thumb kind={c.kind} />
            <span>{c.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
