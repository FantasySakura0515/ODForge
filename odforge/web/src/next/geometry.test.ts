import { describe, expect, test } from "vitest";
import { bounds, gaps, geomOp, moved, resized, riders, snap, union } from "./geometry";
import type { SceneElement } from "./types";

const box = (id: string, x: number, y: number, w: number, h: number, kind: "rect" | "text" = "rect"): SceneElement => ({
  id,
  kind,
  x,
  y,
  w,
  h,
});
const page = { width: 28, height: 15.75 };

describe("snap", () => {
  test("吸到另一個框的左緣,並畫出參考線", () => {
    const r = snap({ x: 10.1, y: 9, w: 3, h: 2 }, [{ x: 10, y: 2, w: 3, h: 1 }], page, 0.2);
    expect(r.dx).toBeCloseTo(-0.1);
    expect(r.dy).toBe(0);
    expect(r.guides).toEqual([{ axis: "v", pos: 10, from: 2, to: 11 }]);
  });

  test("吸到頁面中線", () => {
    const r = snap({ x: 11.9, y: 1, w: 4, h: 2 }, [], page, 0.2);
    expect(r.dx).toBeCloseTo(0.1);
    expect(r.guides[0].pos).toBe(14);
  });

  test("超過門檻就不吸", () => {
    const r = snap({ x: 10.5, y: 3.3, w: 4, h: 2 }, [{ x: 10, y: 9, w: 3, h: 1 }], page, 0.2);
    expect(r.dx).toBe(0);
    expect(r.guides).toEqual([]);
  });
});

test("gaps 量到左右最近、且同一列的鄰居", () => {
  const found = gaps({ x: 10, y: 5, w: 2, h: 2 }, [
    { x: 4, y: 5, w: 3, h: 2 },
    { x: 14, y: 6, w: 1, h: 1 },
    { x: 1, y: 12, w: 1, h: 1 },
  ]);
  expect(found.map((g) => [g.axis, g.from, g.to])).toEqual([
    ["x", 7, 10],
    ["x", 12, 14],
  ]);
});

test("拖卡片時,卡片裡的文字跟著走", () => {
  const els = [box("0/0", 2, 2, 6, 4), box("0/1", 2.5, 2.5, 3, 1, "text"), box("0/2", 9, 2, 3, 1, "text")];
  expect(riders(els[0], els)).toEqual(["0/1"]);
  expect(riders(els[1], els)).toEqual([]);
});

describe("resized", () => {
  test("拉左上角:原點跟著動,右下角不動", () => {
    expect(resized({ x: 2, y: 2, w: 4, h: 2 }, "nw", 1, 0.5)).toEqual({ x: 3, y: 2.5, w: 3, h: 1.5 });
  });
  test("按住 Shift 等比例", () => {
    const r = resized({ x: 0, y: 0, w: 4, h: 2 }, "se", 4, 0, true);
    expect(r.w / r.h).toBeCloseTo(2);
  });
  test("不會縮到比 0.1 cm 還小", () => {
    expect(resized({ x: 0, y: 0, w: 1, h: 1 }, "e", -5, 0).w).toBeCloseTo(0.1);
  });
});

test("線條以兩端移動,geomOp 送出兩端座標", () => {
  const line: SceneElement = { id: "1/3", kind: "line", x1: 1, y1: 1, x2: 4, y2: 1 };
  const m = moved(line, 1, 2);
  expect(bounds(m)).toEqual({ x: 2, y: 3, w: 3, h: 0 });
  expect(geomOp(m)).toEqual({ op: "geom", id: "1/3", x1: 2, y1: 3, x2: 5, y2: 3 });
});

test("union 包住全部", () => {
  expect(union([{ x: 1, y: 1, w: 1, h: 1 }, { x: 3, y: 0, w: 1, h: 4 }])).toEqual({ x: 1, y: 0, w: 3, h: 4 });
});

import { alignOps, containerOf, distributeOps } from "./geometry";

describe("對齊與分布", () => {
  const els: SceneElement[] = [
    box("0/0", 2, 2, 6, 4),
    box("0/1", 2.5, 2.5, 3, 1, "text"),
    box("0/2", 10, 3, 2, 2),
    box("0/3", 14, 1, 2, 2),
  ];

  test("找到包住文字的卡片", () => {
    expect(containerOf(els[1], els)?.id).toBe("0/0");
    expect(containerOf(els[2], els)).toBeNull();
  });

  test("多選靠上對齊,卡片裡的字跟著移", () => {
    const ops = alignOps(els, ["0/0", "0/2", "0/3"], "top", { width: 28, height: 15.75 });
    const ys = Object.fromEntries(ops.map((o) => [o.id as string, o.y]));
    expect(ys).toEqual({ "0/0": 1, "0/1": 1.5, "0/2": 1 });
  });

  test("單選時對齊到頁面", () => {
    const ops = alignOps(els, ["0/2"], "hcenter", { width: 28, height: 15.75 });
    expect(ops).toEqual([{ op: "geom", id: "0/2", x: 13, y: 3, w: 2, h: 2 }]);
  });

  test("水平等距:中間那個移到兩邊的正中間", () => {
    const shifted = els.map((e) => (e.id === "0/2" ? { ...e, x: 9 } : e));
    const ops = distributeOps(shifted, ["0/0", "0/2", "0/3"], "x");
    // 0/0 spans 2–8, 0/3 spans 14–16; 0/2 (2 wide) lands at 10 → a 2 cm gap each side.
    expect(ops).toEqual([{ op: "geom", id: "0/2", x: 10, y: 3, w: 2, h: 2 }]);
  });
});
