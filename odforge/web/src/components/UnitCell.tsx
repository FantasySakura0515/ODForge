import { previewUrl } from "../state/api";
import type { Unit } from "../state/types";

export function UnitCell({ unit, jobId }: { unit: Unit; jobId?: string }) {
  const realPreview = unit.previewUrl && !unit.previewUrl.startsWith("mock:") && jobId;
  return (
    <div className="cell" data-status={unit.status} data-n={unit.n}>
      <span className="cn">{String(unit.n).padStart(2, "0")}</span>
      {realPreview ? (
        <img className="thumb" alt={`第 ${unit.n} 頁預覽`} src={previewUrl(jobId, unit.n)} />
      ) : (
        <div className="thumb placeholder" aria-hidden="true"><span className="ptitle">{unit.title}</span></div>
      )}
    </div>
  );
}
