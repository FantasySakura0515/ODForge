import type { DocType, Unit } from "../state/types";
import { UnitCell } from "./UnitCell";

export function PreviewStage({ units, docType, jobId }: { units: Unit[]; docType: DocType; jobId?: string }) {
  const done = units.filter((u) => u.status === "done" || u.status === "preview").length;
  return (
    <section className="center">
      <div className="stagehead">
        <span className="t">預覽舞台 Preview</span>
        <span className="prog"><b>{done}</b> / {units.length} 頁</span>
      </div>
      <div className="grid" data-doctype={docType}>
        {units.map((u) => <UnitCell key={u.n} unit={u} jobId={jobId} />)}
      </div>
    </section>
  );
}
