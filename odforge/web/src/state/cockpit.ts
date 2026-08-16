import type { CockpitAction, CockpitState, DocType, Unit, UnitQa } from "./types";

export function initialState(docType: DocType = "odp"): CockpitState {
  return {
    phase: "empty", docType, units: [],
    gates: { zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" },
    gateNotes: {},
    qaRounds: [], dropped: [], regenTick: 0,
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
    case "reset":
      // 「再鍛一份」/ 取消:回到空白控制台,但沿用當前 docType。
      return initialState(state.docType);
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
      // url === null:這一頁算不出縮圖(例如重生時沒有 LibreOffice)。清掉舊圖,
      // 不要讓上一版的畫面頂著新頁面的標題繼續掛在牆上。
      if (url === null) {
        return { ...state, units: withUnit(state.units, n, { previewUrl: undefined }) };
      }
      return {
        ...state, phase: "generating",
        units: withUnit(state.units, n, { previewUrl: url, status: "preview" }),
      };
    }
    case "gate_result": {
      const { gate, status, note } = event.data;
      return {
        ...state,
        gates: { ...state.gates, [gate]: status },
        // 有帶原因就記著(沒帶就清掉舊的),讓 GateRail 說得出「為什麼」。
        gateNotes: { ...state.gateNotes, [gate]: note },
      };
    }
    case "content_degraded":
      return { ...state, dropped: [...state.dropped, ...event.data.items] };
    case "qa_round": {
      // 每一輪都是對「目前這份成品」的完整判定,所以整批重算,不在上一輪的結果
      // 上疊加。第 1 輪標紅、第 2 輪修好的頁面必須真的變回乾淨——沿用舊集合會讓
      // 已修復的頁面永遠掛著紅記號。
      const worst = new Map<number, UnitQa>();
      for (const f of event.data.findings) {
        const level: UnitQa = f.severity === "error" ? "flagged" : "warned";
        if (level === "flagged" || worst.get(f.slide_no) !== "flagged") {
          worst.set(f.slide_no, level);
        }
      }
      return {
        ...state,
        phase: "qa",
        units: state.units.map((u) => ({ ...u, qa: worst.get(u.n) ?? "clear" })),
        qaRounds: [...state.qaRounds, event.data],
        gates: { ...state.gates, design: "active" },
      };
    }
    case "complete":
      // gates 保持 gate_result 累積的真值,不再無條件塗綠。
      // 生成維度收斂成 done;QA 維度原封不動 —— 「生成完了」不代表「品檢過了」,
      // 舊版把兩者塞進同一欄位,於是 complete 一到就把 design-failed 的頁面
      // 一起洗成 done,使用者再也看不到哪一頁有問題。
      return {
        ...state, phase: "complete", downloadUrl: event.data.download_url,
        units: state.units.map((u) => ({ ...u, status: "done" as const })),
      };
    case "qa_invalidated":
      // 成品換版了,上一輪品檢的對象已經不存在。unit.qa 與 qaRounds 一起清空——
      // 只清 unit.qa 會留下一份 findings 列表,頁碼指向已經被換掉的內容,讀起來
      // 像是「這一頁還有這些問題」。design 閘由 gate_result{unknown} 表達。
      return {
        ...state,
        units: state.units.map((u) => ({ ...u, qa: undefined })),
        qaRounds: [],
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
      // preview_url === null 代表這一輪算不出圖。以前這裡會退回 cur.previewUrl,
      // 於是新內容配上一版的縮圖——看起來完全正常,而且是錯的。
      const previewUrl = preview_url ? cacheBust(preview_url, tick) : undefined;
      const title = (slide as { title?: string } | null)?.title ?? cur?.title ?? "";
      const status = cur?.regenPrev === "done" ? "done" : "preview";
      return {
        ...state, regenTick: tick,
        // 這一份成品已經不是被品檢過的那一份(後端同步把 job.qa_report 清掉、
        // design 閘改成 unknown)。前端跟著整批作廢,才不會留下綠勾與舊 findings。
        units: state.units.map((u) => (
          u.n === n
            ? { ...u, ir: slide, title, previewUrl, status, regenPrev: undefined, qa: undefined }
            : { ...u, qa: undefined }
        )),
        qaRounds: [],
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
