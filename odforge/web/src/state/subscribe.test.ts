import { expect, test, vi } from "vitest";
import { subscribeJob } from "./sse";
import type { SseEvent } from "./types";

class FakeEventSource {
  listeners: Record<string, (e: { data: string }) => void> = {};
  onerror: ((e: unknown) => void) | null = null;
  closed = false;
  constructor(public url: string) {}
  addEventListener(type: string, cb: (e: { data: string }) => void) { this.listeners[type] = cb; }
  close() { this.closed = true; }
  emit(type: string, data: unknown) { this.listeners[type]?.({ data: JSON.stringify(data) }); }
  fail() { this.onerror?.({}); }
}

function fakeEventSourceCtor(onCreate: (source: FakeEventSource) => void): typeof EventSource {
  return class extends FakeEventSource {
    constructor(url: string) {
      super(url);
      onCreate(this);
    }
  } as unknown as typeof EventSource;
}

test("subscribeJob 把 SSE 事件解析後回呼", () => {
  let src!: FakeEventSource;
  const Ctor = fakeEventSourceCtor((source) => { src = source; });
  const seen: SseEvent[] = [];
  const close = subscribeJob("j1", (e) => seen.push(e), Ctor);

  expect(src.url).toBe("/api/jobs/j1/events");
  src.emit("outline", { design: { palette: {}, fonts: {} }, mode: "presenter", pages: [] });
  src.emit("complete", { download_url: "/d" });
  expect(seen.map((e) => e.type)).toEqual(["outline", "complete"]);

  close();
  expect(src.closed).toBe(true);
});

test("subscribeJob 收到 complete 後自動關閉串流(不需手動 close)", () => {
  let src!: FakeEventSource;
  const Ctor = fakeEventSourceCtor((source) => { src = source; });
  subscribeJob("j1", () => {}, Ctor);
  expect(src.closed).toBe(false);
  src.emit("complete", { download_url: "/d" });
  expect(src.closed).toBe(true);
});

test("subscribeJob 收到 error 後自動關閉串流", () => {
  let src!: FakeEventSource;
  const Ctor = fakeEventSourceCtor((source) => { src = source; });
  subscribeJob("j1", () => {}, Ctor);
  src.emit("error", { message: "x", stage: "slides" });
  expect(src.closed).toBe(true);
});

test("subscribeJob 傳輸層錯誤(如 404)回呼 onError", () => {
  let src!: FakeEventSource;
  const Ctor = fakeEventSourceCtor((source) => { src = source; });
  const onError = vi.fn();
  subscribeJob("j1", () => {}, Ctor, onError);
  src.fail();
  expect(onError).toHaveBeenCalledTimes(1);
});
