import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { SessionSummary } from "../state/api";
import { HomeDashboard } from "./HomeDashboard";

function session(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "session-1",
    title: "論文導讀",
    prompt: "報告這篇研究論文",
    status: "complete",
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    page_count: 9,
    preview_url: "/preview.png",
    download_url: "/deck.odp",
    ...overrides,
  };
}

test("沒有紀錄時顯示可操作的空狀態", () => {
  const onNew = vi.fn();
  render(
    <HomeDashboard
      sessions={[]}
      loading={false}
      error=""
      onNew={onNew}
      onReload={vi.fn()}
      onDelete={vi.fn()}
    />,
  );

  expect(screen.getByText("還沒有簡報")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "建立第一份簡報" }));
  expect(onNew).toHaveBeenCalledOnce();
});

test("session 顯示狀態、頁數、預覽與下載", () => {
  render(
    <HomeDashboard
      sessions={[session()]}
      loading={false}
      error=""
      onNew={vi.fn()}
      onReload={vi.fn()}
      onDelete={vi.fn()}
    />,
  );

  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("論文導讀")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument();
  expect(document.querySelector(".session-thumb img")).toHaveAttribute(
    "src",
    "/preview.png",
  );
  expect(screen.getByRole("link", { name: "下載 論文導讀" })).toHaveAttribute(
    "href",
    "/deck.odp",
  );
});

test("讀取失敗可以重試", () => {
  const onReload = vi.fn();
  render(
    <HomeDashboard
      sessions={[]}
      loading={false}
      error="無法讀取工作紀錄"
      onNew={vi.fn()}
      onReload={onReload}
      onDelete={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  expect(onReload).toHaveBeenCalledOnce();
});

// ---------------------------------------------------------------------------
// 刪除:兩步。清單上每一列長得一樣,而刪掉的是伺服器上唯一的一份成品——第一次
// 點擊只能展開確認,不能是「東西沒了」。
// ---------------------------------------------------------------------------

test("刪除要按兩次:第一次只展開確認,第二次才真的刪", async () => {
  const onDelete = vi.fn().mockResolvedValue(undefined);
  render(
    <HomeDashboard
      sessions={[session()]}
      loading={false}
      error=""
      onNew={vi.fn()}
      onReload={vi.fn()}
      onDelete={onDelete}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "刪除 論文導讀" }));
  expect(onDelete).not.toHaveBeenCalled();
  expect(screen.getByText(/無法復原/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "確定刪除" }));
  await waitFor(() => expect(onDelete).toHaveBeenCalledWith("session-1"));
});

test("按保留就收起確認,什麼都不會被刪掉", () => {
  const onDelete = vi.fn();
  render(
    <HomeDashboard
      sessions={[session()]}
      loading={false}
      error=""
      onNew={vi.fn()}
      onReload={vi.fn()}
      onDelete={onDelete}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "刪除 論文導讀" }));
  fireEvent.click(screen.getByRole("button", { name: "保留" }));

  expect(screen.queryByRole("button", { name: "確定刪除" })).toBeNull();
  expect(onDelete).not.toHaveBeenCalled();
});

test("刪不掉時說出後端給的原因,並把那一列留在畫面上", async () => {
  const onDelete = vi
    .fn()
    .mockRejectedValue(new Error("這份工作還在進行，請等它停下來再刪除。(HTTP 409)"));
  render(
    <HomeDashboard
      sessions={[session({ status: "generating_slides", download_url: null })]}
      loading={false}
      error=""
      onNew={vi.fn()}
      onReload={vi.fn()}
      onDelete={onDelete}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "刪除 論文導讀" }));
  fireEvent.click(screen.getByRole("button", { name: "確定刪除" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("還在進行");
  expect(screen.getByText("論文導讀")).toBeInTheDocument();
});
