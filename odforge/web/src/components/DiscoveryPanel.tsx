import { useEffect, useRef, useState } from "react";
import type {
  DiscoveryPlan,
  DiscoveryProgress,
  DiscoveryStage,
  GenerateBody,
} from "../state/api";
import { buildRefinedPrompt, type DiscoveryAnswers } from "../state/discovery";

interface DiscoveryPanelProps {
  request: GenerateBody;
  plan?: DiscoveryPlan;
  loading?: boolean;
  error?: string;
  progress?: DiscoveryProgress[];
  onBack: () => void;
  onRetry: () => void;
  onGenerate: (body: GenerateBody) => void;
}

// 「不確定」是一個明確答案(會寫進 Brief),「其他」不是——它只是把使用者送去
// 自行補充。兩者要分開判斷,不然按了「不確定」之後「其他」也會亮成已選。
const UNSURE_ANSWER = "尚不確定，請依現有資訊保守建議";

const LOADING_STEPS = [
  { label: "讀取資料", detail: "設定與參考文件" },
  { label: "產生追問", detail: "只問關鍵資訊" },
  { label: "檢查內容", detail: "去除重複與模糊選項" },
  { label: "完成", detail: "開始訪談" },
];

function activeLoadingStep(stage?: DiscoveryStage): number {
  if (stage === "requesting" || stage === "retrying" || stage === "waiting") return 1;
  if (stage === "validating") return 2;
  if (stage === "complete") return 3;
  return 0;
}

export function DiscoveryPanel({
  request,
  plan,
  loading = false,
  error,
  progress = [],
  onBack,
  onRetry,
  onGenerate,
}: DiscoveryPanelProps) {
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<DiscoveryAnswers>({});
  const [brief, setBrief] = useState("");
  // 使用者是否手改過 Brief,以及 Brief 是用哪組答案組出來的。「修改答案」回去又
  // 回來時,若答案沒變就保留手改內容——不然辛苦編的 Brief 會被無聲重建蓋掉。
  const [briefEdited, setBriefEdited] = useState(false);
  const briefBuiltFrom = useRef<string>();
  const [elapsedMs, setElapsedMs] = useState(0);
  const latestProgress = progress[progress.length - 1];
  // 換題與進入 Brief 時,焦點必須跟著搬家。不搬的話,鍵盤與讀屏使用者按下「下一題」
  // 之後焦點還留在原地(甚至掉回 body),畫面已經換了一題卻沒有任何提示——他們得
  // 從頭 Tab 一次才找得到新題目。
  const questionHeadingRef = useRef<HTMLHeadingElement>(null);
  const briefHeadingRef = useRef<HTMLHeadingElement>(null);
  const customAnswerRef = useRef<HTMLTextAreaElement>(null);
  const stepEverChanged = useRef(false);

  useEffect(() => {
    // 第一次渲染不搶焦點(使用者可能還在讀題目);只有真的換了一題才移動。
    if (!stepEverChanged.current) {
      stepEverChanged.current = true;
      return;
    }
    (briefHeadingRef.current ?? questionHeadingRef.current)?.focus();
  }, [step]);

  useEffect(() => {
    if (!loading) {
      setElapsedMs(0);
      return;
    }
    const startedAt = Date.now() - (latestProgress?.elapsed_ms ?? 0);
    const tick = () => setElapsedMs(Date.now() - startedAt);
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [loading, latestProgress?.request_id]);

  useEffect(() => {
    if (plan?.questions.length === 0) {
      setBrief(buildRefinedPrompt(request.prompt, plan, {}));
      briefBuiltFrom.current = JSON.stringify({});
      setBriefEdited(false);
    }
  }, [plan, request.prompt]);

  const question = plan?.questions[step];
  const currentAnswer = question ? answers[question.id] ?? "" : "";

  function setAnswer(value: string) {
    if (!question) return;
    setAnswers((current) => ({ ...current, [question.id]: value }));
  }

  function advance() {
    if (!plan || !question || !currentAnswer.trim()) return;
    if (step === plan.questions.length - 1) {
      const key = JSON.stringify(answers);
      // 答案沒變且使用者手改過 Brief → 保留手改;答案變了才依新答案重建。
      if (!(briefEdited && briefBuiltFrom.current === key)) {
        setBrief(buildRefinedPrompt(request.prompt, plan, answers));
        briefBuiltFrom.current = key;
        setBriefEdited(false);
      }
      setStep(plan.questions.length);
      return;
    }
    setStep((current) => current + 1);
  }

  if (loading) {
    const activeStep = activeLoadingStep(latestProgress?.stage);
    const elapsedSeconds = Math.max(
      Math.round(elapsedMs / 1000),
      Math.round((latestProgress?.elapsed_ms ?? 0) / 1000),
    );
    // 整個 section 設 aria-live 會連秒數計時器一起播報(每秒一次)。live region
    // 縮到下面那一行只在階段改變時才變的文字。
    return (
      <section className="discovery discovery-loading">
        <header className="discovery-loading-head">
          <div className="discovery-orbit" aria-hidden="true">
            <span />
          </div>
          <div>
            <span className="discovery-kicker">讀題進度</span>
            <h2>正在整理需求</h2>
            <p>{latestProgress?.message ?? "正在建立讀題工作…"}</p>
          </div>
          <output className="discovery-elapsed" aria-hidden="true">
            <strong>{String(elapsedSeconds).padStart(2, "0")}</strong>
            <span>sec</span>
          </output>
        </header>

        <span className="sr-only" role="status" aria-live="polite">
          {LOADING_STEPS[activeStep]?.label ?? "讀取資料"}
        </span>

        <ol className="discovery-trace" aria-label="AI 讀題進度">
          {LOADING_STEPS.map((item, index) => {
            const status = index < activeStep
              ? "done"
              : index === activeStep
                ? "active"
                : "pending";
            return (
              <li key={item.label} data-status={status}>
                <span className="trace-mark" aria-hidden="true">
                  {status === "done" ? "✓" : String(index + 1).padStart(2, "0")}
                </span>
                {/* 連接線是獨立元素、住在圓點那一列的專屬格子裡。舊版用絕對定位的
                    ::after 橫過整個文字欄,結果那條灰線壓在「讀取資料」上像刪除線,
                    而且四個階段從頭到尾同一個灰——看起來就是一條卡住的線。 */}
                {index < LOADING_STEPS.length - 1 && (
                  <span className="trace-line" aria-hidden="true" />
                )}
                <span className="trace-copy">
                  <b>{item.label}</b>
                  <small>{item.detail}</small>
                </span>
              </li>
            );
          })}
        </ol>

        <div className="discovery-live-note">
          <span className="live-dot" aria-hidden="true" />
          <p>
            {elapsedSeconds >= 15
              ? "回覆較慢，但服務仍在線。"
              : "只顯示工作狀態，不顯示模型內部推理。"}
          </p>
          <button type="button" className="text-action" onClick={onBack}>
            取消
          </button>
        </div>
      </section>
    );
  }

  if (error || !plan) {
    return (
      <section className="discovery discovery-error" role="alert">
        <span className="discovery-kicker">訪談中斷</span>
        <h2>問題產生失敗</h2>
        <p>{error ?? "沒有收到訪談內容。"}</p>
        <div className="discovery-actions">
          <button type="button" className="forge compact" onClick={onRetry}>
            <span>重試</span><span className="forge-arrow" aria-hidden="true">↻</span>
          </button>
          <button type="button" className="text-action" onClick={onBack}>修改需求</button>
        </div>
      </section>
    );
  }

  const reviewing = step >= plan.questions.length;

  const activeQuestion = question ?? plan.questions[0];
  // 選項是模型給的猜測,不見得涵蓋使用者的情況。沒有「其他」時,唯一的出口是
  // 下面那塊不起眼的「自行補充」,使用者只會在四個都不對的選項裡硬挑一個。
  const answeringOther =
    currentAnswer.trim() !== "" &&
    currentAnswer !== UNSURE_ANSWER &&
    !activeQuestion.options.includes(currentAnswer);
  // 進度條說的是「問到第幾題」——一個使用者查得證的事實,不是估出來的完整度。
  const askedRatio = reviewing ? 1 : (step + 1) / plan.questions.length;

  return (
    <section className="discovery" aria-labelledby="discovery-title">
      <header className="discovery-head">
        <div>
          <span className="discovery-kicker">
            {reviewing ? "需求摘要" : `第 ${String(step + 1).padStart(2, "0")} 題`}
          </span>
          <h2
            id="discovery-title"
            tabIndex={-1}
            // 只有在 Brief 階段才當作焦點目標;問答階段的焦點屬於題目本身。
            ref={reviewing ? briefHeadingRef : undefined}
          >
            {reviewing ? "確認生成內容" : plan.summary}
          </h2>
        </div>
      </header>

      <div className="discovery-meter" aria-hidden="true">
        <span style={{ width: `${Math.round(askedRatio * 100)}%` }} />
      </div>

      {reviewing ? (
        <div className="brief-review">
          <p>可直接修改；確認後會用這份內容生成大綱。</p>
          <textarea
            aria-label="生成規格"
            value={brief}
            onChange={(event) => {
              setBrief(event.target.value);
              setBriefEdited(true);
            }}
            onKeyDown={(event) => {
              // Ctrl/Cmd+Enter = 生成大綱(與 PromptBar 同一套快捷鍵)。
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                if (brief.trim()) onGenerate({ ...request, prompt: brief.trim() });
              }
            }}
          />
          <div className="discovery-actions end">
            <button
              type="button"
              className="forge"
              disabled={!brief.trim()}
              onClick={() => onGenerate({ ...request, prompt: brief.trim() })}
            >
              <span>生成大綱</span>
              <span className="forge-arrow" aria-hidden="true">↗</span>
            </button>
            <button
              type="button"
              className="text-action"
              onClick={() => setStep(Math.max(0, plan.questions.length - 1))}
            >
              修改答案
            </button>
          </div>
        </div>
      ) : (
        <div className="question-card">
          <div className="question-copy">
            <span className="question-count">{step + 1} / {plan.questions.length}</span>
            <h3 id="discovery-question" tabIndex={-1} ref={questionHeadingRef}>
              {activeQuestion.question}
            </h3>
            <p>{activeQuestion.why}</p>
          </div>

          <div className="answer-options" role="group" aria-label={activeQuestion.question}>
            {activeQuestion.options.map((option) => (
              <button
                type="button"
                key={option}
                aria-pressed={currentAnswer === option}
                className={currentAnswer === option ? "answer-option selected" : "answer-option"}
                onClick={() => setAnswer(option)}
              >
                <span aria-hidden="true" />
                {option}
              </button>
            ))}
            <button
              type="button"
              aria-pressed={answeringOther}
              className={answeringOther ? "answer-option other selected" : "answer-option other"}
              onClick={() => {
                // 已經在自行填寫時再按一次,不該把打好的字清掉——只把焦點送回去。
                if (!answeringOther) setAnswer("");
                customAnswerRef.current?.focus();
              }}
            >
              <span aria-hidden="true" />
              其他（自行填寫）
            </button>
          </div>

          <label className="custom-answer">
            <span>自行補充</span>
            <textarea
              ref={customAnswerRef}
              aria-label="自訂回答"
              placeholder="補充具體內容、數據或限制…"
              value={currentAnswer}
              onChange={(event) => setAnswer(event.target.value)}
              onKeyDown={(event) => {
                // Ctrl/Cmd+Enter = 下一題/整理需求(advance 自帶空答案守門)。
                if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  advance();
                }
              }}
            />
          </label>

          <div className="discovery-actions">
            <button type="button" className="text-action" onClick={onBack}>修改原始需求</button>
            {step > 0 && (
              <button type="button" className="text-action" onClick={() => setStep((current) => current - 1)}>
                上一題
              </button>
            )}
            <button
              type="button"
              className="unsure-action"
              onClick={() => setAnswer(UNSURE_ANSWER)}
            >
              不確定
            </button>
            <button type="button" className="next-question" disabled={!currentAnswer.trim()} onClick={advance}>
              {step === plan.questions.length - 1 ? "整理需求" : "下一題"}
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
