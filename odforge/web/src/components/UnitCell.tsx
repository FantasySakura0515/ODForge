import type { Unit } from "../state/types";

export function UnitCell({ unit, jobId, onOpen }: { unit: Unit; jobId?: string; onOpen?: (n: number) => void }) {
  const realPreview = unit.previewUrl && !unit.previewUrl.startsWith("mock:") && jobId;
  const clickable = Boolean(realPreview && onOpen);

  const interactive = clickable
    ? {
        role: "button" as const,
        tabIndex: 0,
        "aria-label": `開啟第 ${unit.n} 頁預覽`,
        onClick: () => onOpen!(unit.n),
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen!(unit.n); }
        },
      }
    : {};

  return (
    <div className={`cell${clickable ? " clickable" : ""}`} data-status={unit.status} data-n={unit.n} {...interactive}>
      <span className="cn">{String(unit.n).padStart(2, "0")}</span>
      {realPreview ? (
        <img className="thumb" alt={`第 ${unit.n} 頁預覽`} src={unit.previewUrl} />
      ) : (
        <div className="thumb placeholder" aria-hidden="true"><span className="ptitle">{unit.title}</span></div>
      )}
    </div>
  );
}
