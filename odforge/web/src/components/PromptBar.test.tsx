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

test("切到文書分頁後送出帶 odt", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "實驗報告" } });
  fireEvent.click(screen.getByRole("tab", { name: /文書/ }));
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect(onGenerate).toHaveBeenCalledWith("實驗報告", "odt");
});
