import { eventsUrl } from "./api";
import type { SseEvent } from "./types";

// unit_done 為 slide_done 的 F4 正名別名(後端並發兩者);兩者都收,reducer 等價處理。
const KNOWN = new Set(["outline", "awaiting_approval", "slide_done", "unit_done", "preview_ready", "gate_result", "qa_round", "content_degraded", "complete", "error"]);

export function parseSseEvent(type: string, data: string): SseEvent | null {
  if (!KNOWN.has(type)) return null;
  try {
    return { type, data: JSON.parse(data) } as SseEvent;
  } catch {
    return null;
  }
}

const EVENT_NAMES = ["outline", "awaiting_approval", "slide_done", "unit_done", "preview_ready", "gate_result", "qa_round", "content_degraded", "complete", "error"];

export function subscribeJob(
  jobId: string,
  onEvent: (e: SseEvent) => void,
  EventSourceCtor: typeof EventSource = EventSource,
  onError?: (e: unknown) => void,
  onOpen?: () => void,
): () => void {
  const es = new EventSourceCtor(eventsUrl(jobId));
  // 我們自己收線之後,任何 onerror 都不是「傳輸壞了」——是我們關的。
  //
  // 少了這個旗標,終局事件會被自己造成的斷線蓋掉:後端送出 error 事件後就結束
  // 回應,瀏覽器把「訊息送達」與「連線結束」排在相鄰的兩個 task 裡。第一個讓
  // 我們 close()(readyState 變 2),第二個仍會叫到 onerror,而呼叫端看到的
  // readyState 已經是 CLOSED —— 它據此判定「放棄重連」,補送一個 stage=connect
  // 的錯誤,把真正的原因(例如供應商回 403:免費額度用完)整句換成「無法連上
  // 後端」。使用者於是去查一台好好活著的伺服器,而真的該做的事沒有一處寫著。
  let closedByUs = false;
  // 每次連線「開啟」都會觸發——含 EventSource 斷線後的自動重連。後端無
  // Last-Event-ID,重連必從頭重播;呼叫端用這個時機重設節流器的去重狀態,
  // 讓重播完整走一遍(reducer 冪等,重建後牆與進度正確)。
  if (onOpen) es.onopen = onOpen;
  for (const name of EVENT_NAMES) {
    es.addEventListener(name, (ev) => {
      const parsed = parseSseEvent(name, (ev as MessageEvent).data);
      if (!parsed) return;
      onEvent(parsed);
      // The backend ends the stream after a terminal event; close our side too so
      // the browser's EventSource doesn't auto-reconnect and replay the whole history.
      if (parsed.type === "complete" || parsed.type === "error") {
        closedByUs = true;
        es.close();
      }
    });
  }
  // Transport-level failures (e.g. the job id is 404 after a server restart) surface
  // through onerror, not the named `error` event. Resume flow uses this to detect an
  // expired job.
  if (onError) es.onerror = (e) => { if (!closedByUs) onError(e); };
  return () => {
    closedByUs = true;
    es.close();
  };
}
