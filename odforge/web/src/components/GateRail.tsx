import type { Finding, GateId, GateStatus } from "../state/types";

const GATES: { id: GateId; label: string; sub: string; code: string; tip: string }[] = [
  { id: "zip", label: "① 封裝結構", sub: "mimetype 為首 · manifest", code: "zip", tip: "檔案打包結構正確(mimetype 為首)" },
  { id: "xml", label: "② XML 正確性", sub: "每個部件 well-formed", code: "xml", tip: "內容格式合法(XML 可解析)" },
  { id: "libreoffice", label: "③ LibreOffice 轉檔", sub: "headless 真轉 PDF", code: "LO", tip: "LibreOffice 實際開得起來(真轉 PDF)" },
  { id: "design", label: "④ 設計閘", sub: "vision 檢視 · 逐頁把關", code: "◑", tip: "AI 視覺品檢(第四道閘)" },
];
const SYM: Record<GateStatus, string> = { pending: "·", active: "⟳", pass: "✓", fail: "✕", skipped: "—" };

const SEV_ZH: Record<Finding["severity"], string> = { error: "錯誤", warn: "警告" };

// 單筆 finding 一列:第 {slide_no} 頁 · {issue} · {severity 徽章} · {fix_hint}。
// 有 onOpen 時整列可點/可鍵盤操作 → 開該頁 lightbox。
function FindingRow({ f, onOpen }: { f: Finding; onOpen?: (n: number) => void }) {
  const clickable = !!onOpen;
  const interactive = clickable
    ? {
        role: "button" as const,
        tabIndex: 0,
        "aria-label": `第 ${f.slide_no} 頁:${f.issue}`,
        onClick: () => onOpen!(f.slide_no),
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen!(f.slide_no); }
        },
      }
    : {};
  return (
    <li className={"finding" + (clickable ? " clickable" : "")} data-severity={f.severity} {...interactive}>
      <span className="fno">第 {f.slide_no} 頁</span>
      <span className="fsev" data-severity={f.severity}>{SEV_ZH[f.severity]}</span>
      <span className="fissue">{f.issue}</span>
      <span className="ffix">{f.fix_hint}</span>
    </li>
  );
}

export function GateRail({
  gates,
  qaRounds,
  onOpenFinding,
}: {
  gates: Record<GateId, GateStatus>;
  qaRounds: { round: number; findings: Finding[] }[];
  onOpenFinding?: (n: number) => void;
}) {
  return (
    <aside className="rail right">
      <div className="railhead"><span className="t">四道閘 Gates</span></div>
      <div className="gates">
        {GATES.map((g) => (
          <div className="gate" key={g.id} data-gate={g.id} data-status={gates[g.id]} title={g.tip}>
            <span className="gi">{g.code}</span>
            <span className="gt"><b>{g.label}</b><small>{g.sub}</small></span>
            <span className="gs">
              {gates[g.id] === "skipped" ? "— 未啟用"
                : gates[g.id] === "active" ? <span className="spin" aria-hidden="true">{SYM.active}</span>
                : SYM[gates[g.id]]}
            </span>
          </div>
        ))}
      </div>
      {qaRounds.length > 0 && (
        <div className="qa">
          <div className="qahead">設計 QA 發現</div>
          {qaRounds.map((r, i) => {
            const latest = i === qaRounds.length - 1;
            return (
              <details className="qaround" key={r.round} open={latest}>
                <summary className="qasum">
                  <span className="round">第 {r.round} 輪</span>
                  <span className="qacount">{r.findings.length > 0 ? `${r.findings.length} 項` : "無發現"}</span>
                </summary>
                {r.findings.length === 0 ? (
                  <p className="qaempty">本輪無發現 ✓</p>
                ) : (
                  <ul className="findings">
                    {r.findings.map((f, j) => <FindingRow key={j} f={f} onOpen={onOpenFinding} />)}
                  </ul>
                )}
              </details>
            );
          })}
        </div>
      )}
    </aside>
  );
}
