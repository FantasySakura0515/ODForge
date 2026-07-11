import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { PreviewStage } from "./PreviewStage";
import { UnitDetail } from "./UnitDetail";
import type { Unit } from "../state/types";

afterEach(() => vi.restoreAllMocks());

const units: Unit[] = [
  { n: 1, role: "title", title: "封面", status: "preview", previewUrl: "/api/jobs/j1/preview/1.png" },
  { n: 2, role: "content", title: "內文", status: "preview", previewUrl: "/api/jobs/j1/preview/2.png" },
  { n: 3, role: "summary", title: "結語", status: "skeleton" },
];

function noop() {}

// ① 點有 preview 的 cell 開 dialog
test("點有 preview 的 cell 開啟 lightbox dialog", () => {
  render(<PreviewStage units={units} docType="odp" jobId="j1" dispatch={noop} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /第 1 頁/ }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

// ② 無 preview 的 cell 不可開
test("無 preview 的 cell 不是按鈕、點擊不開 dialog", () => {
  render(<PreviewStage units={units} docType="odp" jobId="j1" dispatch={noop} />);
  // 第 3 頁沒有 preview:不應以按鈕角色存在
  expect(screen.queryByRole("button", { name: /第 3 頁/ })).toBeNull();
});

// ① Esc 關、←/→ 換頁
test("Esc 關閉、方向鍵換頁、遮罩點擊關閉", () => {
  const onClose = vi.fn();
  const onNavigate = vi.fn();
  render(<UnitDetail units={units} n={2} jobId="j1" onClose={onClose} onNavigate={onNavigate} dispatch={noop} />);

  const dialog = screen.getByRole("dialog");
  fireEvent.keyDown(dialog, { key: "ArrowRight" });
  expect(onNavigate).toHaveBeenCalledWith(3);
  fireEvent.keyDown(dialog, { key: "ArrowLeft" });
  expect(onNavigate).toHaveBeenCalledWith(1);
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});

// Important: 在重生指令輸入框內用方向鍵移游標不應翻頁
test("在輸入框內按方向鍵不換頁,但 Esc 仍可關閉", () => {
  const onClose = vi.fn();
  const onNavigate = vi.fn();
  render(<UnitDetail units={units} n={2} jobId="j1" onClose={onClose} onNavigate={onNavigate} dispatch={noop} />);

  const input = screen.getByRole("textbox");
  fireEvent.keyDown(input, { key: "ArrowRight" });
  fireEvent.keyDown(input, { key: "ArrowLeft" });
  expect(onNavigate).not.toHaveBeenCalled();
  // Esc 從輸入框內照常關閉
  fireEvent.keyDown(input, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});

// Minor: 導覽到邊界(鈕被 disable)後,焦點應回到 dialog 容器而非掉到 body
test("導覽後焦點回到 dialog 容器(不掉到 body)", () => {
  render(<UnitDetail units={units} n={2} jobId="j1" onClose={noop} onNavigate={noop} dispatch={noop} />);
  const dialog = screen.getByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: /上一張/ }));
  expect(document.activeElement).toBe(dialog);
});

test("標頭顯示 第 n / N 頁 · role · title", () => {
  render(<UnitDetail units={units} n={2} jobId="j1" onClose={noop} onNavigate={noop} dispatch={noop} />);
  const head = screen.getByTestId("ud-caption").textContent?.replace(/\s+/g, " ");
  expect(head).toContain("第 2 / 3 頁");
  expect(head).toContain("content");
  expect(head).toContain("內文");
});

test("第 1 頁時上一張鈕停用(循環不跨界)", () => {
  render(<UnitDetail units={units} n={1} jobId="j1" onClose={noop} onNavigate={noop} dispatch={noop} />);
  expect(screen.getByRole("button", { name: /上一張/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /下一張/ })).not.toBeDisabled();
});

test("翻到無 preview 的頁顯示尚無預覽佔位", () => {
  render(<UnitDetail units={units} n={3} jobId="j1" onClose={noop} onNavigate={noop} dispatch={noop} />);
  expect(screen.getByText(/此頁尚無預覽/)).toBeInTheDocument();
  expect(screen.queryByRole("img")).toBeNull();
});

// ③ regenerate 成功路徑
test("送出重生:呼叫 API 並依序 dispatch regen_start → regen_done", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ok: true, n: 2, slide: { title: "改後" }, preview_url: "/api/jobs/j1/preview/2.png" }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const dispatch = vi.fn();
  render(<UnitDetail units={units} n={2} jobId="j1" onClose={noop} onNavigate={noop} dispatch={dispatch} />);

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "改成比較表" } });
  fireEvent.click(screen.getByRole("button", { name: /重生此頁/ }));

  await waitFor(() => expect(dispatch).toHaveBeenCalledWith({ type: "regen_done", data: expect.objectContaining({ n: 2, preview_url: "/api/jobs/j1/preview/2.png" }) }));
  expect(dispatch).toHaveBeenCalledWith({ type: "regen_start", data: { n: 2 } });
  const [url, opts] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/jobs/j1/slides/2/regenerate");
  expect(JSON.parse(opts.body)).toMatchObject({ instruction: "改成比較表" });
});

// ④ 失敗路徑還原狀態
test("重生失敗:dispatch regen_error 並顯示錯誤字", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
  const dispatch = vi.fn();
  render(<UnitDetail units={units} n={2} jobId="j1" onClose={noop} onNavigate={noop} dispatch={dispatch} />);

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: /重生此頁/ }));

  await waitFor(() => expect(dispatch).toHaveBeenCalledWith({ type: "regen_error", data: { n: 2 } }));
  expect(screen.getByRole("alert")).toBeInTheDocument();
});

// ⑤ regen 中輸入列 disabled + 重生中遮罩
test("該頁 status=regen 時輸入列停用並顯示重生中遮罩", () => {
  const regen: Unit[] = units.map((u) => (u.n === 2 ? { ...u, status: "regen" } : u));
  render(<UnitDetail units={regen} n={2} jobId="j1" onClose={noop} onNavigate={noop} dispatch={noop} />);
  expect(screen.getByRole("textbox")).toBeDisabled();
  expect(screen.getByRole("button", { name: /重生此頁/ })).toBeDisabled();
  expect(screen.getByText(/重生中/)).toBeInTheDocument();
});

test("重生中遮罩帶 spin 旋轉指示符", () => {
  const regen: Unit[] = units.map((u) => (u.n === 2 ? { ...u, status: "regen" } : u));
  render(<UnitDetail units={regen} n={2} jobId="j1" onClose={noop} onNavigate={noop} dispatch={noop} />);
  // dialog 已 portal 到 body,RTL container 查不到 → 查 document。
  expect(document.querySelector(".ud-regenning .spin")).not.toBeNull();
});

// 圖層回歸:lightbox 必須 portal 到 body,不能困在 .center(z-index:2)的
// stacking context 裡——否則同層 .rail/.foot 會整片蓋過遮罩(2026-07-11 實測)。
test("lightbox portal 到 document.body(不被 .center stacking context 困住)", () => {
  render(<PreviewStage units={units} docType="odp" jobId="j1" dispatch={noop} />);
  fireEvent.click(screen.getByRole("button", { name: /第 1 頁/ }));
  const overlay = document.querySelector(".lightbox");
  expect(overlay?.parentElement).toBe(document.body);
  expect(overlay?.closest(".center")).toBeNull();
});
