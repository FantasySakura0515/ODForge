import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { PreviewStage } from "./PreviewStage";
import { UnitCell } from "./UnitCell";
import type { Unit } from "../state/types";

const units: Unit[] = [
  { n: 1, role: "title", title: "封面", status: "done", previewUrl: "mock:preview/1" },
  { n: 2, role: "content", title: "內文", status: "skeleton" },
];

test("標頭顯示完成數/總數", () => {
  const { container } = render(<PreviewStage units={units} docType="odp" jobId={undefined} dispatch={() => {}} />);
  expect(container.querySelector(".prog")?.textContent?.replace(/\s+/g, " ")).toContain("1 / 2");
});

test("每個單元一格且帶 data-status", () => {
  const { container } = render(<PreviewStage units={units} docType="odp" jobId={undefined} dispatch={() => {}} />);
  const cells = container.querySelectorAll(".cell");
  expect(cells).toHaveLength(2);
  expect(cells[0].getAttribute("data-status")).toBe("done");
  expect(cells[1].getAttribute("data-status")).toBe("skeleton");
});

test("網格帶 data-doctype", () => {
  const { container } = render(<PreviewStage units={units} docType="odt" jobId={undefined} dispatch={() => {}} />);
  expect(container.querySelector(".grid")?.getAttribute("data-doctype")).toBe("odt");
});

test("有真實 previewUrl + jobId → 渲染 img", () => {
  const { container } = render(
    <UnitCell unit={{ n: 1, role: "title", title: "封面", status: "done", previewUrl: "/api/jobs/j1/preview/1.png" }} jobId="j1" />,
  );
  const img = container.querySelector("img");
  expect(img).not.toBeNull();
  expect(img?.getAttribute("src")).toBe("/api/jobs/j1/preview/1.png");
});

test("mock previewUrl → 佔位而非 img", () => {
  const { container } = render(
    <UnitCell unit={{ n: 2, role: "content", title: "內文", status: "preview", previewUrl: "mock:preview/2" }} jobId="j1" />,
  );
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector(".placeholder")?.textContent).toContain("內文");
});
