import { eventsUrl } from "./api";
import type { SseEvent } from "./types";

// unit_done 為 slide_done 的 F4 正名別名(後端並發兩者);兩者都收,reducer 等價處理。
const KNOWN = new Set(["outline", "awaiting_approval", "slide_done", "unit_done", "preview_ready", "gate_result", "qa_round", "complete", "error"]);

export function parseSseEvent(type: string, data: string): SseEvent | null {
  if (!KNOWN.has(type)) return null;
  try {
    return { type, data: JSON.parse(data) } as SseEvent;
  } catch {
    return null;
  }
}

const EVENT_NAMES = ["outline", "awaiting_approval", "slide_done", "unit_done", "preview_ready", "gate_result", "qa_round", "complete", "error"];

export function subscribeJob(
  jobId: string,
  onEvent: (e: SseEvent) => void,
  EventSourceCtor: typeof EventSource = EventSource,
  onError?: (e: unknown) => void,
): () => void {
  const es = new EventSourceCtor(eventsUrl(jobId));
  for (const name of EVENT_NAMES) {
    es.addEventListener(name, (ev) => {
      const parsed = parseSseEvent(name, (ev as MessageEvent).data);
      if (!parsed) return;
      onEvent(parsed);
      // The backend ends the stream after a terminal event; close our side too so
      // the browser's EventSource doesn't auto-reconnect and replay the whole history.
      if (parsed.type === "complete" || parsed.type === "error") es.close();
    });
  }
  // Transport-level failures (e.g. the job id is 404 after a server restart) surface
  // through onerror, not the named `error` event. Resume flow uses this to detect an
  // expired job. Named-event errors above already close the stream, so a late
  // reconnect onerror won't reach here in the happy path.
  if (onError) es.onerror = onError;
  return () => es.close();
}
