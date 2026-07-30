import { useEffect, useMemo, useState } from "react";
import type {
  DiscoveryPlan,
  DiscoveryProgress,
  DiscoveryStage,
  GenerateBody,
} from "../state/api";
import {
  buildRefinedPrompt,
  discoveryCompleteness,
  type DiscoveryAnswers,
} from "../state/discovery";

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
  const [elapsedMs, setElapsedMs] = useState(0);
  const latestProgress = progress[progress.length - 1];

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
    }
  }, [plan, request.prompt]);

  const question = plan?.questions[step];
  const currentAnswer = question ? answers[question.id] ?? "" : "";
  const completeness = useMemo(
    () => (plan ? discoveryCompleteness(plan, answers) : 0),
    [answers, plan],
  );

  function setAnswer(value: string) {
    if (!question) return;
    setAnswers((current) => ({ ...current, [question.id]: value }));
  }

  function advance() {
    if (!plan || !question || !currentAnswer.trim()) return;
    if (step === plan.questions.length - 1) {
      setBrief(buildRefinedPrompt(request.prompt, plan, answers));
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
    return (
      <section className="discovery discovery-loading" aria-live="polite">
        <header className="discovery-loading-head">
          <div className="discovery-orbit" aria-hidden="true">
            <span />
            <i />
          </div>
          <div>
            <span className="discovery-kicker">讀題進度</span>
            <h3>正在整理需求</h3>
            <p>{latestProgress?.message ?? "正在建立讀題工作…"}</p>
          </div>
          <output className="discovery-elapsed" aria-label={`已等待 ${elapsedSeconds} 秒`}>
            <strong>{String(elapsedSeconds).padStart(2, "0")}</strong>
            <span>sec</span>
          </output>
        </header>

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
                <span>
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
        <h3>問題產生失敗</h3>
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

  return (
    <section className="discovery" aria-labelledby="discovery-title">
      <header className="discovery-head">
        <div>
          <span className="discovery-kicker">
            {reviewing ? "需求摘要" : `第 ${String(step + 1).padStart(2, "0")} 題`}
          </span>
          <h3 id="discovery-title">{reviewing ? "確認生成內容" : plan.summary}</h3>
        </div>
        <div className="brief-score" aria-label={`需求完整度 ${completeness}%`}>
          <strong>{completeness}</strong>
          <span>% 完整</span>
        </div>
      </header>

      <div className="discovery-meter" aria-hidden="true">
        <span style={{ width: `${completeness}%` }} />
      </div>

      {reviewing ? (
        <div className="brief-review">
          <p>可直接修改；確認後會用這份內容生成大綱。</p>
          <textarea
            aria-label="生成規格"
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
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
            <h4>{activeQuestion.question}</h4>
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
          </div>

          <label className="custom-answer">
            <span>自行補充</span>
            <textarea
              aria-label="自訂回答"
              placeholder="補充時程、數據或限制…"
              value={currentAnswer}
              onChange={(event) => setAnswer(event.target.value)}
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
              onClick={() => setAnswer("尚不確定，請依現有資訊保守建議")}
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
