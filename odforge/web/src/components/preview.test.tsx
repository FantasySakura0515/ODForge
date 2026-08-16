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

test("計數把「已有 ir(填充中/尚未 preview)」的 unit 算進進度,不再 LLM 階段全程 0", () => {
  const filling: Unit[] = [
    { n: 1, role: "title", title: "封面", status: "filling", ir: { layout: "title", title: "封面" } },
    { n: 2, role: "content", title: "內文", status: "skeleton" },
  ];
  const { container } = render(<PreviewStage units={filling} docType="odp" jobId={undefined} dispatch={() => {}} />);
  expect(container.querySelector(".prog")?.textContent?.replace(/\s+/g, " ")).toContain("1 / 2");
});

test("submitting 且尚無 units:顯示等待卡(階段時間軸 + 取消鈕),不顯示縮圖牆", () => {
  const { container } = render(
    <PreviewStage units={[]} docType="odp" jobId={undefined} dispatch={() => {}} submitting onCancel={() => {}} />,
  );
  expect(screen.getAllByText(/構思大綱/).length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: /取消/ })).toBeInTheDocument();
  // 計時器完全退出無障礙樹:它每秒變一次,播報它只會蓋掉真正的階段訊息。
  expect(container.querySelector(".waitclock")?.getAttribute("aria-hidden")).toBe("true");
  expect(container.querySelector(".waitcard")?.getAttribute("aria-live")).toBeNull();
  expect(
    container.querySelector('.waitcard [role="status"][aria-live="polite"]')?.textContent,
  ).toMatch(/構思大綱/);
  expect(container.querySelector(".grid")).toBeNull();
});

test("已有 units(大綱已到)時等待卡收起,回到縮圖牆", () => {
  const { container } = render(
    <PreviewStage units={units} docType="odp" jobId={undefined} dispatch={() => {}} submitting onCancel={() => {}} />,
  );
  expect(screen.queryByText(/構思大綱/)).toBeNull();
  expect(container.querySelector(".grid")).not.toBeNull();
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
  expect(img?.getAttribute("loading")).toBe("lazy");
  expect(img?.getAttribute("decoding")).toBe("async");
});

test("mock previewUrl → 佔位而非 img", () => {
  const { container } = render(
    <UnitCell unit={{ n: 2, role: "content", title: "內文", status: "preview", previewUrl: "mock:preview/2" }} jobId="j1" />,
  );
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector(".placeholder")?.textContent).toContain("內文");
});

test("非可點單元有可及名稱(頁碼＋標題),而非只靠 aria-hidden 佔位", () => {
  render(
    <UnitCell unit={{ n: 2, role: "content", title: "內文", status: "skeleton" }} jobId="j1" />,
  );
  // 讀屏器聽得到「第 2 頁 · 內文」,不再是一個無名格子。
  const cell = screen.getByRole("img", { name: /第\s*2\s*頁/ });
  expect(cell.getAttribute("aria-label")).toContain("內文");
});

test("可點單元的可及名稱含開啟動作與標題", () => {
  render(
    <UnitCell
      unit={{ n: 1, role: "title", title: "封面", status: "done", previewUrl: "/api/jobs/j1/preview/1.png" }}
      jobId="j1"
      onOpen={() => {}}
    />,
  );
  const btn = screen.getByRole("button", { name: /開啟第\s*1\s*頁/ });
  expect(btn.getAttribute("aria-label")).toContain("封面");
});
