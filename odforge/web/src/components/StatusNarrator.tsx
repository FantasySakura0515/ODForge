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
    : submitting && phase === "empty" ? "正在生成大綱…"
    : phase === "empty" && expired ? "任務不存在或已過期"
    : phase === "empty" ? "準備就緒"
    // 編輯確認後 synthetic outline dispatch 會把 phase 設回 "outline";此時
    // fillingPending 已立起,narrator 應報「逐頁填充」而非久掛「已產生大綱與配色」。
    : phase === "outline" ? (fillingPending ? "正在逐頁生成…" : "大綱與配色已完成")
    : phase === "await" ? (fillingPending ? "正在逐頁生成…" : "請確認大綱")
    : phase === "qa" ? "正在檢查設計…"
    : phase === "complete" ? "完成 · 原生 ODF"
    : forging ? `正在生成第 ${forging.n} 頁…` : "生成中…";

  // What is *announced* is deliberately coarser than what is *shown*. The visible
  // line updates per page, and pages arrive in throttled bursts every 80–120 ms —
  // a screen reader reading "正在生成第 7 頁…", "第 8 頁…", "第 9 頁…" thirty times
  // is not progress information, it is a denial of service on the user's ears.
  // So the live region carries the discrete phase only, plus a single "N pages
  // done" summary that changes once per phase rather than once per page.
  const done = units.filter((u) => u.status === "done" || u.status === "preview").length;
  const announcement =
    isError ? `發生問題:${humanizeStage(error?.stage)}`
    : phase === "empty" && expired ? "任務不存在或已過期"
    : submitting && phase === "empty" ? "正在生成大綱"
    : phase === "empty" ? "準備就緒"
    : phase === "await" && !fillingPending ? "大綱已完成,請確認後開始生成"
    : phase === "qa" ? "正在檢查設計"
    : phase === "complete" ? `完成,共 ${units.length} 頁,可以下載了`
    : phase === "generating" || fillingPending ? "正在逐頁生成"
    : phase === "outline" ? "大綱與配色已完成"
    : "";

  return (
    <div
      className={"narrator" + (isError ? " is-error" : "")}
      title={isError && error ? error.message : undefined}
    >
      {/* 視覺上的逐頁播報:看得到,但不進 live region。 */}
      <span className="ndot" aria-hidden="true">{isError ? "✕" : "◆"}</span>
      <span className="nn" aria-hidden="true">{text}</span>
      <span className="sr-only" role="status" aria-live="polite">
        {announcement}
      </span>
      {/* 頁數進度給讀屏器一個可查詢(但不主動播報)的數字。 */}
      <span className="sr-only" aria-live="off">
        已完成 {done} / {units.length} 頁
      </span>
    </div>
  );
}
