import type { CockpitState, DocType, SseEvent, Unit } from "./types";

export function initialState(docType: DocType = "odp"): CockpitState {
  return {
    phase: "empty", docType, units: [],
    gates: { zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" },
    qaRounds: [],
  };
}

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
      };
    }
    case "gate_result":
      return { ...state, gates: { ...state.gates, [event.data.gate]: event.data.status } };
    case "qa_round": {
      let units = state.units;
      for (const f of event.data.findings) if (f.severity === "error") units = withUnit(units, f.slide_no, { status: "flagged" });
      return { ...state, phase: "qa", units, qaRounds: [...state.qaRounds, event.data], gates: { ...state.gates, design: "active" } };
    }
    case "complete":
      // gates 保持 gate_result 累積的真值,不再無條件塗綠。
      return {
        ...state, phase: "complete", downloadUrl: event.data.download_url,
        units: state.units.map((u) => ({ ...u, status: "done" })),
      };
    case "error":
      // gate 失敗由 gate_result{fail} 表達;error 只轉 phase 與存訊息,不猜測性動 gates。
      return { ...state, phase: "error", error: event.data };
    default:
      return state;
  }
}
