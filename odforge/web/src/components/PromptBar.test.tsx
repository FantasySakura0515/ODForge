import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import {
  getSources,
  getTemplates,
  resetSourcesCache,
  type DeckTemplate,
  type DesignSpec,
} from "../state/api";
import { PromptBar } from "./PromptBar";

vi.mock("../state/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../state/api")>();
  return { ...actual, getSources: vi.fn(), getTemplates: vi.fn() };
});

function design(accent: string, bg = "#FBF9F4"): DesignSpec {
  return {
    palette: { bg, surface: "#EFEADD", text: "#1F2733", muted: "#5B6470", accent },
    fonts: { display: "Noto Sans TC", body: "Noto Sans TC" },
    scale: "standard",
  };
}

/** The gallery answer: two built-in presets plus one of the user's own. */
function mockTemplates(templates?: DeckTemplate[]) {
  vi.mocked(getTemplates).mockResolvedValue({
    templates: templates ?? [
      {
        id: "academic",
        name: "學術藍",
        design: design("#1A4B8C"),
        style: "classic",
        builtin: true,
        source: "builtin",
        created_at: 0,
      },
      {
        id: "dark",
        name: "深夜藍",
        design: design("#3DD6E6", "#171826"),
        style: "keynote",
        builtin: true,
        source: "builtin",
        created_at: 0,
      },
      {
        id: "tpl-abc123",
        name: "系上公版",
        design: design("#A3212F"),
        style: "editorial",
        builtin: false,
        source: "extracted",
        created_at: 1,
      },
    ],
    languages: [
      { id: "zh-TW", label: "繁體中文" },
      { id: "en", label: "English" },
      { id: "bilingual", label: "中英對照" },
    ],
    styles: [
      { id: "classic", label: "學院派", blurb: "置中封面" },
      { id: "editorial", label: "編輯風", blurb: "左切齊封面" },
      { id: "keynote", label: "舞台", blurb: "滿版出血" },
    ],
  });
}

/** A `/api/sources` answer naming `vision` as the default and saying if it works. */
function mockSources(vision: string, available: boolean, reason = "") {
  vi.mocked(getSources).mockResolvedValue({
    text: [{ name: "deepseek", available: true, reason: "" }],
    vision: [
      { name: "off", available: true, reason: "" },
      { name: vision, available, reason },
    ],
    defaults: { text: "deepseek", vision },
  });
}

beforeEach(() => {
  resetSourcesCache();
  // Default for the tests that are not about source discovery: a working vision
  // source, so the fourth gate runs as it does in a configured deployment.
  mockSources("claude", true);
  mockTemplates();
});

/** Wait for the /api/templates answer to land (the language chipset fills in). */
async function templatesSettled() {
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument(),
  );
}

/** 兩個進階區塊預設收起來,所以碰它們裡面任何欄位的測試都得先按開。 */
function openCustom() {
  fireEvent.click(screen.getByRole("button", { name: "客製化" }));
}

function openCover() {
  fireEvent.click(screen.getByRole("button", { name: "封面署名與校徽" }));
}

/** Wait for the `/api/sources` probe to land.
 *
 * The fourth gate is forced on but still fail-closed: until the probe answers,
 * `qa` is not promised. Tests that click straight through would be asserting
 * the pre-probe state, not the configured one.
 *
 * A working probe now renders *nothing* (the green strip was removed), so the
 * barrier is the state commit itself, not a DOM node: flush the resolved promise
 * inside act() and React has applied it by the time this returns.
 */
async function sourcesSettled() {
  await waitFor(() => expect(vi.mocked(getSources)).toHaveBeenCalled());
  await act(async () => {});
}

test("空 prompt 時送出鈕停用", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.getByRole("button", { name: "繼續" })).toBeDisabled();
});

test("輸入後送出帶預設 body:odp/presenter/interactive 開/qa 開/無 theme 無 pages", async () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  await sourcesSettled();
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

// 需求框是整個產品唯一該寫長的地方:一段完整需求貼進四行高的框裡,使用者只看得
// 到自己文字的最後一段,校對不了也刪不掉重複。jsdom 沒有版面(scrollHeight 恆為
// 0),所以這裡自己給一個「一行 28px、下限 112px」的假版面——要驗的是長高那條
// 邏輯,不是瀏覽器怎麼排字。
test("需求框跟著內容長高,刪字後縮得回去", () => {
  const own = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "scrollHeight",
  );
  Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLTextAreaElement) {
      return Math.max(112, this.value.split("\n").length * 28);
    },
  });
  try {
    render(<PromptBar onDiscover={vi.fn()} />);
    const box = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
    expect(box.style.height).toBe("112px");

    fireEvent.change(box, {
      target: { value: Array.from({ length: 10 }, (_, i) => `第 ${i + 1} 行`).join("\n") },
    });
    expect(box.style.height).toBe("280px");

    // 縮回去同樣重要:少了「先歸零再量」那一步,框只會單向長大。
    fireEvent.change(box, { target: { value: "一行就好" } });
    expect(box.style.height).toBe("112px");
  } finally {
    if (own) {
      Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", own);
    } else {
      Reflect.deleteProperty(HTMLTextAreaElement.prototype, "scrollHeight");
    }
  }
});

test("輸出格式收斂成單一 ODP 簡報標示", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.getByLabelText("輸出格式：ODP 簡報")).toBeInTheDocument();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByText("即將支援")).toBeNull();
});

// ---------------------------------------------------------------------------
// 客製化與封面署名收在 +／− 後面(2026-08-17 使用者決定,推翻先前「一律攤開」的
// 做法:攤開後的表單長到需求欄位自己被推出畫面)。收起來可以,但兩件事必須成立:
// 標題本身就是那顆開關(不是另一顆小圖示),而且收起時要看得見自己設過什麼。
// ---------------------------------------------------------------------------

test("客製化與封面署名預設收起,標題就是開關", async () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  await sourcesSettled();
  const custom = screen.getByRole("button", { name: "客製化" });
  const cover = screen.getByRole("button", { name: "封面署名與校徽" });
  expect(custom).toHaveAttribute("aria-expanded", "false");
  expect(cover).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByLabelText(/頁數/)).toBeNull();
  // 「封面署名」也是那個區塊的 aria 標籤,所以要指名是輸入欄位,不是整個 section。
  expect(screen.queryByRole("textbox", { name: /封面署名/ })).toBeNull();

  fireEvent.click(custom);
  expect(custom).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByLabelText(/閱讀文件/)).toBeInTheDocument();
  expect(screen.getByLabelText(/頁數/)).toBeInTheDocument();
  expect(screen.getByLabelText(/聽眾對象/)).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "簡報場合" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "講述時間" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "語氣風格" })).toBeInTheDocument();

  // 再按一次收回去,而且是真的從 DOM 移除(不是只有視覺上藏起來)。
  fireEvent.click(custom);
  expect(screen.queryByLabelText(/頁數/)).toBeNull();
});

test("收起時仍看得見自己設過什麼;沒設過就不多話", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.queryByText(/已指定/)).toBeNull();

  openCustom();
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "課堂教學" }));
  openCustom(); // 收回去

  const summary = screen.getByText(/已指定/);
  expect(summary).toHaveTextContent("12 頁");
  expect(summary).toHaveTextContent("課堂教學");
});

test("收起來的欄位不會被清掉:展開時值還在,送出也照樣帶著", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  openCustom();
  openCustom();
  expect(screen.getByLabelText(/頁數/)).toHaveValue(12);

  openCustom();
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0].pages).toBe(12);
});

test("視覺主題不再出現在需求頁,而且不送 theme／design／style", async () => {
  // 使用者 2026-08-17 決定把主題選格移出需求頁:配色與版式一律由 AI 依題目定調。
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  openCustom();
  // 清單真的載進來了(語言那組出現),而主題選格依然不存在——不是「還沒載到」。
  await templatesSettled();
  expect(screen.queryByRole("radiogroup", { name: "視覺主題" })).toBeNull();
  expect(screen.queryByLabelText(/學術藍/)).toBeNull();

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  const body = onDiscover.mock.calls[0][0];
  expect("theme" in body).toBe(false);
  expect("design" in body).toBe(false);
  expect("style" in body).toBe(false);
});

test("參考文件是第一層的區塊，不再收在輸出設定裡", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "參考文件" })).toBeInTheDocument();
  expect(screen.getByTestId("asset-input")).toBeInTheDocument();
});

test("內容密度與頁數照常進 body", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  fireEvent.click(screen.getByLabelText(/閱讀文件/));
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0]).toMatchObject({
    prompt: "樹", mode: "detailed", pages: 12, interactive: true,
  });
  expect("theme" in onDiscover.mock.calls[0][0]).toBe(false);
  expect("backend" in onDiscover.mock.calls[0][0]).toBe(false);
});

test("場合／聽眾／語氣／時間附加在需求尾端,留白的不出現", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  fireEvent.click(screen.getByRole("button", { name: "專題提案" }));
  fireEvent.change(screen.getByLabelText(/聽眾對象/), { target: { value: "系上老師" } });
  fireEvent.click(screen.getByRole("button", { name: "15 分鐘" }));
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));

  const { prompt } = onDiscover.mock.calls[0][0];
  expect(prompt).toContain("樹");
  expect(prompt).toContain("場合:專題提案");
  expect(prompt).toContain("受眾:系上老師");
  expect(prompt).toContain("講述時間:15 分鐘");
  expect(prompt).not.toContain("語氣");
});

test("晶片再點一次可取消，回到交給 AI 判斷", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  const chip = screen.getByRole("button", { name: "課堂教學" });
  fireEvent.click(chip);
  expect(chip).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(chip);
  expect(chip).toHaveAttribute("aria-pressed", "false");

  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0].prompt).not.toContain("場合");
});

// ---------------------------------------------------------------------------
// 輸出語言
// ---------------------------------------------------------------------------

test("輸出語言預設繁中(不送欄位)，選了才送", async () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  openCustom();
  await templatesSettled();
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect("language" in onDiscover.mock.calls[0][0]).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "English" }));
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[1][0].language).toBe("en");
});

test("語言選單讀後端註冊表；讀不到就整組不出現", async () => {
  vi.mocked(getTemplates).mockRejectedValue(new Error("offline"));
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  openCustom();
  await waitFor(() => expect(vi.mocked(getTemplates)).toHaveBeenCalled());
  // 拿不到清單是加分項失效,不是故障:語言那一組收起來,但簡報照樣生成得出來。
  expect(screen.queryByRole("button", { name: "English" })).toBeNull();

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover).toHaveBeenCalled();
});

// ---------------------------------------------------------------------------
// 封面署名與校徽
// ---------------------------------------------------------------------------

test("署名原樣送出，留白則不送", async () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect("byline" in onDiscover.mock.calls[0][0]).toBe(false);

  openCover();
  fireEvent.change(screen.getByRole("textbox", { name: /封面署名/ }), {
    target: { value: "XX大學資工系 · 王小明" },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[1][0].byline).toBe("XX大學資工系 · 王小明");
});

test("校徽選了才出現位置選項，並隨 body 送出", async () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCover();
  // 沒有校徽時,「放在哪些頁」是對一張不存在的圖做設定。
  expect(screen.queryByTestId("logo-placement")).toBeNull();

  fireEvent.change(screen.getByTestId("logo-input"), {
    target: { files: [new File(["png"], "校徽.png", { type: "image/png" })] },
  });
  await waitFor(() => expect(screen.getByTestId("logo-chosen")).toBeInTheDocument());
  expect(screen.getByTestId("logo-placement")).toBeInTheDocument();

  fireEvent.click(screen.getByLabelText(/每頁角落/));
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  const body = onDiscover.mock.calls[0][0];
  expect(body.logo.data_url).toMatch(/^data:image\/png;base64,/);
  expect(body.logo_placement).toBe("all");
});

test("PDF 不能當校徽", async () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  openCover();
  fireEvent.change(screen.getByTestId("logo-input"), {
    target: { files: [new File(["%PDF-1.7"], "簡章.pdf", { type: "application/pdf" })] },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent(/PNG 或 JPEG/);
  expect(screen.queryByTestId("logo-chosen")).toBeNull();
});

test("頁數超界時擋住送出,並把使用者帶回出問題的欄位", () => {
  // 舊行為是「靜默省略成 AI 決定」:使用者輸入 100 頁、拿到 12 頁,而且畫面上
  // 沒有任何一處說明發生過什麼。非法值必須阻止送出,不是被悄悄丟掉。
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  const pages = screen.getByLabelText(/頁數/);
  fireEvent.change(pages, { target: { value: "100" } });

  expect(screen.getByRole("alert").textContent).toMatch(/3.*30/);
  expect(pages).toHaveAttribute("aria-invalid", "true");
  expect(pages).toHaveAttribute("aria-describedby", "pages-error");
  expect(screen.getByRole("button", { name: /繼續/ })).toHaveAttribute("aria-disabled", "true");

  fireEvent.click(screen.getByRole("button", { name: /繼續/ }));
  expect(onDiscover).not.toHaveBeenCalled();
  expect(pages).toHaveFocus();
});

test("收起來的時候頁數非法:按繼續要自己展開,不能只是不動", () => {
  // 收起來 + 錯誤訊息藏在裡面 = 一顆按不動又沒有解釋的按鈕,正是最難排除的故障。
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "100" } });
  openCustom(); // 收起來,連錯誤訊息一起藏了

  fireEvent.click(screen.getByRole("button", { name: /繼續/ }));
  expect(onDiscover).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "客製化" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByLabelText(/頁數/)).toHaveFocus();
  expect(screen.getByRole("alert").textContent).toMatch(/3.*30/);
});

// 非數字("abc")不在列:`type="number"` 的輸入框根本收不下,value 直接是空字串
// ——那等同「留白 = 交給 AI」,是正確行為,不是被靜默丟掉的非法值。
test.each(["0", "-1", "2", "31", "3.5", "999"])(
  "非法頁數 %s 一律擋住送出",
  (bad) => {
    const onDiscover = vi.fn();
    render(<PromptBar onDiscover={onDiscover} />);
    fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
    openCustom();
    fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: bad } });
    fireEvent.click(screen.getByRole("button", { name: /繼續/ }));
    expect(onDiscover).not.toHaveBeenCalled();
  },
);

test("合法頁數照常送出", () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  openCustom();
  fireEvent.change(screen.getByLabelText(/頁數/), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: /繼續/ }));
  expect(onDiscover.mock.calls[0][0].pages).toBe(12);
});

test("不暴露模型與 pipeline 開關", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.queryByLabelText(/模型後端/)).toBeNull();
  expect(screen.queryByLabelText(/大綱確認/)).toBeNull();
});

// ---------------------------------------------------------------------------
// 第四道閘不再是選項:設定得起來就一定跑。使用者關不掉,但也絕不會被承諾一件
// 這台伺服器做不到的事——qa:true 依舊只在探測成功且來源可用時才送出。
// ---------------------------------------------------------------------------

test("設計品質檢查沒有開關,可用時一律送 qa:true 而且畫面上一個字都不說", async () => {
  // 「會跑」是預設,不值得佔一條版面;只有「跑不成」才必須說出口(下面幾個測試)。
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  await sourcesSettled();
  expect(screen.queryByRole("checkbox", { name: /設計品質檢查/ })).toBeNull();
  expect(screen.queryByTestId("qa-status")).toBeNull();
  expect(screen.queryByText(/一律執行/)).toBeNull();
  expect(screen.queryByText(/第四道閘/)).toBeNull();

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0]).toMatchObject({ qa: true });
});

test("沒有設定視覺來源時,狀態列說明原因且不送 qa:true", async () => {
  resetSourcesCache();
  mockSources("off", true);
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);

  await waitFor(() =>
    expect(screen.getByTestId("qa-status")).toHaveAttribute("data-state", "off"),
  );
  expect(screen.getByText(/ODFORGE_VISION_BACKEND/)).toBeInTheDocument();

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "樹" } });
  fireEvent.click(screen.getByRole("button", { name: /繼續/ }));
  expect(onDiscover.mock.calls[0][0].qa).toBe(false);
});

test("視覺來源存在但不可用時,顯示供應商給的真正原因", async () => {
  resetSourcesCache();
  mockSources("claude", false, "環境變數 ANTHROPIC_API_KEY 未設定");
  render(<PromptBar onDiscover={vi.fn()} />);

  await waitFor(() =>
    expect(screen.getByText(/ANTHROPIC_API_KEY 未設定/)).toBeInTheDocument(),
  );
  expect(screen.getByTestId("qa-status")).toHaveAttribute("data-state", "off");
});

test("來源還在確認時不會送出 qa:true,也不先講任何一種結論", async () => {
  // A probe that never settles: exactly the first few hundred ms of every load.
  vi.mocked(getSources).mockReturnValue(new Promise(() => {}));
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);

  // 暫態不出聲:既不承諾會跑,也不宣告跑不成——兩種說法在這一刻都還沒有依據。
  expect(screen.queryByTestId("qa-status")).toBeNull();

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "樹" },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0]).toMatchObject({ qa: false });
});

test("探測失敗時不送 qa:true,並說明原因", async () => {
  vi.mocked(getSources).mockRejectedValue(new Error("network down"));
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);

  await waitFor(() =>
    expect(screen.getByText(/無法確認視覺模型/)).toBeInTheDocument(),
  );

  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "樹" },
  });
  fireEvent.click(screen.getByRole("button", { name: "繼續" }));
  expect(onDiscover.mock.calls[0][0]).toMatchObject({ qa: false });
});

test("探測失敗可以重試,而且真的會再問一次", async () => {
  vi.mocked(getSources).mockRejectedValueOnce(new Error("network down"));
  render(<PromptBar onDiscover={vi.fn()} />);
  await waitFor(() =>
    expect(screen.getByTestId("retry-sources")).toBeInTheDocument(),
  );

  mockSources("claude", true);
  fireEvent.click(screen.getByTestId("retry-sources"));

  // 重問成功 → 沒有什麼要報告了,整條狀態列消失(而不是留在錯誤狀態)。
  await waitFor(() => expect(screen.queryByTestId("qa-status")).toBeNull());
});

// ---------------------------------------------------------------------------
// 參考文件(素材上傳)
// ---------------------------------------------------------------------------

test("PDF 與圖片會作為參考文件進入訪談 body", async () => {
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "醫療流程" },
  });
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

test("參考文件讀取中不得送出(選完立刻按繼續也不會漏掉檔案)", async () => {
  resetSourcesCache();
  mockSources("claude", true);
  const onDiscover = vi.fn();
  render(<PromptBar onDiscover={onDiscover} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), { target: { value: "醫療流程" } });

  fireEvent.change(screen.getByTestId("asset-input"), {
    target: { files: [new File(["%PDF-1.7"], "論文.pdf", { type: "application/pdf" })] },
  });
  // 讀取中:CTA 停用且說明自己在忙。
  expect(screen.getByRole("button", { name: /讀取參考文件/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /讀取參考文件/ }));
  expect(onDiscover).not.toHaveBeenCalled();

  // 讀完之後才送得出去,而且檔案在裡面。
  await waitFor(() => expect(screen.getByTestId("asset-list")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: /繼續/ }));
  expect(onDiscover.mock.calls[0][0].assets).toHaveLength(1);
});

test("整批參考文件超過總容量上限時直接拒絕", async () => {
  resetSourcesCache();
  mockSources("claude", true);
  render(<PromptBar onDiscover={vi.fn()} />);

  // 三個 7 MiB 的檔案:每個都在單檔上限內,合計卻遠超總量上限。
  const big = () => {
    const file = new File(["x"], "大檔.pdf", { type: "application/pdf" });
    Object.defineProperty(file, "size", { value: 7 * 1024 * 1024 });
    return file;
  };
  fireEvent.change(screen.getByTestId("asset-input"), {
    target: { files: [big(), big(), big()] },
  });

  expect(await screen.findByRole("alert")).toHaveTextContent(/合計/);
  expect(screen.queryByTestId("asset-list")).toBeNull();
});

// R2-05 — re-selecting an invalid file must not wedge the loader.
//
// Each call bumps the run counter, which retires the previous run's `finally`.
// The early returns did not clear `assetsLoading` themselves, so a bad file
// chosen while an earlier read was in flight left the submit button disabled
// for the rest of the session, explained only by a line about file formats.
test("讀取中又選了非法檔:錯誤要說,但送出鈕不能就此鎖死", async () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "樹" },
  });
  const input = screen.getByTestId("asset-input");

  const good = new File([new Uint8Array([1, 2, 3])], "ref.pdf", {
    type: "application/pdf",
  });
  fireEvent.change(input, { target: { files: [good] } });

  // …and immediately swap in something the validator refuses.
  const bad = new File([new Uint8Array([1])], "notes.txt", { type: "text/plain" });
  fireEvent.change(input, { target: { files: [bad] } });

  await waitFor(() =>
    expect(screen.getByText(/請選擇 .* 以下的 PDF、PNG 或 JPEG。/)).toBeInTheDocument(),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "繼續" })).not.toBeDisabled(),
  );
});

test("超過合計上限也一樣:擋下這批,但不留下卡住的讀取狀態", async () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  fireEvent.change(screen.getByRole("textbox", { name: /主題/ }), {
    target: { value: "樹" },
  });
  const input = screen.getByTestId("asset-input");

  // Each file is under the per-file cap; three of them are over the batch cap.
  // That is the case the aggregate limit exists for.
  const chunky = (name: string) => {
    const file = new File([new Uint8Array([1])], name, { type: "application/pdf" });
    Object.defineProperty(file, "size", { value: 6 * 1024 * 1024 });
    return file;
  };
  fireEvent.change(input, {
    target: { files: [chunky("a.pdf"), chunky("b.pdf"), chunky("c.pdf")] },
  });

  await waitFor(() =>
    expect(screen.getByText(/超過上限/)).toBeInTheDocument(),
  );
  expect(screen.getByRole("button", { name: "繼續" })).not.toBeDisabled();
});

// ---------------------------------------------------------------------------
// 需求輸入本身
// ---------------------------------------------------------------------------

test("不再提供範例句子讓人照抄", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  expect(screen.queryAllByTestId("example-chip")).toHaveLength(0);
  expect(screen.queryByText("範例")).toBeNull();
});

test("placeholder 要求詳細內容,不再教人用一句話", () => {
  render(<PromptBar onDiscover={vi.fn()} />);
  const ta = screen.getByRole("textbox", { name: /主題/ }) as HTMLTextAreaElement;
  expect(ta.placeholder).not.toContain("一句話");
  expect(ta.placeholder).toMatch(/詳細/);
  expect(ta.placeholder).toMatch(/主題.*受眾.*重點/s);
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
