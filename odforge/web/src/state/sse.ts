import type { SseEvent } from "./types";

const KNOWN = new Set(["outline", "awaiting_approval", "unit_done", "preview_ready", "qa_round", "complete", "error"]);

export function parseSseEvent(type: string, data: string): SseEvent | null {
  if (!KNOWN.has(type)) return null;
  try {
    return { type, data: JSON.parse(data) } as SseEvent;
  } catch {
    return null;
  }
}
