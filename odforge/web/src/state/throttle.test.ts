import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createThrottledDispatch } from "./throttle";
import type { SseEvent } from "./types";

const sd = (n: number): SseEvent => ({ type: "slide_done", data: { n, slide: { layout: "x", title: `t${n}` } } });
const pr = (n: number): SseEvent => ({ type: "preview_ready", data: { n, url: `/p/${n}` } });
const ns = (seen: SseEvent[]) => seen.map((e) => (e.data as { n?: number }).n);

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

test("連發 5 個 slide_done:第一個立即 dispatch,其餘每 ~100ms 分批放行", () => {
  const seen: SseEvent[] = [];
  const t = createThrottledDispatch((e) => seen.push(e), { intervalMs: 100 });
  for (let n = 1; n <= 5; n++) t.push(sd(n));
  // leading edge:只有第一個同步出去,其餘進佇列
  expect(seen).toHaveLength(1);
  expect(ns(seen)).toEqual([1]);
  vi.advanceTimersByTime(100);
  expect(ns(seen)).toEqual([1, 2]);
  vi.advanceTimersByTime(100);
  expect(ns(seen)).toEqual([1, 2, 3]);
  vi.advanceTimersByTime(300);
  expect(ns(seen)).toEqual([1, 2, 3, 4, 5]);
});

test("preview_ready 同樣被節流", () => {
  const seen: SseEvent[] = [];
  const t = createThrottledDispatch((e) => seen.push(e), { intervalMs: 100 });
  t.push(pr(1));
  t.push(pr(2));
  t.push(pr(3));
  expect(seen).toHaveLength(1);
  vi.advanceTimersByTime(200);
  expect(seen).toHaveLength(3);
});

test("complete 不排隊、不延遲:先同步排空既有佇列再放行,順序保留", () => {
  const seen: SseEvent[] = [];
  const t = createThrottledDispatch((e) => seen.push(e), { intervalMs: 100 });
  for (let n = 1; n <= 3; n++) t.push(sd(n));
  expect(seen).toHaveLength(1); // n1 leading,n2/n3 佇列中
  t.push({ type: "complete", data: { download_url: "/d" } });
  // 不推進計時器:佇列排空 + complete 同步完成
  expect(seen.map((e) => e.type)).toEqual(["slide_done", "slide_done", "slide_done", "complete"]);
  expect((seen[2].data as { n: number }).n).toBe(3);
});

test("gate_result / error 立即通過,不被節流延遲", () => {
  const seen: SseEvent[] = [];
  const t = createThrottledDispatch((e) => seen.push(e), { intervalMs: 100 });
  t.push(sd(1));
  t.push({ type: "gate_result", data: { gate: "zip", status: "pass" } });
  t.push({ type: "error", data: { message: "x", stage: "s" } });
  expect(seen.map((e) => e.type)).toEqual(["slide_done", "gate_result", "error"]);
});

test("gate_result 放行但不排空視覺佇列——逐格點亮不被 gate 中斷", () => {
  const seen: SseEvent[] = [];
  const t = createThrottledDispatch((e) => seen.push(e), { intervalMs: 100 });
  t.push(sd(1)); // leading
  t.push(sd(2)); // 佇列
  t.push(sd(3)); // 佇列
  t.push({ type: "gate_result", data: { gate: "zip", status: "pass" } });
  // gate 立即出去,但 n2/n3 仍逐格排隊,未被一次 dump
  expect(seen.map((e) => e.type)).toEqual(["slide_done", "gate_result"]);
  vi.advanceTimersByTime(100);
  expect(seen.map((e) => e.type)).toEqual(["slide_done", "gate_result", "slide_done"]);
  vi.advanceTimersByTime(100);
  expect(seen.map((e) => e.type)).toEqual(["slide_done", "gate_result", "slide_done", "slide_done"]);
});

test("cancel 清掉待播佇列與計時器,之後不再 dispatch", () => {
  const seen: SseEvent[] = [];
  const t = createThrottledDispatch((e) => seen.push(e), { intervalMs: 100 });
  for (let n = 1; n <= 4; n++) t.push(sd(n));
  t.cancel();
  vi.advanceTimersByTime(1000);
  expect(seen).toHaveLength(1); // 只有 leading n1,其餘被取消
});
