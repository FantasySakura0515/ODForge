import { afterEach, expect, test, vi } from "vitest";
import { downloadUrl, eventsUrl, postGenerate, postRegenerate, previewUrl } from "./api";

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
