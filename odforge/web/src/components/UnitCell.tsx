import type { Unit } from "../state/types";

// 品檢維度獨立於生成維度:一頁可以「生成完成」同時「品檢未過」。徽章把後者說出來,
// 而不是靠塗掉前者。
const QA_ZH = { flagged: "品檢未過", warned: "品檢有建議", clear: "" } as const;

export function UnitCell({ unit, jobId, onOpen }: { unit: Unit; jobId?: string; onOpen?: (n: number) => void }) {
  const realPreview = unit.previewUrl && !unit.previewUrl.startsWith("mock:") && jobId;
  const clickable = Boolean(realPreview && onOpen);
  const qaLabel = unit.qa && unit.qa !== "clear" ? QA_ZH[unit.qa] : "";

  const content = (
    <>
      <span className="cn">{String(unit.n).padStart(2, "0")}</span>
      {qaLabel && <span className="cqa" data-qa={unit.qa}>{qaLabel}</span>}
      {realPreview ? (
        <img className="thumb" alt={`第 ${unit.n} 頁預覽`} src={unit.previewUrl} loading="lazy" decoding="async" />
      ) : (
        <div className="thumb placeholder" data-role={unit.role} aria-hidden="true">
          <span className="pmeta">ODFORGE / {String(unit.n).padStart(2, "0")}</span>
          <span className="pmark" />
          <span className="ptitle">{unit.title}</span>
          <span className="pcopy"><i /><i /><i /></span>
          <span className="pfolio">{unit.role}</span>
        </div>
      )}
    </>
  );

  // 真預覽用原生 button，讓鍵盤、觸控與 disabled/focus 語意由瀏覽器保證；
  // 尚不可開啟時則保留具名圖片語意，不製造假的互動 affordance。
  if (clickable) {
    return (
      <button
        type="button"
        className="cell clickable"
        data-status={unit.status}
        data-qa={unit.qa}
        data-n={unit.n}
        aria-label={`開啟第 ${unit.n} 頁預覽:${unit.title}${qaLabel ? `(${qaLabel})` : ""}`}
        onClick={() => onOpen!(unit.n)}
      >
        {content}
      </button>
    );
  }

  return (
    <div
      className="cell"
      data-status={unit.status}
      data-qa={unit.qa}
      data-n={unit.n}
      role="img"
      aria-label={`第 ${unit.n} 頁:${unit.title}${qaLabel ? `(${qaLabel})` : ""}`}
    >
      {content}
    </div>
  );
}
