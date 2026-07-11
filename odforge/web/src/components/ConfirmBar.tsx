import { useState } from "react";

/**
 * Bottom bar for the outline-approval gate. Owns its own submit/loading/error
 * state so a 409/422/network failure surfaces inline and the user can retry —
 * the gate must never be a dead end. `onConfirm` throws on failure.
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
      <button className="confirmgo" type="button" disabled={busy} onClick={go}>
        ✓ 就這樣鍛
      </button>
      <span className="confirmhint">可直接改標題或刪頁</span>
      {error && <span className="confirmerr" role="alert">{error}</span>}
    </div>
  );
}
