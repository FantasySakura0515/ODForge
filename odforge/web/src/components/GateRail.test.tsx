import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { GateRail } from "./GateRail";
import type { Finding, GateId, GateStatus } from "../state/types";

const gates: Record<GateId, GateStatus> = {
  zip: "pass", xml: "pass", libreoffice: "pass", design: "active",
};

test("四道閘各帶白話 title tooltip", () => {
  const { container } = render(<GateRail gates={gates} qaRounds={[]} />);
  expect(container.querySelector('[data-gate="zip"]')?.getAttribute("title")).toMatch(/mimetype 為首/);
  expect(container.querySelector('[data-gate="xml"]')?.getAttribute("title")).toMatch(/XML 可解析/);
  expect(container.querySelector('[data-gate="libreoffice"]')?.getAttribute("title")).toMatch(/真轉 PDF/);
  expect(container.querySelector('[data-gate="design"]')?.getAttribute("title")).toMatch(/第四道閘/);
});

test("FindingRow 呈現頁碼 · issue · severity 徽章 · fix_hint", () => {
  const findings: Finding[] = [
    { slide_no: 3, issue: "文字溢出框外", severity: "error", fix_hint: "減兩行內文" },
    { slide_no: 5, issue: "對比偏低", severity: "warn", fix_hint: "加深標題色" },
  ];
  const { container } = render(<GateRail gates={gates} qaRounds={[{ round: 1, findings }]} />);
  const rows = container.querySelectorAll(".finding");
  expect(rows).toHaveLength(2);

  const first = within(rows[0] as HTMLElement);
  expect(first.getByText(/第 3 頁/)).toBeInTheDocument();
  expect(first.getByText("文字溢出框外")).toBeInTheDocument();
  expect(first.getByText("減兩行內文")).toBeInTheDocument();
  // severity 徽章:error 紅調(data-severity=error),warn 琥珀調(data-severity=warn)
  expect((rows[0] as HTMLElement).getAttribute("data-severity")).toBe("error");
  expect((rows[1] as HTMLElement).getAttribute("data-severity")).toBe("warn");
  expect(within(rows[0] as HTMLElement).getByText("錯誤")).toBeInTheDocument();
  expect(within(rows[1] as HTMLElement).getByText("警告")).toBeInTheDocument();
});

test("空 findings 的一輪顯示「本輪無發現 ✓」", () => {
  render(<GateRail gates={gates} qaRounds={[{ round: 1, findings: [] }]} />);
  expect(screen.getByText(/本輪無發現/)).toBeInTheDocument();
});

test("最新一輪預設展開(open),舊輪收合", () => {
  const { container } = render(
    <GateRail
      gates={gates}
      qaRounds={[
        { round: 1, findings: [{ slide_no: 1, issue: "a", severity: "warn", fix_hint: "b" }] },
        { round: 2, findings: [{ slide_no: 2, issue: "c", severity: "error", fix_hint: "d" }] },
      ]}
    />,
  );
  const rounds = container.querySelectorAll("details.qaround");
  expect(rounds).toHaveLength(2);
  expect((rounds[0] as HTMLDetailsElement).open).toBe(false); // 第 1 輪收合
  expect((rounds[1] as HTMLDetailsElement).open).toBe(true); // 最新展開
});

test("點 FindingRow → 以該頁碼呼叫 onOpenFinding", () => {
  const onOpen = vi.fn();
  render(
    <GateRail
      gates={gates}
      qaRounds={[{ round: 1, findings: [{ slide_no: 4, issue: "x", severity: "error", fix_hint: "y" }] }]}
      onOpenFinding={onOpen}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /第 4 頁/ }));
  expect(onOpen).toHaveBeenCalledWith(4);
});

test("無 onOpenFinding(如 error 態,lightbox host 未掛載)→ FindingRow 不可點、無按鈕語義與 clickable affordance", () => {
  const { container } = render(
    <GateRail
      gates={gates}
      qaRounds={[{ round: 1, findings: [{ slide_no: 4, issue: "溢出", severity: "error", fix_hint: "y" }] }]}
    />,
  );
  // 沒有 button 語義(不會被讀屏器/滑鼠當可點),也不加 clickable class。
  expect(screen.queryByRole("button", { name: /第 4 頁/ })).toBeNull();
  const row = container.querySelector(".finding");
  expect(row?.classList.contains("clickable")).toBe(false);
  // 內容照樣顯示(只是不可點)。
  expect(screen.getByText("溢出")).toBeInTheDocument();
});
