import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { GateRail } from "./GateRail";
import { OutlineRail } from "./OutlineRail";
import { StatusNarrator } from "./StatusNarrator";
import type { Outline } from "../state/types";

const outline: Outline = {
  design: { palette: { bg: "#fbfaf7", surface: "#eef2fb", text: "#1b2430", muted: "#7b8494", accent: "#2a5caa" }, fonts: { display: "x", body: "y" } },
  mode: "presenter",
  units: [ { n: 1, role: "title", title: "樹與二元樹", gist: "開場" } ],
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

test("StatusNarrator 完成態文字", () => {
  render(<StatusNarrator phase="complete" units={[]} />);
  expect(screen.getByText(/四道閘全綠|完成/)).toBeInTheDocument();
});
