import { humanizeStage } from "../state/errors";
import type { CockpitState } from "../state/types";

/**
 * 生成失敗時的中央錯誤區:stage 翻成白話標題,原始技術訊息收進可展開的
 * <details>「技術細節」,並提供「重試」——以同樣參數重新送出上一次 generate。
 * 錯誤絕不是死路。
 */
export function ErrorPanel({ error, onRetry }: { error?: CockpitState["error"]; onRetry?: () => void }) {
  const head = humanizeStage(error?.stage);
  return (
    <section className="center errorpanel">
      <div className="errbox" role="alert">
        <div className="erricon" aria-hidden="true">✕</div>
        <h2 className="errhead">{head}</h2>
        {error?.message && (
          <details className="errdetails">
            <summary>技術細節</summary>
            <pre className="errmsg">{error.message}</pre>
          </details>
        )}
        {onRetry && (
          <button type="button" className="errretry" onClick={onRetry}>
            重試
          </button>
        )}
      </div>
    </section>
  );
}
