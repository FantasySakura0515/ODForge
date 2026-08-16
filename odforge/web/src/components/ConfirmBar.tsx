import { useState } from "react";

/**
 * The outline-approval gate, rendered at the TOP of the outline rail. It used to
 * sit under the page list, where a 20-page outline pushed it out of sight: the run
 * looked stalled because nothing said generation was still waiting on a click.
 * Hence the explicit "按下開始逐頁生成" sub-label — the button must state what
 * pressing it starts, not just agree with the outline.
 *
 * Owns its own submit/loading/error state so a 409/422/network failure surfaces
 * inline and the user can retry — the gate must never be a dead end.
 * `onConfirm` throws on failure.
 */
export function ConfirmBar({ onConfirm }: { onConfirm: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function go() {
    setBusy(true);
    setError(undefined);
    try {
      await onConfirm();
    } catch (e) {
      setError(e instanceof Error ? e.message : "送出失敗,請重試");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="confirmbar">
      <span className="confirmkicker">Gate / 待你確認</span>
      <button className="confirmgo" type="button" disabled={busy} onClick={go}>
        <b>✓ 就這樣鍛</b>
        <small>{busy ? "送出中…" : "按下開始逐頁生成"}</small>
      </button>
      <span className="confirmhint">
        可直接改標題或刪頁；沒按這個鈕就不會開始生成。
      </span>
      {error && <span className="confirmerr" role="alert">{error}</span>}
    </div>
  );
}
