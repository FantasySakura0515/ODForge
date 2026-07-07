import { expect, test } from "vitest";
import { parseSseEvent } from "./sse";

test("解析 outline 事件", () => {
  const ev = parseSseEvent("outline", JSON.stringify({ design: { palette: {}, fonts: {} }, mode: "presenter", units: [] }));
  expect(ev?.type).toBe("outline");
});

test("解析 unit_done 事件帶 n", () => {
  const ev = parseSseEvent("unit_done", JSON.stringify({ n: 3, unit: { role: "content", title: "x" } }));
  expect(ev).toEqual({ type: "unit_done", data: { n: 3, unit: { role: "content", title: "x" } } });
});

test("未知事件名回 null", () => {
  expect(parseSseEvent("bogus", "{}")).toBeNull();
});

test("壞 JSON 回 null 不拋", () => {
  expect(parseSseEvent("preview_ready", "{not json")).toBeNull();
});
