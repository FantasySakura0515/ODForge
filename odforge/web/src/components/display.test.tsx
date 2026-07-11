import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { GateRail } from "./GateRail";
import { OutlineRail } from "./OutlineRail";
import { StatusNarrator } from "./StatusNarrator";
import type { Outline, Unit } from "../state/types";

const outline: Outline = {
  design: { palette: { bg: "#fbfaf7", surface: "#eef2fb", text: "#1b2430", muted: "#7b8494", accent: "#2a5caa" }, fonts: { display: "x", body: "y" } },
  mode: "presenter",
  pages: [ { role: "title", title: "樹與二元樹", gist: "開場" } ],
};

test("OutlineRail 顯示單元標題與色盤", () => {
  const { container } = render(<OutlineRail outline={outline} />);
  expect(screen.getByText("樹與二元樹")).toBeInTheDocument();
  expect(container.querySelectorAll(".swatches i")).toHaveLength(5);
});

test("OutlineRail 無 outline 回 null", () => {
  const { container } = render(<OutlineRail outline={undefined} />);
  expect(container.firstChild).toBeNull();
});

test("GateRail 反映各閘狀態", () => {
  const { container } = render(<GateRail gates={{ zip: "pass", xml: "pass", libreoffice: "pass", design: "pending" }} qaRounds={[]} />);
  expect(container.querySelector('[data-gate="zip"]')?.getAttribute("data-status")).toBe("pass");
  expect(container.querySelector('[data-gate="design"]')?.getAttribute("data-status")).toBe("pending");
});

test("GateRail skipped 顯示「未啟用」而非勾/叉", () => {
  const { container } = render(<GateRail gates={{ zip: "pass", xml: "pass", libreoffice: "skipped", design: "skipped" }} qaRounds={[]} />);
  const lo = container.querySelector('[data-gate="libreoffice"]');
  expect(lo?.getAttribute("data-status")).toBe("skipped");
  expect(lo?.textContent).toContain("未啟用");
  expect(lo?.querySelector(".gs")?.textContent).not.toContain("✓");
  expect(lo?.querySelector(".gs")?.textContent).not.toContain("✕");
});

test("StatusNarrator 報最新填充的一頁(最大 n),而非第一個 filling", () => {
  const units: Unit[] = [
    { n: 1, role: "title", title: "a", status: "done" },
    { n: 2, role: "content", title: "b", status: "filling" },
    { n: 3, role: "content", title: "c", status: "filling" },
  ];
  render(<StatusNarrator phase="generating" units={units} />);
  expect(screen.getByText(/第\s*3\s*頁/)).toBeInTheDocument();
  expect(screen.queryByText(/第\s*2\s*頁/)).toBeNull();
});

test("StatusNarrator await 但已確認大綱時,報逐頁填充而非等待確認", () => {
  render(<StatusNarrator phase="await" units={[{ n: 1, role: "title", title: "a", status: "skeleton" }]} fillingPending />);
  expect(screen.getByText(/逐頁填充/)).toBeInTheDocument();
  expect(screen.queryByText(/等待你確認/)).toBeNull();
});

test("StatusNarrator 完成態文字", () => {
  render(<StatusNarrator phase="complete" units={[]} />);
  expect(screen.getByText(/四道閘全綠|完成/)).toBeInTheDocument();
});

test("StatusNarrator 錯誤態顯示訊息與階段", () => {
  const { container } = render(
    <StatusNarrator phase="error" units={[]} error={{ message: "環境變數 DEEPSEEK_API_KEY 未設定", stage: "outline" }} />,
  );
  expect(screen.getByText(/DEEPSEEK_API_KEY 未設定/)).toBeInTheDocument();
  expect(screen.getByText(/outline/)).toBeInTheDocument();
  expect(container.querySelector(".narrator.is-error")).not.toBeNull();
});
