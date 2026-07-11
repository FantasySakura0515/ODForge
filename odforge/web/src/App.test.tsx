import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { postGenerate, postOutlineAction } from "./state/api";

vi.mock("./state/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./state/api")>();
  return { ...actual, postGenerate: vi.fn(), postOutlineAction: vi.fn() };
});

// Minimal EventSource stand-in so App tests can drive the SSE stream by hand.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, (e: { data: string }) => void> = {};
  closed = false;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, cb: (e: { data: string }) => void) { this.listeners[type] = cb; }
  close() { this.closed = true; }
  emit(type: string, data: unknown) {
    act(() => this.listeners[type]?.({ data: JSON.stringify(data) }));
  }
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
  FakeEventSource.instances = [];
});

afterEach(() => {
  vi.mocked(postGenerate).mockReset();
  vi.mocked(postOutlineAction).mockReset();
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

test("主題切換鈕有可及名稱(淺色主題/深色主題)", () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  expect(screen.getByRole("button", { name: "淺色主題" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "深色主題" })).toBeInTheDocument();
});

test("大綱刪 1 頁 → 確認成功 → units 隨編輯後大綱重同步,complete 後無幽靈 done 格", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });
  vi.mocked(postOutlineAction).mockResolvedValue(undefined);

  const { container } = render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];

  // 後端先給 3 頁大綱並進入確認站。
  es.emit("outline", {
    design: null,
    mode: "presenter",
    pages: [
      { role: "title", title: "封面舊標題", gist: "g1" },
      { role: "agenda", title: "議程", gist: "g2" },
      { role: "closing", title: "結語", gist: "g3" },
    ],
  });
  es.emit("awaiting_approval", {});

  // 進入 await:三列可就地編輯。
  await waitFor(() => expect(screen.getAllByRole("textbox", { name: /頁標題/ })).toHaveLength(3));

  // 刪掉第 1 頁(封面舊標題),送出。
  fireEvent.click(screen.getAllByRole("button", { name: /刪除第 1 頁/ })[0]);
  fireEvent.click(screen.getByRole("button", { name: /就這樣鍛/ }));
  await waitFor(() => expect(postOutlineAction).toHaveBeenCalledTimes(1));
  expect(vi.mocked(postOutlineAction).mock.calls[0][1]).toMatchObject({ action: "edit" });

  // 後端不重發 outline,直接發 N-1 個 slide_done(編輯後大綱)後 complete。
  es.emit("slide_done", { n: 1, slide: { layout: "agenda", title: "議程", bullets: [] } });
  es.emit("slide_done", { n: 2, slide: { layout: "closing", title: "結語", bullets: [] } });
  es.emit("complete", { download_url: "/d" });

  // units 應為 2 格(N-1),沒有第 3 格幽靈。
  await waitFor(() => expect(container.querySelectorAll(".cell")).toHaveLength(2));
  // 幽靈頁的舊標題不得殘留在任何地方(縮圖牆或大綱 rail)。
  expect(screen.queryByText("封面舊標題")).toBeNull();
  // 縮圖牆兩格標題為編輯後大綱。
  const titles = Array.from(container.querySelectorAll(".cell .ptitle")).map((e) => e.textContent);
  expect(titles).toEqual(["議程", "結語"]);
});
