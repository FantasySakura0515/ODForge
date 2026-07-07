export type DocType = "odp" | "odt" | "ods";
export type UnitStatus = "skeleton" | "filling" | "preview" | "flagged" | "regen" | "done";
export type GateId = "zip" | "xml" | "libreoffice" | "design";
export type GateStatus = "pending" | "active" | "pass" | "fail";
export type Phase = "empty" | "outline" | "await" | "generating" | "qa" | "complete" | "error";

export interface PaletteHex { bg: string; surface: string; text: string; muted: string; accent: string; }
export interface DesignSpec { palette: PaletteHex; fonts: { display: string; body: string }; }
export interface OutlineRow { n: number; role: string; title: string; gist: string; }
export interface Outline { design: DesignSpec; mode: "detailed" | "presenter"; units: OutlineRow[]; }
export interface Unit { n: number; role: string; title: string; ir?: unknown; previewUrl?: string; status: UnitStatus; }
export interface Finding { unit_no: number; issue: string; severity: "error" | "warn"; fix_hint: string; }

export type SseEvent =
  | { type: "outline"; data: Outline }
  | { type: "awaiting_approval"; data: Record<string, never> }
  | { type: "unit_done"; data: { n: number; unit: { role: string; title: string; ir?: unknown } } }
  | { type: "preview_ready"; data: { n: number; url: string } }
  | { type: "qa_round"; data: { round: number; findings: Finding[] } }
  | { type: "complete"; data: { download_url: string; qa_report?: unknown } }
  | { type: "error"; data: { message: string; stage: string } };

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
}
