import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { postRegenerate } from "../state/api";
import type { CockpitAction, Unit } from "../state/types";

interface Props {
  units: Unit[];
  n: number;
  jobId?: string;
  onClose: () => void;
  onNavigate: (n: number) => void;
  dispatch: (action: CockpitAction) => void;
}

function hasRealPreview(unit: Unit | undefined, jobId?: string): boolean {
  return Boolean(unit?.previewUrl && !unit.previewUrl.startsWith("mock:") && jobId);
}

export function UnitDetail({ units, n, jobId, onClose, onNavigate, dispatch }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [instruction, setInstruction] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const total = units.length;
  const unit = units.find((u) => u.n === n);
  const hasPreview = hasRealPreview(unit, jobId);
  const isRegen = unit?.status === "regen";
  const disabled = isRegen || busy;

  // Move focus into the dialog on open; restore it to the opener on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  if (!unit) return null;

  function go(delta: number) {
    const next = n + delta;
    if (next >= 1 && next <= total) {
      onNavigate(next);
      // 邊界鈕會被 disable,焦點可能掉到 body;導覽後把焦點拉回 dialog 容器,
      // 讓後續方向鍵/Esc 仍由 dialog 的 onKeyDown 接住。
      dialogRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
    // 使用者在重生指令輸入框(input/textarea/contenteditable)內按方向鍵是移游標,
    // 不應被劫持成翻頁。Esc 仍照常關閉(在上面已處理)。
    const target = e.target as HTMLElement;
    const typing =
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target.isContentEditable;
    if (typing && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (e.key === "ArrowLeft") { go(-1); return; }
    if (e.key === "ArrowRight") { go(1); return; }
    if (e.key === "Tab") {
      // Basic focus trap: keep Tab within the dialog.
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), [href], input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === dialogRef.current)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault(); first.focus();
      }
    }
  }

  async function onRegen(e: React.FormEvent) {
    e.preventDefault();
    const text = instruction.trim();
    if (!text || !jobId || disabled) return;
    setError(null);
    setBusy(true);
    dispatch({ type: "regen_start", data: { n } });
    try {
      const res = await postRegenerate(jobId, n, text);
      dispatch({ type: "regen_done", data: { n: res.n, slide: res.slide, preview_url: res.preview_url } });
      // 成品換了一版 → 四道閘是後端重算過的結果,照單接收。特別是設計閘:
      // 視覺評審看的是被換掉的那一頁,舊的綠勾不能留在畫面上冒充這一版的保證。
      for (const gate of res.gates ?? []) dispatch({ type: "gate_result", data: gate });
      setInstruction("");
    } catch (err) {
      dispatch({ type: "regen_error", data: { n } });
      // 後端在失敗時已完整回滾(IR、成品、預覽、閘門都是原樣),所以這裡只要說
      // 清楚「這一頁沒有變」,使用者才知道不必擔心檔案已經被改壞。
      setError(
        err instanceof Error && err.message
          ? `重生失敗,這一頁維持原樣:${err.message}`
          : "重生失敗,這一頁維持原樣,請再試一次",
      );
    } finally {
      setBusy(false);
    }
  }

  // Portal 到 body:.center 是 position:relative + z-index:2 的 stacking context,
  // 在裡面時 z-index:50 只跟 center 的子元素比;同層的 .rail/.foot(z-index:2、DOM
  // 在後)會整片蓋過遮罩。跳出去,fixed 遮罩才真的罩住整個 cockpit。
  return createPortal(
    <div className="lightbox" onClick={onClose}>
      <div
        className="ud-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`第 ${n} 頁預覽:${unit.title}`}
        tabIndex={-1}
        ref={dialogRef}
        onKeyDown={onKeyDown}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ud-head">
          <span className="ud-caption" data-testid="ud-caption">
            第 {n} / {total} 頁 · <span className="ud-role">{unit.role}</span> · {unit.title}
          </span>
          <button className="ud-close" type="button" aria-label="關閉" onClick={onClose}>✕</button>
        </div>

        <div className="ud-stage">
          <button className="ud-nav prev" type="button" aria-label="上一張" disabled={n <= 1} onClick={() => go(-1)}>‹</button>

          <div className="ud-canvas" data-status={unit.status}>
            {hasPreview ? (
              <img className="ud-img" src={unit.previewUrl} alt={`第 ${n} 頁預覽`} />
            ) : (
              <div className="ud-empty">
                <div className="ud-skel" aria-hidden="true" />
                <span>此頁尚無預覽</span>
              </div>
            )}
            {isRegen && <div className="ud-regenning" aria-hidden="true"><span className="spin">⟳</span> 重生中…</div>}
          </div>

          <button className="ud-nav next" type="button" aria-label="下一張" disabled={n >= total} onClick={() => go(1)}>›</button>
        </div>

        <form className="ud-regen" onSubmit={onRegen}>
          <input
            type="text"
            className="ud-input"
            aria-label="重生指令"
            placeholder="用一句話描述要怎麼改,例如:改成比較表、字再少一點"
            value={instruction}
            disabled={disabled}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <button className="ud-forge" type="submit" disabled={disabled || instruction.trim() === ""}>重生此頁</button>
        </form>
        {error && <p className="ud-error" role="alert">{error}</p>}
      </div>
    </div>,
    document.body,
  );
}
