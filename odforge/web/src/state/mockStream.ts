import type { DesignSpec, OutlineRow, SseEvent } from "./types";

const DESIGN: DesignSpec = {
  palette: { bg: "#fbfaf7", surface: "#eef2fb", text: "#1b2430", muted: "#7b8494", accent: "#2a5caa" },
  fonts: { display: "Noto Serif TC", body: "Noto Sans TC" },
};
const ROWS: (OutlineRow & { n: number })[] = [
  { n: 1, role: "title", title: "樹與二元樹", gist: "開場" },
  { n: 2, role: "agenda", title: "本章路線圖", gist: "議程" },
  { n: 3, role: "section", title: "一、什麼是樹", gist: "分節" },
  { n: 4, role: "two-col", title: "節點·邊·根·葉", gist: "名詞" },
  { n: 5, role: "big-fact", title: "樹高 ≈ log₂n", gist: "關鍵數字" },
  { n: 6, role: "title-content", title: "二元樹的定義", gist: "定義" },
  { n: 7, role: "comparison", title: "前序 / 中序 / 後序", gist: "走訪" },
  { n: 8, role: "title-content", title: "應用:檔案系統", gist: "應用" },
  { n: 9, role: "chart", title: "走訪法比較", gist: "圖表" },
  { n: 10, role: "section", title: "二、平衡樹", gist: "分節" },
  { n: 11, role: "agenda", title: "重點回顧", gist: "回顧" },
  { n: 12, role: "closing", title: "下一章:圖", gist: "結語" },
];

export function mockTreeEvents(): SseEvent[] {
  const pages: OutlineRow[] = ROWS.map(({ role, title, gist }) => ({ role, title, gist }));
  const evs: SseEvent[] = [{ type: "outline", data: { design: DESIGN, mode: "presenter", pages } }];
  for (const r of ROWS) evs.push({ type: "slide_done", data: { n: r.n, slide: { layout: r.role, title: r.title, bullets: [] } } });
  // render 後:封裝結構閘、XML 閘依序點亮(真事件路徑,非偽造齊亮)
  evs.push({ type: "gate_result", data: { gate: "zip", status: "pass" } });
  evs.push({ type: "gate_result", data: { gate: "xml", status: "pass" } });
  for (const r of ROWS) evs.push({ type: "preview_ready", data: { n: r.n, url: `mock:preview/${r.n}` } });
  // preview 後:LibreOffice 轉檔閘點亮
  evs.push({ type: "gate_result", data: { gate: "libreoffice", status: "pass" } });
  evs.push({ type: "qa_round", data: { round: 1, findings: [{ slide_no: 3, issue: "文字溢出框外", severity: "error", fix_hint: "減兩行內文" }] } });
  evs.push({ type: "preview_ready", data: { n: 3, url: "mock:preview/3?fixed=1" } });
  // complete 前:設計閘(demo 未開 QA gate → skipped)
  evs.push({ type: "gate_result", data: { gate: "design", status: "skipped" } });
  evs.push({ type: "complete", data: { download_url: "mock:download" } });
  return evs;
}

export function playMock(onEvent: (e: SseEvent) => void, opts: { step?: number } = {}): () => void {
  const step = opts.step ?? 250;
  const evs = mockTreeEvents();
  const timers = evs.map((e, i) => setTimeout(() => onEvent(e), step * (i + 1)));
  return () => timers.forEach(clearTimeout);
}
