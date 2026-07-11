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
  onerror: ((e: unknown) => void) | null = null;
  closed = false;
  // 0 CONNECTING · 1 OPEN · 2 CLOSED,鏡射真實 EventSource.readyState。
  readyState = 0;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, cb: (e: { data: string }) => void) { this.listeners[type] = cb; }
  close() { this.closed = true; this.readyState = 2; }
  emit(type: string, data: unknown) {
    act(() => this.listeners[type]?.({ data: JSON.stringify(data) }));
  }
  // 預設以 CLOSED(2)失敗(已放棄重連 → 視為過期);傳 0 模擬自動重連中的暫時錯誤。
  fail(readyState = 2) {
    this.readyState = readyState;
    act(() => this.onerror?.({ target: this }));
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

test("?mock 模式:mock 流跑到完成,格式閘依序點亮、設計閘顯示未啟用、下載鈕誠實 disabled", async () => {
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
  const { container } = render(<App />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(screen.getAllByText("樹與二元樹").length).toBeGreaterThan(0));
  // 完成訊號:頂欄「再鍛一份」出現(mock 產物非真檔,不再以假下載連結當完成證據)。
  await waitFor(() => expect(screen.getByRole("button", { name: /再鍛一份/ })).toBeInTheDocument(), { timeout: 4000 });
  // 展示模式誠實:不擺點了就 404 的假下載連結,改 disabled 說明鈕。
  expect(screen.queryByRole("link", { name: /下載/ })).toBeNull();
  expect(screen.getByRole("button", { name: /不提供下載/ })).toBeDisabled();
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

  // 連線錯誤訊息出現(人話前綴在 narrator + ErrorPanel 標題皆會出現)
  await waitFor(() => expect(screen.getAllByText(/無法連上後端/).length).toBeGreaterThan(0));
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

test("生成中頂欄顯示真實 prompt(帶 title 全文),非佔位字", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "資結第三章教學" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  FakeEventSource.instances[0].emit("outline", {
    design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }],
  });

  // PromptBar 收起(生成中),頂欄 promptline 顯示原 prompt 且 title 給全文。
  await waitFor(() => expect(screen.queryByRole("button", { name: /鍛造/ })).toBeNull());
  const line = document.querySelector(".promptline");
  expect(line?.textContent).toContain("資結第三章教學");
  expect(line?.getAttribute("title")).toBe("資結第三章教學");
});

test("完成後「再鍛一份」重置回 empty:PromptBar 回來、輸入框保留原文、縮圖牆清空", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "我的講稿" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  es.emit("outline", { design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }] });
  es.emit("slide_done", { n: 1, slide: { layout: "title", title: "封面", bullets: [] } });
  es.emit("complete", { download_url: "/d" });

  const reforge = await screen.findByRole("button", { name: /再鍛一份/ });
  fireEvent.click(reforge);

  const ta = (await screen.findByRole("textbox", { name: /主題/ })) as HTMLTextAreaElement;
  expect(ta.value).toBe("我的講稿");
  // 回 empty:縮圖牆清空,下載鈕消失。
  expect(document.querySelectorAll(".cell")).toHaveLength(0);
  expect(screen.queryByRole("link", { name: /下載/ })).toBeNull();
});

test("錯誤後輸入框保留原 prompt(受控)", async () => {
  window.history.replaceState({}, "", "/");
  vi.mocked(postGenerate).mockRejectedValue(new Error("network down"));

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "請保留這句" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(screen.getAllByText(/無法連上後端/).length).toBeGreaterThan(0));
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.value).toBe("請保留這句");
});

test("等待卡:submitting 顯示、outline 到收起", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "等待測試" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  // 等待卡出現(submitting、尚無 outline)。
  await waitFor(() => expect(screen.getByText(/逐頁填充內容/)).toBeInTheDocument());
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

  // outline 到 → 等待卡收起,縮圖牆出現。
  FakeEventSource.instances[0].emit("outline", {
    design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }],
  });
  await waitFor(() => expect(document.querySelectorAll(".cell").length).toBeGreaterThan(0));
  expect(screen.queryByText(/逐頁填充內容/)).toBeNull();
});

test("等待卡取消鈕:關閉 SSE、重置回 empty、prompt 保留", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "取消我" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const cancel = await screen.findByRole("button", { name: /取消/ });
  fireEvent.click(cancel);

  const ta = (await screen.findByRole("textbox", { name: /主題/ })) as HTMLTextAreaElement;
  expect(ta.value).toBe("取消我");
  expect(FakeEventSource.instances[0].closed).toBe(true);
});

test("主題切換鈕有可及名稱(淺色主題/深色主題)", () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  expect(screen.getByRole("button", { name: "淺色主題" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "深色主題" })).toBeInTheDocument();
});

test("空台(大綱未到)左欄收掉:cockpit data-outline=absent;大綱到後轉 present", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  const { container } = render(<App />);
  // 一開始 empty:左欄不佔位。
  expect(container.querySelector(".cockpit")?.getAttribute("data-outline")).toBe("absent");
  // 空台不擺「生成中…」假下載鈕。
  expect(screen.queryByText(/生成中…/)).toBeNull();

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "左欄測試" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  FakeEventSource.instances[0].emit("outline", {
    design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }],
  });

  // 大綱到達 → 左欄回來(present)。
  await waitFor(() => expect(container.querySelector(".cockpit")?.getAttribute("data-outline")).toBe("present"));
});

test("生成開始把 jobId 寫進 URL(?job=),供重整復原", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "abc123" });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "寫入 URL" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(new URLSearchParams(window.location.search).get("job")).toBe("abc123"));
});

test("?job= 重整復原:重播 outline→slide_done→complete,下載鈕可用", async () => {
  window.history.replaceState({}, "", "/?job=resumeC");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  // 直接訂閱重播,不需按鍛造。
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  expect(FakeEventSource.instances[0].url).toBe("/api/jobs/resumeC/events");

  const es = FakeEventSource.instances[0];
  es.emit("outline", { design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }] });
  es.emit("slide_done", { n: 1, slide: { layout: "title", title: "封面", bullets: [] } });
  es.emit("complete", { download_url: "/d" });

  await waitFor(() => expect(screen.getByRole("link", { name: /下載/ })).toBeInTheDocument());
  // postGenerate 不該被呼叫(復原走純重播)。
  expect(postGenerate).not.toHaveBeenCalled();
});

test("?job= 重整復原:awaiting_approval 的 job 重整後回到大綱確認站(ConfirmBar)", async () => {
  window.history.replaceState({}, "", "/?job=resumeA");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  es.emit("outline", {
    design: null, mode: "presenter",
    pages: [{ role: "title", title: "封面", gist: "g" }, { role: "closing", title: "結語", gist: "g2" }],
  });
  es.emit("awaiting_approval", {});

  // 回到確認站:可就地編輯 + 「就這樣鍛」鈕出現。
  await waitFor(() => expect(screen.getByRole("button", { name: /就這樣鍛/ })).toBeInTheDocument());
  expect(screen.getAllByRole("textbox", { name: /頁標題/ })).toHaveLength(2);
});

test("?job= 但 job 不存在(傳輸錯誤/404):顯示過期文案並清掉 URL 參數", async () => {
  window.history.replaceState({}, "", "/?job=gone");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  // 尚未收到任何事件即傳輸失敗 → 視為過期。
  FakeEventSource.instances[0].fail();

  await waitFor(() => expect(screen.getByText(/找不到這個任務/)).toBeInTheDocument());
  // URL 的 ?job= 已清掉,回到輸入畫面。
  expect(new URLSearchParams(window.location.search).get("job")).toBeNull();
  expect(screen.getByRole("button", { name: /鍛造/ })).toBeInTheDocument();
});

test("?job= 復原:自動重連中(readyState=CONNECTING)的暫時錯誤不誤判為過期", async () => {
  window.history.replaceState({}, "", "/?job=reconnecting");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  // 尚未收到任何事件,但這是自動重連中的暫時性錯誤(CONNECTING)→ 不算過期。
  FakeEventSource.instances[0].fail(0);

  // 不顯示過期文案,?job= 仍保留(繼續等重連)。
  expect(screen.queryByText(/找不到這個任務/)).toBeNull();
  expect(new URLSearchParams(window.location.search).get("job")).toBe("reconnecting");
});

test("錯誤區「重試」以同樣參數重新送出上一次 generate", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockRejectedValue(new Error("network down"));

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "重試主題" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  // 錯誤區出現白話標題與重試鈕。
  await waitFor(() => expect(screen.getByRole("button", { name: "重試" })).toBeInTheDocument());
  expect(postGenerate).toHaveBeenCalledTimes(1);
  const firstBody = vi.mocked(postGenerate).mock.calls[0][0];

  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  // 以同樣 body 再送一次。
  await waitFor(() => expect(postGenerate).toHaveBeenCalledTimes(2));
  expect(vi.mocked(postGenerate).mock.calls[1][0]).toEqual(firstBody);
});

test("點設計 QA 的 FindingRow → 開該頁 lightbox", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "jqa" });

  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "QA 測試" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  es.emit("outline", { design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }] });
  es.emit("slide_done", { n: 1, slide: { layout: "title", title: "封面", bullets: [] } });
  es.emit("preview_ready", { n: 1, url: "/api/jobs/jqa/preview/1.png" });
  es.emit("qa_round", { round: 1, findings: [{ slide_no: 1, issue: "溢出", severity: "error", fix_hint: "減行" }] });

  // FindingRow 出現且可點 → 開 lightbox dialog(以 issue 文字定位,避開縮圖格同名)。
  const row = await screen.findByRole("button", { name: /溢出/ });
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(row);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
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
