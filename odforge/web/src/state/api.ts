import type { DocType, GateId, GateResultStatus, Outline } from "./types";

/**
 * 後端「有回應但拒絕」(非 2xx)的錯誤。帶狀態碼,讓呼叫端能與「根本連不上」
 * 區分——429(同時工作數上限)或 422(驗證失敗)時叫使用者去重啟伺服器是誤導。
 */
export class ApiHttpError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = "ApiHttpError";
  }
}

/** 非 2xx → 優先用後端 detail(字串)當訊息,並附上狀態碼;沒有就用 fallback。 */
async function httpError(res: Response, fallback: string): Promise<ApiHttpError> {
  let message = `${fallback}(HTTP ${res.status})`;
  try {
    const data = await res.json() as { detail?: unknown };
    if (typeof data.detail === "string" && data.detail.trim()) {
      message = `${data.detail}(HTTP ${res.status})`;
    }
  } catch {
    // 非 JSON 回應(或無 body)→ 保留 fallback + 狀態碼。
  }
  return new ApiHttpError(message, res.status);
}

export interface AssetUpload {
  description: string;
  credit: string;
  data_url: string;
}

/** 一組設計代幣;與後端 ir.DesignSpec 同形。 */
export interface DesignSpec {
  palette: {
    bg: string;
    surface: string;
    text: string;
    muted: string;
    accent: string;
  };
  fonts: { display: string; body: string };
  scale: "compact" | "standard" | "display";
  mode?: "presenter" | "detailed";
}

export type LanguageId = "zh-TW" | "en" | "bilingual";
/** 版式 id;與後端 themes.STYLES 對應。 */
export type StyleId =
  | "classic"
  | "report"
  | "academic"
  | "keynote"
  | "editorial"
  | "zen";
export type LogoPlacement = "cover" | "cover-closing" | "all";

export interface GenerateBody {
  prompt: string;
  doc_type: DocType;
  mode?: string;
  /** 內建主題 id;與 design 互斥(自訂範本走 design)。 */
  theme?: string;
  /** 自訂範本的設計代幣;送了就整份鎖定這一組。 */
  design?: DesignSpec;
  /** 版式(封面構圖、標題記號、分節頁、頁尾家具);與配色正交。 */
  style?: StyleId;
  language?: LanguageId;
  pages?: number;
  /** 封面署名(單位／講者／日期)。 */
  byline?: string;
  /** 封面校徽;PNG/JPEG 的 base64 data URI。 */
  logo?: AssetUpload;
  logo_placement?: LogoPlacement;
  interactive?: boolean;
  qa?: boolean;
  assets?: AssetUpload[];
  backend?: "deepseek" | "ollama" | "custom";
}

/**
 * 範本庫的一列。內建(builtin)與自訂的差別不只是能不能刪:
 * 內建以 `theme: <id>` 送出(渲染器本來就認得的預設),自訂則整包送 `design`。
 * 呼叫端不必記這條規則——`applyTemplate` 依這個旗標決定要送哪一個欄位。
 */
export interface DeckTemplate {
  id: string;
  name: string;
  design: DesignSpec;
  style: StyleId;
  builtin: boolean;
  source: "builtin" | "custom" | "extracted";
  created_at: number;
}

export interface LanguageOption {
  id: LanguageId;
  label: string;
}

export interface StyleOption {
  id: StyleId;
  label: string;
  blurb: string;
}

export interface TemplatesReport {
  templates: DeckTemplate[];
  languages: LanguageOption[];
  styles: StyleOption[];
}

export async function getTemplates(): Promise<TemplatesReport> {
  const res = await fetch("/api/templates");
  if (!res.ok) throw await httpError(res, "無法讀取範本庫");
  return res.json();
}

export async function saveTemplate(body: {
  name: string;
  design: DesignSpec;
  style: StyleId;
  source?: "custom" | "extracted";
  id?: string;
}): Promise<DeckTemplate> {
  const res = await fetch("/api/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await httpError(res, "範本儲存失敗");
  return res.json();
}

export async function deleteTemplate(id: string): Promise<void> {
  const res = await fetch(`/api/templates/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await httpError(res, "範本刪除失敗");
}

/** 上傳現有 .otp/.odp 範本檔,抽出設計代幣供預覽(不會直接存進範本庫)。 */
export async function extractTemplateDesign(dataUrl: string): Promise<DesignSpec> {
  const res = await fetch("/api/templates/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_url: dataUrl }),
  });
  if (!res.ok) throw await httpError(res, "無法讀取這個範本檔");
  const data = await res.json() as { design: DesignSpec };
  return data.design;
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

export interface SourceStatus {
  name: string;
  available: boolean;
  /** 為什麼不能用(缺金鑰、找不到 CLI…);available 時為空字串。 */
  reason: string;
}

export interface SourcesReport {
  text: SourceStatus[];
  vision: SourceStatus[];
  defaults: { text: string; vision: string };
}

/**
 * 目前有哪些模型來源、哪些真的跑得起來。
 *
 * 前端必須問過才敢承諾:設計品質檢查的開關預設是開的,但若環境沒有視覺來源,
 * 打勾等於承諾一件不會發生的事——使用者以為第四道閘跑過了,實際上那一格從頭到尾
 * 都是空的。快取一次(每個 session 問一次就夠),失敗時回 undefined 讓呼叫端降級。
 */
let sourcesCache: Promise<SourcesReport> | undefined;

export function getSources(): Promise<SourcesReport> {
  sourcesCache ??= fetch("/api/sources")
    .then(async (res) => {
      if (!res.ok) throw await httpError(res, "無法讀取模型來源");
      return res.json() as Promise<SourcesReport>;
    })
    // 只快取「成功的答案」。舊版連 rejected promise 一起留著,於是後端晚三秒
    // 才起來、或網路抖一下,這個 session 就永遠問不到來源了——每次重試都拿回
    // 同一個早就失敗的 promise,連一次真正的請求都不會再送出去。
    .catch((error) => {
      sourcesCache = undefined;
      throw error;
    });
  return sourcesCache;
}

/** Test seam: drop the cached answer (also used when the backend restarts). */
export function resetSourcesCache(): void {
  sourcesCache = undefined;
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
  /** 自訂範本的設計代幣;與 theme 互斥,送了就整份鎖定 */
  design?: DesignSpec;
  /** 版式;undefined = 用配色配對的預設版式 */
  style?: StyleId;
  /** 預設 zh-TW;非預設才送 */
  language?: LanguageId;
  /** 封面署名;空字串不送 */
  byline?: string;
  /** 封面校徽;沒選就不送(placement 也隨之省略) */
  logo?: AssetUpload;
  logoPlacement?: LogoPlacement;
  /** undefined = 留空(交給 AI)→ pages 欄位不送 */
  pages?: number;
  /** 後端沒有此欄位,附加進 prompt 尾端 */
  purpose?: string;
  /** 後端沒有此欄位,附加進 prompt 尾端 */
  audience?: string;
  /** 後端沒有此欄位,附加進 prompt 尾端 */
  tone?: string;
  /** 後端沒有此欄位,附加進 prompt 尾端 */
  duration?: string;
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
 * - 場合/受眾/語氣/講述時間沒有對應後端欄位 → 以「場合:X;受眾:Y」的形式附加進
 *   prompt 尾端(訪談與大綱兩段都讀同一段文字,所以填了就真的會影響輸出)。
 */
export function buildGenerateBody(prompt: string, docType: DocType, opts: GenerateOptions): GenerateBody {
  const bits: string[] = [];
  const purpose = opts.purpose?.trim();
  const audience = opts.audience?.trim();
  const tone = opts.tone?.trim();
  const duration = opts.duration?.trim();
  if (purpose) bits.push(`場合:${purpose}`);
  if (audience) bits.push(`受眾:${audience}`);
  if (tone) bits.push(`語氣:${tone}`);
  if (duration) bits.push(`講述時間:${duration}`);
  const finalPrompt = bits.length ? `${prompt}\n\n${bits.join(";")}` : prompt;

  const body: GenerateBody = {
    prompt: finalPrompt,
    doc_type: docType,
    mode: opts.mode,
    interactive: opts.interactive,
    qa: opts.qa,
  };
  // 自訂範本(design)勝過內建主題:兩個都送只會讓後端在兩種「使用者指定的樣子」
  // 之間挑一個,而使用者只挑過一次。
  if (opts.design) body.design = opts.design;
  else if (opts.theme) body.theme = opts.theme;
  if (opts.style) body.style = opts.style;
  if (isValidPages(opts.pages)) body.pages = opts.pages;
  if (opts.language && opts.language !== "zh-TW") body.language = opts.language;
  const byline = opts.byline?.trim();
  if (byline) body.byline = byline;
  // 沒有校徽時連 placement 都不送:一個「校徽放哪裡」的設定配上不存在的校徽,
  // 只會讓後續讀 job metadata 的人以為有一張圖不見了。
  if (opts.logo) {
    body.logo = opts.logo;
    body.logo_placement = opts.logoPlacement ?? "cover-closing";
  }
  if (opts.assets?.length) body.assets = opts.assets;
  if (opts.backend) body.backend = opts.backend;
  return body;
}

/** 把範本庫的一列折成 GenerateOptions 的兩個欄位(內建走 theme,自訂走 design)。 */
export function templateSelection(
  template: DeckTemplate | undefined,
): Pick<GenerateOptions, "theme" | "design" | "style"> {
  if (!template) return {};
  // 版式一律明講:內建範本的配色與版式是配對好的,靠後端預設也對得起來,但送
  // 出去的 body 若沒寫,使用者在畫廊看到的與生成出來的就不是同一份契約。
  return template.builtin
    ? { theme: template.id, style: template.style }
    : { design: template.design, style: template.style };
}

export async function postGenerate(body: GenerateBody): Promise<{ job_id: string }> {
  const res = await fetch("/api/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw await httpError(res, "generate 失敗");
  return res.json();
}

function discoveryPayload(body: GenerateBody) {
  return {
    prompt: body.prompt,
    doc_type: body.doc_type,
    mode: body.mode,
    theme: body.theme,
    // 訪談仍以繁體中文提問;這只是讓讀題模型知道成品要寫成哪一種語言。
    // (後端 DiscoveryBody 不收 design / byline / logo,送了會被 422 擋下。)
    language: body.language,
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
  if (!res.ok) throw await httpError(res, "取消失敗");
}

export interface RegenerateResult {
  ok: boolean;
  n: number;
  slide: unknown;
  preview_url: string | null;
  /** 交付的成品版本號;每次成功重生 +1(失敗會回滾,號碼不動)。 */
  version?: number;
  /**
   * 重生後重新算過的四道閘。成品換了一版,上一版的綠勾就不再代表這一版——
   * 尤其設計閘:視覺評審看的是被換掉的那一頁,結果一律退回 unknown。
   * 型別與 SSE 的 gate_result 同一組(已定案的結果,不含 pending/active),
   * 這些物件會被原樣 dispatch 進 reducer。
   */
  gates?: { gate: GateId; status: GateResultStatus; note?: string }[];
}

export async function postRegenerate(jobId: string, n: number, instruction: string): Promise<RegenerateResult> {
  const res = await fetch(`/api/jobs/${jobId}/slides/${n}/regenerate`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instruction }),
  });
  if (!res.ok) throw await httpError(res, "重生失敗");
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
  if (!res.ok) throw await httpError(res, "outline 失敗");
}

export const eventsUrl = (jobId: string) => `/api/jobs/${jobId}/events`;
export const previewUrl = (jobId: string, n: number) => `/api/jobs/${jobId}/preview/${n}.png`;
export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;
