import { useState } from "react";
import type { CockpitAction, DocType, Unit } from "../state/types";
import { UnitCell } from "./UnitCell";
import { UnitDetail } from "./UnitDetail";

export function PreviewStage({
  units, docType, jobId, dispatch,
}: {
  units: Unit[];
  docType: DocType;
  jobId?: string;
  dispatch: (action: CockpitAction) => void;
}) {
  const [selectedN, setSelectedN] = useState<number | null>(null);
  const done = units.filter((u) => u.status === "done" || u.status === "preview").length;

  return (
    <section className="center">
      <div className="stagehead">
        <span className="t">預覽舞台 Preview</span>
        <span className="prog"><b>{done}</b> / {units.length} 頁</span>
      </div>
      <div className="grid" data-doctype={docType}>
        {units.map((u) => <UnitCell key={u.n} unit={u} jobId={jobId} onOpen={setSelectedN} />)}
      </div>
      {selectedN != null && (
        <UnitDetail
          units={units}
          n={selectedN}
          jobId={jobId}
          onClose={() => setSelectedN(null)}
          onNavigate={setSelectedN}
          dispatch={dispatch}
        />
      )}
    </section>
  );
}
