import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { PromptBar } from "./PromptBar";

test("空 prompt 時送出鈕停用", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  expect(screen.getByRole("button", { name: /鍛造/ })).toBeDisabled();
});

test("輸入後送出帶預設 body:odp/presenter/interactive 開/qa 關/無 theme 無 pages", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  const body = onGenerate.mock.calls[0][0];
  expect(body).toMatchObject({ prompt: "樹與二元樹", doc_type: "odp", mode: "presenter", interactive: true, qa: false });
  expect("theme" in body).toBe(false);
  expect("pages" in body).toBe(false);
});

test("odt/ods 分頁停用(disabled + aria-disabled)且標示「即將支援」", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  const odt = screen.getByRole("tab", { name: /文書/ });
  const ods = screen.getByRole("tab", { name: /試算表/ });
  expect(odt).toBeDisabled();
  expect(ods).toBeDisabled();
  expect(odt).toHaveAttribute("aria-disabled", "true");
  expect(ods).toHaveAttribute("aria-disabled", "true");
  expect(screen.getAllByText(/即將支援/).length).toBeGreaterThan(0);
});

test("點停用的文書分頁無效果,送出仍為 odp", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "實驗報告" } });
  fireEvent.click(screen.getByRole("tab", { name: /文書/ }));
  // 分頁未切換,odp 仍為選中
  expect(screen.getByRole("tab", { name: /簡報/ })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  // 停用分頁切不過去,送出仍為 odp(新簽名:單一 body 物件)
  expect(onGenerate.mock.calls[0][0]).toMatchObject({ prompt: "實驗報告", doc_type: "odp" });
});

test("進階抽屜的選項如實進 body(mode/theme/pages/qa/interactive)", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /進階選項/ }));
  fireEvent.click(screen.getByLabelText(/自讀型/));
  fireEvent.click(screen.getByLabelText(/^dark$/i));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  fireEvent.click(screen.getByLabelText(/設計 QA/));
  fireEvent.click(screen.getByLabelText(/大綱確認/)); // 預設開 → 關
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect(onGenerate.mock.calls[0][0]).toMatchObject({
    prompt: "樹", mode: "detailed", theme: "dark", pages: 12, qa: true, interactive: false,
  });
});

test("頁數超界時顯示即時提示,且送出不帶 pages 欄位", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /進階選項/ }));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "100" } });
  // 即時提示出現
  expect(screen.getByRole("alert").textContent).toMatch(/3.*30/);
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect("pages" in onGenerate.mock.calls[0][0]).toBe(false);
});

test("受眾與語氣填入抽屜後附加進 prompt", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /進階選項/ }));
  fireEvent.change(screen.getByLabelText(/受眾/), { target: { value: "大一新生" } });
  fireEvent.change(screen.getByLabelText(/語氣/), { target: { value: "親切" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  const body = onGenerate.mock.calls[0][0];
  expect(body.prompt).toContain("受眾:大一新生");
  expect(body.prompt).toContain("語氣:親切");
});

test("收合時偏離預設的選項以 chip 摘要顯示", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /進階選項/ }));
  fireEvent.click(screen.getByLabelText(/^dark$/i));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  fireEvent.click(screen.getByLabelText(/設計 QA/));
  // 收合抽屜
  fireEvent.click(screen.getByRole("button", { name: /進階選項/ }));
  const summary = screen.getByTestId("adv-summary");
  expect(summary.textContent).toContain("12 頁");
  expect(summary.textContent).toContain("dark");
  expect(summary.textContent).toContain("QA");
});

test("預設狀態下收合不顯示 chip 摘要", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  expect(screen.queryByTestId("adv-summary")).toBeNull();
});

test("placeholder 鼓勵細節,不再教人用一句話", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.placeholder).not.toContain("一句話");
  expect(ta.placeholder).toMatch(/愈具體|越具體|具體/);
});

test("點範例 chip 填入 textarea", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  const chips = screen.getAllByTestId("example-chip");
  expect(chips.length).toBeGreaterThanOrEqual(2);
  fireEvent.click(chips[0]);
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.value).toBe(chips[0].textContent);
});

test("Ctrl+Enter 送出;Enter 換行不送;空白不送", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  const ta = screen.getByRole("textbox", { name: /主題/ });
  // 空白時 Ctrl+Enter 不動作
  fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
  expect(onGenerate).not.toHaveBeenCalled();
  // 有內容:Enter 單獨不送
  fireEvent.change(ta, { target: { value: "資料結構教學" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  expect(onGenerate).not.toHaveBeenCalled();
  // Ctrl+Enter 送出
  fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
  expect(onGenerate).toHaveBeenCalledTimes(1);
  // Cmd+Enter(metaKey)也送出
  fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
  expect(onGenerate).toHaveBeenCalledTimes(2);
});
