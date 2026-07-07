import { useEffect, useReducer, useRef, useState } from "react";
import { PromptBar } from "./components/PromptBar";
import { OutlineRail } from "./components/OutlineRail";
import { PreviewStage } from "./components/PreviewStage";
import { GateRail } from "./components/GateRail";
import { StatusNarrator } from "./components/StatusNarrator";
import { DownloadDock } from "./components/DownloadDock";
import { postGenerate } from "./state/api";
import { cockpitReducer, initialState } from "./state/cockpit";
import { playMock } from "./state/mockStream";
import { subscribeJob } from "./state/sse";
import type { DocType } from "./state/types";
import { useTheme } from "./theme/useTheme";
import "./theme/tokens.css";
import "./styles/app.css";

export default function App() {
  const [state, dispatch] = useReducer(cockpitReducer, undefined, () => initialState());
  const [jobId, setJobId] = useState<string | undefined>();
  const { theme, setTheme } = useTheme();
  const cancelRef = useRef<() => void>();
  const params = new URLSearchParams(window.location.search);
  const mockMode = params.has("mock");
  const mockStep = Number(params.get("mockStep") ?? "250");

  useEffect(() => () => cancelRef.current?.(), []);

  async function onGenerate(prompt: string, docType: DocType) {
    cancelRef.current?.();
    if (mockMode) { setJobId("mock"); cancelRef.current = playMock(dispatch, { step: mockStep }); return; }
    try {
      const { job_id } = await postGenerate({ prompt, doc_type: docType });
      setJobId(job_id);
      cancelRef.current = subscribeJob(job_id, dispatch);
    } catch {
      // 後端不可用 → 退回 mock,讓 UI 仍可展示
      setJobId("mock");
      cancelRef.current = playMock(dispatch, { step: mockStep });
    }
  }

  const started = state.phase !== "empty";
  return (
    <div className="page">
      <div className="stage">
        <div className="cockpit">
          <header className="top">
            <div className="brand"><span className="mark">文鍛</span><span className="en">ODForge</span></div>
            {!started ? <PromptBar onGenerate={onGenerate} /> : <div className="promptline">生成中的文件</div>}
            <div className="themetoggle">
              <button aria-pressed={theme === "light"} onClick={() => setTheme("light")}>☀</button>
              <button aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>☾</button>
            </div>
          </header>
          <OutlineRail outline={state.outline} />
          <PreviewStage units={state.units} docType={state.docType} jobId={jobId} />
          <GateRail gates={state.gates} qaRounds={state.qaRounds} />
          <footer className="foot">
            <StatusNarrator phase={state.phase} units={state.units} />
            <DownloadDock jobId={jobId} downloadUrl={state.downloadUrl} docType={state.docType} />
          </footer>
        </div>
      </div>
    </div>
  );
}
