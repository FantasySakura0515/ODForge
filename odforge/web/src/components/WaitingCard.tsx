import { useEffect, useState } from "react";

const STEPS = [
  "① 構思大綱",
  "② 產生投影片",
  "③ 渲染與驗證",
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
    // 刻意不把 role="status" 放在整張卡上:卡片裡有一個每秒跳動的計時器,整片
    // 設成 live region 等於叫讀屏器每秒念一次時間。只有「階段變了」才值得播報,
    // 所以 live region 縮到下面那一行離散的階段文字。
    <div className="waitcard">
      <ol className="waitsteps">
        {STEPS.map((label, i) => (
          <li key={i} className={i === activeStep ? "on" : i < activeStep ? "past" : ""} aria-current={i === activeStep || undefined}>
            {label}
          </li>
        ))}
      </ol>
      <span className="sr-only" role="status" aria-live="polite">
        {STEPS[activeStep] ?? STEPS[0]}
      </span>
      <div className="waitclock" aria-hidden="true">
        <span className="waitspin" />
        <b>{mm}:{ss}</b>
      </div>
      <p className="waitcalm">正在生成大綱，通常需要 30–60 秒。</p>
      <button type="button" className="waitcancel" onClick={onCancel}>
        取消
      </button>
    </div>
  );
}
