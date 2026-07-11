import type { CockpitAction, CockpitState, DocType, Unit } from "./types";

export function initialState(docType: DocType = "odp"): CockpitState {
  return {
    phase: "empty", docType, units: [],
    gates: { zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" },
    qaRounds: [], regenTick: 0,
  };
}

function withUnit(units: Unit[], n: number, patch: Partial<Unit>): Unit[] {
  return units.map((u) => (u.n === n ? { ...u, ...patch } : u));
}

// Re-point an image URL at the same resource with a fresh query so the browser
// refetches it (per-slide regeneration keeps the same preview path).
function cacheBust(url: string, tick: number): string {
  return `${url.split("?")[0]}?t=${tick}`;
}

export function cockpitReducer(state: CockpitState, event: CockpitAction): CockpitState {
  switch (event.type) {
    case "outline": {
      const units: Unit[] = event.data.pages.map((p, i) => ({ n: i + 1, role: p.role, title: p.title, status: "skeleton" as const }));
      return { ...state, phase: "outline", outline: event.data, units };
    }
    case "awaiting_approval":
      return { ...state, phase: "await" };
    // slide_done 與其 F4 正名別名 unit_done 收斂於此(同 data、同處理)。
    case "unit_done":
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
    case "regen_start": {
      const cur = state.units.find((u) => u.n === event.data.n);
      return { ...state, units: withUnit(state.units, event.data.n, { status: "regen", regenPrev: cur?.status }) };
    }
    case "regen_done": {
      const { n, slide, preview_url } = event.data;
      const cur = state.units.find((u) => u.n === n);
      const tick = state.regenTick + 1;
      const base = preview_url ?? cur?.previewUrl;
      const previewUrl = base ? cacheBust(base, tick) : cur?.previewUrl;
      const title = (slide as { title?: string } | null)?.title ?? cur?.title ?? "";
      const status = cur?.regenPrev === "done" ? "done" : "preview";
      return {
        ...state, regenTick: tick,
        units: withUnit(state.units, n, { ir: slide, title, previewUrl, status, regenPrev: undefined }),
      };
    }
    case "regen_error": {
      const cur = state.units.find((u) => u.n === event.data.n);
      const status = cur?.regenPrev ?? cur?.status ?? "preview";
      return { ...state, units: withUnit(state.units, event.data.n, { status, regenPrev: undefined }) };
    }
    default:
      return state;
  }
}
