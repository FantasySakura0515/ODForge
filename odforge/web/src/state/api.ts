import type { DocType } from "./types";

export interface GenerateBody { prompt: string; doc_type: DocType; mode?: string; theme?: string; interactive?: boolean; qa?: boolean; }

export async function postGenerate(body: GenerateBody): Promise<{ job_id: string }> {
  const res = await fetch("/api/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`generate 失敗:${res.status}`);
  return res.json();
}

export interface RegenerateResult { ok: boolean; n: number; slide: unknown; preview_url: string | null; }

export async function postRegenerate(jobId: string, n: number, instruction: string): Promise<RegenerateResult> {
  const res = await fetch(`/api/jobs/${jobId}/slides/${n}/regenerate`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instruction }),
  });
  if (!res.ok) throw new Error(`重生失敗:${res.status}`);
  return res.json();
}

export const eventsUrl = (jobId: string) => `/api/jobs/${jobId}/events`;
export const previewUrl = (jobId: string, n: number) => `/api/jobs/${jobId}/preview/${n}.png`;
export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;
