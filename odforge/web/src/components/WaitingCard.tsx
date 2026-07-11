import { useEffect, useState } from "react";

const STEPS = [
  "① AI 構思大綱與美術方向",
  "② 逐頁填充內容",
  "③ 引擎渲染與驗證",
];

/**
 * The pre-outline vale: the backend can spend 30–60s thinking before the first
 * `outline` event. Rather than a dead grey screen, show where we are (stage
 * timeline), how long it's taken (mm:ss), a calming line, and an escape hatch.
 *
 * `activeStep` (0-based) highlights the current phase; while this card is shown
 * we are always on step 0 (outline). The clock ticks each second — that's
 * content, not decoration, so it stays under reduced-motion; only the pulse
 * animation degrades (handled in CSS).
 */
export function WaitingCard({ onCancel, activeStep = 0 }: { onCancel: () => void; activeStep?: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");

  return (
    <div className="waitcard" role="status" aria-live="polite">
      <ol className="waitsteps">
        {STEPS.map((label, i) => (
          <li key={i} className={i === activeStep ? "on" : i < activeStep ? "past" : ""} aria-current={i === activeStep || undefined}>
            {label}
          </li>
        ))}
      </ol>
      <div className="waitclock" aria-label="已等待時間">
        <span className="waitspin" aria-hidden="true" />
        <b>{mm}:{ss}</b>
      </div>
      <p className="waitcalm">AI 正在管內容,引擎待會管格式——大綱通常需要 30–60 秒</p>
      <button type="button" className="waitcancel" onClick={onCancel}>
        取消
      </button>
    </div>
  );
}
