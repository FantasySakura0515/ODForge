import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { SlideView, readParagraphs } from "./SlideView";
import type { Page } from "./types";

test("readParagraphs:一個區塊一段,<br> 是段內換行", () => {
  const root = document.createElement("div");
  root.innerHTML = "<div>第一段</div><div>第二<br>行</div><div><br></div>";
  expect(readParagraphs(root)).toEqual(["第一段", "第二\n行", ""]);
});

test("readParagraphs:直接打在根節點的字也算一段", () => {
  const root = document.createElement("div");
  root.innerHTML = "只有一段";
  expect(readParagraphs(root)).toEqual(["只有一段"]);
});

const page: Page = {
  id: "0",
  name: "page1",
  background: "#ffffff",
  master: [],
  elements: [
    { id: "0/0", kind: "rect", x: 1, y: 1, w: 4, h: 2, fill: "#eeeeee", radius: 0.2 },
    {
      id: "0/1",
      kind: "text",
      x: 1,
      y: 4,
      w: 10,
      h: 1,
      text: {
        padding: { top: 0, right: 0, bottom: 0, left: 0 },
        valign: "top",
        paragraphs: [
          { runs: [{ text: "重點", size: 18, weight: 700 }], style: { size: 18 }, bullet: { char: "▪", indent: 0, width: 0.6 } },
        ],
      },
    },
  ],
};

test("SlideView 依公分與比例擺放元素", () => {
  const { container } = render(<SlideView page={page} width={28} height={15.75} s={10} />);
  const rect = container.querySelector('[data-id="0/0"]') as HTMLElement;
  expect(rect.style.left).toBe("10px");
  expect(rect.style.width).toBe("40px");
  expect(rect.style.background).toContain("238");
  const bullet = container.querySelector("[data-bullet]") as HTMLElement;
  expect(bullet.dataset.bullet).toBe("▪");
  expect(container.textContent).toContain("重點");
});
