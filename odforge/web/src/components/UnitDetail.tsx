import { useEffect, useRef, useState } from "react";
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
      setInstruction("");
    } catch {
      dispatch({ type: "regen_error", data: { n } });
      setError("重生失敗,請再試一次");
    } finally {
      setBusy(false);
    }
  }

  return (
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
            {isRegen && <div className="ud-regenning" aria-hidden="true">重生中…</div>}
          </div>

          <button className="ud-nav next" type="button" aria-label="下一張" disabled={n >= total} onClick={() => go(1)}>›</button>
        </div>

        <form className="ud-regen" onSubmit={onRegen}>
          <input
            type="text"
            className="ud-input"
            placeholder="用一句話描述要怎麼改,例如:改成比較表、字再少一點"
            value={instruction}
            disabled={disabled}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <button className="ud-forge" type="submit" disabled={disabled || instruction.trim() === ""}>重生此頁</button>
        </form>
        {error && <p className="ud-error" role="alert">{error}</p>}
      </div>
    </div>
  );
}
