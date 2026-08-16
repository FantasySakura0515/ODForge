import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import {
  deleteTemplate,
  extractTemplateDesign,
  getTemplates,
  saveTemplate,
  type DeckTemplate,
  type DesignSpec,
} from "../state/api";
import { contrastProblems, TemplateGallery } from "./TemplateGallery";

vi.mock("../state/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../state/api")>();
  return {
    ...actual,
    getTemplates: vi.fn(),
    saveTemplate: vi.fn(),
    deleteTemplate: vi.fn(),
    extractTemplateDesign: vi.fn(),
  };
});

function design(accent = "#1A4B8C"): DesignSpec {
  return {
    palette: { bg: "#FBF9F4", surface: "#EFEADD", text: "#1F2733", muted: "#5B6470", accent },
    fonts: { display: "Noto Serif TC", body: "Noto Sans TC" },
    scale: "standard",
  };
}

const BUILTIN: DeckTemplate = {
  id: "academic",
  name: "學術藍",
  design: design(),
  builtin: true,
  source: "builtin",
  created_at: 0,
};

const CUSTOM: DeckTemplate = {
  id: "tpl-abc123",
  name: "系上公版",
  design: design("#A3212F"),
  builtin: false,
  source: "extracted",
  created_at: 1,
};

function mockList(templates: DeckTemplate[]) {
  vi.mocked(getTemplates).mockResolvedValue({
    templates,
    languages: [{ id: "zh-TW", label: "繁體中文" }],
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList([BUILTIN, CUSTOM]);
});

test("內建與自訂分區列出，內建沒有刪除鈕", async () => {
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());

  expect(screen.getByTestId("tpl-builtin").textContent).toContain("學術藍");
  expect(screen.getByTestId("tpl-custom").textContent).toContain("系上公版");
  // 刪掉一套內建主題會讓每一份指名它的簡報失去配色——它本來就不該可刪。
  expect(screen.queryByRole("button", { name: /刪除範本 學術藍/ })).toBeNull();
  expect(screen.getByRole("button", { name: /刪除範本 系上公版/ })).toBeInTheDocument();
});

test("以內建為底開新範本，預設名稱看得出來源", async () => {
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());
  fireEvent.click(screen.getAllByRole("button", { name: "以此為底" })[0]);

  const name = screen.getByRole("textbox", { name: /範本名稱/ }) as HTMLInputElement;
  expect(name.value).toContain("系上公版");
});

test("儲存自訂範本後重新讀取清單", async () => {
  vi.mocked(saveTemplate).mockResolvedValue({ ...CUSTOM, name: "新範本" });
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /自訂配色/ }));
  fireEvent.change(screen.getByRole("textbox", { name: /範本名稱/ }), {
    target: { value: "新範本" },
  });
  fireEvent.click(screen.getByRole("button", { name: /儲存範本/ }));

  await waitFor(() => expect(vi.mocked(saveTemplate)).toHaveBeenCalled());
  expect(vi.mocked(saveTemplate).mock.calls[0][0].name).toBe("新範本");
  await waitFor(() => expect(vi.mocked(getTemplates)).toHaveBeenCalledTimes(2));
});

test("沒有名稱不能存", async () => {
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /自訂配色/ }));
  expect(screen.getByRole("button", { name: /儲存範本/ })).toBeDisabled();
});

test("對比不足時當場說明並擋下儲存", async () => {
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /自訂配色/ }));
  fireEvent.change(screen.getByRole("textbox", { name: /範本名稱/ }), {
    target: { value: "看不見的配色" },
  });
  // 白底配淺灰字:後端本來就會退件,但要在調色的當下就說。
  fireEvent.change(screen.getByLabelText("內文"), { target: { value: "#eeeeee" } });

  expect(screen.getByTestId("tpl-contrast").textContent).toMatch(/低於 4.5/);
  expect(screen.getByRole("button", { name: /儲存範本/ })).toBeDisabled();
  expect(vi.mocked(saveTemplate)).not.toHaveBeenCalled();
});

test("匯入現有範本檔會抽出設計並帶進編輯器", async () => {
  vi.mocked(extractTemplateDesign).mockResolvedValue(design("#0F766E"));
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());

  fireEvent.change(screen.getByTestId("template-import"), {
    target: { files: [new File(["PK"], "校內公版.otp", { type: "application/octet-stream" })] },
  });

  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: /範本名稱/ })).toHaveValue("校內公版"),
  );
  // 抽出來的是預覽,不是自動存檔:名字都還沒取。
  expect(vi.mocked(saveTemplate)).not.toHaveBeenCalled();
});

test("匯入失敗說原因，不留下半套編輯狀態", async () => {
  vi.mocked(extractTemplateDesign).mockRejectedValue(
    new Error("無法讀取這個範本檔：not a zip"),
  );
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());

  fireEvent.change(screen.getByTestId("template-import"), {
    target: { files: [new File(["x"], "壞檔.otp", { type: "application/octet-stream" })] },
  });

  expect(await screen.findByText(/無法讀取這個範本檔/)).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /範本名稱/ })).toBeNull();
});

test("刪除自訂範本後重新讀取清單", async () => {
  vi.mocked(deleteTemplate).mockResolvedValue(undefined);
  render(<TemplateGallery />);
  await waitFor(() => expect(screen.getByText("系上公版")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: /刪除範本 系上公版/ }));
  await waitFor(() => expect(vi.mocked(deleteTemplate)).toHaveBeenCalledWith("tpl-abc123"));
  await waitFor(() => expect(vi.mocked(getTemplates)).toHaveBeenCalledTimes(2));
});

test("讀不到範本庫時說明並可重試", async () => {
  vi.mocked(getTemplates).mockRejectedValueOnce(new Error("後端未啟動"));
  render(<TemplateGallery />);
  expect(await screen.findByText("後端未啟動")).toBeInTheDocument();

  mockList([BUILTIN]);
  fireEvent.click(screen.getByRole("button", { name: "重試" }));
  await waitFor(() => expect(screen.getByText("學術藍")).toBeInTheDocument());
});

test("對比檢查與後端同一組門檻", () => {
  // 全部達標 → 沒有問題;內文對比不足 → 指名是哪一條。
  expect(contrastProblems(design().palette)).toEqual([]);
  const bad = { ...design().palette, text: "#EEEEEE" };
  expect(contrastProblems(bad).some((p) => p.includes("內文"))).toBe(true);
});
