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

test("GateRail 說得出第四道閘為什麼沒跑,而不是丟一句「未啟用」", () => {
  render(
    <GateRail
      gates={{ zip: "pass", xml: "pass", libreoffice: "pass", design: "skipped" }}
      gateNotes={{ design: "視覺品檢無法執行（來源：custom）：403 free quota has been exhausted" }}
      qaRounds={[]}
    />,
  );
  expect(screen.getByTestId("gate-note-design").textContent).toContain("403");
  // 沒帶原因的閘不會多出空行。
  expect(screen.queryByTestId("gate-note-zip")).toBeNull();
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

test("StatusNarrator await 但已確認大綱時，報逐頁生成而非等待確認", () => {
  const { container } = render(<StatusNarrator phase="await" units={[{ n: 1, role: "title", title: "a", status: "skeleton" }]} fillingPending />);
  expect(container.querySelector(".nn")?.textContent).toMatch(/逐頁生成/);
  expect(screen.queryByText(/請確認大綱/)).toBeNull();
});

test("StatusNarrator outline 但已確認大綱時，報逐頁生成而非完成大綱", () => {
  const { container } = render(<StatusNarrator phase="outline" units={[{ n: 1, role: "title", title: "a", status: "skeleton" }]} fillingPending />);
  expect(container.querySelector(".nn")?.textContent).toMatch(/逐頁生成/);
  expect(screen.queryByText(/大綱與配色已完成/)).toBeNull();
});

test("GateRail active 閘的 ⟳ 帶 spin 類(轉檔中會旋轉)", () => {
  const { container } = render(
    <GateRail gates={{ zip: "pass", xml: "pass", libreoffice: "active", design: "pending" }} qaRounds={[]} />,
  );
  const spin = container.querySelector('[data-gate="libreoffice"] .gs .spin');
  expect(spin).not.toBeNull();
  expect(spin?.textContent).toBe("⟳");
});

test("StatusNarrator 完成態文字", () => {
  render(<StatusNarrator phase="complete" units={[]} />);
  expect(screen.getByText("完成 · 原生 ODF")).toBeInTheDocument();
});

test("StatusNarrator 播報的是離散階段,不是每一頁", () => {
  // 容器本身不再是 live region:可見文字每 80–120ms 換一次頁碼,整片設成
  // aria-live 會讓讀屏器把三十次「第 N 頁」全念出來。live region 縮到只在
  // 階段改變時才變的那一行。
  const { container } = render(<StatusNarrator phase="qa" units={[]} />);
  expect(container.querySelector(".narrator")?.getAttribute("aria-live")).toBeNull();
  const live = container.querySelector('[role="status"][aria-live="polite"]');
  expect(live?.textContent).toMatch(/檢查設計/);
});

test("StatusNarrator 逐頁的頁碼不進 live region", () => {
  const units = [
    { n: 1, role: "title", title: "a", status: "done" as const },
    { n: 2, role: "title-content", title: "b", status: "filling" as const },
  ];
  const { container } = render(<StatusNarrator phase="generating" units={units} />);
  // 看得到頁碼……
  expect(container.querySelector(".nn")?.textContent).toMatch(/第 2 頁/);
  // ……但被播報的那一行只說階段。
  const live = container.querySelector('[role="status"][aria-live="polite"]');
  expect(live?.textContent).toBe("正在逐頁生成");
  expect(live?.textContent).not.toMatch(/第 2 頁/);
});

test("StatusNarrator 錯誤態顯示白話前綴,原始技術訊息收進 title 不當標題轟人", () => {
  const { container } = render(
    <StatusNarrator phase="error" units={[]} error={{ message: "環境變數 DEEPSEEK_API_KEY 未設定", stage: "outline" }} />,
  );
  // 人話前綴(stage outline → 白話),不再把 stage 代碼與 traceback 直接印出來。
  expect(screen.getByText("AI 構思大綱時出錯")).toBeInTheDocument();
  expect(screen.queryByText(/DEEPSEEK_API_KEY 未設定/)).toBeNull();
  // 原始技術訊息仍可及:掛在 title 供滑鼠停留檢視。
  expect(container.querySelector(".narrator")?.getAttribute("title")).toBe("環境變數 DEEPSEEK_API_KEY 未設定");
  expect(container.querySelector(".narrator.is-error")).not.toBeNull();
});
