import { useEffect, useReducer, useRef, useState } from "react";
import { PromptBar } from "./components/PromptBar";
import { OutlineRail } from "./components/OutlineRail";
import { PreviewStage } from "./components/PreviewStage";
import { GateRail } from "./components/GateRail";
import { StatusNarrator } from "./components/StatusNarrator";
import { DownloadDock } from "./components/DownloadDock";
import { postGenerate, postOutlineAction, type GenerateBody, type OutlineActionBody } from "./state/api";
import { cockpitReducer, initialState } from "./state/cockpit";
import { playMock } from "./state/mockStream";
import { subscribeJob } from "./state/sse";
import { createThrottledDispatch } from "./state/throttle";
import { useTheme } from "./theme/useTheme";
import "./theme/tokens.css";
import "./styles/app.css";

export default function App() {
  const [state, dispatch] = useReducer(cockpitReducer, undefined, () => initialState());
  const [jobId, setJobId] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  // Prompt text lives here (single source of truth) so it survives an error or a
  // completed run — PromptBar is controlled from this state.
  const [prompt, setPrompt] = useState("");
  // 大綱已確認、等待第一個 slide_done 的空窗:讓 narrator 報「逐頁填充」而非「等待確認」。
  const [fillingPending, setFillingPending] = useState(false);
  // ?job= 復原失敗(job 不存在/已過期)時,於輸入畫面報一行人話。
  const [expired, setExpired] = useState(false);
  const { theme, setTheme } = useTheme();
  const cancelRef = useRef<() => void>();
  const params = new URLSearchParams(window.location.search);
  const mockMode = params.has("mock");
  const mockStep = Number(params.get("mockStep") ?? "250");

  useEffect(() => () => cancelRef.current?.(), []);

  // 重整/斷線復原:URL 帶 ?job= 時直接重新訂閱事件流,後端會從頭重播全部事件
  // (outline → slide_done×N → …),reducer 天然重建狀態——含 awaiting_approval
  // 回到確認站、complete 回到可下載。傳輸層失敗(如 server 重啟 job 404)且尚未
  // 收到任何事件 → 視為過期:清 URL、回輸入畫面並提示。重播事件同樣走視覺節流器。
  useEffect(() => {
    const jobParam = params.get("job");
    if (!jobParam || mockMode) return;
    setJobId(jobParam);
    setSubmitting(true); // 首個事件到達前顯示等待卡
    const throttle = createThrottledDispatch(dispatch);
    let received = false;
    const close = subscribeJob(
      jobParam,
      (e) => { received = true; throttle.push(e); },
      EventSource,
      () => {
        // 已在重播事件了才錯 → 屬於正常結束後的斷線,忽略;完全沒收到事件才算過期。
        if (received) return;
        close();
        throttle.cancel();
        cancelRef.current = undefined;
        history.replaceState(null, "", window.location.pathname);
        setJobId(undefined);
        setSubmitting(false);
        setExpired(true);
      },
    );
    cancelRef.current = () => { close(); throttle.cancel(); };
    return () => { close(); throttle.cancel(); };
    // 僅在掛載時執行一次(讀初始 URL);後續生成走 onGenerate。
    // eslint-disable-line react-hooks/exhaustive-deps
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Clear the "submitting" indicator once the first SSE event moves us off "empty".
  useEffect(() => { if (state.phase !== "empty") setSubmitting(false); }, [state.phase]);
  // 一旦真的進入填充(generating)或被重置(empty),關掉「已確認等填充」空窗旗標。
  useEffect(() => {
    if (state.phase === "generating" || state.phase === "empty") setFillingPending(false);
  }, [state.phase]);

  async function onGenerate(body: GenerateBody) {
    cancelRef.current?.();
    setExpired(false);
    setSubmitting(true);
    if (mockMode) { setJobId("mock"); cancelRef.current = playMock(dispatch, { step: mockStep }); return; }
    try {
      const { job_id } = await postGenerate(body);
      setJobId(job_id);
      // 把 jobId 寫進 URL,重整/斷線後可用 ?job= 重新訂閱重播復原(mock 模式不寫)。
      history.replaceState(null, "", `?job=${encodeURIComponent(job_id)}`);
      // 只有 SSE 事件流走視覺節流器;本地 dispatch(大綱重同步、重生)直接進 reducer。
      const throttle = createThrottledDispatch(dispatch);
      const closeSse = subscribeJob(job_id, throttle.push);
      cancelRef.current = () => { closeSse(); throttle.cancel(); };
    } catch {
      // 後端不可用時,誠實回報連線失敗(不再靜默退回 mock 演假簡報)。
      // phase 進 error 後 PromptBar 會回來,使用者可重試。
      dispatch({ type: "error", data: { message: "無法連上後端,請確認 odforge serve 是否在執行", stage: "connect" } });
    }
  }

  // 「再鍛一份」/ 等待卡取消:關閉舊 SSE、丟掉待播佇列、清 jobId、重置 cockpit 回 empty。
  // prompt 文字刻意保留在輸入框(受控 state 不動),使用者可微調再送。
  function resetCockpit() {
    cancelRef.current?.();
    cancelRef.current = undefined;
    setJobId(undefined);
    setSubmitting(false);
    setFillingPending(false);
    setExpired(false);
    // 放棄此 job → 清掉 URL 的 ?job=,重整不再嘗試復原舊任務。
    history.replaceState(null, "", window.location.pathname);
    dispatch({ type: "reset" });
  }

  // Resolve the outline-approval gate. On success the SSE stream resumes on its
  // own; failures surface in the ConfirmBar (thrown → caught there).
  async function onConfirmOutline(action: OutlineActionBody) {
    if (!jobId || jobId === "mock") return;
    await postOutlineAction(jobId, action);
    // 確認成功 → 後端開始逐頁填充,但第一個 slide_done 到來前 phase 仍是 await。
    // 標記空窗,讓 narrator 不再顯示「等待你確認大綱…」。
    setFillingPending(true);
    // 編輯成功後,後端不會重發 outline,直接發 N-1 個 slide_done。若不在本地
    // 重新同步,被刪的第 N 格會永遠停在 skeleton,最後 complete 又無條件塗成
    // done → 縮圖牆出現一張「已完成」但不存在於下載檔的幽靈頁。於是就地補一個
    // synthetic outline 事件,讓 state.outline 與 units 依編輯後大綱重建。
    // approve 未改動大綱,無需重同步。
    if (action.action === "edit") dispatch({ type: "outline", data: action.outline });
  }

  // Show the prompt bar when idle OR after an error (so the user can retry);
  // hide it while a request is in flight or generation is streaming.
  const busy = submitting || (state.phase !== "empty" && state.phase !== "error");
  // 頂欄常駐狀態 chip:展示模式(?mock)優先;否則連線失敗顯示「後端未連線」;正常不顯示。
  const disconnected = state.phase === "error" && state.error?.stage === "connect";
  const chip = mockMode
    ? { kind: "mock" as const, label: "展示模式" }
    : disconnected
    ? { kind: "offline" as const, label: "後端未連線" }
    : null;
  return (
    <div className="page">
      <div className="stage">
        <div className="cockpit">
          <header className="top">
            <div className="brand"><span className="mark">文鍛</span><span className="en">ODForge</span></div>
            {chip && <span className="statuschip" data-kind={chip.kind}>{chip.label}</span>}
            {!busy ? (
              <PromptBar onGenerate={onGenerate} value={prompt} onValueChange={setPrompt} />
            ) : state.phase === "complete" ? (
              <div className="promptline done">
                <span className="pl-text" title={prompt}>{prompt || "文件"}</span>
                <button type="button" className="reforge" onClick={resetCockpit}>再鍛一份</button>
              </div>
            ) : (
              // 生成中:頂欄細條顯示真實 prompt(過長由 CSS 截斷,title 給全文)。
              <div className="promptline" title={prompt || undefined}>{prompt || "生成中的文件"}</div>
            )}
            <div className="themetoggle">
              <button aria-pressed={theme === "light"} onClick={() => setTheme("light")}>☀</button>
              <button aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>☾</button>
            </div>
          </header>
          <OutlineRail outline={state.outline} phase={state.phase} onConfirm={onConfirmOutline} />
          <PreviewStage units={state.units} docType={state.docType} jobId={jobId} dispatch={dispatch} submitting={submitting} onCancel={resetCockpit} />
          <GateRail gates={state.gates} qaRounds={state.qaRounds} />
          <footer className="foot">
            <StatusNarrator phase={state.phase} units={state.units} error={state.error} submitting={submitting} fillingPending={fillingPending} expired={expired} />
            <DownloadDock jobId={jobId} downloadUrl={state.downloadUrl} docType={state.docType} />
          </footer>
        </div>
      </div>
    </div>
  );
}
