import { expect, test } from "vitest";
import { humanizeStage } from "./errors";

test("大綱確認逾時與取消有專屬白話標題,不落到「發生錯誤」", () => {
  // 後端 stage 對照:確認站 30 分鐘逾時 → outline_approval;主動取消 → cancel。
  expect(humanizeStage("outline_approval")).toBe("大綱確認逾時，請重新生成");
  expect(humanizeStage("cancel")).toBe("已取消這次生成");
});

test("後端拒絕請求(429/422 等)用 request 標題,與「連不上」區分", () => {
  expect(humanizeStage("request")).toBe("後端拒絕了這次請求");
  expect(humanizeStage("connect")).toBe("無法連上後端");
});

test("未知 stage 才退回通用「發生錯誤」", () => {
  expect(humanizeStage("nonsense")).toBe("發生錯誤");
  expect(humanizeStage(undefined)).toBe("發生錯誤");
});
