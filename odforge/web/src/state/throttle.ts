import type { SseEvent } from "./types";

// Only the two high-frequency, per-slide visual events are throttled.
const THROTTLED = new Set(["slide_done", "preview_ready"]);

// State-critical / terminal events. These flip phase or rewrite unit status, so a
// throttled slide_done/preview_ready arriving *after* them would corrupt state
// (e.g. a late slide_done after `complete` un-does the done wall → phantom page).
// On arrival we drain the pending visual queue synchronously (preserving order)
// then dispatch — never delayed, but never reordered past its slides either.
const DRAIN_FIRST = new Set(["complete", "error", "qa_round"]);
// Everything else (gate_result, outline, awaiting_approval) is independent of the
// slide wall, so it passes straight through WITHOUT draining — that keeps the
// cell-by-cell animation going even when gates land mid-burst.

export interface ThrottledDispatch {
  /** Feed an SSE event through the visual throttle. */
  push: (e: SseEvent) => void;
  /** Drop the pending queue and stop the timer (on cancel / reset / unmount). */
  cancel: () => void;
}

/**
 * Visual-only throttle for the SSE stream. The backend can emit a whole batch of
 * `slide_done` / `preview_ready` events in the same instant; feeding them to the
 * reducer at once makes the preview wall light up in a single flash. This queues
 * throttled events and releases one per `intervalMs` so the wall fills in
 * cell-by-cell. It never mutates or drops data — only the *timing* of dispatch.
 *
 * Non-throttled events are never delayed. State-critical ones (complete / error /
 * qa_round) additionally drain the pending queue synchronously first, so a late
 * slide can never land after `complete` and undo the done wall. Independent ones
 * (gate_result / outline) pass straight through, leaving the queue to keep
 * animating cell-by-cell even when a gate lands mid-burst.
 */
export function createThrottledDispatch(
  dispatch: (e: SseEvent) => void,
  opts: { intervalMs?: number } = {},
): ThrottledDispatch {
  const intervalMs = opts.intervalMs ?? 100;
  const queue: SseEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  function tick() {
    const next = queue.shift();
    if (next) {
      dispatch(next);
      timer = setTimeout(tick, intervalMs);
    } else {
      timer = null;
    }
  }

  function drain() {
    if (timer != null) {
      clearTimeout(timer);
      timer = null;
    }
    while (queue.length) dispatch(queue.shift()!);
  }

  return {
    push(e) {
      if (THROTTLED.has(e.type)) {
        if (timer == null) {
          dispatch(e); // leading edge — first of a burst lights up immediately
          timer = setTimeout(tick, intervalMs);
        } else {
          queue.push(e); // within cooldown → queue for the next tick
        }
        return;
      }
      if (DRAIN_FIRST.has(e.type)) drain();
      dispatch(e);
    },
    cancel() {
      queue.length = 0;
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}
