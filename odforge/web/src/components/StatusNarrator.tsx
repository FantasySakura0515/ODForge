import type { CockpitState, Phase, Unit } from "../state/types";

export function StatusNarrator({
  phase,
  units,
  error,
  submitting,
}: {
  phase: Phase;
  units: Unit[];
  error?: CockpitState["error"];
  submitting?: boolean;
}) {
  const forging = units.find((u) => u.status === "filling" || u.status === "regen");
  const isError = phase === "error";
  const text =
    isError && error ? `錯誤(${error.stage}):${error.message}`
    : isError ? "發生錯誤"
    : submitting && phase === "empty" ? "已送出,正在生成大綱…"
    : phase === "empty" ? "準備就緒"
    : phase === "outline" ? "已產生大綱與配色"
    : phase === "await" ? "等待你確認大綱…"
    : phase === "qa" ? "設計閘檢視中…"
    : phase === "complete" ? "完成 · 原生 ODF"
    : forging ? `第 ${forging.n} 頁鍛造中…` : "生成中…";
  return (
    <div className={"narrator" + (isError ? " is-error" : "")} title={isError && error ? error.message : undefined}>
      <span className="ndot">{isError ? "✕" : "◆"}</span>
      <span className="nn">{text}</span>
    </div>
  );
}
