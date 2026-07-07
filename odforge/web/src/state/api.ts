import type { DocType } from "./types";

export interface GenerateBody { prompt: string; doc_type: DocType; mode?: string; theme?: string; interactive?: boolean; qa?: boolean; }

export async function postGenerate(body: GenerateBody): Promise<{ job_id: string }> {
  const res = await fetch("/api/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`generate 失敗:${res.status}`);
  return res.json();
}

export const eventsUrl = (jobId: string) => `/api/jobs/${jobId}/events`;
export const previewUrl = (jobId: string, n: number) => `/api/jobs/${jobId}/preview/${n}.png`;
export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;
