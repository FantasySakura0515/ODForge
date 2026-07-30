import type { DocType, Outline } from "./types";

export interface AssetUpload {
  description: string;
  credit: string;
  data_url: string;
}

export interface GenerateBody {
  prompt: string;
  doc_type: DocType;
  mode?: string;
  theme?: string;
  pages?: number;
  interactive?: boolean;
  qa?: boolean;
  assets?: AssetUpload[];
  backend?: "deepseek" | "ollama" | "custom";
}

export interface SessionSummary {
  id: string;
  title: string;
  prompt: string;
  status: string;
  created_at: number;
  updated_at: number;
  page_count: number;
  preview_url?: string | null;
  download_url?: string | null;
  error?: { message: string; stage: string } | null;
}

export async function getSessions(): Promise<SessionSummary[]> {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new Error(`無法讀取工作紀錄：${res.status}`);
  const data = await res.json() as { sessions?: SessionSummary[] };
  return data.sessions ?? [];
}

export interface DiscoveryQuestion {
  id: string;
  question: string;
  why: string;
  options: string[];
}

export interface DiscoveryPlan {
  summary: string;
  known_context: string[];
  questions: DiscoveryQuestion[];
  completeness: number;
}

export type DiscoveryStage =
  | "accepted"
  | "preparing"
  | "requesting"
  | "retrying"
  | "validating"
  | "complete"
  | "waiting";

export interface DiscoveryProgress {
  request_id: string;
  stage: DiscoveryStage;
  message: string;
  elapsed_ms: number;
}

export interface DiscoveryRequestOptions {
  onProgress?: (progress: DiscoveryProgress) => void;
  signal?: AbortSignal;
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
  assets?: AssetUpload[];
  backend?: "deepseek" | "ollama" | "custom";
}

/** 後端接受的頁數範圍(webapi 驗證 3–30);超界或非數字一律不送。 */
export const PAGES_MIN = 3;
export const PAGES_MAX = 30;

/** 頁數是否為可送出的有效整數(有限、落在 3–30)。NaN/超界回 false。 */
export function isValidPages(pages: number | undefined): boolean {
  return pages != null && Number.isInteger(pages) && pages >= PAGES_MIN && pages <= PAGES_MAX;
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
  if (opts.assets?.length) body.assets = opts.assets;
  if (opts.backend) body.backend = opts.backend;
  return body;
}

export async function postGenerate(body: GenerateBody): Promise<{ job_id: string }> {
  const res = await fetch("/api/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`generate 失敗:${res.status}`);
  return res.json();
}

function discoveryPayload(body: GenerateBody) {
  return {
    prompt: body.prompt,
    doc_type: body.doc_type,
    mode: body.mode,
    theme: body.theme,
    pages: body.pages,
    backend: body.backend,
    assets: body.assets?.map(({ description, credit, data_url }) => ({
      description,
      credit,
      // PDF text informs the interview. Image bytes stay client-side until
      // generation because the interview only needs their descriptions.
      data_url: data_url.startsWith("data:application/pdf;base64,")
        ? data_url
        : undefined,
    })),
  };
}

async function discoveryError(res: Response): Promise<Error> {
  if (res.status === 404 || res.status === 405) {
    return new Error("目前執行的是舊版後端，請重新啟動 odforge serve 後再試一次。");
  }
  let detail = `需求訪談失敗：${res.status}`;
  try {
    const data = await res.json() as { detail?: string };
    if (data.detail) detail = data.detail;
  } catch {
    // Keep the status-based fallback when the server did not return JSON.
  }
  return new Error(detail);
}

async function postLegacyDiscovery(
  payload: ReturnType<typeof discoveryPayload>,
  signal?: AbortSignal,
): Promise<DiscoveryPlan> {
  const res = await fetch("/api/discovery/questions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) throw await discoveryError(res);
  return res.json();
}

export async function postDiscoveryQuestions(
  body: GenerateBody,
  options: DiscoveryRequestOptions = {},
): Promise<DiscoveryPlan> {
  const payload = discoveryPayload(body);
  const res = await fetch("/api/discovery/questions/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: options.signal,
  });

  // A short compatibility window lets a just-upgraded frontend still work
  // against the previous API. A truly stale server returns 404/405 again and
  // postLegacyDiscovery surfaces an explicit restart instruction.
  if (res.status === 404 || res.status === 405) {
    return postLegacyDiscovery(payload, options.signal);
  }
  if (!res.ok) throw await discoveryError(res);
  if (!res.body) {
    throw new Error("瀏覽器無法讀取 AI 進度串流，請更新瀏覽器後再試。");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: DiscoveryPlan | undefined;

  function consume(line: string) {
    if (!line.trim()) return;
    const event = JSON.parse(line) as {
      type: "progress" | "result" | "error";
      data: DiscoveryProgress | {
        plan?: DiscoveryPlan;
        message?: string;
      };
    };
    if (event.type === "progress") {
      options.onProgress?.(event.data as DiscoveryProgress);
      return;
    }
    if (event.type === "error") {
      const data = event.data as { message?: string };
      throw new Error(data.message ?? "需求訪談生成失敗。");
    }
    const data = event.data as { plan?: DiscoveryPlan };
    if (data.plan) result = data.plan;
  }

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      consume(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
    }
    if (done) break;
  }
  consume(buffer);

  if (!result) {
    throw new Error("AI 進度串流已結束，但沒有收到訪談結果。");
  }
  return result;
}

export async function postCancel(jobId: string): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(`取消失敗:${res.status}`);
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
