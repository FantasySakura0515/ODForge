import type { CockpitState, DocType, GateId, SseEvent, Unit } from "./types";

export function initialState(docType: DocType = "odp"): CockpitState {
  return {
    phase: "empty", docType, units: [],
    gates: { zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" },
    qaRounds: [],
  };
}

const GATE_IDS: GateId[] = ["zip", "xml", "libreoffice", "design"];

function withUnit(units: Unit[], n: number, patch: Partial<Unit>): Unit[] {
  return units.map((u) => (u.n === n ? { ...u, ...patch } : u));
}

export function cockpitReducer(state: CockpitState, event: SseEvent): CockpitState {
  switch (event.type) {
    case "outline": {
      const units: Unit[] = event.data.pages.map((p, i) => ({ n: i + 1, role: p.role, title: p.title, status: "skeleton" as const }));
      return { ...state, phase: "outline", outline: event.data, units };
    }
    case "awaiting_approval":
      return { ...state, phase: "await" };
    case "slide_done": {
      const { n, slide } = event.data;
      const cur = state.units.find((u) => u.n === n);
      const status = cur?.status === "preview" || cur?.status === "done" ? cur.status : "filling";
      return { ...state, phase: "generating", units: withUnit(state.units, n, { title: slide.title, ir: slide, status }) };
    }
    case "preview_ready": {
      const { n, url } = event.data;
      return {
        ...state, phase: "generating",
        units: withUnit(state.units, n, { previewUrl: url, status: "preview" }),
        gates: { ...state.gates, zip: "pass", xml: "pass", libreoffice: "pass" },
      };
    }
    case "qa_round": {
      let units = state.units;
      for (const f of event.data.findings) if (f.severity === "error") units = withUnit(units, f.slide_no, { status: "flagged" });
      return { ...state, phase: "qa", units, qaRounds: [...state.qaRounds, event.data], gates: { ...state.gates, design: "active" } };
    }
    case "complete":
      return {
        ...state, phase: "complete", downloadUrl: event.data.download_url,
        units: state.units.map((u) => ({ ...u, status: "done" })),
        gates: { zip: "pass", xml: "pass", libreoffice: "pass", design: "pass" },
      };
    case "error": {
      const gates = { ...state.gates };
      if ((GATE_IDS as string[]).includes(event.data.stage)) gates[event.data.stage as GateId] = "fail";
      return { ...state, phase: "error", error: event.data, gates };
    }
    default:
      return state;
  }
}
