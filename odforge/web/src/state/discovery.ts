import type { DiscoveryPlan } from "./api";

export type DiscoveryAnswers = Record<string, string>;

export function buildRefinedPrompt(
  originalPrompt: string,
  plan: DiscoveryPlan,
  answers: DiscoveryAnswers,
): string {
  const known = plan.known_context.length
    ? plan.known_context.map((item) => `- ${item}`).join("\n")
    : "- 無額外已知資訊";
  const interview = plan.questions
    .map((question) => {
      const answer = answers[question.id]?.trim() || "尚未確認，請採保守假設並明確標示";
      return `- ${question.question}\n  回答：${answer}`;
    })
    .join("\n");

  return [
    "【簡報任務】",
    originalPrompt.trim(),
    "",
    "【AI 對任務的理解】",
    plan.summary.trim(),
    "",
    "【已確認資訊】",
    known,
    "",
    "【訪談補充】",
    interview,
    "",
    "【製作原則】",
    "- 每頁只傳達一個明確結論，內容必須回應上述受眾與目的。",
    "- 優先使用使用者提供的事實、素材與時程；不得自行捏造數據或成果。",
    "- 資訊仍不確定時，以可編輯的保守敘述呈現，不要用空泛口號填頁。",
  ].join("\n");
}
