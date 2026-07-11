import type { DocType, Outline } from "./types";

export interface GenerateBody {
  prompt: string;
  doc_type: DocType;
  mode?: string;
  theme?: string;
  pages?: number;
  interactive?: boolean;
  qa?: boolean;
}

/** Raw advanced-drawer selections, before they are folded into a GenerateBody. */
export interface GenerateOptions {
  mode: "presenter" | "detailed";
  /** undefined = 自動(交給 AI)→ theme 欄位不送 */
  theme?: string;
  /** undefined = 留空(交給 AI)→ pages 欄位不送 */
  pages?: number;
  /** 後端沒有此欄位,附加進 prompt 尾端 */
  audience?: string;
  /** 後端沒有此欄位,附加進 prompt 尾端 */
  tone?: string;
  qa: boolean;
  interactive: boolean;
}

/** 後端接受的頁數範圍(webapi 驗證 3–30);超界或非數字一律不送。 */
export const PAGES_MIN = 3;
export const PAGES_MAX = 30;

/** 頁數是否為可送出的有效整數(有限、落在 3–30)。NaN/超界回 false。 */
export function isValidPages(pages: number | undefined): boolean {
  return pages != null && Number.isFinite(pages) && pages >= PAGES_MIN && pages <= PAGES_MAX;
}

/**
 * Fold the advanced-drawer selections into the request body the backend expects.
 * - 自動主題(theme undefined)→ omit the theme field entirely.
 * - 頁數留空/非數字(NaN)/超界(<3 或 >30)→ omit the pages field
 *   (避免 JSON.stringify(NaN) 把 pages 序列化成 null 送給後端)。
 * - 受眾/語氣沒有對應後端欄位 → 以「受眾:X;語氣:Y」附加進 prompt 尾端。
 */
export function buildGenerateBody(prompt: string, docType: DocType, opts: GenerateOptions): GenerateBody {
  const bits: string[] = [];
  const audience = opts.audience?.trim();
  const tone = opts.tone?.trim();
  if (audience) bits.push(`受眾:${audience}`);
  if (tone) bits.push(`語氣:${tone}`);
  const finalPrompt = bits.length ? `${prompt}\n\n${bits.join(";")}` : prompt;

  const body: GenerateBody = {
    prompt: finalPrompt,
    doc_type: docType,
    mode: opts.mode,
    interactive: opts.interactive,
    qa: opts.qa,
  };
  if (opts.theme) body.theme = opts.theme;
  if (isValidPages(opts.pages)) body.pages = opts.pages;
  return body;
}

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

export type OutlineActionBody =
  | { action: "approve" }
  | { action: "edit"; outline: Outline };

/**
 * Resolve the outline-approval gate. Only valid while the job is
 * `awaiting_approval`; the backend returns 409 otherwise. After a 2xx the SSE
 * stream resumes on its own — no re-subscribe needed.
 */
export async function postOutlineAction(jobId: string, body: OutlineActionBody): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}/outline`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`outline 失敗:${res.status}`);
}

export const eventsUrl = (jobId: string) => `/api/jobs/${jobId}/events`;
export const previewUrl = (jobId: string, n: number) => `/api/jobs/${jobId}/preview/${n}.png`;
export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;
