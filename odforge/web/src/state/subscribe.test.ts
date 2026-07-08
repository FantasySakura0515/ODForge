import { expect, test, vi } from "vitest";
import { subscribeJob } from "./sse";
import type { SseEvent } from "./types";

class FakeEventSource {
  listeners: Record<string, (e: { data: string }) => void> = {};
  closed = false;
  constructor(public url: string) {}
  addEventListener(type: string, cb: (e: { data: string }) => void) { this.listeners[type] = cb; }
  close() { this.closed = true; }
  emit(type: string, data: unknown) { this.listeners[type]?.({ data: JSON.stringify(data) }); }
}

test("subscribeJob 把 SSE 事件解析後回呼", () => {
  let src!: FakeEventSource;
  const Ctor = vi.fn((url: string) => (src = new FakeEventSource(url))) as unknown as typeof EventSource;
  const seen: SseEvent[] = [];
  const close = subscribeJob("j1", (e) => seen.push(e), Ctor);

  expect(src.url).toBe("/api/jobs/j1/events");
  src.emit("outline", { design: { palette: {}, fonts: {} }, mode: "presenter", pages: [] });
  src.emit("complete", { download_url: "/d" });
  expect(seen.map((e) => e.type)).toEqual(["outline", "complete"]);

  close();
  expect(src.closed).toBe(true);
});
