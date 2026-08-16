import { eventsUrl } from "./api";
import type { SseEvent } from "./types";

// unit_done 為 slide_done 的 F4 正名別名(後端並發兩者);兩者都收,reducer 等價處理。
const KNOWN = new Set(["outline", "awaiting_approval", "slide_done", "unit_done", "preview_ready", "gate_result", "qa_round", "content_degraded", "complete", "error"]);

export function parseSseEvent(type: string, data: string): SseEvent | null {
  if (!KNOWN.has(type)) return null;
  try {
    return { type, data: JSON.parse(data) } as SseEvent;
  } catch {
    return null;
  }
}

const EVENT_NAMES = ["outline", "awaiting_approval", "slide_done", "unit_done", "preview_ready", "gate_result", "qa_round", "content_degraded", "complete", "error"];

export function subscribeJob(
  jobId: string,
  onEvent: (e: SseEvent) => void,
  EventSourceCtor: typeof EventSource = EventSource,
  onError?: (e: unknown) => void,
  onOpen?: () => void,
): () => void {
  const es = new EventSourceCtor(eventsUrl(jobId));
  // 每次連線「開啟」都會觸發——含 EventSource 斷線後的自動重連。後端無
  // Last-Event-ID,重連必從頭重播;呼叫端用這個時機重設節流器的去重狀態,
  // 讓重播完整走一遍(reducer 冪等,重建後牆與進度正確)。
  if (onOpen) es.onopen = onOpen;
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
