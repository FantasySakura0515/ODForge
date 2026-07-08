import { afterEach, expect, test, vi } from "vitest";
import { downloadUrl, eventsUrl, postGenerate, previewUrl } from "./api";

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

test("URL 組裝", () => {
  expect(eventsUrl("j1")).toBe("/api/jobs/j1/events");
  expect(previewUrl("j1", 3)).toBe("/api/jobs/j1/preview/3.png");
  expect(downloadUrl("j1")).toBe("/api/jobs/j1/download");
});
