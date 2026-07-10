import { eventsUrl } from "./api";
import type { SseEvent } from "./types";

const KNOWN = new Set(["outline", "awaiting_approval", "slide_done", "preview_ready", "qa_round", "complete", "error"]);

export function parseSseEvent(type: string, data: string): SseEvent | null {
  if (!KNOWN.has(type)) return null;
  try {
    return { type, data: JSON.parse(data) } as SseEvent;
  } catch {
    return null;
  }
}

const EVENT_NAMES = ["outline", "awaiting_approval", "slide_done", "preview_ready", "qa_round", "complete", "error"];

export function subscribeJob(
  jobId: string,
  onEvent: (e: SseEvent) => void,
  EventSourceCtor: typeof EventSource = EventSource,
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
  return () => es.close();
}
