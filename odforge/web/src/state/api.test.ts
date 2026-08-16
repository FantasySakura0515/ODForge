import { afterEach, expect, test, vi } from "vitest";
import {
  ApiHttpError,
  buildGenerateBody,
  downloadUrl,
  eventsUrl,
  getSessions,
  postDiscoveryQuestions,
  postGenerate,
  postCancel,
  postOutlineAction,
  postRegenerate,
  previewUrl,
  type GenerateOptions,
} from "./api";
import type { Outline } from "./types";

const DEFAULT_OPTS: GenerateOptions = {
  mode: "presenter", interactive: true, qa: false,
};

afterEach(() => vi.restoreAllMocks());

function ndjsonResponse(events: unknown[]) {
  const bytes = new TextEncoder().encode(
    `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
  );
  let sent = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { value: undefined, done: true };
          sent = true;
          return { value: bytes, done: false };
        },
      }),
    },
  } as unknown as Response;
}

test("postGenerate POST /api/generate 並回 job_id", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "abc" }) });
  vi.stubGlobal("fetch", fetchMock);
  const out = await postGenerate({ prompt: "樹", doc_type: "odp" });
  expect(out.job_id).toBe("abc");
  const [url, opts] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/generate");
  expect(opts.method).toBe("POST");
  expect(JSON.parse(opts.body)).toMatchObject({ prompt: "樹", doc_type: "odp" });
});

test("getSessions 讀取首頁工作紀錄", async () => {
  const sessions = [{
    id: "s1",
    title: "測試簡報",
    prompt: "測試",
    status: "complete",
    created_at: 1,
    updated_at: 2,
    page_count: 6,
  }];
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ sessions }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await expect(getSessions()).resolves.toEqual(sessions);
  expect(fetchMock).toHaveBeenCalledWith("/api/sessions");
});

test("postGenerate 非 2xx 拋錯", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
  await expect(postGenerate({ prompt: "x", doc_type: "odp" })).rejects.toThrow();
});

test("postGenerate 非 2xx:轉述後端 detail 與狀態碼(429 上限不是連線問題)", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false,
    status: 429,
    json: async () => ({ detail: "too many active jobs; limit is 2" }),
  }));
  const err = await postGenerate({ prompt: "x", doc_type: "odp" }).catch((e) => e);
  expect(err).toBeInstanceOf(ApiHttpError);
  expect(err.status).toBe(429);
  expect(err.message).toContain("too many active jobs");
  expect(err.message).toContain("429");
});

test("postGenerate 非 2xx 無 detail(或非 JSON)→ 後備訊息仍帶狀態碼", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false,
    status: 422,
    json: async () => ({ detail: [{ loc: ["body", "pages"], msg: "invalid" }] }), // FastAPI 驗證陣列
  }));
  const err = await postGenerate({ prompt: "x", doc_type: "odp" }).catch((e) => e);
  expect(err).toBeInstanceOf(ApiHttpError);
  expect(err.message).toContain("generate 失敗");
  expect(err.message).toContain("422");
});

test("postDiscoveryQuestions 只送素材描述，不送 base64 圖片 bytes", async () => {
  const plan = {
    summary: "需求摘要",
    known_context: [],
    questions: [
      { id: "a", question: "問題 A", why: "原因", options: ["一", "二"] },
      { id: "b", question: "問題 B", why: "原因", options: ["一", "二"] },
    ],
    completeness: 30,
  };
  const progress = {
    request_id: "req-1",
    stage: "requesting",
    message: "正在產生關鍵問題",
    elapsed_ms: 1200,
  };
  const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
    { type: "progress", data: progress },
    { type: "result", data: { request_id: "req-1", elapsed_ms: 2300, plan } },
  ]));
  vi.stubGlobal("fetch", fetchMock);
  const onProgress = vi.fn();

  const result = await postDiscoveryQuestions({
    prompt: "畢業專題",
    doc_type: "odp",
    backend: "custom",
    assets: [
      {
        description: "架構圖",
        credit: "專題小組",
        data_url: "data:image/png;base64,SECRET_BYTES",
      },
    ],
  }, { onProgress });

  expect(result).toEqual(plan);
  expect(onProgress).toHaveBeenCalledWith(progress);
  const [url, opts] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/discovery/questions/stream");
  const payload = JSON.parse(opts.body);
  expect(payload.assets).toEqual([{ description: "架構圖", credit: "專題小組" }]);
  expect(opts.body).not.toContain("SECRET_BYTES");
});

test("postDiscoveryQuestions 會把 PDF 送去讀題，但不傳圖片 bytes", async () => {
  const plan = {
    summary: "論文報告",
    known_context: [],
    questions: [],
    completeness: 100,
  };
  const fetchMock = vi.fn().mockResolvedValue(ndjsonResponse([
    { type: "result", data: { request_id: "req-pdf", elapsed_ms: 30, plan } },
  ]));
  vi.stubGlobal("fetch", fetchMock);

  await postDiscoveryQuestions({
    prompt: "報告這篇論文",
    doc_type: "odp",
    assets: [
      {
        description: "研究論文",
        credit: "",
        data_url: "data:application/pdf;base64,PDF_BYTES",
      },
      {
        description: "架構圖",
        credit: "",
        data_url: "data:image/png;base64,IMAGE_BYTES",
      },
    ],
  });

  const payload = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(payload.assets[0].data_url).toBe(
    "data:application/pdf;base64,PDF_BYTES",
  );
  expect(payload.assets[1]).toEqual({ description: "架構圖", credit: "" });
  expect(fetchMock.mock.calls[0][1].body).not.toContain("IMAGE_BYTES");
});

test("postDiscoveryQuestions 優先顯示後端 detail", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false,
    status: 502,
    json: async () => ({ detail: "需求訪談生成失敗：模型忙碌" }),
  }));
  await expect(
    postDiscoveryQuestions({ prompt: "x", doc_type: "odp" }),
  ).rejects.toThrow("模型忙碌");
});

test("postDiscoveryQuestions 遇到舊後端時給出明確重啟提示", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 405 })
    .mockResolvedValueOnce({ ok: false, status: 405, json: async () => ({}) }));

  await expect(
    postDiscoveryQuestions({ prompt: "x", doc_type: "odp" }),
  ).rejects.toThrow("重新啟動 odforge serve");
});

test("postRegenerate POST 到單張重生端點並回結果", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ok: true, n: 2, slide: { title: "改後" }, preview_url: "/api/jobs/j1/preview/2.png" }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const out = await postRegenerate("j1", 2, "改成比較表");
  expect(out.ok).toBe(true);
  expect(out.n).toBe(2);
  expect(out.preview_url).toBe("/api/jobs/j1/preview/2.png");
  const [url, opts] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/jobs/j1/slides/2/regenerate");
  expect(opts.method).toBe("POST");
  expect(JSON.parse(opts.body)).toMatchObject({ instruction: "改成比較表" });
});

test("postRegenerate 非 2xx 拋錯", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
  await expect(postRegenerate("j1", 1, "x")).rejects.toThrow();
});

test("postCancel POST 到工作取消端點", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true });
  vi.stubGlobal("fetch", fetchMock);

  await postCancel("j1");

  expect(fetchMock).toHaveBeenCalledWith("/api/jobs/j1/cancel", { method: "POST" });
});

test("postCancel 非 2xx 拋錯", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409 }));
  await expect(postCancel("j1")).rejects.toThrow();
});

test("URL 組裝", () => {
  expect(eventsUrl("j1")).toBe("/api/jobs/j1/events");
  expect(previewUrl("j1", 3)).toBe("/api/jobs/j1/preview/3.png");
  expect(downloadUrl("j1")).toBe("/api/jobs/j1/download");
});

test("buildGenerateBody 帶入 mode/qa/interactive 與 doc_type", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, mode: "detailed", qa: true, interactive: false });
  expect(body).toMatchObject({ prompt: "樹", doc_type: "odp", mode: "detailed", qa: true, interactive: false });
});

test("buildGenerateBody 自動主題不送 theme 欄位", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS });
  expect("theme" in body).toBe(false);
});

test("buildGenerateBody 指定主題時送 theme", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, theme: "dark" });
  expect(body.theme).toBe("dark");
});

test("buildGenerateBody 傳遞圖片素材與指定 backend", () => {
  const assets = [
    { description: "候診區", credit: "院方", data_url: "data:image/png;base64,AA==" },
  ];
  const body = buildGenerateBody("樹", "odp", {
    ...DEFAULT_OPTS,
    backend: "custom",
    assets,
  });
  expect(body.backend).toBe("custom");
  expect(body.assets).toEqual(assets);
});

test("buildGenerateBody 沒有素材或 backend 時不送空欄位", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, assets: [] });
  expect("assets" in body).toBe(false);
  expect("backend" in body).toBe(false);
});

test("buildGenerateBody 頁數留空不送 pages,填了才送", () => {
  expect("pages" in buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS })).toBe(false);
  expect(buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 12 }).pages).toBe(12);
});

test("buildGenerateBody 頁數為 NaN 不送 pages(修掉 NaN→null 序列化 bug)", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: Number("abc") });
  expect("pages" in body).toBe(false);
});

test("buildGenerateBody 頁數超界(<3 或 >30)不送 pages", () => {
  expect("pages" in buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 2 })).toBe(false);
  expect("pages" in buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 100 })).toBe(false);
  // 邊界值 3 與 30 有效
  expect(buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 3 }).pages).toBe(3);
  expect(buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 30 }).pages).toBe(30);
});

test("buildGenerateBody 小數頁數不送 pages", () => {
  expect("pages" in buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 3.5 })).toBe(false);
});

test("buildGenerateBody 受眾與語氣附加進 prompt 尾端", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, audience: "大一新生", tone: "親切" });
  expect(body.prompt).toContain("樹");
  expect(body.prompt).toContain("受眾:大一新生");
  expect(body.prompt).toContain("語氣:親切");
  // 只填一個時只附加那一個
  expect(buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, audience: "老師" }).prompt).not.toContain("語氣");
});

test("buildGenerateBody 場合與講述時間同樣附加進 prompt 尾端", () => {
  const body = buildGenerateBody("樹", "odp", {
    ...DEFAULT_OPTS,
    purpose: "專題提案",
    duration: "15 分鐘",
  });
  expect(body.prompt).toContain("場合:專題提案");
  expect(body.prompt).toContain("講述時間:15 分鐘");
  // 空字串等同留白:不得留下一個「場合:」的空承諾。
  expect(
    buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, purpose: "  ", duration: "" }).prompt,
  ).toBe("樹");
});

test("postOutlineAction approve 打對端點與 body", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  await postOutlineAction("j1", { action: "approve" });
  const [url, opts] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/jobs/j1/outline");
  expect(opts.method).toBe("POST");
  expect(JSON.parse(opts.body)).toEqual({ action: "approve" });
});

test("postOutlineAction edit 送出改後 outline", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal("fetch", fetchMock);
  const outline: Outline = { design: null, mode: "presenter", pages: [{ role: "title", title: "改", gist: "g" }] };
  await postOutlineAction("j1", { action: "edit", outline });
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ action: "edit", outline });
});

test("postOutlineAction 非 2xx(409)拋錯", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409 }));
  await expect(postOutlineAction("j1", { action: "approve" })).rejects.toThrow();
});
