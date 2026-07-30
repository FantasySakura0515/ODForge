import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { DiscoveryPlan, GenerateBody } from "../state/api";
import { DiscoveryPanel } from "./DiscoveryPanel";

const request: GenerateBody = {
  prompt: "向系上老師提案的畢業專題進度報告",
  doc_type: "odp",
  mode: "presenter",
  interactive: true,
  qa: false,
};

const plan: DiscoveryPlan = {
  summary: "向系上老師報告畢業專題進度。",
  known_context: ["受眾是系上老師"],
  questions: [
    {
      id: "decision",
      question: "這次希望老師提供什麼？",
      why: "決定簡報最後的行動請求。",
      options: ["確認進度", "提供技術建議"],
    },
    {
      id: "progress",
      question: "目前完成到哪個階段？",
      why: "才能把時程與風險說具體。",
      options: ["核心功能開發中", "進入測試"],
    },
  ],
  completeness: 40,
};

test("逐題回答後顯示可編輯 Brief，確認才送出生成", () => {
  const onGenerate = vi.fn();
  render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={onGenerate}
    />,
  );

  expect(screen.getByText("這次希望老師提供什麼？")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "提供技術建議" }));
  fireEvent.click(screen.getByRole("button", { name: "下一題" }));

  expect(screen.getByText("目前完成到哪個階段？")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "自訂回答" }), {
    target: { value: "登入與資料庫已完成，推薦模組開發中" },
  });
  fireEvent.click(screen.getByRole("button", { name: "整理需求" }));

  const brief = screen.getByRole("textbox", { name: "生成規格" }) as HTMLTextAreaElement;
  expect(brief.value).toContain("回答：提供技術建議");
  expect(brief.value).toContain("推薦模組開發中");
  fireEvent.change(brief, { target: { value: `${brief.value}\n- Demo 日期：6 月 20 日` } });
  fireEvent.click(screen.getByRole("button", { name: "生成大綱" }));

  expect(onGenerate).toHaveBeenCalledTimes(1);
  expect(onGenerate.mock.calls[0][0]).toMatchObject({
    doc_type: "odp",
    mode: "presenter",
  });
  expect(onGenerate.mock.calls[0][0].prompt).toContain("Demo 日期：6 月 20 日");
});

test("不確定也是明確答案，不會讓使用者卡住", () => {
  render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );

  const next = screen.getByRole("button", { name: "下一題" });
  expect(next).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "不確定" }));
  expect(next).toBeEnabled();
  expect(screen.getByRole("textbox", { name: "自訂回答" })).toHaveValue(
    "尚不確定，請依現有資訊保守建議",
  );
});

test("載入與錯誤狀態提供清楚回復動作", () => {
  const { rerender } = render(
    <DiscoveryPanel
      request={request}
      loading
      progress={[
        {
          request_id: "req-1",
          stage: "requesting",
          message: "正在產生關鍵問題",
          elapsed_ms: 3200,
        },
      ]}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );
  expect(screen.getByText("正在整理需求")).toBeInTheDocument();
  expect(screen.getByText("正在產生關鍵問題")).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "AI 讀題進度" })).toBeInTheDocument();
  expect(screen.getByText("產生追問").closest("li")).toHaveAttribute(
    "data-status",
    "active",
  );
  expect(screen.getByText(/不顯示模型內部推理/)).toBeInTheDocument();

  const retry = vi.fn();
  const back = vi.fn();
  rerender(
    <DiscoveryPanel
      request={request}
      error="模型暫時無法使用"
      onBack={back}
      onRetry={retry}
      onGenerate={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  fireEvent.click(screen.getByRole("button", { name: "修改需求" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(back).toHaveBeenCalledOnce();
});
