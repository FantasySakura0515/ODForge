import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ConfirmBar } from "./ConfirmBar";

test("顯示主鈕與說明文字", () => {
  render(<ConfirmBar onConfirm={vi.fn().mockResolvedValue(undefined)} />);
  expect(screen.getByRole("button", { name: /就這樣鍛/ })).toBeInTheDocument();
  expect(screen.getByText(/可直接改標題或刪頁/)).toBeInTheDocument();
});

test("點主鈕呼叫 onConfirm", async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(<ConfirmBar onConfirm={onConfirm} />);
  const btn = screen.getByRole("button", { name: /就這樣鍛/ });
  fireEvent.click(btn);
  expect(onConfirm).toHaveBeenCalledTimes(1);
  // 等 busy 落定,避免 async setState 落在 act() 外觸發警告。
  await waitFor(() => expect(btn).not.toBeDisabled());
});

test("onConfirm 失敗顯示錯誤字且可重試", async () => {
  const onConfirm = vi.fn()
    .mockRejectedValueOnce(new Error("outline 失敗:409"))
    .mockResolvedValueOnce(undefined);
  render(<ConfirmBar onConfirm={onConfirm} />);
  const btn = screen.getByRole("button", { name: /就這樣鍛/ });
  fireEvent.click(btn);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/409/));
  // 重試:鈕在忙碌結束後可再按;等到 busy 落定(鈕解除 disabled)才斷言,
  // 避免 setState 落在 act() 之外觸發 React act(...) 警告。
  await waitFor(() => expect(btn).not.toBeDisabled());
  fireEvent.click(btn);
  await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(2));
  // 第二次成功後 busy 再度落定,確保無殘留非同步狀態更新。
  await waitFor(() => expect(btn).not.toBeDisabled());
});
