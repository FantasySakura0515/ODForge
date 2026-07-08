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
      if (parsed) onEvent(parsed);
    });
  }
  return () => es.close();
}
