import { fireEvent, render, screen, within } from "@testing-library/react";
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

test("不顯示估算完整度：進度條講的是問到第幾題", () => {
  const { container } = render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );

  expect(screen.queryByText(/% 完整/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/完整度/)).not.toBeInTheDocument();
  // 兩題中的第一題 → 一半。
  expect(container.querySelector(".discovery-meter span")).toHaveStyle({ width: "50%" });
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

test("選項都不對時有「其他」:清掉選擇並把焦點送到自行補充", () => {
  render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "確認進度" }));
  const other = screen.getByRole("button", { name: "其他（自行填寫）" });
  fireEvent.click(other);

  const custom = screen.getByRole("textbox", { name: "自訂回答" });
  expect(custom).toHaveValue("");
  expect(custom).toHaveFocus();
  expect(screen.getByRole("button", { name: "確認進度" })).toHaveAttribute("aria-pressed", "false");

  // 自己打的字 → 「其他」才算被選中;再按一次不會把打好的字清掉。
  fireEvent.change(custom, { target: { value: "希望老師同意換題目" } });
  expect(other).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(other);
  expect(custom).toHaveValue("希望老師同意換題目");

  // 「不確定」是明確答案,不該讓「其他」跟著亮起來。
  fireEvent.click(screen.getByRole("button", { name: "不確定" }));
  expect(other).toHaveAttribute("aria-pressed", "false");
});

test("修改答案回去再回來(答案沒變):手改過的 Brief 保留,不被無聲重建蓋掉", () => {
  render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "提供技術建議" }));
  fireEvent.click(screen.getByRole("button", { name: "下一題" }));
  fireEvent.click(screen.getByRole("button", { name: "核心功能開發中" }));
  fireEvent.click(screen.getByRole("button", { name: "整理需求" }));

  const brief = screen.getByRole("textbox", { name: "生成規格" }) as HTMLTextAreaElement;
  fireEvent.change(brief, { target: { value: "我自己整理的最終規格" } });

  // 回去看答案但一個都沒改,再回 Brief。
  fireEvent.click(screen.getByRole("button", { name: "修改答案" }));
  fireEvent.click(screen.getByRole("button", { name: "整理需求" }));

  expect((screen.getByRole("textbox", { name: "生成規格" }) as HTMLTextAreaElement).value).toBe(
    "我自己整理的最終規格",
  );
});

test("修改答案且真的改了 → Brief 依新答案重建(明示放棄舊手改)", () => {
  render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "提供技術建議" }));
  fireEvent.click(screen.getByRole("button", { name: "下一題" }));
  fireEvent.click(screen.getByRole("button", { name: "核心功能開發中" }));
  fireEvent.click(screen.getByRole("button", { name: "整理需求" }));

  fireEvent.change(screen.getByRole("textbox", { name: "生成規格" }), {
    target: { value: "我自己整理的最終規格" },
  });
  fireEvent.click(screen.getByRole("button", { name: "修改答案" }));
  fireEvent.click(screen.getByRole("button", { name: "進入測試" })); // 改了最後一題的答案
  fireEvent.click(screen.getByRole("button", { name: "整理需求" }));

  const brief = screen.getByRole("textbox", { name: "生成規格" }) as HTMLTextAreaElement;
  expect(brief.value).toContain("回答：進入測試");
  expect(brief.value).not.toContain("我自己整理的最終規格");
});

test("Ctrl/⌘+Enter:自訂回答=下一題/整理需求,生成規格=生成大綱", () => {
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

  const answer = screen.getByRole("textbox", { name: "自訂回答" });
  fireEvent.change(answer, { target: { value: "希望老師確認方向" } });
  fireEvent.keyDown(answer, { key: "Enter", ctrlKey: true });
  // 到第二題(與「下一題」鈕同效)。
  expect(screen.getByText("目前完成到哪個階段？")).toBeInTheDocument();

  const answer2 = screen.getByRole("textbox", { name: "自訂回答" });
  fireEvent.change(answer2, { target: { value: "開發中" } });
  fireEvent.keyDown(answer2, { key: "Enter", metaKey: true });

  // 最後一題的 Ctrl+Enter = 整理需求 → Brief;Brief 上的 Ctrl+Enter = 生成大綱。
  const brief = screen.getByRole("textbox", { name: "生成規格" });
  fireEvent.keyDown(brief, { key: "Enter", ctrlKey: true });
  expect(onGenerate).toHaveBeenCalledTimes(1);
  expect(onGenerate.mock.calls[0][0].prompt).toContain("回答：開發中");
});

test("Ctrl/⌘+Enter 空答案不前進(與鈕的 disabled 同一守門)", () => {
  render(
    <DiscoveryPanel
      request={request}
      plan={plan}
      onBack={vi.fn()}
      onRetry={vi.fn()}
      onGenerate={vi.fn()}
    />,
  );
  const answer = screen.getByRole("textbox", { name: "自訂回答" });
  fireEvent.keyDown(answer, { key: "Enter", ctrlKey: true });
  // 還在第一題。
  expect(screen.getByText("這次希望老師提供什麼？")).toBeInTheDocument();
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
  const trace = screen.getByRole("list", { name: "AI 讀題進度" });
  expect(within(trace).getByText("產生追問").closest("li")).toHaveAttribute(
    "data-status",
    "active",
  );
  // 只有離散階段進 live region;秒數計時器整個退出無障礙樹。
  expect(
    document.querySelector('.discovery-loading [role="status"][aria-live="polite"]')
      ?.textContent,
  ).toBe("產生追問");
  expect(document.querySelector(".discovery-elapsed")).toHaveAttribute(
    "aria-hidden",
    "true",
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
