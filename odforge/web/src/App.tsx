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
import { useTheme } from "./theme/useTheme";
import "./theme/tokens.css";
import "./styles/app.css";

export default function App() {
  const [state, dispatch] = useReducer(cockpitReducer, undefined, () => initialState());
  const [jobId, setJobId] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const { theme, setTheme } = useTheme();
  const cancelRef = useRef<() => void>();
  const params = new URLSearchParams(window.location.search);
  const mockMode = params.has("mock");
  const mockStep = Number(params.get("mockStep") ?? "250");

  useEffect(() => () => cancelRef.current?.(), []);
  // Clear the "submitting" indicator once the first SSE event moves us off "empty".
  useEffect(() => { if (state.phase !== "empty") setSubmitting(false); }, [state.phase]);

  async function onGenerate(body: GenerateBody) {
    cancelRef.current?.();
    setSubmitting(true);
    if (mockMode) { setJobId("mock"); cancelRef.current = playMock(dispatch, { step: mockStep }); return; }
    try {
      const { job_id } = await postGenerate(body);
      setJobId(job_id);
      cancelRef.current = subscribeJob(job_id, dispatch);
    } catch {
      // 後端不可用時,誠實回報連線失敗(不再靜默退回 mock 演假簡報)。
      // phase 進 error 後 PromptBar 會回來,使用者可重試。
      dispatch({ type: "error", data: { message: "無法連上後端,請確認 odforge serve 是否在執行", stage: "connect" } });
    }
  }

  // Resolve the outline-approval gate. On success the SSE stream resumes on its
  // own; failures surface in the ConfirmBar (thrown → caught there).
  async function onConfirmOutline(action: OutlineActionBody) {
    if (!jobId || jobId === "mock") return;
    await postOutlineAction(jobId, action);
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
            {!busy ? <PromptBar onGenerate={onGenerate} /> : <div className="promptline">生成中的文件</div>}
            <div className="themetoggle">
              <button aria-pressed={theme === "light"} onClick={() => setTheme("light")}>☀</button>
              <button aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>☾</button>
            </div>
          </header>
          <OutlineRail outline={state.outline} phase={state.phase} onConfirm={onConfirmOutline} />
          <PreviewStage units={state.units} docType={state.docType} jobId={jobId} dispatch={dispatch} />
          <GateRail gates={state.gates} qaRounds={state.qaRounds} />
          <footer className="foot">
            <StatusNarrator phase={state.phase} units={state.units} error={state.error} submitting={submitting} />
            <DownloadDock jobId={jobId} downloadUrl={state.downloadUrl} docType={state.docType} />
          </footer>
        </div>
      </div>
    </div>
  );
}
