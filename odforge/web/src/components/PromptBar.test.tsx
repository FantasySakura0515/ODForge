import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { PromptBar } from "./PromptBar";

test("空 prompt 時送出鈕停用", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.getByRole("button", { name: "繼續" })).toBeDisabled();
});

test("輸入後送出帶預設 body:odp/presenter/interactive 開/qa 開/無 theme 無 pages", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  const body = onDiscover.mock.calls[0][0];
  expect(body).toMatchObject({ prompt: "樹與二元樹", doc_type: "odp", mode: "presenter", interactive: true, qa: true });
  expect("theme" in body).toBe(false);
  expect("pages" in body).toBe(false);
});

test("只有單一入口，送出後一律進入訪談", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "畢業專題進度報告" },
  });

  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover).toHaveBeenCalledWith(
    expect.objectContaining({ prompt: "畢業專題進度報告", doc_type: "odp" }),
  );
  expect(screen.queryByRole("button", { name: "直接生成" })).toBeNull();
  expect(screen.queryByRole("button", { name: "先問幾題" })).toBeNull();
});

test("輸出格式收斂成單一 ODP 簡報標示", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.getByLabelText("輸出格式：ODP 簡報")).toBeInTheDocument();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByText("即將支援")).toBeNull();
});

test("輸出設定只調整內容密度與頁數，流程維持安全預設", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  fireEvent.click(screen.getByLabelText(/閱讀文件/));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0]).toMatchObject({
    prompt: "樹", mode: "detailed", pages: 12, qa: true, interactive: true,
  });
  expect("theme" in onDiscover.mock.calls[0][0]).toBe(false);
  expect("backend" in onDiscover.mock.calls[0][0]).toBe(false);
});

test("頁數超界時顯示即時提示,且送出不帶 pages 欄位", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "100" } });
  // 即時提示出現
  expect(screen.getByRole("alert").textContent).toMatch(/3.*30/);
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect("pages" in onDiscover.mock.calls[0][0]).toBe(false);
});

test("輸出設定不暴露模型、主題與 pipeline 開關", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  expect(screen.queryByLabelText(/模型後端/)).toBeNull();
  expect(screen.queryByLabelText(/大綱確認/)).toBeNull();
  expect(screen.queryByLabelText(/主題配色/)).toBeNull();
  expect(screen.queryByLabelText(/^受眾$/)).toBeNull();
  expect(screen.queryByLabelText(/^語氣$/)).toBeNull();
});

test("設計品質檢查預設開啟", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  expect(screen.getByLabelText(/設計品質檢查/)).toBeChecked();
});

test("關掉設計品質檢查後,送出 body 帶 qa:false", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  fireEvent.click(screen.getByLabelText(/設計品質檢查/));
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0]).toMatchObject({ qa: false });
});

test("關掉設計品質檢查後,收合的摘要 chip 說明已跳過", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  fireEvent.click(screen.getByLabelText(/設計品質檢查/));
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  expect(screen.getByTestId("adv-summary").textContent).toMatch(/設計品質檢查/);
});

test("PDF 與圖片會作為參考文件進入訪談 body", async () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "醫療流程" },
  });
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  const image = new File(["png"], "候診區.png", { type: "image/png" });
  const pdf = new File(["%PDF-1.7"], "研究論文.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByTestId("asset-input"), {
    target: { files: [image, pdf] },
  });
  await waitFor(() => expect(screen.getByTestId("asset-list")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  const body = onDiscover.mock.calls[0][0];
  expect("backend" in body).toBe(false);
  expect(body.assets).toHaveLength(2);
  expect(body.assets[0].description).toBe("候診區");
  expect(body.assets[0].data_url).toMatch(/^data:image\/png;base64,/);
  expect(body.assets[1].description).toBe("研究論文");
  expect(body.assets[1].data_url).toMatch(/^data:application\/pdf;base64,/);
});

test("收合時偏離預設的選項以 chip 摘要顯示", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  fireEvent.click(screen.getByLabelText(/閱讀文件/));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  // 收合抽屜
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  const summary = screen.getByTestId("adv-summary");
  expect(summary.textContent).toContain("12 頁");
  expect(summary.textContent).toContain("閱讀文件");
});

test("預設狀態下收合不顯示 chip 摘要", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.queryByTestId("adv-summary")).toBeNull();
});

test("抽屜收合且頁數非法時，摘要顯示警示 chip", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "100" } });
  // 收合抽屜:欄位提示消失,但摘要要補警示 chip。
  fireEvent.click(screen.getByRole("button", { name: /輸出設定/ }));
  const warn = screen.getByTestId("adv-warn");
  expect(warn).toBeInTheDocument();
  expect(warn.textContent).toMatch(/頁數無效/);
});

test("placeholder 鼓勵細節,不再教人用一句話", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.placeholder).not.toContain("一句話");
  expect(ta.placeholder).toMatch(/主題.*受眾.*重點/);
});

test("點範例 chip 填入 textarea", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  const chips = screen.getAllByTestId("example-chip");
  expect(chips.length).toBeGreaterThanOrEqual(2);
  fireEvent.click(chips[0]);
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.value).toBe(chips[0].textContent);
});

test("Ctrl+Enter 送出;Enter 換行不送;空白不送", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  const ta = screen.getByRole("textbox", { name: /主題/ });
  // 空白時 Ctrl+Enter 不動作
  fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
  expect(onDiscover).not.toHaveBeenCalled();
  // 有內容:Enter 單獨不送
  fireEvent.change(ta, { target: { value: "資料結構教學" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  expect(onDiscover).not.toHaveBeenCalled();
  // Ctrl+Enter 送出
  fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
  expect(onDiscover).toHaveBeenCalledTimes(1);
  // Cmd+Enter(metaKey)也送出
  fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
  expect(onDiscover).toHaveBeenCalledTimes(2);
});
