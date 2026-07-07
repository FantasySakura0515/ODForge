import { describe, expect, test, beforeEach, afterEach, vi } from "vitest";
import { postGenerate, eventsUrl, previewUrl, downloadUrl } from "./api";
import type { Outline } from "./types";

describe("API client", () => {
  const outline: Outline = {
    design: { palette: { bg: "#fff", surface: "#eee", text: "#111", muted: "#888", accent: "#234e9e" }, fonts: { display: "Noto Serif TC", body: "Noto Sans TC" } },
    mode: "presenter",
    units: [
      { n: 1, role: "title", title: "封面", gist: "開場" },
      { n: 2, role: "content", title: "內文", gist: "重點" },
    ],
  };

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  test("postGenerate POSTs outline and returns jobId", async () => {
    const mockResponse = { jobId: "job-123-abc" };
    (global.fetch as any).mockResolvedValueOnce(new Response(JSON.stringify(mockResponse), { status: 200 }));

    const jobId = await postGenerate("odp", outline);

    expect(jobId).toBe("job-123-abc");
    expect(global.fetch).toHaveBeenCalledWith("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docType: "odp", outline }),
    });
  });

  test("eventsUrl returns correct SSE endpoint", () => {
    const url = eventsUrl("job-456-def");
    expect(url).toBe("/api/jobs/job-456-def/events");
  });

  test("previewUrl returns correct preview image URL", () => {
    const url = previewUrl("job-789-ghi", 2);
    expect(url).toBe("/api/jobs/job-789-ghi/preview/2.png");
  });

  test("downloadUrl returns correct download URL", () => {
    const url = downloadUrl("job-xyz-123");
    expect(url).toBe("/api/jobs/job-xyz-123/download");
  });

  test("postGenerate handles error response", async () => {
    (global.fetch as any).mockResolvedValueOnce(new Response(JSON.stringify({ error: "Invalid outline" }), { status: 400 }));

    await expect(postGenerate("odt", outline)).rejects.toThrow();
  });

  test("postGenerate throws on network error", async () => {
    (global.fetch as any).mockRejectedValueOnce(new Error("Network error"));

    await expect(postGenerate("ods", outline)).rejects.toThrow("Network error");
  });
});
