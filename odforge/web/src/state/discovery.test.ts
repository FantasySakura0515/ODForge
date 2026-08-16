import { describe, expect, test } from "vitest";
import type { DiscoveryPlan } from "./api";
import { buildRefinedPrompt } from "./discovery";

const plan: DiscoveryPlan = {
  summary: "向老師報告專題進度。",
  known_context: ["受眾是系上老師"],
  questions: [
    {
      id: "decision",
      question: "希望老師提供什麼？",
      why: "決定行動請求。",
      options: ["確認進度", "技術建議"],
    },
    {
      id: "progress",
      question: "目前做到哪裡？",
      why: "讓時程具體。",
      options: ["開發中", "測試中"],
    },
  ],
  completeness: 40,
};

describe("discovery brief", () => {
  test("回答會被整理成可直接交給 outline 模型的規格", () => {
    const prompt = buildRefinedPrompt("畢業專題進度報告", plan, {
      decision: "提供技術建議",
      progress: "核心功能開發中",
    });

    expect(prompt).toContain("【簡報任務】");
    expect(prompt).toContain("畢業專題進度報告");
    expect(prompt).toContain("受眾是系上老師");
    expect(prompt).toContain("回答：提供技術建議");
    expect(prompt).toContain("不得自行捏造數據");
  });

  test("未回答的題目在規格裡標成保守假設,不假裝有答案", () => {
    const prompt = buildRefinedPrompt("畢業專題進度報告", plan, {
      decision: "提供技術建議",
    });

    expect(prompt).toContain("尚未確認，請採保守假設並明確標示");
  });
});
