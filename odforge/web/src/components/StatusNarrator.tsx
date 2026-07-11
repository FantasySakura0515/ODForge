import { humanizeStage } from "../state/errors";
import type { CockpitState, Phase, Unit } from "../state/types";

export function StatusNarrator({
  phase,
  units,
  error,
  submitting,
  fillingPending,
  expired,
}: {
  phase: Phase;
  units: Unit[];
  error?: CockpitState["error"];
  submitting?: boolean;
  /** 大綱已確認、等待第一個 slide_done 的空窗:報「逐頁填充」而非「等待確認」。 */
  fillingPending?: boolean;
  /** ?job= 復原失敗:於輸入畫面報「找不到這個任務」。 */
  expired?: boolean;
}) {
  // 報「最新」正在填充的一頁(最大 n),而非第一個——批次到達時才不會永遠卡在第 1 頁。
  const forging = units.reduce<Unit | undefined>(
    (best, u) => ((u.status === "filling" || u.status === "regen") && (!best || u.n > best.n) ? u : best),
    undefined,
  );
  const isError = phase === "error";
  const text =
    // 人話前綴(stage → 白話);原始技術訊息留給 ErrorPanel 的「技術細節」與此處 title。
    isError ? humanizeStage(error?.stage)
    : submitting && phase === "empty" ? "已送出,正在生成大綱…"
    : phase === "empty" && expired ? "找不到這個任務,可能已過期"
    : phase === "empty" ? "準備就緒"
    : phase === "outline" ? "已產生大綱與配色"
    : phase === "await" ? (fillingPending ? "大綱已確認,正在逐頁填充…" : "等待你確認大綱…")
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
