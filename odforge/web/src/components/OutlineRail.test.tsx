import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { OutlineRail } from "./OutlineRail";
import type { Outline } from "../state/types";

function makeOutline(): Outline {
  return {
    design: null,
    mode: "presenter",
    pages: [
      { role: "title", title: "封面", gist: "開場" },
      { role: "agenda", title: "議程", gist: "路線" },
      { role: "closing", title: "結語", gist: "收尾" },
    ],
  };
}

test("非 await 階段:唯讀,不顯示確認鈕", () => {
  render(<OutlineRail outline={makeOutline()} phase="generating" onConfirm={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /就這樣鍛/ })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

test("await 階段:每列可就地編輯且出現 ConfirmBar", () => {
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={vi.fn().mockResolvedValue(undefined)} />);
  expect(screen.getAllByRole("textbox")).toHaveLength(3);
  expect(screen.getByRole("button", { name: /就這樣鍛/ })).toBeInTheDocument();
});

test("未改動 → approve 路徑", async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({ action: "approve" }));
});

test("改標題 → edit 路徑,送出改後 Outline(保留 design/mode/gist)", async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "新封面" } });
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  expect(onConfirm.mock.calls[0][0]).toEqual({
    action: "edit",
    outline: {
      design: null,
      mode: "presenter",
      pages: [
        { role: "title", title: "新封面", gist: "開場" },
        { role: "agenda", title: "議程", gist: "路線" },
        { role: "closing", title: "結語", gist: "收尾" },
      ],
    },
  });
});

test("刪頁 → 送出的 outline.pages 少一列", async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.click(screen.getAllByRole("button", { name: /刪除/ })[0]);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  const body = onConfirm.mock.calls[0][0];
  expect(body.action).toBe("edit");
  expect(body.outline.pages).toHaveLength(2);
  expect(body.outline.pages[0].title).toBe("議程");
});

test("刪到剩 1 頁時刪除鈕 disabled", () => {
  const one: Outline = { design: null, mode: "presenter", pages: [{ role: "title", title: "只有一頁", gist: "g" }] };
  render(<OutlineRail outline={one} phase="await" onConfirm={vi.fn()} />);
  expect(screen.getByRole("button", { name: /刪除/ })).toBeDisabled();
});

test("送出失敗 → ConfirmBar 顯示錯誤,可重試", async () => {
  const onConfirm = vi.fn().mockRejectedValue(new Error("outline 失敗:409"));
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/409/));
});
