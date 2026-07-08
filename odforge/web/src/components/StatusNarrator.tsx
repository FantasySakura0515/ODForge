import type { Phase, Unit } from "../state/types";

export function StatusNarrator({ phase, units }: { phase: Phase; units: Unit[] }) {
  const forging = units.find((u) => u.status === "filling" || u.status === "regen");
  const text =
    phase === "empty" ? "準備就緒"
    : phase === "outline" ? "已產生大綱與配色"
    : phase === "await" ? "等待你確認大綱…"
    : phase === "qa" ? "設計閘檢視中…"
    : phase === "complete" ? "完成 · 四道閘全綠 · 原生 ODF"
    : phase === "error" ? "發生錯誤,請看右側閘門"
    : forging ? `第 ${forging.n} 頁鍛造中…` : "生成中…";
  return <div className="narrator"><span className="ndot">◆</span><span className="nn">{text}</span></div>;
}
