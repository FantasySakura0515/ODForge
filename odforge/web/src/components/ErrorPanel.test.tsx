import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ErrorPanel } from "./ErrorPanel";
import { humanizeStage } from "../state/errors";

test("humanizeStage 把 stage 代碼翻成白話,未知回退「發生錯誤」", () => {
  expect(humanizeStage("outline")).toBe("AI 構思大綱時出錯");
  expect(humanizeStage("slides")).toBe("AI 填充內容時出錯");
  expect(humanizeStage("render")).toBe("引擎渲染時出錯");
  expect(humanizeStage("validate")).toBe("驗證關卡未通過");
  expect(humanizeStage("preview")).toBe("產生預覽時出錯");
  expect(humanizeStage("qa")).toBe("設計 QA 時出錯");
  expect(humanizeStage("connect")).toBe("無法連上後端");
  expect(humanizeStage("wat")).toBe("發生錯誤");
  expect(humanizeStage(undefined)).toBe("發生錯誤");
});

test("標題為白話前綴,原始 message 收進 <details>「技術細節」而非當標題", () => {
  render(<ErrorPanel error={{ message: "Traceback: KeyError 'slide'", stage: "slides" }} />);
  // 白話標題
  expect(screen.getByRole("heading", { name: "AI 填充內容時出錯" })).toBeInTheDocument();
  // 原始技術訊息在 details 內,summary 為「技術細節」
  expect(screen.getByText("技術細節")).toBeInTheDocument();
  expect(screen.getByText(/Traceback: KeyError/)).toBeInTheDocument();
  // 技術訊息不得是標題
  expect(screen.queryByRole("heading", { name: /Traceback/ })).toBeNull();
});

test("重試鈕呼叫 onRetry;未提供時不渲染", () => {
  const onRetry = vi.fn();
  const { rerender } = render(<ErrorPanel error={{ message: "x", stage: "connect" }} onRetry={onRetry} />);
  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  expect(onRetry).toHaveBeenCalledTimes(1);

  rerender(<ErrorPanel error={{ message: "x", stage: "connect" }} />);
  expect(screen.queryByRole("button", { name: "重試" })).toBeNull();
});
