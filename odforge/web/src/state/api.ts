import type { DocType, Outline } from "./types";

export async function postGenerate(docType: DocType, outline: Outline): Promise<string> {
  const response = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ docType, outline }),
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.statusText}`);
  }

  const data = await response.json();
  return data.jobId;
}

export function eventsUrl(jobId: string): string {
  return `/api/jobs/${jobId}/events`;
}

export function previewUrl(jobId: string, unitNo: number): string {
  return `/api/jobs/${jobId}/preview/${unitNo}.png`;
}

export function downloadUrl(jobId: string): string {
  return `/api/jobs/${jobId}/download`;
}
