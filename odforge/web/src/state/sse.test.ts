import { expect, test, vi } from "vitest";
import { parseSseEvent, subscribeJob } from "./sse";

/** EventSource 替身:可以手動送具名事件,也可以模擬連線失敗。 */
class FakeEventSource {
  listeners: Record<string, (e: { data: string }) => void> = {};
  onerror: ((e: unknown) => void) | null = null;
  onopen: (() => void) | null = null;
  readyState = 0;
  constructor(public url: string) {
    last = this;
  }
  addEventListener(type: string, cb: (e: { data: string }) => void) {
    this.listeners[type] = cb;
  }
  close() {
    this.readyState = 2;
  }
  emit(type: string, data: unknown) {
    this.listeners[type]?.({ data: JSON.stringify(data) });
  }
  /** 真實 EventSource 的失敗;readyState 2 = 已放棄重連。 */
  fail(readyState = 2) {
    this.readyState = readyState;
    this.onerror?.({ target: this });
  }
}

let last: FakeEventSource;
const Ctor = FakeEventSource as unknown as typeof EventSource;

test("解析 outline 事件", () => {
  const ev = parseSseEvent("outline", JSON.stringify({ design: { palette: {}, fonts: {} }, mode: "presenter", pages: [] }));
  expect(ev?.type).toBe("outline");
});

test("解析 slide_done 事件帶 n", () => {
  const ev = parseSseEvent("slide_done", JSON.stringify({ n: 3, slide: { layout: "title-content", title: "x" } }));
  expect(ev).toEqual({ type: "slide_done", data: { n: 3, slide: { layout: "title-content", title: "x" } } });
});

test("解析 unit_done 事件(slide_done 的 F4 正名別名,同 data)", () => {
  const ev = parseSseEvent("unit_done", JSON.stringify({ n: 2, slide: { layout: "title", title: "y" } }));
  expect(ev).toEqual({ type: "unit_done", data: { n: 2, slide: { layout: "title", title: "y" } } });
});

test("未知事件名回 null", () => {
  expect(parseSseEvent("bogus", "{}")).toBeNull();
});

test("壞 JSON 回 null 不拋", () => {
  expect(parseSseEvent("preview_ready", "{not json")).toBeNull();
});

// ---------------------------------------------------------------------------
// 收線之後的 onerror 不是傳輸故障。後端送完終局事件就結束回應,瀏覽器把「訊息
// 送達」與「連線結束」排在相鄰兩個 task:前者讓我們 close(),後者仍會叫到
// onerror,而那時 readyState 已是 CLOSED。呼叫端據此判定「放棄重連」,補一個
// 「無法連上後端」蓋掉真正的原因——實測就是這樣把供應商回的 403 額度用完換成
// 一句叫使用者去檢查伺服器的話。
// ---------------------------------------------------------------------------

test("終局 error 事件之後的斷線不再回報成傳輸錯誤", () => {
  const onEvent = vi.fn();
  const onError = vi.fn();
  subscribeJob("j1", onEvent, Ctor, onError);

  last.emit("error", { message: "403 free quota exhausted", stage: "outline" });
  last.fail(); // 伺服器結束回應造成的斷線

  expect(onEvent).toHaveBeenCalledWith({
    type: "error",
    data: { message: "403 free quota exhausted", stage: "outline" },
  });
  expect(onError).not.toHaveBeenCalled();
});

test("complete 之後的斷線同樣不回報", () => {
  const onError = vi.fn();
  subscribeJob("j1", vi.fn(), Ctor, onError);

  last.emit("complete", { download_url: "/d.odp" });
  last.fail();

  expect(onError).not.toHaveBeenCalled();
});

test("真正的傳輸失敗(沒收過終局事件)照樣回報", () => {
  const onError = vi.fn();
  subscribeJob("j1", vi.fn(), Ctor, onError);

  last.fail();

  expect(onError).toHaveBeenCalledOnce();
});

test("呼叫端取消訂閱之後的 onerror 不回報", () => {
  const onError = vi.fn();
  const close = subscribeJob("j1", vi.fn(), Ctor, onError);

  close();
  last.fail();

  expect(onError).not.toHaveBeenCalled();
});
