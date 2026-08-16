export type DocType = "odp" | "odt" | "ods";
// 生成生命週期。刻意不含 QA 結果 —— 「這一頁生成到哪了」與「這一頁品檢過了沒」
// 是兩個獨立維度,擠進同一個欄位就會互相覆蓋:complete 把所有格子塗成 done 時,
// 被標紅的頁面連同它的問題一起被洗掉。
export type UnitStatus = "skeleton" | "filling" | "preview" | "regen" | "done";
// 品檢維度。undefined = 這一輪還沒有品檢結果可說。
export type UnitQa = "flagged" | "warned" | "clear";
export type GateId = "zip" | "xml" | "libreoffice" | "design";
// "skipped" = 沒人要求這道閘(使用者關掉);"unknown" = 要求了但檢查不成立
// (沒有 soffice、視覺來源拒絕、回應讀不懂)。兩者都不是 pass,但意思相反:
// 前者沒問題,後者是「這份的設計其實沒被驗過」。
export type GateStatus = "pending" | "active" | "pass" | "fail" | "skipped" | "unknown";
// 後端會回報的「已定案」結果。pending/active 是前端自己的過場狀態,後端從不送。
export type GateResultStatus = "pass" | "fail" | "skipped" | "unknown";
export type Phase = "empty" | "outline" | "await" | "generating" | "qa" | "complete" | "error";

export interface PaletteHex { bg: string; surface: string; text: string; muted: string; accent: string; }
export interface DesignSpec { palette: PaletteHex; fonts: { display: string; body: string }; }
export interface OutlineRow { role: string; title: string; gist: string; visual_intent?: string; }
export interface Outline {
  design: DesignSpec | null;
  mode: "detailed" | "presenter";
  pages: OutlineRow[];
  source_prompt?: string;
  media_assets?: { id: string; description: string; credit?: string }[];
  image_generation_available?: boolean;
}
export interface Unit {
  n: number;
  role: string;
  title: string;
  ir?: unknown;
  previewUrl?: string;
  status: UnitStatus;
  /** 最近一輪 QA 對這一頁的判定;每輪整批重算,不累積。 */
  qa?: UnitQa;
  regenPrev?: UnitStatus;
}
export interface Finding { slide_no: number; issue: string; severity: "error" | "warn"; fix_hint: string; }
export interface DroppedContent { slide_no: number; title: string; items: string[]; }

export type SseEvent =
  | { type: "outline"; data: Outline }
  | { type: "awaiting_approval"; data: Record<string, never> }
  | { type: "slide_done"; data: { n: number; slide: { layout: string; title: string; [key: string]: unknown } } }
  // F4 起的正名:unit_done 與 slide_done 同 data,後端兩者並發,reducer 收斂成同一 case。
  | { type: "unit_done"; data: { n: number; slide: { layout: string; title: string; [key: string]: unknown } } }
  // ``url: null`` 是「這一頁目前沒有縮圖」,不是「沒有消息」。重生後若算不出圖,
  // 沿用上一版的圖等於把舊頁面貼上新頁面的標籤。
  | { type: "preview_ready"; data: { n: number; url: string | null } }
  // note:閘門為什麼是這個結果(例如視覺模型沒設定、被供應商擋掉)。只有需要解釋
  // 時後端才會帶,前端照字面顯示。
  | { type: "gate_result"; data: { gate: GateId; status: GateResultStatus; note?: string } }
  | { type: "qa_round"; data: { round: number; findings: Finding[] } }
  // 版面預算被迫刪掉的內容。使用者要求過這些字,消失了就必須說出來 —— 只寫進
  // 備忘稿等同於沒說。
  | { type: "content_degraded"; data: { items: DroppedContent[] } }
  | { type: "complete"; data: { download_url: string; qa_report?: unknown } }
  // 重生換掉了成品,上一輪的品檢是對「另一份檔案」下的判斷。整批作廢比逐頁保留
  // 誠實:findings 的頁碼還在,指的內容已經不在了。
  | { type: "qa_invalidated"; data: { n: number; reason?: string } }
  | { type: "error"; data: { message: string; stage: string } };

// Client-only actions (not sent by the server). Per-slide regeneration uses the
// synchronous /regenerate endpoint, so its lifecycle is driven locally from the
// UnitDetail lightbox rather than the SSE stream.
export type LocalAction =
  | { type: "reset" }
  | { type: "regen_start"; data: { n: number } }
  | { type: "regen_done"; data: { n: number; slide: unknown; preview_url: string | null } }
  | { type: "regen_error"; data: { n: number } };

export type CockpitAction = SseEvent | LocalAction;

export interface CockpitState {
  phase: Phase;
  docType: DocType;
  jobId?: string;
  outline?: Outline;
  units: Unit[];
  gates: Record<GateId, GateStatus>;
  // 閘門結果的原因說明(後端有帶才有);例如第四道閘為什麼沒跑。
  gateNotes: Partial<Record<GateId, string>>;
  qaRounds: { round: number; findings: Finding[] }[];
  /** 因版面限制未能保留的內容,逐頁逐條。 */
  dropped: DroppedContent[];
  downloadUrl?: string;
  error?: { message: string; stage: string };
  regenTick: number;
}
