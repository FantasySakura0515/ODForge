export type DocType = "odp" | "odt" | "ods";
export type UnitStatus = "skeleton" | "filling" | "preview" | "flagged" | "regen" | "done";
export type GateId = "zip" | "xml" | "libreoffice" | "design";
export type GateStatus = "pending" | "active" | "pass" | "fail" | "skipped";
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
export interface Unit { n: number; role: string; title: string; ir?: unknown; previewUrl?: string; status: UnitStatus; regenPrev?: UnitStatus; }
export interface Finding { slide_no: number; issue: string; severity: "error" | "warn"; fix_hint: string; }

export type SseEvent =
  | { type: "outline"; data: Outline }
  | { type: "awaiting_approval"; data: Record<string, never> }
  | { type: "slide_done"; data: { n: number; slide: { layout: string; title: string; [key: string]: unknown } } }
  // F4 起的正名:unit_done 與 slide_done 同 data,後端兩者並發,reducer 收斂成同一 case。
  | { type: "unit_done"; data: { n: number; slide: { layout: string; title: string; [key: string]: unknown } } }
  | { type: "preview_ready"; data: { n: number; url: string } }
  | { type: "gate_result"; data: { gate: GateId; status: "pass" | "fail" | "skipped" } }
  | { type: "qa_round"; data: { round: number; findings: Finding[] } }
  | { type: "complete"; data: { download_url: string; qa_report?: unknown } }
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
  qaRounds: { round: number; findings: Finding[] }[];
  downloadUrl?: string;
  error?: { message: string; stage: string };
  regenTick: number;
}
