import { expect, test, vi } from "vitest";
import { mockTreeEvents, playMock } from "./mockStream";
import type { SseEvent } from "./types";

test("mockTreeEvents 以 outline 開頭、complete 結尾、含 12 單元", () => {
  const evs = mockTreeEvents();
  expect(evs[0].type).toBe("outline");
  expect(evs[evs.length - 1].type).toBe("complete");
  const outline = evs[0] as Extract<SseEvent, { type: "outline" }>;
  expect(outline.data.units).toHaveLength(12);
  expect(evs.filter((e) => e.type === "unit_done")).toHaveLength(12);
});

test("playMock 依序送出全部事件", () => {
  vi.useFakeTimers();
  const seen: SseEvent[] = [];
  playMock((e) => seen.push(e), { step: 10 });
  vi.advanceTimersByTime(10 * (mockTreeEvents().length + 1));
  expect(seen.length).toBe(mockTreeEvents().length);
  expect(seen[0].type).toBe("outline");
  vi.useRealTimers();
});
