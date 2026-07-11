import type { GateId, GateStatus } from "../state/types";

const GATES: { id: GateId; label: string; sub: string; code: string }[] = [
  { id: "zip", label: "① 封裝結構", sub: "mimetype 為首 · manifest", code: "zip" },
  { id: "xml", label: "② XML 正確性", sub: "每個部件 well-formed", code: "xml" },
  { id: "libreoffice", label: "③ LibreOffice 轉檔", sub: "headless 真轉 PDF", code: "LO" },
  { id: "design", label: "④ 設計閘", sub: "vision 檢視 · 逐頁把關", code: "◑" },
];
const SYM: Record<GateStatus, string> = { pending: "·", active: "⟳", pass: "✓", fail: "✕", skipped: "—" };

export function GateRail({ gates, qaRounds }: { gates: Record<GateId, GateStatus>; qaRounds: { round: number }[] }) {
  return (
    <aside className="rail right">
      <div className="railhead"><span className="t">四道閘 Gates</span></div>
      <div className="gates">
        {GATES.map((g) => (
          <div className="gate" key={g.id} data-gate={g.id} data-status={gates[g.id]}>
            <span className="gi">{g.code}</span>
            <span className="gt"><b>{g.label}</b><small>{g.sub}</small></span>
            <span className="gs">{gates[g.id] === "skipped" ? "— 未啟用" : SYM[gates[g.id]]}</span>
          </div>
        ))}
      </div>
      {qaRounds.length > 0 && <div className="qa"><span className="round">第 {qaRounds.length} 輪</span></div>}
    </aside>
  );
}
