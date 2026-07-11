import { afterEach, expect, test, vi } from "vitest";
import {
  buildGenerateBody,
  downloadUrl,
  eventsUrl,
  postGenerate,
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

test("postGenerate 非 2xx 拋錯", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
  await expect(postGenerate({ prompt: "x", doc_type: "odp" })).rejects.toThrow();
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

test("buildGenerateBody 頁數留空不送 pages,填了才送", () => {
  expect("pages" in buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS })).toBe(false);
  expect(buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, pages: 12 }).pages).toBe(12);
});

test("buildGenerateBody 受眾與語氣附加進 prompt 尾端", () => {
  const body = buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, audience: "大一新生", tone: "親切" });
  expect(body.prompt).toContain("樹");
  expect(body.prompt).toContain("受眾:大一新生");
  expect(body.prompt).toContain("語氣:親切");
  // 只填一個時只附加那一個
  expect(buildGenerateBody("樹", "odp", { ...DEFAULT_OPTS, audience: "老師" }).prompt).not.toContain("語氣");
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
