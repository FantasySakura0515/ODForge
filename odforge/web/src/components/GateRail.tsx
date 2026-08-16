import type { DroppedContent, Finding, GateId, GateStatus } from "../state/types";

const GATES: { id: GateId; label: string; sub: string; code: string; tip: string }[] = [
  { id: "zip", label: "封裝結構", sub: "ODF 打包與 manifest", code: "01", tip: "檔案打包結構正確(mimetype 為首)" },
  { id: "xml", label: "內容格式", sub: "XML 可正確解析", code: "02", tip: "內容格式合法(XML 可解析)" },
  { id: "libreoffice", label: "實際開檔", sub: "LibreOffice 轉檔驗證", code: "03", tip: "LibreOffice 實際開得起來(真轉 PDF)" },
  { id: "design", label: "設計品質", sub: "逐頁檢視與修復", code: "04", tip: "AI 視覺品檢(第四道閘)" },
];
const SYM: Record<GateStatus, string> = {
  pending: "·", active: "⟳", pass: "✓", fail: "✕", skipped: "—", unknown: "?",
};
const STATUS_ZH: Record<GateStatus, string> = {
  pending: "待處理", active: "進行中", pass: "已通過", fail: "未通過",
  skipped: "未啟用", unknown: "無法判定",
};
// 「未啟用」與「無法判定」長得不能一樣:前者是使用者關掉了這道閘,後者是這道閘
// 該跑卻跑不成,這份交付的設計實際上沒被驗過。
const LONG_ZH: Partial<Record<GateStatus, string>> = {
  skipped: "— 未啟用",
  unknown: "? 無法判定",
};

const SEV_ZH: Record<Finding["severity"], string> = { error: "錯誤", warn: "警告" };

// 單筆 finding 一列:第 {slide_no} 頁 · {issue} · {severity 徽章} · {fix_hint}。
// 有 onOpen 時整列可點/可鍵盤操作 → 開該頁 lightbox。
function FindingRow({ f, onOpen }: { f: Finding; onOpen?: (n: number) => void }) {
  const content = (
    <>
      <span className="fno">第 {f.slide_no} 頁</span>
      <span className="fsev" data-severity={f.severity}>{SEV_ZH[f.severity]}</span>
      <span className="fissue">{f.issue}</span>
      <span className="ffix">{f.fix_hint}</span>
    </>
  );
  if (onOpen) {
    return (
      <li className="finding-wrap">
        <button
          type="button"
          className="finding clickable"
          data-severity={f.severity}
          aria-label={`第 ${f.slide_no} 頁:${f.issue}`}
          onClick={() => onOpen(f.slide_no)}
        >
          {content}
        </button>
      </li>
    );
  }
  return (
    <li className="finding" data-severity={f.severity}>
      {content}
    </li>
  );
}

export function GateRail({
  gates,
  gateNotes = {},
  qaRounds,
  dropped = [],
  onOpenFinding,
}: {
  gates: Record<GateId, GateStatus>;
  // 後端對某道閘的原因說明。「未啟用」不附原因就等於叫使用者去猜是設定問題還是壞了。
  gateNotes?: Partial<Record<GateId, string>>;
  qaRounds: { round: number; findings: Finding[] }[];
  /** 因版面限制未能保留的內容。使用者要求過這些字,消失了就必須看得見。 */
  dropped?: DroppedContent[];
  onOpenFinding?: (n: number) => void;
}) {
  const passed = GATES.filter((g) => gates[g.id] === "pass").length;

  return (
    <aside className="rail right">
      <div className="railhead">
        <div>
          <span className="section-index">Quality / 品質</span>
          <h2 className="t">交付檢查</h2>
        </div>
        <span className="gate-score"><b>{passed}</b> / 4</span>
      </div>

      {/* 四道閘保證的是「格式正確、打得開、版面看過」。它們一個字都沒有查證過
          內容的真偽 —— 數字、日期、引言與來源連結都可能是模型生成的。不寫出來,
          四個綠勾看起來就像在替內容背書。 */}
      <p className="gate-scope">
        這四道閘檢查的是<b>格式與版面</b>；內容的數字、日期、引言與來源連結
        <b>未經查證</b>，請自行確認。
      </p>
      <div className="gates">
        {GATES.map((g) => (
          <div
            className="gate"
            key={g.id}
            data-gate={g.id}
            data-status={gates[g.id]}
            title={g.tip}
            role="group"
            aria-label={`${g.label}：${STATUS_ZH[gates[g.id]]}`}
          >
            <span className="gi">{g.code}</span>
            <span className="gt"><b>{g.label}</b><small>{g.sub}</small></span>
            <span className="gs" aria-hidden="true">
              {LONG_ZH[gates[g.id]] ??
                (gates[g.id] === "active"
                  ? <span className="spin" aria-hidden="true">{SYM.active}</span>
                  : SYM[gates[g.id]])}
            </span>
            {gateNotes[g.id] && (
              <span className="gnote" data-testid={`gate-note-${g.id}`}>{gateNotes[g.id]}</span>
            )}
          </div>
        ))}
      </div>
      {dropped.length > 0 && (
        <div className="qa dropped" data-testid="dropped-content">
          <div className="qahead">未能保留的內容</div>
          <p className="qaempty">
            以下內容因版面放不下而被移除,已寫進該頁備忘稿。想保留的話,提高頁數或
            精簡需求後再鍛一次。
          </p>
          <ul className="findings">
            {dropped.map((d, i) => (
              <li className="finding" data-severity="warn" key={i}>
                <span className="fno">第 {d.slide_no} 頁</span>
                <span className="fissue">{d.title}</span>
                <span className="ffix">{d.items.join("、")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

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
