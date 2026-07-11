import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { postGenerate } from "./state/api";

vi.mock("./state/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./state/api")>();
  return { ...actual, postGenerate: vi.fn() };
});

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
});

afterEach(() => {
  vi.mocked(postGenerate).mockReset();
});

test("?mock 模式:mock 流跑到完成,格式閘依序點亮、設計閘顯示未啟用、可下載", async () => {
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
  const { container } = render(<App />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(screen.getAllByText("樹與二元樹").length).toBeGreaterThan(0));
  await waitFor(() => expect(screen.getByRole("link", { name: /下載/ })).toBeInTheDocument(), { timeout: 4000 });
  // 誠實的 gate 真值:zip/xml/libreoffice 通過,design 因 demo 未開 QA 而 skipped(非偽造全綠)
  await waitFor(() => expect(container.querySelectorAll('[data-gate][data-status="pass"]')).toHaveLength(3));
  expect(container.querySelector('[data-gate="design"]')?.getAttribute("data-status")).toBe("skipped");
});

test("?mock 模式頂欄常駐「展示模式」chip", () => {
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
  render(<App />);
  expect(screen.getByText("展示模式")).toBeInTheDocument();
});

test("非 mock 模式且後端連不上:顯示「無法連上後端」、不出現 mock 假內容、PromptBar 回來可重試、頂欄顯示「後端未連線」chip", async () => {
  window.history.replaceState({}, "", "/");
  vi.mocked(postGenerate).mockRejectedValue(new Error("network down"));

  render(<App />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  // 連線錯誤訊息出現
  await waitFor(() => expect(screen.getByText(/無法連上後端/)).toBeInTheDocument());
  // 絕不退回 mock 演假簡報:mock 專屬標題不得出現
  expect(screen.queryByText("本章路線圖")).toBeNull();
  expect(screen.queryByRole("link", { name: /下載/ })).toBeNull();
  // PromptBar 回來,可重試
  expect(screen.getByRole("textbox")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /鍛造/ })).toBeInTheDocument();
  // 頂欄「後端未連線」chip
  expect(screen.getByText("後端未連線")).toBeInTheDocument();
  // 展示模式 chip 不該出現(非 mock)
  expect(screen.queryByText("展示模式")).toBeNull();
});

test("正常(非 mock、未失敗)頂欄不顯示狀態 chip", () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  expect(screen.queryByText("展示模式")).toBeNull();
  expect(screen.queryByText("後端未連線")).toBeNull();
});
