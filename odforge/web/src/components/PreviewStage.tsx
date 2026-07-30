import { useState } from "react";
import type { CockpitAction, DocType, Unit } from "../state/types";
import { UnitCell } from "./UnitCell";
import { UnitDetail } from "./UnitDetail";
import { WaitingCard } from "./WaitingCard";

export function PreviewStage({
  units, docType, jobId, dispatch, submitting, onCancel, selectedN: selectedNProp, onSelect,
}: {
  units: Unit[];
  docType: DocType;
  jobId?: string;
  dispatch: (action: CockpitAction) => void;
  submitting?: boolean;
  onCancel?: () => void;
  /** 受控選取(由 App 提升,讓 FindingRow 也能開 lightbox);未提供時退回內部 state。 */
  selectedN?: number | null;
  onSelect?: (n: number | null) => void;
}) {
  const [selfN, setSelfN] = useState<number | null>(null);
  const selectedN = selectedNProp !== undefined ? selectedNProp : selfN;
  const setSelectedN = onSelect ?? setSelfN;
  // 進度算「已有內容(ir)或已 preview/done」的頁——LLM 填充階段就開始計數,
  // 不再等到 preview 才從 0 跳動。
  const filled = units.filter((u) => u.ir != null || u.status === "preview" || u.status === "done").length;
  // outline 尚未到達的空窗:顯示等待卡而非空縮圖牆。outline 一到 units 便非空,自動收起。
  const waiting = !!submitting && units.length === 0;

  return (
    <section className="center">
      <div className="stagehead">
        <div>
          <span className="section-index">Canvas / 文件畫布</span>
          <h2 className="t">預覽舞台</h2>
        </div>
        <div className="progress-block" aria-label={`已完成 ${filled} 頁，共 ${units.length} 頁`}>
          <span className="prog"><b>{filled}</b><i> / </i>{units.length}</span>
          <span className="progress-track" aria-hidden="true">
            <i style={{ width: `${units.length ? (filled / units.length) * 100 : 0}%` }} />
          </span>
        </div>
      </div>
      {waiting ? (
        <WaitingCard onCancel={() => onCancel?.()} />
      ) : (
        <>
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
        </>
      )}
    </section>
  );
}
