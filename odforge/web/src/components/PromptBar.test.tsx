import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { PromptBar } from "./PromptBar";

test("空 prompt 時送出鈕停用", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  expect(screen.getByRole("button", { name: /鍛造/ })).toBeDisabled();
});

test("輸入後送出帶 prompt 與預設 docType=odp", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect(onGenerate).toHaveBeenCalledWith("樹與二元樹", "odp");
});

test("odt/ods 分頁停用(disabled + aria-disabled)且標示「即將支援」", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  const odt = screen.getByRole("tab", { name: /文書/ });
  const ods = screen.getByRole("tab", { name: /試算表/ });
  expect(odt).toBeDisabled();
  expect(ods).toBeDisabled();
  expect(odt).toHaveAttribute("aria-disabled", "true");
  expect(ods).toHaveAttribute("aria-disabled", "true");
  expect(screen.getAllByText(/即將支援/).length).toBeGreaterThan(0);
});

test("點停用的文書分頁無效果,送出仍為 odp", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "實驗報告" } });
  fireEvent.click(screen.getByRole("tab", { name: /文書/ }));
  // 分頁未切換,odp 仍為選中
  expect(screen.getByRole("tab", { name: /簡報/ })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect(onGenerate).toHaveBeenCalledWith("實驗報告", "odp");
});
