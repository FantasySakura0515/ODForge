import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import {
  ApiHttpError,
  getSessions,
  postCancel,
  postDiscoveryQuestions,
  postGenerate,
  postOutlineAction,
} from "./state/api";

vi.mock("./state/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./state/api")>();
  return {
    ...actual,
    getSessions: vi.fn(),
    postCancel: vi.fn(),
    postDiscoveryQuestions: vi.fn(),
    postGenerate: vi.fn(),
    postOutlineAction: vi.fn(),
  };
});

// Minimal EventSource stand-in so App tests can drive the SSE stream by hand.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, (e: { data: string }) => void> = {};
  onerror: ((e: unknown) => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  // 0 CONNECTING · 1 OPEN · 2 CLOSED,鏡射真實 EventSource.readyState。
  readyState = 0;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, cb: (e: { data: string }) => void) { this.listeners[type] = cb; }
  close() { this.closed = true; this.readyState = 2; }
  emit(type: string, data: unknown) {
    act(() => this.listeners[type]?.({ data: JSON.stringify(data) }));
  }
  // 連線(重)開啟。真實 EventSource 自動重連成功時會在同一實例上再次觸發 onopen,
  // 後端接著從 cursor 0 重播——測試以 open()+重 emit 模擬整段重連重播。
  open() {
    this.readyState = 1;
    act(() => this.onopen?.());
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
  vi.mocked(postCancel).mockResolvedValue(undefined);
  vi.mocked(getSessions).mockResolvedValue([]);
  vi.mocked(postDiscoveryQuestions).mockResolvedValue({
    summary: "需求已足夠。",
    known_context: [],
    questions: [],
    completeness: 100,
  });
});

afterEach(() => {
  vi.mocked(postGenerate).mockReset();
  vi.mocked(postDiscoveryQuestions).mockReset();
  vi.mocked(postCancel).mockReset();
  vi.mocked(postOutlineAction).mockReset();
  vi.mocked(getSessions).mockReset();
});

function openComposer() {
  const button = screen.queryByRole("button", { name: /新增簡報/ });
  if (button) fireEvent.click(button);
}

async function submitThroughDiscovery(prompt: string) {
  openComposer();
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: prompt },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  fireEvent.click(await screen.findByRole("button", { name: "生成大綱" }));
}

test("首頁顯示新增入口與過去 session", async () => {
  window.history.replaceState({}, "", "/");
  vi.mocked(getSessions).mockResolvedValue([
    {
      id: "abc123",
      title: "資料結構教學",
      prompt: "給大一新生的資料結構簡報",
      status: "complete",
      created_at: 1_700_000_000,
      updated_at: 1_700_000_100,
      page_count: 12,
      preview_url: "/api/jobs/abc123/preview/1.png",
      download_url: "/api/jobs/abc123/download",
    },
  ]);

  render(<App />);

  expect(await screen.findByText("資料結構教學")).toBeInTheDocument();
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(document.querySelector(".session-main")).toHaveAttribute(
    "href",
    "?job=abc123",
  );
  expect(screen.getByRole("link", { name: "下載 資料結構教學" })).toHaveAttribute(
    "href",
    "/api/jobs/abc123/download",
  );

  fireEvent.click(screen.getByRole("button", { name: /新增簡報/ }));
  expect(screen.getByRole("textbox", { name: /主題/ })).toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).get("new")).toBe("1");
});

// D3 修正後行為改變:展示模式沒有後端可訪談,按「繼續」跳過訪談直接進 mock 生成流
//(修前這裡會走 submitThroughDiscovery 的訪談步驟——但真環境無後端,第一下點擊就死)。
test("?mock 模式:送出即進 mock 流(跳過訪談、零網路),格式閘依序點亮、設計閘顯示未啟用、下載鈕誠實 disabled", async () => {
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
  const { container } = render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "樹與二元樹" },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));

  // 零網路:不打訪談、不打 generate,直接播 mock 事件流。
  expect(postDiscoveryQuestions).not.toHaveBeenCalled();
  expect(postGenerate).not.toHaveBeenCalled();

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

test("AI 訪談流程：短需求→動態問題→可編輯 Brief→才建立生成工作", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postDiscoveryQuestions).mockResolvedValue({
    summary: "向系上老師報告畢業專題進度。",
    known_context: ["受眾是系上老師"],
    questions: [
      {
        id: "decision",
        question: "希望老師提供什麼？",
        why: "決定結尾的行動請求。",
        options: ["確認進度", "技術建議"],
      },
      {
        id: "progress",
        question: "目前完成到哪裡？",
        why: "讓架構與時程具體。",
        options: ["開發中", "測試中"],
      },
    ],
    completeness: 38,
  });
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "brief-job" });

  render(<App />);
  openComposer();
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "畢業專題進度報告，重點是架構與時程" },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));

  await waitFor(() => expect(screen.getByText("希望老師提供什麼？")).toBeInTheDocument());
  expect(postDiscoveryQuestions).toHaveBeenCalledWith(
    expect.objectContaining({ prompt: "畢業專題進度報告，重點是架構與時程" }),
    expect.objectContaining({
      signal: expect.any(AbortSignal),
      onProgress: expect.any(Function),
    }),
  );
  expect(postGenerate).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "技術建議" }));
  fireEvent.click(screen.getByRole("button", { name: "下一題" }));
  fireEvent.click(screen.getByRole("button", { name: "開發中" }));
  fireEvent.click(screen.getByRole("button", { name: "整理需求" }));

  const brief = await screen.findByRole("textbox", { name: "生成規格" }) as HTMLTextAreaElement;
  expect(brief.value).toContain("回答：技術建議");
  fireEvent.click(screen.getByRole("button", { name: "生成大綱" }));

  await waitFor(() => expect(postGenerate).toHaveBeenCalledOnce());
  expect(vi.mocked(postGenerate).mock.calls[0][0].prompt).toContain("回答：開發中");
});

test("AI 讀題串流會即時更新工作軌跡，取消時中止請求", async () => {
  window.history.replaceState({}, "", "/");
  let signal: AbortSignal | undefined;
  vi.mocked(postDiscoveryQuestions).mockImplementation(async (_body, options) => {
    signal = options?.signal;
    options?.onProgress?.({
      request_id: "trace-1",
      stage: "requesting",
      message: "正在產生關鍵問題",
      elapsed_ms: 2400,
    });
    return await new Promise<never>(() => undefined);
  });

  render(<App />);
  openComposer();
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "畢業專題進度報告" },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));

  expect(await screen.findByText("正在產生關鍵問題")).toBeInTheDocument();
  // 「產生追問」同時出現在可見的進度清單與 sr-only live region;要驗的是清單那個。
  expect(
    within(screen.getByRole("list", { name: "AI 讀題進度" }))
      .getByText("產生追問")
      .closest("li"),
  ).toHaveAttribute(
    "data-status",
    "active",
  );
  fireEvent.click(screen.getByRole("button", { name: "取消" }));

  expect(signal?.aborted).toBe(true);
  expect(screen.getByRole("textbox", { name: /主題/ })).toHaveValue("畢業專題進度報告");
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
  await submitThroughDiscovery("樹與二元樹");

  // 連線錯誤訊息出現(人話前綴在 narrator + ErrorPanel 標題皆會出現)
  await waitFor(() => expect(screen.getAllByText(/無法連上後端/).length).toBeGreaterThan(0));
  // 絕不退回 mock 演假簡報:mock 專屬標題不得出現
  expect(screen.queryByText("本章路線圖")).toBeNull();
  expect(screen.queryByRole("link", { name: /下載/ })).toBeNull();
  // PromptBar 回來,可重試
  expect(screen.getByRole("textbox", { name: /主題/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "繼續" })).toBeInTheDocument();
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

test("目前任務用模型定的文件標題;大綱到之前才顯示原始需求", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  await submitThroughDiscovery("資結第三章教學");

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  // PromptBar 收起(生成中);大綱還沒到 → 先顯示使用者那句需求,不是佔位字。
  await waitFor(() => expect(screen.queryByRole("button", { name: "繼續" })).toBeNull());
  expect(document.querySelector(".promptline")?.textContent).toContain("資結第三章教學");

  FakeEventSource.instances[0].emit("outline", {
    design: null,
    mode: "presenter",
    pages: [{ role: "title", title: "資料結構：樹與走訪", gist: "g" }],
  });

  // 大綱到了 → 換成模型讀完需求後定的標題;原始需求仍留在 title 供 hover 查證。
  const line = await waitFor(() => {
    const el = document.querySelector(".promptline");
    expect(el?.textContent).toContain("資料結構：樹與走訪");
    return el;
  });
  expect(line?.getAttribute("title")).toBe("資結第三章教學");
});

test("完成後「再鍛一份」重置回 empty:PromptBar 回來、輸入框保留原文、縮圖牆清空", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  await submitThroughDiscovery("我的講稿");

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
  await submitThroughDiscovery("請保留這句");

  await waitFor(() => expect(screen.getAllByText(/無法連上後端/).length).toBeGreaterThan(0));
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.value).toBe("請保留這句");
});

test("等待卡:submitting 顯示、outline 到收起", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  await submitThroughDiscovery("等待測試");

  // 等待卡出現(submitting、尚無 outline)。
  await waitFor(() => expect(screen.getByText(/產生投影片/)).toBeInTheDocument());
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

  // outline 到 → 等待卡收起,縮圖牆出現。
  FakeEventSource.instances[0].emit("outline", {
    design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }],
  });
  await waitFor(() => expect(document.querySelectorAll(".cell").length).toBeGreaterThan(0));
  expect(screen.queryByText(/產生投影片/)).toBeNull();
});

test("等待卡取消鈕:關閉 SSE、重置回 empty、prompt 保留", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  render(<App />);
  await submitThroughDiscovery("取消我");

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const cancel = await screen.findByRole("button", { name: /取消/ });
  fireEvent.click(cancel);

  const ta = (await screen.findByRole("textbox", { name: /主題/ })) as HTMLTextAreaElement;
  expect(ta.value).toBe("取消我");
  expect(FakeEventSource.instances[0].closed).toBe(true);
  expect(postCancel).toHaveBeenCalledWith("j1");
});

test("主題切換鈕有可及名稱(淺色主題/深色主題)", () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  expect(screen.getByRole("button", { name: "淺色主題" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "深色主題" })).toBeInTheDocument();
});

test("空台(大綱未到)左欄收掉:cockpit data-outline=absent;大綱到後轉 present", async () => {
  window.history.replaceState({}, "", "/?new=1");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j1" });

  const { container } = render(<App />);
  // 一開始 empty:左欄不佔位。
  expect(container.querySelector(".cockpit")?.getAttribute("data-outline")).toBe("absent");
  // 空台不擺「生成中…」假下載鈕。
  expect(screen.queryByText(/生成中…/)).toBeNull();

  await submitThroughDiscovery("左欄測試");
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
  await submitThroughDiscovery("寫入 URL");

  await waitFor(() => expect(new URLSearchParams(window.location.search).get("job")).toBe("abc123"));
});

test("?job= 重整復原:重播 outline→slide_done→complete,下載鈕可用", async () => {
  window.history.replaceState({}, "", "/?job=resumeC");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  // 直接訂閱重播，不需按生成。
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

  await waitFor(() => expect(screen.getAllByText(/任務不存在或已過期/).length).toBeGreaterThan(0));
  // URL 的 ?job= 已清掉,回到輸入畫面。
  expect(new URLSearchParams(window.location.search).get("job")).toBeNull();
  expect(screen.getByRole("button", { name: "繼續" })).toBeInTheDocument();
});

test("?job= 復原:自動重連中(readyState=CONNECTING)的暫時錯誤不誤判為過期", async () => {
  window.history.replaceState({}, "", "/?job=reconnecting");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  // 尚未收到任何事件,但這是自動重連中的暫時性錯誤(CONNECTING)→ 不算過期。
  FakeEventSource.instances[0].fail(0);

  // 不顯示過期文案,?job= 仍保留(繼續等重連)。
  expect(screen.queryAllByText(/任務不存在或已過期/)).toHaveLength(0);
  expect(new URLSearchParams(window.location.search).get("job")).toBe("reconnecting");
});

test("後端送來的真錯誤不會被自己造成的斷線蓋成「無法連上後端」", async () => {
  // 實測踩到的:模型端回 403「免費額度已用完」,畫面卻寫「無法連上後端」,還掛上
  // 「後端未連線」chip——使用者於是去檢查一台好好活著的伺服器,而真正該做的事
  // (換來源或加值)一個字都沒出現。
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j-403" });

  render(<App />);
  await submitThroughDiscovery("額度用完的那一次");
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];

  es.emit("error", {
    message: "Error code: 403 - The free quota has been exhausted.",
    stage: "outline",
  });
  // 終局事件讓訂閱端自己 close();瀏覽器仍會為「連線結束」再叫一次 onerror,而
  // 那時 readyState 已是 CLOSED——舊版據此判定「放棄重連」並補一個 stage=connect。
  es.fail();

  await waitFor(() =>
    expect(screen.getAllByText("AI 構思大綱時出錯").length).toBeGreaterThan(0),
  );
  expect(screen.getByText(/free quota has been exhausted/)).toBeInTheDocument();
  expect(screen.queryByText(/無法連上後端/)).toBeNull();
  expect(screen.queryByText("後端未連線")).toBeNull();
});

test("錯誤區「重試」以同樣參數重新送出上一次 generate", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockRejectedValue(new Error("network down"));

  render(<App />);
  await submitThroughDiscovery("重試主題");

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
  await submitThroughDiscovery("QA 測試");

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
  await submitThroughDiscovery("樹與二元樹");

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

test("重連重播重建牆:onopen 重設節流去重,重播的 slide_done 不再被吞", async () => {
  window.history.replaceState({}, "", "/?job=replayWall");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  const { container } = render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  const outline = {
    design: null, mode: "presenter",
    pages: [{ role: "title", title: "封面", gist: "g" }, { role: "closing", title: "結語", gist: "g2" }],
  };
  es.emit("outline", outline);
  es.emit("slide_done", { n: 1, slide: { layout: "title", title: "封面", bullets: [] } });
  es.emit("slide_done", { n: 2, slide: { layout: "closing", title: "結語", bullets: [] } });
  await waitFor(() => expect(container.querySelectorAll('.cell[data-status="filling"]')).toHaveLength(2));

  // 傳輸小斷線後 EventSource 自動重連:同一實例再次 onopen,後端從 cursor 0 重播。
  es.open();
  // 重播的 outline 把牆重設回 skeleton;重播的 slide_done(同 n、新物件)必須能再次
  // 點亮——修前節流器 seenUnitN 還記著 1/2,重播被當成別名重複吞掉,牆永遠 skeleton。
  es.emit("outline", { ...outline, pages: outline.pages.map((p) => ({ ...p })) });
  es.emit("slide_done", { n: 1, slide: { layout: "title", title: "封面", bullets: [] } });
  es.emit("slide_done", { n: 2, slide: { layout: "closing", title: "結語", bullets: [] } });

  await waitFor(() => expect(container.querySelectorAll('.cell[data-status="filling"]')).toHaveLength(2));
  // 進度計數也復原(重播回填 ir),不是掉回 0。
  expect(container.querySelector(".prog b")?.textContent).toBe("2");
});

test("確認站編輯到一半遇重連重播(同內容大綱):牆重建但草稿編輯保留", async () => {
  window.history.replaceState({}, "", "/?job=replayEdit");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);

  render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  const pages = [{ role: "title", title: "封面", gist: "g" }, { role: "closing", title: "結語", gist: "g2" }];
  es.emit("outline", { design: null, mode: "presenter", pages });
  es.emit("awaiting_approval", {});

  const box = (await screen.findAllByRole("textbox", { name: /頁標題/ }))[0] as HTMLInputElement;
  fireEvent.change(box, { target: { value: "使用者改到一半" } });

  // 重連重播:同內容、全新物件身分的 outline + awaiting_approval 再來一輪。
  es.open();
  es.emit("outline", { design: null, mode: "presenter", pages: pages.map((p) => ({ ...p })) });
  es.emit("awaiting_approval", {});

  // 修前:OutlineRail 以物件身分變化重播種草稿 → 編輯中的標題被抹掉。
  expect((screen.getAllByRole("textbox", { name: /頁標題/ })[0] as HTMLInputElement).value).toBe("使用者改到一半");
});

test("生成中途後端報錯:工作台就地顯示可重試的錯誤面板,牆不卸載", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "jerr" });

  const { container } = render(<App />);
  await submitThroughDiscovery("中途出錯");

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  es.emit("outline", {
    design: null, mode: "presenter",
    pages: [{ role: "title", title: "封面", gist: "g" }, { role: "closing", title: "結語", gist: "g2" }],
  });
  es.emit("slide_done", { n: 1, slide: { layout: "title", title: "封面", bullets: [] } });
  es.emit("error", { message: "LLM 供應商 500", stage: "slides" });

  // 修前:ErrorPanel 只長在輸入畫面分支,工作台只剩凍住的牆+hover 訊息死路。
  await waitFor(() => expect(container.querySelector(".stage-error")).not.toBeNull());
  // 白話標題出現在錯誤面板本體(narrator 那份是 hover 訊息,不算)。
  expect(container.querySelector(".stage-error .errhead")?.textContent).toBe("AI 填充內容時出錯");
  // 牆沒被卸載:縮圖格仍在(已生成內容看得到)。
  expect(container.querySelectorAll(".cell")).toHaveLength(2);

  // 重試接上既有 retryGenerate/lastBodyRef 機制:同參數重送。
  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  await waitFor(() => expect(postGenerate).toHaveBeenCalledTimes(2));
  expect(vi.mocked(postGenerate).mock.calls[1][0]).toEqual(vi.mocked(postGenerate).mock.calls[0][0]);
});

test("生成中傳輸層死亡(server 重啟後 404 → CLOSED):收線報錯可重試,不再永遠轉圈", async () => {
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "jdead" });

  render(<App />);
  await submitThroughDiscovery("斷線測試");

  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const es = FakeEventSource.instances[0];
  es.emit("outline", { design: null, mode: "presenter", pages: [{ role: "title", title: "封面", gist: "g" }] });

  // 自動重連中(CONNECTING)的暫時錯誤:不動,交給瀏覽器續試。
  es.fail(0);
  expect(screen.queryByRole("button", { name: "重試" })).toBeNull();

  // 放棄重連(CLOSED,如 server 重啟後 job 404):收線並以 error 收尾。
  es.fail(2);
  await waitFor(() => expect(screen.getByRole("button", { name: "重試" })).toBeInTheDocument());
  expect(es.closed).toBe(true);
  expect(screen.getAllByText(/事件串流已中斷/).length).toBeGreaterThan(0);
});

test("?mock 不因 URL 重寫而丟失:再鍛一份/回首頁後仍在展示模式", async () => {
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
  render(<App />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /再鍛一份/ })).toBeInTheDocument(), { timeout: 4000 });

  // 再鍛一份重寫 URL(?new=1)也要帶著 mock/mockStep 走,demo 不得中途退出展示模式。
  fireEvent.click(screen.getByRole("button", { name: /再鍛一份/ }));
  let params = new URLSearchParams(window.location.search);
  expect(params.get("mock")).toBe("1");
  expect(params.get("mockStep")).toBe("5");
  expect(params.get("new")).toBe("1");
  expect(screen.getByText("展示模式")).toBeInTheDocument();

  // 回首頁(清參數的重寫)同樣保留 mock。
  fireEvent.click(screen.getByRole("button", { name: "回到首頁" }));
  params = new URLSearchParams(window.location.search);
  expect(params.get("mock")).toBe("1");
  expect(params.get("mockStep")).toBe("5");
  expect(screen.getByText("展示模式")).toBeInTheDocument();
});

test("429 之類「後端有回應但拒絕」:轉述真實原因與狀態碼,不誤導成連線失敗", async () => {
  window.history.replaceState({}, "", "/");
  vi.mocked(postGenerate).mockRejectedValue(
    new ApiHttpError("too many active jobs; limit is 2(HTTP 429)", 429),
  );

  render(<App />);
  await submitThroughDiscovery("上限測試");

  // 白話標題說「被拒絕」,細節帶後端 detail 與狀態碼。
  await waitFor(() => expect(screen.getByText("後端拒絕了這次請求")).toBeInTheDocument());
  expect(screen.getByText(/too many active jobs.*429/)).toBeInTheDocument();
  // 不是連線問題:連線提示與「後端未連線」chip 都不該出現(叫人重啟伺服器是誤導)。
  expect(screen.queryByText(/無法連上後端/)).toBeNull();
  expect(screen.queryByText("後端未連線")).toBeNull();
});

test("工作台有且只有一個 h1,說得出正在看的是哪一份文件", async () => {
  // R2-06:工作台原本完全沒有 h1。讀屏使用者用「跳到標題 1」會直接掠過整個
  // 工作台,而首頁與輸入頁各有自己的 h1 —— 只有真正在做事的那一頁沒有。
  window.history.replaceState({}, "", "/");
  vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  vi.mocked(postGenerate).mockResolvedValue({ job_id: "j-h1" });

  render(<App />);
  await submitThroughDiscovery("資結第三章教學");
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

  const h1s = await waitFor(() => {
    const found = screen.getAllByRole("heading", { level: 1 });
    expect(found).toHaveLength(1);
    return found;
  });
  expect(h1s[0].textContent).toContain("資結第三章教學");
});

// ---------------------------------------------------------------------------
// 範本庫是自己的一頁,不是首頁捲到底的附屬區塊
// ---------------------------------------------------------------------------

test("首頁不直接鋪出範本庫,入口在左側常駐導覽", async () => {
  window.history.replaceState({}, "", "/");
  vi.mocked(getSessions).mockResolvedValue([]);
  render(<App />);

  await screen.findByText("還沒有簡報");
  // 十三張縮圖掛在工作紀錄底下 = 首頁被一個次要功能佔滿。
  expect(document.querySelector(".template-gallery")).toBeNull();
  const nav = screen.getByRole("navigation", { name: "主要導覽" });
  expect(within(nav).getByRole("button", { name: /範本庫/ })).toBeInTheDocument();
  // 目前所在的那一項要標出來,而不是兩個看起來一樣。
  expect(within(nav).getByRole("button", { name: /工作紀錄/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("進入範本庫會換頁並寫進 URL,重整回得來", async () => {
  window.history.replaceState({}, "", "/");
  vi.mocked(getSessions).mockResolvedValue([]);
  const { unmount } = render(<App />);

  await screen.findByText("還沒有簡報");
  const nav = screen.getByRole("navigation", { name: "主要導覽" });
  fireEvent.click(within(nav).getByRole("button", { name: /範本庫/ }));

  expect(
    await screen.findByRole("heading", { level: 1, name: "範本庫" }),
  ).toBeInTheDocument();
  expect(window.location.search).toContain("templates=1");
  // 工作紀錄讓位給範本庫,而不是兩個疊在同一頁。
  expect(screen.queryByText("還沒有簡報")).toBeNull();

  unmount();
  render(<App />);
  expect(
    await screen.findByRole("heading", { level: 1, name: "範本庫" }),
  ).toBeInTheDocument();
});
