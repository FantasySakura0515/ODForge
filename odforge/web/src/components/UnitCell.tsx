import type { Unit } from "../state/types";

export function UnitCell({ unit, jobId, onOpen }: { unit: Unit; jobId?: string; onOpen?: (n: number) => void }) {
  const realPreview = unit.previewUrl && !unit.previewUrl.startsWith("mock:") && jobId;
  const clickable = Boolean(realPreview && onOpen);

  // 每格都要有可及名稱:可點時是「開啟…」的按鈕,不可點時是一張具名縮圖(role=img)。
  // 標題原本只掛在 aria-hidden 的 placeholder 上,讀屏器聽不到,故名稱一律帶頁碼＋標題。
  const interactive = clickable
    ? {
        role: "button" as const,
        tabIndex: 0,
        "aria-label": `開啟第 ${unit.n} 頁預覽:${unit.title}`,
        onClick: () => onOpen!(unit.n),
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen!(unit.n); }
        },
      }
    : { role: "img" as const, "aria-label": `第 ${unit.n} 頁:${unit.title}` };

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
