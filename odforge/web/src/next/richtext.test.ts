import { expect, test } from "vitest";
import { formatAll, normalise, plain, richOf } from "./richtext";
import type { Paragraph } from "./types";

const paragraphs: Paragraph[] = [
  { align: "left", style: { size: 18 }, runs: [{ text: "一般", size: 18 }, { text: "粗體", size: 18, weight: 700 }] },
];

test("整框粗體:有一段不是粗體就全部變粗", () => {
  const out = formatAll(richOf(paragraphs), { kind: "bold" });
  expect(out[0].runs.map((r) => r.weight)).toEqual([700, 700]);
  const back = formatAll(out, { kind: "bold" });
  expect(back[0].runs.map((r) => r.weight)).toEqual([400, 400]);
});

test("字級加減與顏色、對齊", () => {
  const bigger = formatAll(richOf(paragraphs), { kind: "size", delta: 2 });
  expect(bigger[0].runs.map((r) => r.size)).toEqual([20, 20]);
  const red = formatAll(richOf(paragraphs), { kind: "color", value: "#c62828" });
  expect(red[0].runs.every((r) => r.color === "#C62828")).toBe(true);
  expect(formatAll(richOf(paragraphs), { kind: "align", value: "center" })[0].align).toBe("center");
});

test("normalise:同樣的內容與樣式視為沒改,相鄰同樣式合併", () => {
  const a = richOf(paragraphs);
  const b = [{ align: "left" as const, runs: [{ text: "一", size: 18, weight: 400 }, { text: "般", size: 18 }, { text: "粗體", size: 18, weight: 700 }] }];
  expect(normalise(a)).toBe(normalise(b));
  expect(plain(a)).toEqual(["一般粗體"]);
});
