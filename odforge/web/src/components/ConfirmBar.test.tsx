import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ConfirmBar } from "./ConfirmBar";

test("顯示主鈕與說明文字", () => {
  render(<ConfirmBar onConfirm={vi.fn().mockResolvedValue(undefined)} />);
  expect(screen.getByRole("button", { name: /就這樣鍛/ })).toBeInTheDocument();
  expect(screen.getByText(/可直接改標題或刪頁/)).toBeInTheDocument();
});

test("點主鈕呼叫 onConfirm", () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(<ConfirmBar onConfirm={onConfirm} />);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});

test("onConfirm 失敗顯示錯誤字且可重試", async () => {
  const onConfirm = vi.fn()
    .mockRejectedValueOnce(new Error("outline 失敗:409"))
    .mockResolvedValueOnce(undefined);
  render(<ConfirmBar onConfirm={onConfirm} />);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/409/));
  // 重試:錯誤仍在畫面,鈕可再按
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  expect(onConfirm).toHaveBeenCalledTimes(2);
});
