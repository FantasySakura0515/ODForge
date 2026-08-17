import { useEffect, useReducer, useRef, useState } from "react";
import { PromptBar } from "./components/PromptBar";
import { OutlineRail } from "./components/OutlineRail";
import { PreviewStage } from "./components/PreviewStage";
import { GateRail } from "./components/GateRail";
import { StatusNarrator } from "./components/StatusNarrator";
import { DownloadDock } from "./components/DownloadDock";
import { ErrorPanel } from "./components/ErrorPanel";
import { DiscoveryPanel } from "./components/DiscoveryPanel";
import { HomeDashboard } from "./components/HomeDashboard";
import { TemplatesPage } from "./components/TemplatesPage";
import { SideNav } from "./components/SideNav";
import {
  ApiHttpError,
  getSessions,
  postCancel,
  postDiscoveryQuestions,
  postGenerate,
  postOutlineAction,
  type DiscoveryPlan,
  type DiscoveryProgress,
  type GenerateBody,
  type OutlineActionBody,
  type SessionSummary,
} from "./state/api";
import { cockpitReducer, initialState } from "./state/cockpit";
import { taskNameFromOutline } from "./state/taskName";
import { playMock } from "./state/mockStream";
import { subscribeJob } from "./state/sse";
import { createThrottledDispatch } from "./state/throttle";
import { useTheme } from "./theme/useTheme";
import "./theme/tokens.css";
import "./styles/app.css";

// 展示模式活在 URL 參數裡(?mock / ?mockStep):所有 URL 重寫都要帶著它們走,
// 否則「再鍛一份 / 新增簡報 / 首頁」重寫 URL 的瞬間,demo 就無聲退出展示模式。
function replaceUrl(next: URLSearchParams) {
  const cur = new URLSearchParams(window.location.search);
  for (const key of ["mock", "mockStep"]) {
    const value = cur.get(key);
    if (value != null) next.set(key, value);
  }
  const qs = next.toString();
  history.replaceState(null, "", qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
}

export default function App() {
  const [state, dispatch] = useReducer(cockpitReducer, undefined, () => initialState());
  const [view, setView] = useState<"home" | "create" | "workspace" | "templates">(() => {
    const initial = new URLSearchParams(window.location.search);
    if (initial.has("job")) return "workspace";
    if (initial.has("new") || initial.has("mock")) return "create";
    // 範本庫是自己的一頁,不是首頁捲到底的附屬區塊:它有自己的 URL,重整、
    // 分享、上一頁都成立。
    if (initial.has("templates")) return "templates";
    return "home";
  });
  const [jobId, setJobId] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  // Prompt text lives here (single source of truth) so it survives an error or a
  // completed run — PromptBar is controlled from this state.
  const [prompt, setPrompt] = useState("");
  const [discoveryRequest, setDiscoveryRequest] = useState<GenerateBody>();
  const [discoveryPlan, setDiscoveryPlan] = useState<DiscoveryPlan>();
  const [discoveryLoading, setDiscoveryLoading] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");
  const [discoveryProgress, setDiscoveryProgress] = useState<DiscoveryProgress[]>([]);
  // 大綱已確認、等待第一個 slide_done 的空窗:讓 narrator 報「逐頁填充」而非「等待確認」。
  const [fillingPending, setFillingPending] = useState(false);
  // ?job= 復原失敗(job 不存在/已過期)時,於輸入畫面報一行人話。
  const [expired, setExpired] = useState(false);
  // 目前開啟 lightbox 的頁碼(提升到 App,讓 FindingRow 點擊也能開該頁)。
  const [selectedN, setSelectedN] = useState<number | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsError, setSessionsError] = useState("");
  const { theme, setTheme } = useTheme();
  const cancelRef = useRef<() => void>();
  const discoveryAbortRef = useRef<AbortController>();
  // 上一次送出的 generate body,供錯誤區「重試」以同樣參數重送。
  const lastBodyRef = useRef<GenerateBody>();
  const params = new URLSearchParams(window.location.search);
  const mockMode = params.has("mock");
  const mockStep = Number(params.get("mockStep") ?? "250");

  async function loadSessions() {
    setSessionsLoading(true);
    setSessionsError("");
    try {
      setSessions(await getSessions());
    } catch (error) {
      setSessionsError(
        error instanceof Error ? error.message : "無法讀取工作紀錄。",
      );
    } finally {
      setSessionsLoading(false);
    }
  }

  useEffect(() => {
    if (view === "home") void loadSessions();
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    cancelRef.current?.();
    discoveryAbortRef.current?.abort();
  }, []);

  // 重整/斷線復原:URL 帶 ?job= 時直接重新訂閱事件流,後端會從頭重播全部事件
  // (outline → slide_done×N → …),reducer 天然重建狀態——含 awaiting_approval
  // 回到確認站、complete 回到可下載。傳輸層失敗(如 server 重啟 job 404)且尚未
  // 收到任何事件 → 視為過期:清 URL、回輸入畫面並提示。重播事件同樣走視覺節流器。
  useEffect(() => {
    const jobParam = params.get("job");
    if (!jobParam || mockMode) return;
    setView("workspace");
    setJobId(jobParam);
    setSubmitting(true); // 首個事件到達前顯示等待卡
    const throttle = createThrottledDispatch(dispatch);
    let received = false;
    const close = subscribeJob(
      jobParam,
      (e) => { received = true; throttle.push(e); },
      EventSource,
      (e) => {
        // 已在重播事件了才錯 → 屬於正常結束後的斷線,忽略;完全沒收到事件才算過期。
        if (received) return;
        // EventSource 在斷線後會「自動重連」:那段期間 readyState 為 CONNECTING(0),
        // 是暫時性錯誤,不該誤判 job 過期。只有 readyState 已 CLOSED(2)——真正放棄
        // 重連(如 job 404)——才視為過期。無 readyState 資訊時退回舊行為(視為過期)。
        const rs = (e as { target?: { readyState?: number } } | undefined)?.target?.readyState;
        if (rs != null && rs !== 2 /* EventSource.CLOSED */) return;
        close();
        throttle.cancel();
        cancelRef.current = undefined;
        history.replaceState(null, "", window.location.pathname);
        setJobId(undefined);
        setSubmitting(false);
        setExpired(true);
        setView("create");
      },
      // 連線(重)開啟 → 後端無 Last-Event-ID,必從 cursor 0 重播全部事件。
      // 清掉節流器的去重集合與待播佇列,讓重播完整流進 reducer(冪等重建牆)。
      throttle.reset,
    );
    cancelRef.current = () => { close(); throttle.cancel(); };
    return () => { close(); throttle.cancel(); };
    // 僅在掛載時執行一次(讀初始 URL);後續生成走 onGenerate。
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Clear the "submitting" indicator once the first SSE event moves us off "empty".
  useEffect(() => { if (state.phase !== "empty") setSubmitting(false); }, [state.phase]);
  // 一旦真的進入填充(generating)或被重置(empty),關掉「已確認等填充」空窗旗標。
  useEffect(() => {
    if (state.phase === "generating" || state.phase === "empty") setFillingPending(false);
  }, [state.phase]);

  async function onGenerate(body: GenerateBody) {
    cancelRef.current?.();
    discoveryAbortRef.current?.abort();
    discoveryAbortRef.current = undefined;
    setDiscoveryRequest(undefined);
    setDiscoveryPlan(undefined);
    setDiscoveryLoading(false);
    setDiscoveryError("");
    setDiscoveryProgress([]);
    setExpired(false);
    setSelectedN(null); // 新一輪生成:關掉任何殘留開著的 lightbox
    lastBodyRef.current = body;
    setSubmitting(true);
    setView("workspace");
    if (mockMode) { setJobId("mock"); cancelRef.current = playMock(dispatch, { step: mockStep }); return; }
    try {
      const { job_id } = await postGenerate(body);
      setJobId(job_id);
      // 把 jobId 寫進 URL,重整/斷線後可用 ?job= 重新訂閱重播復原(mock 模式不寫)。
      history.replaceState(null, "", `?job=${encodeURIComponent(job_id)}`);
      // 只有 SSE 事件流走視覺節流器;本地 dispatch(大綱重同步、重生)直接進 reducer。
      const throttle = createThrottledDispatch(dispatch);
      const closeSse = subscribeJob(
        job_id,
        throttle.push,
        EventSource,
        (e) => {
          // 與 ?job= 復原路徑同款生死判準:CONNECTING(0)是自動重連中的暫時錯誤,
          // 交給瀏覽器繼續試;readyState 已 CLOSED(2)= 放棄重連(如 server 重啟後
          // job 404)→ 不能讓 UI 永遠轉圈,收線並以 error 收尾(錯誤區可重試)。
          const rs = (e as { target?: { readyState?: number } } | undefined)?.target?.readyState;
          if (rs != null && rs !== 2 /* EventSource.CLOSED */) return;
          closeSse();
          // 走節流器的 DRAIN_FIRST:先排空已到的 slide,再收 error,牆保住已生成頁。
          throttle.push({ type: "error", data: { message: "與後端的事件串流已中斷（伺服器可能重啟過），請重試。", stage: "connect" } });
        },
        // 重連重播 → 重設節流器去重/佇列,讓 reducer 冪等重建(同 ?job= 復原路徑)。
        throttle.reset,
      );
      cancelRef.current = () => { closeSse(); throttle.cancel(); };
    } catch (error) {
      // 後端「有回應但拒絕」(如 429 同時工作數上限、422 驗證失敗)→ 原樣轉述
      // detail 與狀態碼,不誤導使用者去重啟伺服器;真的連不上(fetch 拋網路錯誤)
      // 才給連線提示。phase 進 error 後 PromptBar 會回來,使用者可重試。
      dispatch({
        type: "error",
        data: error instanceof ApiHttpError
          ? { message: error.message, stage: "request" }
          : { message: "無法連上後端,請確認 odforge serve 是否在執行", stage: "connect" },
      });
      setView("create");
    }
  }

  async function beginDiscovery(body: GenerateBody) {
    // 展示模式沒有後端可訪談:跳過訪談直接進 mock 生成流。否則第一下點擊就打
    // 真網路請求,死在「舊版後端」的誤導訊息裡,onGenerate 的 mock 分支永遠到不了。
    if (mockMode) {
      await onGenerate(body);
      return;
    }
    discoveryAbortRef.current?.abort();
    const controller = new AbortController();
    discoveryAbortRef.current = controller;
    setDiscoveryRequest(body);
    setDiscoveryPlan(undefined);
    setDiscoveryError("");
    setDiscoveryProgress([]);
    setDiscoveryLoading(true);
    try {
      const plan = await postDiscoveryQuestions(body, {
        signal: controller.signal,
        onProgress: (progress) => {
          setDiscoveryProgress((current) => {
            const previous = current[current.length - 1];
            if (previous?.stage === progress.stage) {
              return [...current.slice(0, -1), progress];
            }
            return [...current, progress];
          });
        },
      });
      setDiscoveryPlan(plan);
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return;
      setDiscoveryError(
        error instanceof Error ? error.message : "需求訪談暫時無法使用。",
      );
    } finally {
      if (discoveryAbortRef.current === controller) {
        discoveryAbortRef.current = undefined;
        setDiscoveryLoading(false);
      }
    }
  }

  function leaveDiscovery() {
    discoveryAbortRef.current?.abort();
    discoveryAbortRef.current = undefined;
    setDiscoveryRequest(undefined);
    setDiscoveryPlan(undefined);
    setDiscoveryLoading(false);
    setDiscoveryError("");
    setDiscoveryProgress([]);
  }

  function clearWorkspace() {
    cancelRef.current?.();
    cancelRef.current = undefined;
    discoveryAbortRef.current?.abort();
    discoveryAbortRef.current = undefined;
    setJobId(undefined);
    setSubmitting(false);
    setFillingPending(false);
    setExpired(false);
    setSelectedN(null);
    setDiscoveryRequest(undefined);
    setDiscoveryPlan(undefined);
    setDiscoveryLoading(false);
    setDiscoveryError("");
    setDiscoveryProgress([]);
    dispatch({ type: "reset" });
  }

  function startNew() {
    clearWorkspace();
    setPrompt("");
    replaceUrl(new URLSearchParams({ new: "1" }));
    setView("create");
  }

  function goHome() {
    clearWorkspace();
    replaceUrl(new URLSearchParams());
    setView("home");
  }

  function goTemplates() {
    clearWorkspace();
    replaceUrl(new URLSearchParams({ templates: "1" }));
    setView("templates");
  }

  // 「再鍛一份」/ 等待卡取消:關閉舊 SSE、丟掉待播佇列、清 jobId、重置 cockpit 回 empty。
  // prompt 文字刻意保留在輸入框(受控 state 不動),使用者可微調再送。
  function resetCockpit() {
    clearWorkspace();
    replaceUrl(new URLSearchParams({ new: "1" }));
    setView("create");
  }

  function cancelCurrentJob() {
    const currentJobId = jobId;
    if (currentJobId && currentJobId !== "mock") {
      // Best effort: reset the local UI immediately, while the backend stops all
      // remaining stages for this job. A request already sent to an LLM cannot
      // be recalled, but no later generation/render work will be started.
      void postCancel(currentJobId).catch(() => undefined);
    }
    resetCockpit();
  }

  // 錯誤區「重試」:清掉失敗殘留的 units/gates,再以同樣參數重送上一次 generate。
  function retryGenerate() {
    const body = lastBodyRef.current;
    if (!body) return;
    dispatch({ type: "reset" });
    onGenerate(body);
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

  // 頂欄常駐狀態 chip:展示模式(?mock)優先;否則連線失敗顯示「後端未連線」;正常不顯示。
  const disconnected = state.phase === "error" && state.error?.stage === "connect";
  const chip = mockMode
    ? { kind: "mock" as const, label: "展示模式" }
    : disconnected
    ? { kind: "offline" as const, label: "後端未連線" }
    : null;
  const taskName = taskNameFromOutline(state.outline);
  const home = view === "home" && !submitting && state.phase === "empty";
  const templates = view === "templates" && !submitting && state.phase === "empty";
  const composer = view === "create"
    && !submitting
    && (state.phase === "empty" || state.phase === "error");

  return (
    <div
      className="page"
      data-view={
        home || templates ? "home" : composer ? "welcome" : "workspace"
      }
    >
      <a className="skiplink" href="#main-content">跳到主要內容</a>

      <header className="sitebar">
        <div className="sitebar-inner">
          <button type="button" className="brand" aria-label="回到首頁" onClick={goHome}>
            <span className="brand-seal" aria-hidden="true">文</span>
            <span className="brand-copy">
              <span className="mark">文鍛</span>
              <span className="en">ODForge</span>
            </span>
          </button>
          <div className="sitebar-status">
            {chip && <span className="statuschip" data-kind={chip.kind}>{chip.label}</span>}
            <div className="themetoggle" role="group" aria-label="主題">
              <button aria-label="淺色主題" aria-pressed={theme === "light"} onClick={() => setTheme("light")}>
                <span aria-hidden="true">日</span>
              </button>
              <button aria-label="深色主題" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>
                <span aria-hidden="true">夜</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      {home || templates ? (
        <div className="shell-with-nav">
          <SideNav
            current={templates ? "templates" : "home"}
            onNavigate={(next) => (next === "templates" ? goTemplates() : goHome())}
            onNew={startNew}
          />
          {templates ? (
            <TemplatesPage />
          ) : (
            <HomeDashboard
              sessions={sessions}
              loading={sessionsLoading}
              error={sessionsError}
              onNew={startNew}
              onReload={() => void loadSessions()}
            />
          )}
        </div>
      ) : composer ? (
        <main className="welcome-shell" id="main-content">
          <span className="cockpit state-probe" data-outline="absent" hidden />
          <section className="composer-panel" aria-labelledby="composer-title">
            <div className="panel-intro">
              <h1 id="composer-title">
                {discoveryRequest ? "補齊需求" : "想做什麼簡報？"}
              </h1>
              <p>
                {discoveryRequest
                  ? "回答幾個關鍵問題，再生成大綱。"
                  : "先描述需求；缺少的資訊會再問你。"}
              </p>
            </div>
            {discoveryRequest ? (
              <DiscoveryPanel
                request={discoveryRequest}
                plan={discoveryPlan}
                loading={discoveryLoading}
                error={discoveryError}
                progress={discoveryProgress}
                onBack={leaveDiscovery}
                onRetry={() => void beginDiscovery(discoveryRequest)}
                onGenerate={onGenerate}
              />
            ) : (
              <PromptBar
                onDiscover={(body) => void beginDiscovery(body)}
                value={prompt}
                onValueChange={setPrompt}
              />
            )}
            {state.phase === "error" && (
              <ErrorPanel error={state.error} onRetry={lastBodyRef.current ? retryGenerate : undefined} />
            )}
            {expired && (
              <div className="welcome-status">
                <StatusNarrator
                  phase={state.phase}
                  units={state.units}
                  error={state.error}
                  submitting={submitting}
                  fillingPending={fillingPending}
                  expired={expired}
                />
              </div>
            )}
          </section>
        </main>
      ) : (
        <main className="workbench" id="main-content">
          <header className="workhead">
            <div className="workbrief">
              <span className="worklabel">目前任務</span>
              {/* 任務名稱用模型讀完需求後定的文件標題(封面頁),不是使用者那句原始輸入
                  ——輸入是「參考文件後製作一個…」這種指令句,當成任務名稱既冗長也不像
                  一份文件的名字。大綱還沒到之前才退回原句。title 屬性保留原始需求。 */}
              {/* 工作台的唯一 h1。這裡本來完全沒有 h1 —— 一個讀屏使用者跳到
                  「標題 1」會直接掠過整個工作台,或落在某個區塊標題上,沒有任何
                  一處說得出「你正在看的是哪一份文件」。 */}
              <h1 className="promptline" title={prompt || undefined}>
                <span className="pl-text">{taskName || prompt || "生成中的文件"}</span>
              </h1>
            </div>
            <div className="work-actions">
              <button type="button" className="work-home" onClick={goHome}>首頁</button>
              {state.phase === "complete" && (
                <button type="button" className="reforge" onClick={resetCockpit}>
                  <span aria-hidden="true">＋</span> 再鍛一份
                </button>
              )}
            </div>
          </header>

          {/* data-outline 讓 CSS 在大綱未到前收掉左欄,避免空白直條。
              data-phase 讓窄螢幕能依階段換欄位順序:等待確認時,承載 CTA 的大綱欄
              必須排在縮圖牆之前——12 頁的骨架牆會把「就這樣鍛」推到數千像素之後,
              使用者看到的是一個沒有按鈕、像是卡住的畫面。 */}
          <div
            className="cockpit"
            data-outline={state.outline ? "present" : "absent"}
            data-phase={state.phase}
          >
          <OutlineRail outline={state.outline} phase={state.phase} onConfirm={onConfirmOutline} />
          <PreviewStage
            units={state.units}
            docType={state.docType}
            jobId={jobId}
            dispatch={dispatch}
            submitting={submitting}
            onCancel={cancelCurrentJob}
            selectedN={selectedN}
            onSelect={setSelectedN}
          />
          {/* 生成中途出錯:錯誤面板直接壓在舞台區上——牆不卸載(已生成頁留著),
              但錯誤與「重試」必須是看得見的第一層,不能只躲在 narrator 的 hover 裡。 */}
          {state.phase === "error" && (
            <div className="stage-error">
              <ErrorPanel error={state.error} onRetry={lastBodyRef.current ? retryGenerate : undefined} />
            </div>
          )}
          <GateRail gates={state.gates} gateNotes={state.gateNotes} qaRounds={state.qaRounds} dropped={state.dropped} onOpenFinding={state.phase === "error" ? undefined : (n) => setSelectedN(n)} />
          </div>

          <footer className="foot">
            <StatusNarrator phase={state.phase} units={state.units} error={state.error} submitting={submitting} fillingPending={fillingPending} expired={expired} />
            <DownloadDock jobId={jobId} downloadUrl={state.downloadUrl} docType={state.docType} phase={state.phase} />
          </footer>
        </main>
      )}
    </div>
  );
}
