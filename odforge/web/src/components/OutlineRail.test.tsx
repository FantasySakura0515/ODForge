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

test("確認鈕排在大綱清單之前,且標明按下去會開始生成", () => {
  const { container } = render(
    <OutlineRail outline={makeOutline()} phase="await" onConfirm={vi.fn().mockResolvedValue(undefined)} />,
  );
  const bar = container.querySelector(".confirmbar")!;
  const list = container.querySelector(".outline")!;
  // 位置回歸守門:排在清單之後 → 長大綱會把它推出視窗,使用者以為系統卡住。
  expect(bar.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole("button", { name: /就這樣鍛/ })).toHaveTextContent(/開始逐頁生成/);
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

test("成功送出後(仍在 await)rail 顯示送出的 draft 而非舊標題", async () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "新封面" } });
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  // 收起編輯後為唯讀,但顯示的是改過的標題(修好前這裡會退回 props 的舊「封面」,
  // 找不到「新封面」;role 標籤「封面」是另一回事,不在此斷言)。
  await waitFor(() => expect(screen.queryByRole("button", { name: /就這樣鍛/ })).toBeNull());
  expect(screen.getByText("新封面")).toBeInTheDocument();
});

test("送出失敗 → ConfirmBar 顯示錯誤,可重試", async () => {
  const onConfirm = vi.fn().mockRejectedValue(new Error("outline 失敗:409"));
  render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/409/));
});

test("重播的同內容大綱(新物件身分)不得抹掉編輯中的草稿", () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "改到一半的標題" } });

  // SSE 重連重播:內容相同、物件身分全新的 outline prop 再來一次。
  rerender(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  expect((screen.getAllByRole("textbox")[0] as HTMLInputElement).value).toBe("改到一半的標題");
});

test("內容真的變了的大綱到達 → 草稿重新播種(不是永遠黏著舊草稿)", () => {
  const onConfirm = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(<OutlineRail outline={makeOutline()} phase="await" onConfirm={onConfirm} />);
  fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "改到一半的標題" } });

  const changed = makeOutline();
  changed.pages[0] = { ...changed.pages[0], title: "後端改寫的新封面" };
  rerender(<OutlineRail outline={changed} phase="await" onConfirm={onConfirm} />);
  expect((screen.getAllByRole("textbox")[0] as HTMLInputElement).value).toBe("後端改寫的新封面");
});
