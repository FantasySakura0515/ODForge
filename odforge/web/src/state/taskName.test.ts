import { expect, test } from "vitest";
import type { Outline } from "./types";
import { taskNameFromOutline } from "./taskName";

function outline(pages: { role: string; title: string }[]): Outline {
  return {
    design: null,
    mode: "presenter",
    pages: pages.map((page) => ({ ...page, gist: "..." })),
  };
}

test("任務名稱取封面頁標題,不是第一頁碰巧是誰", () => {
  const name = taskNameFromOutline(
    outline([
      { role: "agenda", title: "今日議程" },
      { role: "title", title: "助理申請與日誌填寫操作指南" },
    ]),
  );

  expect(name).toBe("助理申請與日誌填寫操作指南");
});

test("沒有封面頁就退回第一頁標題", () => {
  expect(taskNameFromOutline(outline([{ role: "agenda", title: "今日議程" }]))).toBe(
    "今日議程",
  );
});

test("大綱還沒到或標題空白 → undefined(呼叫端退回原始需求)", () => {
  expect(taskNameFromOutline(undefined)).toBeUndefined();
  expect(taskNameFromOutline(outline([{ role: "title", title: "   " }]))).toBeUndefined();
});
