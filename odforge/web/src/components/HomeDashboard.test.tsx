import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { HomeDashboard } from "./HomeDashboard";

test("沒有紀錄時顯示可操作的空狀態", () => {
  const onNew = vi.fn();
  render(
    <HomeDashboard
      sessions={[]}
      loading={false}
      error=""
      onNew={onNew}
      onReload={vi.fn()}
    />,
  );

  expect(screen.getByText("還沒有簡報")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "新增簡報" })[0]);
  expect(onNew).toHaveBeenCalledOnce();
});

test("session 顯示狀態、頁數、預覽與下載", () => {
  render(
    <HomeDashboard
      sessions={[
        {
          id: "session-1",
          title: "論文導讀",
          prompt: "報告這篇研究論文",
          status: "complete",
          created_at: 1_700_000_000,
          updated_at: 1_700_000_100,
          page_count: 9,
          preview_url: "/preview.png",
          download_url: "/deck.odp",
        },
      ]}
      loading={false}
      error=""
      onNew={vi.fn()}
      onReload={vi.fn()}
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
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  expect(onReload).toHaveBeenCalledOnce();
});
