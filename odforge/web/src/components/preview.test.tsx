import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { PreviewStage } from "./PreviewStage";
import type { Unit } from "../state/types";

const units: Unit[] = [
  { n: 1, role: "title", title: "封面", status: "done", previewUrl: "mock:preview/1" },
  { n: 2, role: "content", title: "內文", status: "skeleton" },
];

test("標頭顯示完成數/總數", () => {
  const { container } = render(<PreviewStage units={units} docType="odp" jobId={undefined} />);
  expect(container.querySelector(".prog")?.textContent?.replace(/\s+/g, " ")).toContain("1 / 2");
});

test("每個單元一格且帶 data-status", () => {
  const { container } = render(<PreviewStage units={units} docType="odp" jobId={undefined} />);
  const cells = container.querySelectorAll(".cell");
  expect(cells).toHaveLength(2);
  expect(cells[0].getAttribute("data-status")).toBe("done");
  expect(cells[1].getAttribute("data-status")).toBe("skeleton");
});

test("網格帶 data-doctype", () => {
  const { container } = render(<PreviewStage units={units} docType="odt" jobId={undefined} />);
  expect(container.querySelector(".grid")?.getAttribute("data-doctype")).toBe("odt");
});
