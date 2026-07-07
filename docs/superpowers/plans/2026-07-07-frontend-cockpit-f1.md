# ODForge 前端 F1(核心控制台)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 ODForge Web 前端的 F1 垂直切片——空台輸入 → SSE 逐單元點亮 → 三道格式閘打勾 → 下載,型別無關 shell 先接 `.odp`,可用 mock SSE 獨立開發與 demo。

**Architecture:** React 單頁,`useReducer` 狀態機吃 SSE 事件吐 UI 狀態(與後端 Phase 18 契約 1:1)。無畫布、無編輯器——使用者只送 prompt、看逐單元點亮、下載。核心邏輯(reducer / SSE 解析 / API)是純函式,優先 TDD;元件用 React Testing Library 驗渲染。視覺真理來源是已核可的 Artifact 與設計 spec §4。

**Tech Stack:** React 18 + Vite + TypeScript + 原生 CSS 變數(design tokens);Vitest + @testing-library/react + jsdom;原生 `EventSource`(SSE)。**不用** Tailwind / shadcn / 任何 UI 元件庫。

**設計 spec(規格源):** `docs/superpowers/specs/2026-07-07-frontend-cockpit-design.md`。每個 Task 標註對應 §。
**視覺真理來源(可互動,樣式從此移植):** Artifact「ODForge 前端 · 一個產品兩種主題」<https://claude.ai/code/artifact/5c2e1028-3996-449e-a3ef-03b1887c8f03>。

## Global Constraints

- **技術鐵則:** React + Vite + TypeScript + 原生 CSS 變數。禁用 Tailwind、shadcn、任何 UI 元件庫(客製辨識度不可被通用預設稀釋,spec §11)。
- **狀態:** 單一 `useReducer`;所有畫面變化來自 SSE 事件的純轉移(spec §9)。SSE 用原生 `EventSource`。
- **型別無關:** `DocType = "odp" | "odt" | "ods"` 從第一天貫穿 state 與元件 props;F1 只接 `odp`,但不得把 odp 寫死進 shell(spec §6、§12)。
- **契約詞彙:** 用 `unit`(涵蓋 slide/page/sheet),不用 `slide`;事件 `unit_done`、端點 `/units/{n}/regenerate`(spec §10)。
- **無 auth:** 本機單人工具,無登入/帳號/多租戶(spec §1)。
- **反設計:** 無畫布、拖拉、屬性面板;使用者只碰 prompt(F1)、之後大綱/配色/mode/重生(spec §2)。
- **主題:** light/dark 皆完整;`data-theme` 覆寫 `prefers-color-scheme` 兩個方向(spec §4)。
- **編碼:** 所有原始檔 UTF-8;繁體中文文案(zh-TW)。
- **commit:** 乾淨的 conventional commits,**不加任何 AI 署名或 co-author trailer**(專案鐵則,repo 將公開)。每個 Task 結束 commit,不自動 push。
- **平台:** 開發機 Windows / PowerShell;Node 18+ LTS;npm。前端子專案在 `odforge/web/`,不污染 Python 套件。

---

## 檔案結構(F1 交付後)

```
odforge/web/
├── package.json            # deps + scripts(dev/build/test)
├── vite.config.ts          # React plugin + Vitest(jsdom)
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx            # React 掛載
│   ├── App.tsx             # 組裝:reducer + SSE + 主題;四區 shell
│   ├── state/
│   │   ├── types.ts        # 契約型別(DocType/Unit/SseEvent/CockpitState…)
│   │   ├── cockpit.ts      # initialState + cockpitReducer(狀態機核心)
│   │   ├── sse.ts          # parseSseEvent + subscribeJob(EventSource)
│   │   ├── api.ts          # postGenerate / eventsUrl / previewUrl / downloadUrl
│   │   └── mockStream.ts   # dev/demo 用:tree fixture 事件流(不必等後端)
│   ├── theme/
│   │   ├── tokens.css      # 兩主題 CSS 變數(spec §4.1)
│   │   └── useTheme.ts     # data-theme 切換 + 跟隨系統
│   ├── components/
│   │   ├── PromptBar.tsx   # 輸入 + docType 分頁 + 送出
│   │   ├── OutlineRail.tsx # 大綱 + 色盤(唯讀顯示)
│   │   ├── PreviewStage.tsx# 單元網格
│   │   ├── UnitCell.tsx    # 單元格 5 態 + 縮圖
│   │   ├── GateRail.tsx    # 四道閘(F1 用三道格式閘)
│   │   ├── StatusNarrator.tsx
│   │   └── DownloadDock.tsx
│   └── styles/app.css      # shell 版面 + 元件樣式(從 Artifact 移植)
└── (tests 與被測檔同層,*.test.ts / *.test.tsx)
```

---

## Task 1: 專案鷹架 + 測試骨架

**Files:**
- Create: `odforge/web/package.json`, `odforge/web/vite.config.ts`, `odforge/web/tsconfig.json`, `odforge/web/index.html`, `odforge/web/src/main.tsx`, `odforge/web/src/App.tsx`, `odforge/web/src/smoke.test.ts`

**Interfaces:**
- Produces:可運行的 Vite React TS 專案;`npm test` 跑 Vitest(jsdom);`npm run dev`/`npm run build` 可用。

- [ ] **Step 1: 寫 package.json**

```json
{
  "name": "odforge-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.8",
    "@testing-library/react": "^16.0.1",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^25.0.0",
    "typescript": "^5.5.4",
    "vite": "^5.4.2",
    "vitest": "^2.0.5"
  }
}
```

- [ ] **Step 2: 寫 vite.config.ts、tsconfig.json、index.html、main.tsx、最小 App.tsx**

`vite.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: { environment: "jsdom", globals: true, setupFiles: [] },
});
```
`tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020", "useDefineForClassFields": true, "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext", "skipLibCheck": true, "moduleResolution": "bundler",
    "resolveJsonModule": true, "isolatedModules": true, "noEmit": true, "jsx": "react-jsx",
    "strict": true, "noUnusedLocals": true, "noUnusedParameters": true
  },
  "include": ["src"],
  "exclude": ["src/**/*.test.ts", "src/**/*.test.tsx"]
}
```
> `exclude` 讓 `npm run build`(`tsc -b`)略過測試檔;Vitest 有自己的測試 glob,照跑不誤。
`index.html`:
```html
<!doctype html>
<html lang="zh-Hant">
  <head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>文鍛 ODForge</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
```
`src/main.tsx`:
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
```
`src/App.tsx`(暫時最小,Task 10 補完):
```tsx
export default function App() {
  return <div>文鍛 ODForge</div>;
}
```

- [ ] **Step 3: 寫冒煙測試 `src/smoke.test.ts`**

```ts
import { expect, test } from "vitest";

test("測試框架運作", () => {
  expect(1 + 1).toBe(2);
});
```

- [ ] **Step 4: 安裝並跑測試**

Run(在 `odforge/web/`):`npm install && npm test`
Expected: `1 passed`;`npm run build` 成功產出 `dist/`。

- [ ] **Step 5: Commit**

```bash
git add odforge/web
git commit -m "chore: scaffold web frontend (vite + react + ts + vitest)"
```

---

## Task 2: 契約型別 + SSE 事件解析

**Files:**
- Create: `odforge/web/src/state/types.ts`, `odforge/web/src/state/sse.ts`, `odforge/web/src/state/sse.test.ts`

**Interfaces:**
- Produces:
  - `types.ts` 匯出:`DocType`、`UnitStatus`、`GateId`、`GateStatus`、`Phase`、`PaletteHex`、`DesignSpec`、`OutlineRow`、`Outline`、`Unit`、`Finding`、`SseEvent`、`CockpitState`。
  - `sse.ts`:`parseSseEvent(type: string, data: string): SseEvent | null`——把 SSE 的 `event:` 名稱 + JSON `data:` 字串解析成 `SseEvent`;未知事件或壞 JSON 回 `null`。

- [ ] **Step 1: 寫 `types.ts`**

```ts
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
```

- [ ] **Step 2: 寫失敗測試 `sse.test.ts`**

```ts
import { expect, test } from "vitest";
import { parseSseEvent } from "./sse";

test("解析 outline 事件", () => {
  const ev = parseSseEvent("outline", JSON.stringify({ design: { palette: {}, fonts: {} }, mode: "presenter", units: [] }));
  expect(ev?.type).toBe("outline");
});

test("解析 unit_done 事件帶 n", () => {
  const ev = parseSseEvent("unit_done", JSON.stringify({ n: 3, unit: { role: "content", title: "x" } }));
  expect(ev).toEqual({ type: "unit_done", data: { n: 3, unit: { role: "content", title: "x" } } });
});

test("未知事件名回 null", () => {
  expect(parseSseEvent("bogus", "{}")).toBeNull();
});

test("壞 JSON 回 null 不拋", () => {
  expect(parseSseEvent("preview_ready", "{not json")).toBeNull();
});
```

- [ ] **Step 3: 跑測試確認失敗**

Run:`npx vitest run src/state/sse.test.ts`
Expected: FAIL(`parseSseEvent` 不存在)

- [ ] **Step 4: 寫 `sse.ts` 的 `parseSseEvent`**

```ts
import type { SseEvent } from "./types";

const KNOWN = new Set(["outline", "awaiting_approval", "unit_done", "preview_ready", "qa_round", "complete", "error"]);

export function parseSseEvent(type: string, data: string): SseEvent | null {
  if (!KNOWN.has(type)) return null;
  try {
    return { type, data: JSON.parse(data) } as SseEvent;
  } catch {
    return null;
  }
}
```

- [ ] **Step 5: 跑測試確認通過並 commit**

Run:`npx vitest run src/state/sse.test.ts` → Expected: PASS
```bash
git add odforge/web/src/state/types.ts odforge/web/src/state/sse.ts odforge/web/src/state/sse.test.ts
git commit -m "feat(web): contract types + sse event parser"
```

---

## Task 3: 狀態機核心(cockpitReducer)

**Files:**
- Create: `odforge/web/src/state/cockpit.ts`, `odforge/web/src/state/cockpit.test.ts`

**Interfaces:**
- Consumes:`types.ts` 的全部型別;`SseEvent`。
- Produces:
  - `initialState(docType?: DocType): CockpitState`
  - `cockpitReducer(state: CockpitState, event: SseEvent): CockpitState`——純函式,每個事件一個轉移。
  - 轉移規則:`outline`→ phase="outline"、outline 存入、依 `units` 建骨架 `Unit[]`;`unit_done`→ phase="generating"、對應 unit 填 role/title/ir 且 status="filling"(若已 preview 則保留);`preview_ready`→ 該 unit previewUrl+status="preview",並把 zip/xml/libreoffice 三閘設為 "pass"(渲染成功的證據);`complete`→ phase="complete"、downloadUrl、所有 unit status="done"、四閘 pass;`qa_round`→ phase="qa"、push、severity==="error" 的 unit 標 "flagged"、design 閘 "active";`awaiting_approval`→ phase="await";`error`→ phase="error"、error 存入,stage 命中閘 id 則該閘 "fail"。

- [ ] **Step 1: 寫失敗測試 `cockpit.test.ts`**

```ts
import { describe, expect, test } from "vitest";
import { cockpitReducer, initialState } from "./cockpit";
import type { Outline, SseEvent } from "./types";

const outline: Outline = {
  design: { palette: { bg: "#fff", surface: "#eee", text: "#111", muted: "#888", accent: "#234e9e" }, fonts: { display: "Noto Serif TC", body: "Noto Sans TC" } },
  mode: "presenter",
  units: [ { n: 1, role: "title", title: "封面", gist: "開場" }, { n: 2, role: "content", title: "內文", gist: "重點" } ],
};
const send = (s = initialState(), ...evs: SseEvent[]) => evs.reduce(cockpitReducer, s);

describe("cockpitReducer", () => {
  test("初始狀態", () => {
    const s = initialState();
    expect(s.phase).toBe("empty");
    expect(s.docType).toBe("odp");
    expect(s.units).toEqual([]);
    expect(s.gates).toEqual({ zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" });
  });

  test("outline 建立骨架單元", () => {
    const s = send(initialState(), { type: "outline", data: outline });
    expect(s.phase).toBe("outline");
    expect(s.units.map((u) => u.status)).toEqual(["skeleton", "skeleton"]);
    expect(s.units[0].title).toBe("封面");
  });

  test("unit_done 填內容、進 generating", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "unit_done", data: { n: 1, unit: { role: "title", title: "封面!" } } });
    expect(s.phase).toBe("generating");
    expect(s.units[0].status).toBe("filling");
    expect(s.units[0].title).toBe("封面!");
  });

  test("preview_ready 設縮圖並讓三道格式閘通過", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } });
    expect(s.units[0].previewUrl).toContain("preview/1.png");
    expect(s.units[0].status).toBe("preview");
    expect(s.gates.zip).toBe("pass");
    expect(s.gates.libreoffice).toBe("pass");
  });

  test("complete:全單元 done、四閘 pass、有下載連結", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "complete", data: { download_url: "/api/jobs/x/download" } });
    expect(s.phase).toBe("complete");
    expect(s.downloadUrl).toContain("download");
    expect(s.units.every((u) => u.status === "done")).toBe(true);
    expect(Object.values(s.gates).every((g) => g === "pass")).toBe(true);
  });

  test("qa_round 標記 error 單元並讓設計閘 active", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "qa_round", data: { round: 1, findings: [{ unit_no: 2, issue: "溢出", severity: "error", fix_hint: "減字" }] } });
    expect(s.phase).toBe("qa");
    expect(s.units[1].status).toBe("flagged");
    expect(s.gates.design).toBe("active");
    expect(s.qaRounds[0].round).toBe(1);
  });

  test("error 把對應閘設 fail", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "error", data: { message: "轉檔失敗", stage: "libreoffice" } });
    expect(s.phase).toBe("error");
    expect(s.gates.libreoffice).toBe("fail");
    expect(s.error?.message).toBe("轉檔失敗");
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/state/cockpit.test.ts`
Expected: FAIL(`initialState` / `cockpitReducer` 不存在)

- [ ] **Step 3: 寫 `cockpit.ts`**

```ts
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
      const units: Unit[] = event.data.units.map((r) => ({ n: r.n, role: r.role, title: r.title, status: "skeleton" }));
      return { ...state, phase: "outline", outline: event.data, units };
    }
    case "awaiting_approval":
      return { ...state, phase: "await" };
    case "unit_done": {
      const { n, unit } = event.data;
      const cur = state.units.find((u) => u.n === n);
      const status = cur?.status === "preview" || cur?.status === "done" ? cur.status : "filling";
      return { ...state, phase: "generating", units: withUnit(state.units, n, { role: unit.role, title: unit.title, ir: unit.ir, status }) };
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
      for (const f of event.data.findings) if (f.severity === "error") units = withUnit(units, f.unit_no, { status: "flagged" });
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
```

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/state/cockpit.test.ts`
Expected: PASS(7 passed)

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/state/cockpit.ts odforge/web/src/state/cockpit.test.ts
git commit -m "feat(web): cockpit state machine reducer"
```

---

## Task 4: API 客戶端

**Files:**
- Create: `odforge/web/src/state/api.ts`, `odforge/web/src/state/api.test.ts`

**Interfaces:**
- Consumes:`DocType`。
- Produces:
  - `postGenerate(body: { prompt: string; doc_type: DocType; mode?: string; theme?: string; interactive?: boolean; qa?: boolean }): Promise<{ job_id: string }>`(POST `/api/generate`)。
  - `eventsUrl(jobId: string): string` → `/api/jobs/{id}/events`
  - `previewUrl(jobId: string, n: number): string` → `/api/jobs/{id}/preview/{n}.png`
  - `downloadUrl(jobId: string): string` → `/api/jobs/{id}/download`

- [ ] **Step 1: 寫失敗測試 `api.test.ts`**

```ts
import { afterEach, expect, test, vi } from "vitest";
import { downloadUrl, eventsUrl, postGenerate, previewUrl } from "./api";

afterEach(() => vi.restoreAllMocks());

test("postGenerate POST /api/generate 並回 job_id", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "abc" }) });
  vi.stubGlobal("fetch", fetchMock);
  const out = await postGenerate({ prompt: "樹", doc_type: "odp" });
  expect(out.job_id).toBe("abc");
  const [url, opts] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/generate");
  expect(opts.method).toBe("POST");
  expect(JSON.parse(opts.body)).toMatchObject({ prompt: "樹", doc_type: "odp" });
});

test("postGenerate 非 2xx 拋錯", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
  await expect(postGenerate({ prompt: "x", doc_type: "odp" })).rejects.toThrow();
});

test("URL 組裝", () => {
  expect(eventsUrl("j1")).toBe("/api/jobs/j1/events");
  expect(previewUrl("j1", 3)).toBe("/api/jobs/j1/preview/3.png");
  expect(downloadUrl("j1")).toBe("/api/jobs/j1/download");
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/state/api.test.ts` → Expected: FAIL

- [ ] **Step 3: 寫 `api.ts`**

```ts
import type { DocType } from "./types";

export interface GenerateBody { prompt: string; doc_type: DocType; mode?: string; theme?: string; interactive?: boolean; qa?: boolean; }

export async function postGenerate(body: GenerateBody): Promise<{ job_id: string }> {
  const res = await fetch("/api/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`generate 失敗:${res.status}`);
  return res.json();
}

export const eventsUrl = (jobId: string) => `/api/jobs/${jobId}/events`;
export const previewUrl = (jobId: string, n: number) => `/api/jobs/${jobId}/preview/${n}.png`;
export const downloadUrl = (jobId: string) => `/api/jobs/${jobId}/download`;
```

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/state/api.test.ts` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/state/api.ts odforge/web/src/state/api.test.ts
git commit -m "feat(web): api client"
```

---

## Task 5: SSE 訂閱(subscribeJob)

**Files:**
- Modify: `odforge/web/src/state/sse.ts`(加 `subscribeJob`)
- Create: `odforge/web/src/state/subscribe.test.ts`

**Interfaces:**
- Consumes:`parseSseEvent`、`eventsUrl`、`SseEvent`。
- Produces:`subscribeJob(jobId: string, onEvent: (e: SseEvent) => void, EventSourceCtor?: typeof EventSource): () => void`——對每個已知事件名 `addEventListener`,解析後回呼;回傳 `close` 函式。第三參數可注入假 EventSource(測試用)。

- [ ] **Step 1: 寫失敗測試 `subscribe.test.ts`**

```ts
import { expect, test, vi } from "vitest";
import { subscribeJob } from "./sse";
import type { SseEvent } from "./types";

class FakeEventSource {
  listeners: Record<string, (e: { data: string }) => void> = {};
  closed = false;
  constructor(public url: string) {}
  addEventListener(type: string, cb: (e: { data: string }) => void) { this.listeners[type] = cb; }
  close() { this.closed = true; }
  emit(type: string, data: unknown) { this.listeners[type]?.({ data: JSON.stringify(data) }); }
}

test("subscribeJob 把 SSE 事件解析後回呼", () => {
  let src!: FakeEventSource;
  const Ctor = vi.fn((url: string) => (src = new FakeEventSource(url))) as unknown as typeof EventSource;
  const seen: SseEvent[] = [];
  const close = subscribeJob("j1", (e) => seen.push(e), Ctor);

  expect(src.url).toBe("/api/jobs/j1/events");
  src.emit("outline", { design: { palette: {}, fonts: {} }, mode: "presenter", units: [] });
  src.emit("complete", { download_url: "/d" });
  expect(seen.map((e) => e.type)).toEqual(["outline", "complete"]);

  close();
  expect(src.closed).toBe(true);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/state/subscribe.test.ts` → Expected: FAIL(`subscribeJob` 不存在)

- [ ] **Step 3: 在 `sse.ts` 加 `subscribeJob`**

```ts
import { eventsUrl } from "./api";
import type { SseEvent } from "./types";

const EVENT_NAMES = ["outline", "awaiting_approval", "unit_done", "preview_ready", "qa_round", "complete", "error"];

export function subscribeJob(
  jobId: string,
  onEvent: (e: SseEvent) => void,
  EventSourceCtor: typeof EventSource = EventSource,
): () => void {
  const es = new EventSourceCtor(eventsUrl(jobId));
  for (const name of EVENT_NAMES) {
    es.addEventListener(name, (ev) => {
      const parsed = parseSseEvent(name, (ev as MessageEvent).data);
      if (parsed) onEvent(parsed);
    });
  }
  return () => es.close();
}
```
(保留既有 `parseSseEvent`;檔頂補上 `import { eventsUrl }`。)

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/state/subscribe.test.ts` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/state/sse.ts odforge/web/src/state/subscribe.test.ts
git commit -m "feat(web): sse subscription over EventSource"
```

---

## Task 6: Mock 事件流(獨立開發/demo 用)

**Files:**
- Create: `odforge/web/src/state/mockStream.ts`, `odforge/web/src/state/mockStream.test.ts`

**Interfaces:**
- Consumes:`SseEvent`。
- Produces:`mockTreeEvents(): SseEvent[]`——「資料結構第三章:樹與二元樹」12 單元的完整事件序列(outline → 12×unit_done → 12×preview_ready → qa_round → complete);`playMock(onEvent, opts?): () => void`——依序以 `setTimeout` 播放,回傳取消函式(注入 `step` 間隔便於測試,預設 250ms)。

- [ ] **Step 1: 寫失敗測試 `mockStream.test.ts`**

```ts
import { expect, test, vi } from "vitest";
import { mockTreeEvents, playMock } from "./mockStream";
import type { SseEvent } from "./types";

test("mockTreeEvents 以 outline 開頭、complete 結尾、含 12 單元", () => {
  const evs = mockTreeEvents();
  expect(evs[0].type).toBe("outline");
  expect(evs[evs.length - 1].type).toBe("complete");
  const outline = evs[0] as Extract<SseEvent, { type: "outline" }>;
  expect(outline.data.units).toHaveLength(12);
  expect(evs.filter((e) => e.type === "unit_done")).toHaveLength(12);
});

test("playMock 依序送出全部事件", () => {
  vi.useFakeTimers();
  const seen: SseEvent[] = [];
  playMock((e) => seen.push(e), { step: 10 });
  vi.advanceTimersByTime(10 * (mockTreeEvents().length + 1));
  expect(seen.length).toBe(mockTreeEvents().length);
  expect(seen[0].type).toBe("outline");
  vi.useRealTimers();
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/state/mockStream.test.ts` → Expected: FAIL

- [ ] **Step 3: 寫 `mockStream.ts`**

```ts
import type { DesignSpec, OutlineRow, SseEvent } from "./types";

const DESIGN: DesignSpec = {
  palette: { bg: "#fbfaf7", surface: "#eef2fb", text: "#1b2430", muted: "#7b8494", accent: "#2a5caa" },
  fonts: { display: "Noto Serif TC", body: "Noto Sans TC" },
};
const ROWS: OutlineRow[] = [
  { n: 1, role: "title", title: "樹與二元樹", gist: "開場" },
  { n: 2, role: "agenda", title: "本章路線圖", gist: "議程" },
  { n: 3, role: "section", title: "一、什麼是樹", gist: "分節" },
  { n: 4, role: "two-col", title: "節點·邊·根·葉", gist: "名詞" },
  { n: 5, role: "big-fact", title: "樹高 ≈ log₂n", gist: "關鍵數字" },
  { n: 6, role: "content", title: "二元樹的定義", gist: "定義" },
  { n: 7, role: "compare", title: "前序 / 中序 / 後序", gist: "走訪" },
  { n: 8, role: "content", title: "應用:檔案系統", gist: "應用" },
  { n: 9, role: "chart", title: "走訪法比較", gist: "圖表" },
  { n: 10, role: "section", title: "二、平衡樹", gist: "分節" },
  { n: 11, role: "agenda", title: "重點回顧", gist: "回顧" },
  { n: 12, role: "closing", title: "下一章:圖", gist: "結語" },
];

export function mockTreeEvents(): SseEvent[] {
  const evs: SseEvent[] = [{ type: "outline", data: { design: DESIGN, mode: "presenter", units: ROWS } }];
  for (const r of ROWS) evs.push({ type: "unit_done", data: { n: r.n, unit: { role: r.role, title: r.title } } });
  for (const r of ROWS) evs.push({ type: "preview_ready", data: { n: r.n, url: `mock:preview/${r.n}` } });
  evs.push({ type: "qa_round", data: { round: 1, findings: [{ unit_no: 3, issue: "文字溢出框外", severity: "error", fix_hint: "減兩行內文" }] } });
  evs.push({ type: "preview_ready", data: { n: 3, url: "mock:preview/3?fixed=1" } });
  evs.push({ type: "complete", data: { download_url: "mock:download" } });
  return evs;
}

export function playMock(onEvent: (e: SseEvent) => void, opts: { step?: number } = {}): () => void {
  const step = opts.step ?? 250;
  const evs = mockTreeEvents();
  const timers = evs.map((e, i) => setTimeout(() => onEvent(e), step * (i + 1)));
  return () => timers.forEach(clearTimeout);
}
```

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/state/mockStream.test.ts` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/state/mockStream.ts odforge/web/src/state/mockStream.test.ts
git commit -m "feat(web): mock event stream for standalone dev"
```

---

## Task 7: 主題 tokens + useTheme

**Files:**
- Create: `odforge/web/src/theme/tokens.css`, `odforge/web/src/theme/useTheme.ts`, `odforge/web/src/theme/useTheme.test.ts`

**Interfaces:**
- Produces:
  - `tokens.css`:兩主題 CSS 變數(spec §4.1),定義於 `:root`、`@media (prefers-color-scheme: dark)`、`:root[data-theme="light"]`、`:root[data-theme="dark"]`。
  - `useTheme(): { theme: "light" | "dark"; setTheme: (t: "light" | "dark") => void }`——讀/寫 `document.documentElement.dataset.theme`,初值跟隨 `prefers-color-scheme`。

- [ ] **Step 1: 寫 `tokens.css`**(數值逐字取自 spec §4.1;此處為契約,完整列出)

```css
:root {
  --page-bg:#eceae4; --bg:#f6f5f2; --surface:#ffffff; --surface2:#eef1f7; --text:#191d24;
  --muted:#6b7079; --line:#e3e0d9; --accent:#234e9e; --accent2:#8a6d1f; --glow:rgba(35,78,158,.20);
  --pass:#2e8b57; --warn:#b6841c; --crit:#c0453f; --grid-op:0;
  --f-brand:Georgia,"Noto Serif TC","Times New Roman",serif;
  --f-ui:"微軟正黑體","Microsoft JhengHei",system-ui,-apple-system,"Segoe UI",sans-serif;
  --f-mono:"Consolas","SFMono-Regular",ui-monospace,monospace;
  --f-title:Georgia,"Noto Serif TC",serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page-bg:#070a0e; --bg:#0b0e14; --surface:#12161f; --surface2:#171d29; --text:#d6dee8;
    --muted:#6d7787; --line:#212a38; --accent:#35d0ba; --accent2:#6aa9ff; --glow:rgba(53,208,186,.45);
    --pass:#4ade80; --warn:#fbbf44; --crit:#f87171; --grid-op:.5;
    --f-title:"微軟正黑體","Microsoft JhengHei",system-ui,sans-serif;
  }
}
:root[data-theme="light"] {
  --page-bg:#eceae4; --bg:#f6f5f2; --surface:#ffffff; --surface2:#eef1f7; --text:#191d24;
  --muted:#6b7079; --line:#e3e0d9; --accent:#234e9e; --accent2:#8a6d1f; --glow:rgba(35,78,158,.20);
  --pass:#2e8b57; --warn:#b6841c; --crit:#c0453f; --grid-op:0; --f-title:Georgia,"Noto Serif TC",serif;
}
:root[data-theme="dark"] {
  --page-bg:#070a0e; --bg:#0b0e14; --surface:#12161f; --surface2:#171d29; --text:#d6dee8;
  --muted:#6d7787; --line:#212a38; --accent:#35d0ba; --accent2:#6aa9ff; --glow:rgba(53,208,186,.45);
  --pass:#4ade80; --warn:#fbbf44; --crit:#f87171; --grid-op:.5;
  --f-title:"微軟正黑體","Microsoft JhengHei",system-ui,sans-serif;
}
```

- [ ] **Step 2: 寫失敗測試 `useTheme.test.ts`**

```ts
import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { useTheme } from "./useTheme";

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("dark"), media: q, addEventListener() {}, removeEventListener() {} }));
});

test("初值跟隨 prefers-color-scheme(dark)", () => {
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("dark");
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
});

test("setTheme 寫入 data-theme", () => {
  const { result } = renderHook(() => useTheme());
  act(() => result.current.setTheme("light"));
  expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  expect(result.current.theme).toBe("light");
});
```

- [ ] **Step 3: 跑測試確認失敗**

Run:`npx vitest run src/theme/useTheme.test.ts` → Expected: FAIL

- [ ] **Step 4: 寫 `useTheme.ts`**

```ts
import { useCallback, useEffect, useState } from "react";

type Theme = "light" | "dark";

export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  );
  const setTheme = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    setThemeState(t);
  }, []);
  useEffect(() => { setTheme(theme); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return { theme, setTheme };
}
```

- [ ] **Step 5: 跑測試通過並 commit**

Run:`npx vitest run src/theme/useTheme.test.ts` → Expected: PASS
```bash
git add odforge/web/src/theme
git commit -m "feat(web): theme tokens + useTheme"
```

---

## Task 8: PromptBar(輸入 + 型別分頁 + 送出)

**Files:**
- Create: `odforge/web/src/components/PromptBar.tsx`, `odforge/web/src/components/PromptBar.test.tsx`

**Interfaces:**
- Consumes:`DocType`。
- Produces:`<PromptBar onGenerate={(prompt: string, docType: DocType) => void} />`——textarea + 三個型別分頁(簡報/文書/試算表,預設 odp)+ 送出鈕;空 prompt 時鈕 disabled。

- [ ] **Step 1: 寫失敗測試 `PromptBar.test.tsx`**

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { PromptBar } from "./PromptBar";

test("空 prompt 時送出鈕停用", () => {
  render(<PromptBar onGenerate={vi.fn()} />);
  expect(screen.getByRole("button", { name: /鍛造/ })).toBeDisabled();
});

test("輸入後送出帶 prompt 與預設 docType=odp", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect(onGenerate).toHaveBeenCalledWith("樹與二元樹", "odp");
});

test("切到文書分頁後送出帶 odt", () => {
  const onGenerate = vi.fn();
  render(<PromptBar onGenerate={onGenerate} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "實驗報告" } });
  fireEvent.click(screen.getByRole("tab", { name: /文書/ }));
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));
  expect(onGenerate).toHaveBeenCalledWith("實驗報告", "odt");
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/components/PromptBar.test.tsx` → Expected: FAIL

- [ ] **Step 3: 寫 `PromptBar.tsx`**

```tsx
import { useState } from "react";
import type { DocType } from "../state/types";

const TABS: { id: DocType; label: string }[] = [
  { id: "odp", label: "簡報" }, { id: "odt", label: "文書" }, { id: "ods", label: "試算表" },
];

export function PromptBar({ onGenerate }: { onGenerate: (prompt: string, docType: DocType) => void }) {
  const [prompt, setPrompt] = useState("");
  const [docType, setDocType] = useState<DocType>("odp");
  return (
    <div className="promptbar">
      <div className="doctabs" role="tablist" aria-label="文件型別">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={docType === t.id} className={docType === t.id ? "on" : ""} onClick={() => setDocType(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <textarea aria-label="主題" placeholder="用一句話描述你要的文件…" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <button className="forge" disabled={!prompt.trim()} onClick={() => onGenerate(prompt.trim(), docType)}>
        鍛造 ▸
      </button>
    </div>
  );
}
```

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/components/PromptBar.test.tsx` → Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/components/PromptBar.tsx odforge/web/src/components/PromptBar.test.tsx
git commit -m "feat(web): prompt bar with doc-type tabs"
```

---

## Task 9: 唯讀顯示元件(OutlineRail / GateRail / StatusNarrator)

**Files:**
- Create: `odforge/web/src/components/OutlineRail.tsx`, `odforge/web/src/components/GateRail.tsx`, `odforge/web/src/components/StatusNarrator.tsx`, `odforge/web/src/components/display.test.tsx`

**Interfaces:**
- Consumes:`CockpitState`、`Outline`、`GateId`、`GateStatus`。
- Produces:
  - `<OutlineRail outline={Outline | undefined} />`——渲染 `units` 列(序號/角色/標題)與 5 格色盤色卡;無 outline 時回 `null`。
  - `<GateRail gates={Record<GateId, GateStatus>} qaRounds={...} />`——四道閘各一列,`data-status` 帶狀態;F1 只有三道格式閘會變 pass。
  - `<StatusNarrator phase={Phase} units={Unit[]} />`——依 phase 產一行敘事文字(如「第 7 頁鍛造中…」「完成 · 四道閘全綠」)。

- [ ] **Step 1: 寫失敗測試 `display.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { GateRail } from "./GateRail";
import { OutlineRail } from "./OutlineRail";
import { StatusNarrator } from "./StatusNarrator";
import type { Outline } from "../state/types";

const outline: Outline = {
  design: { palette: { bg: "#fbfaf7", surface: "#eef2fb", text: "#1b2430", muted: "#7b8494", accent: "#2a5caa" }, fonts: { display: "x", body: "y" } },
  mode: "presenter",
  units: [ { n: 1, role: "title", title: "樹與二元樹", gist: "開場" } ],
};

test("OutlineRail 顯示單元標題與色盤", () => {
  const { container } = render(<OutlineRail outline={outline} />);
  expect(screen.getByText("樹與二元樹")).toBeInTheDocument();
  expect(container.querySelectorAll(".swatches i")).toHaveLength(5);
});

test("OutlineRail 無 outline 回 null", () => {
  const { container } = render(<OutlineRail outline={undefined} />);
  expect(container.firstChild).toBeNull();
});

test("GateRail 反映各閘狀態", () => {
  const { container } = render(<GateRail gates={{ zip: "pass", xml: "pass", libreoffice: "pass", design: "pending" }} qaRounds={[]} />);
  expect(container.querySelector('[data-gate="zip"]')?.getAttribute("data-status")).toBe("pass");
  expect(container.querySelector('[data-gate="design"]')?.getAttribute("data-status")).toBe("pending");
});

test("StatusNarrator 完成態文字", () => {
  render(<StatusNarrator phase="complete" units={[]} />);
  expect(screen.getByText(/四道閘全綠|完成/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/components/display.test.tsx` → Expected: FAIL

- [ ] **Step 3: 寫三個元件**

`OutlineRail.tsx`:
```tsx
import type { Outline } from "../state/types";

const ROLE_ZH: Record<string, string> = {
  title: "封面", agenda: "議程", section: "分節", "two-col": "雙欄", "big-fact": "關鍵數字",
  content: "內文", compare: "對比", chart: "圖表", closing: "結語",
};

export function OutlineRail({ outline }: { outline?: Outline }) {
  if (!outline) return null;
  const p = outline.design.palette;
  return (
    <aside className="rail left">
      <div className="railhead"><span className="t">大綱 Outline</span><span>{outline.units.length}</span></div>
      <div className="palette">
        <div className="cap">🎨 AI 為此主題設計的配色</div>
        <div className="swatches">
          {[p.bg, p.surface, p.accent, p.text, p.muted].map((c, i) => <i key={i} style={{ background: c }} />)}
        </div>
      </div>
      <div className="outline">
        {outline.units.map((u) => (
          <div className="orow" key={u.n}>
            <span className="num">{String(u.n).padStart(2, "0")}</span>
            <span><span className="ti">{u.title}</span><span className="role">{ROLE_ZH[u.role] ?? u.role}</span></span>
          </div>
        ))}
      </div>
    </aside>
  );
}
```
`GateRail.tsx`:
```tsx
import type { GateId, GateStatus } from "../state/types";

const GATES: { id: GateId; label: string; sub: string; code: string }[] = [
  { id: "zip", label: "① 封裝結構", sub: "mimetype 為首 · manifest", code: "zip" },
  { id: "xml", label: "② XML 正確性", sub: "每個部件 well-formed", code: "xml" },
  { id: "libreoffice", label: "③ LibreOffice 轉檔", sub: "headless 真轉 PDF", code: "LO" },
  { id: "design", label: "④ 設計閘", sub: "vision 檢視 · 逐頁把關", code: "◑" },
];
const SYM: Record<GateStatus, string> = { pending: "·", active: "⟳", pass: "✓", fail: "✕" };

export function GateRail({ gates, qaRounds }: { gates: Record<GateId, GateStatus>; qaRounds: { round: number }[] }) {
  return (
    <aside className="rail right">
      <div className="railhead"><span className="t">四道閘 Gates</span></div>
      <div className="gates">
        {GATES.map((g) => (
          <div className="gate" key={g.id} data-gate={g.id} data-status={gates[g.id]}>
            <span className="gi">{g.code}</span>
            <span className="gt"><b>{g.label}</b><small>{g.sub}</small></span>
            <span className="gs">{SYM[gates[g.id]]}</span>
          </div>
        ))}
      </div>
      {qaRounds.length > 0 && <div className="qa"><span className="round">第 {qaRounds.length} 輪</span></div>}
    </aside>
  );
}
```
`StatusNarrator.tsx`:
```tsx
import type { Phase, Unit } from "../state/types";

export function StatusNarrator({ phase, units }: { phase: Phase; units: Unit[] }) {
  const forging = units.find((u) => u.status === "filling" || u.status === "regen");
  const text =
    phase === "empty" ? "準備就緒"
    : phase === "outline" ? "已產生大綱與配色"
    : phase === "await" ? "等待你確認大綱…"
    : phase === "qa" ? "設計閘檢視中…"
    : phase === "complete" ? "完成 · 四道閘全綠 · 原生 ODF"
    : phase === "error" ? "發生錯誤,請看右側閘門"
    : forging ? `第 ${forging.n} 頁鍛造中…` : "生成中…";
  return <div className="narrator"><span className="ndot">◆</span><span className="nn">{text}</span></div>;
}
```

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/components/display.test.tsx` → Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/components/OutlineRail.tsx odforge/web/src/components/GateRail.tsx odforge/web/src/components/StatusNarrator.tsx odforge/web/src/components/display.test.tsx
git commit -m "feat(web): outline rail, gate rail, status narrator"
```

---

## Task 10: 預覽舞台(PreviewStage + UnitCell)

**Files:**
- Create: `odforge/web/src/components/UnitCell.tsx`, `odforge/web/src/components/PreviewStage.tsx`, `odforge/web/src/components/preview.test.tsx`

**Interfaces:**
- Consumes:`Unit`、`UnitStatus`、`DocType`、`previewUrl`。
- Produces:
  - `<UnitCell unit={Unit} jobId={string | undefined} />`——單格,`data-status` 帶 unit.status;有 previewUrl 且非 mock 時放 `<img>`(來源 `previewUrl(jobId,n)`),否則放骨架/縮圖佔位。
  - `<PreviewStage units={Unit[]} docType={DocType} jobId={string | undefined} />`——標頭(完成數/總數)+ 網格;`data-doctype` 決定格子長寬比(odp 16/10、odt 直式、ods 寬版,由 CSS 讀)。

- [ ] **Step 1: 寫失敗測試 `preview.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { PreviewStage } from "./PreviewStage";
import type { Unit } from "../state/types";

const units: Unit[] = [
  { n: 1, role: "title", title: "封面", status: "done", previewUrl: "mock:preview/1" },
  { n: 2, role: "content", title: "內文", status: "skeleton" },
];

test("標頭顯示完成數/總數", () => {
  const { container } = render(<PreviewStage units={units} docType="odp" jobId={undefined} />);
  expect(container.querySelector(".prog")?.textContent?.replace(/\s+/g, " ")).toContain("1 / 2");
});

test("每個單元一格且帶 data-status", () => {
  const { container } = render(<PreviewStage units={units} docType="odp" jobId={undefined} />);
  const cells = container.querySelectorAll(".cell");
  expect(cells).toHaveLength(2);
  expect(cells[0].getAttribute("data-status")).toBe("done");
  expect(cells[1].getAttribute("data-status")).toBe("skeleton");
});

test("網格帶 data-doctype", () => {
  const { container } = render(<PreviewStage units={units} docType="odt" jobId={undefined} />);
  expect(container.querySelector(".grid")?.getAttribute("data-doctype")).toBe("odt");
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/components/preview.test.tsx` → Expected: FAIL

- [ ] **Step 3: 寫兩個元件**

`UnitCell.tsx`:
```tsx
import { previewUrl } from "../state/api";
import type { Unit } from "../state/types";

export function UnitCell({ unit, jobId }: { unit: Unit; jobId?: string }) {
  const realPreview = unit.previewUrl && !unit.previewUrl.startsWith("mock:") && jobId;
  return (
    <div className="cell" data-status={unit.status} data-n={unit.n}>
      <span className="cn">{String(unit.n).padStart(2, "0")}</span>
      {realPreview ? (
        <img className="thumb" alt={`第 ${unit.n} 頁預覽`} src={previewUrl(jobId, unit.n)} />
      ) : (
        <div className="thumb placeholder" aria-hidden="true"><span className="ptitle">{unit.title}</span></div>
      )}
    </div>
  );
}
```
`PreviewStage.tsx`:
```tsx
import type { DocType, Unit } from "../state/types";
import { UnitCell } from "./UnitCell";

export function PreviewStage({ units, docType, jobId }: { units: Unit[]; docType: DocType; jobId?: string }) {
  const done = units.filter((u) => u.status === "done" || u.status === "preview").length;
  return (
    <section className="center">
      <div className="stagehead">
        <span className="t">預覽舞台 Preview</span>
        <span className="prog"><b>{done}</b> / {units.length} 頁</span>
      </div>
      <div className="grid" data-doctype={docType}>
        {units.map((u) => <UnitCell key={u.n} unit={u} jobId={jobId} />)}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/components/preview.test.tsx` → Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/components/UnitCell.tsx odforge/web/src/components/PreviewStage.tsx odforge/web/src/components/preview.test.tsx
git commit -m "feat(web): preview stage with unit cells"
```

---

## Task 11: DownloadDock

**Files:**
- Create: `odforge/web/src/components/DownloadDock.tsx`, `odforge/web/src/components/DownloadDock.test.tsx`

**Interfaces:**
- Consumes:`DocType`、`downloadUrl`。
- Produces:`<DownloadDock jobId={string | undefined} downloadUrl={string | undefined} docType={DocType} />`——未完成時鈕 disabled 顯示「生成中」;有 downloadUrl 時鈕為 `<a>` 指向 `downloadUrl(jobId)`,標籤顯示副檔名(.odp/.odt/.ods)。

- [ ] **Step 1: 寫失敗測試 `DownloadDock.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { DownloadDock } from "./DownloadDock";

test("未完成時顯示生成中且不可下載", () => {
  render(<DownloadDock jobId={undefined} downloadUrl={undefined} docType="odp" />);
  expect(screen.getByText(/生成中/)).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});

test("完成時為連結、副檔名依型別", () => {
  render(<DownloadDock jobId="j1" downloadUrl="/api/jobs/j1/download" docType="ods" />);
  const link = screen.getByRole("link");
  expect(link).toHaveAttribute("href", "/api/jobs/j1/download");
  expect(link).toHaveTextContent(/\.ods/);
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/components/DownloadDock.test.tsx` → Expected: FAIL

- [ ] **Step 3: 寫 `DownloadDock.tsx`**

```tsx
import { downloadUrl as buildUrl } from "../state/api";
import type { DocType } from "../state/types";

const EXT: Record<DocType, string> = { odp: ".odp", odt: ".odt", ods: ".ods" };

export function DownloadDock({ jobId, downloadUrl, docType }: { jobId?: string; downloadUrl?: string; docType: DocType }) {
  if (downloadUrl && jobId) {
    return <a className="dl done" href={buildUrl(jobId)} download>下載 {EXT[docType]} <span className="odp">ODF</span></a>;
  }
  return <button className="dl" disabled>生成中… <span className="odp">ODF</span></button>;
}
```
> 註:`downloadUrl` prop 存在即代表完成;實際連結用 `buildUrl(jobId)` 組(避免信任事件裡的相對/絕對路徑歧義)。

- [ ] **Step 4: 跑測試確認通過**

Run:`npx vitest run src/components/DownloadDock.test.tsx` → Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add odforge/web/src/components/DownloadDock.tsx odforge/web/src/components/DownloadDock.test.tsx
git commit -m "feat(web): download dock"
```

---

## Task 12: App 組裝 + 樣式 + 整合測試

**Files:**
- Modify: `odforge/web/src/App.tsx`
- Create: `odforge/web/src/styles/app.css`, `odforge/web/src/App.test.tsx`

**Interfaces:**
- Consumes:全部 state 模組與元件、`useTheme`、`playMock`/`subscribeJob`、`postGenerate`。
- Produces:完整單頁。`App` 內 `useReducer(cockpitReducer, initialState())`;送出 prompt 後——真實模式打 `postGenerate` + `subscribeJob`,mock 模式(URL 帶 `?mock=1` 或無後端時的 fallback)用 `playMock`。組裝四區 shell,套 `tokens.css` + `app.css`。

- [ ] **Step 1: 寫失敗整合測試 `App.test.tsx`**(用 mock 流驗端到端狀態機→UI)

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
  // 強制 mock 模式且加速(mockStep=5ms),避免打真後端、避免測試過慢
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
});

test("送出 prompt 後,mock 流最終跑到完成:四道閘全綠 + 可下載", async () => {
  const { container } = render(<App />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  // 大綱出現
  await waitFor(() => expect(screen.getByText("樹與二元樹")).toBeInTheDocument());
  // 最終完成:下載連結出現
  await waitFor(() => expect(screen.getByRole("link", { name: /下載/ })).toBeInTheDocument(), { timeout: 4000 });
  // 四道閘皆 pass
  expect(container.querySelectorAll('[data-gate][data-status="pass"]')).toHaveLength(4);
});
```
> 註:mock 流共 28 個事件,`mockStep=5ms` → 約 150ms 內跑完;`timeout: 4000` 綽綽有餘。App 讀 URL 的 `mockStep` 覆寫預設 250ms 間隔。

- [ ] **Step 2: 跑測試確認失敗**

Run:`npx vitest run src/App.test.tsx` → Expected: FAIL(App 尚未組裝)

- [ ] **Step 3: 寫 `App.tsx`**

```tsx
import { useEffect, useReducer, useRef, useState } from "react";
import { PromptBar } from "./components/PromptBar";
import { OutlineRail } from "./components/OutlineRail";
import { PreviewStage } from "./components/PreviewStage";
import { GateRail } from "./components/GateRail";
import { StatusNarrator } from "./components/StatusNarrator";
import { DownloadDock } from "./components/DownloadDock";
import { postGenerate } from "./state/api";
import { cockpitReducer, initialState } from "./state/cockpit";
import { playMock } from "./state/mockStream";
import { subscribeJob } from "./state/sse";
import type { DocType } from "./state/types";
import { useTheme } from "./theme/useTheme";
import "./theme/tokens.css";
import "./styles/app.css";

export default function App() {
  const [state, dispatch] = useReducer(cockpitReducer, undefined, () => initialState());
  const [jobId, setJobId] = useState<string | undefined>();
  const { theme, setTheme } = useTheme();
  const cancelRef = useRef<() => void>();
  const params = new URLSearchParams(window.location.search);
  const mockMode = params.has("mock");
  const mockStep = Number(params.get("mockStep") ?? "250");

  useEffect(() => () => cancelRef.current?.(), []);

  async function onGenerate(prompt: string, docType: DocType) {
    cancelRef.current?.();
    if (mockMode) { setJobId("mock"); cancelRef.current = playMock(dispatch, { step: mockStep }); return; }
    try {
      const { job_id } = await postGenerate({ prompt, doc_type: docType });
      setJobId(job_id);
      cancelRef.current = subscribeJob(job_id, dispatch);
    } catch {
      // 後端不可用 → 退回 mock,讓 UI 仍可展示
      setJobId("mock");
      cancelRef.current = playMock(dispatch, { step: mockStep });
    }
  }

  const started = state.phase !== "empty";
  return (
    <div className="page">
      <div className="stage">
        <div className="cockpit">
          <header className="top">
            <div className="brand"><span className="mark">文鍛</span><span className="en">ODForge</span></div>
            {!started ? <PromptBar onGenerate={onGenerate} /> : <div className="promptline">生成中的文件</div>}
            <div className="themetoggle">
              <button aria-pressed={theme === "light"} onClick={() => setTheme("light")}>☀</button>
              <button aria-pressed={theme === "dark"} onClick={() => setTheme("dark")}>☾</button>
            </div>
          </header>
          <OutlineRail outline={state.outline} />
          <PreviewStage units={state.units} docType={state.docType} jobId={jobId} />
          <GateRail gates={state.gates} qaRounds={state.qaRounds} />
          <footer className="foot">
            <StatusNarrator phase={state.phase} units={state.units} />
            <DownloadDock jobId={jobId} downloadUrl={state.downloadUrl} docType={state.docType} />
          </footer>
        </div>
      </div>
    </div>
  );
}
```
> `App.tsx` 開場態(phase="empty")只顯示 PromptBar;為讓整合測試在無 outline 時也能輸入,PromptBar 一律在 header 顯示直到 started。空台的 hero 大字版(spec §7 狀態 0)可在此 CSS 處理,不影響邏輯。

- [ ] **Step 4: 寫 `styles/app.css`**

從已核可的 Artifact 原始碼(視覺真理來源,見計畫頂部連結)移植 shell 版面與元件樣式:`.page/.stage/.cockpit`(grid-areas 四區)、`.top/.left/.right/.center/.foot`、`.grid[data-doctype]`(odp `aspect-ratio:16/10`、odt 直式 `3/4`、ods 寬版 `16/9`)、`.cell[data-status]` 各態、`.gate[data-status]`、`.promptbar/.doctabs`、`.dl`。tokens 全走 `var(--…)`。此步無單元測試(純樣式);驗收靠 Step 5 人工開檔。

- [ ] **Step 5: 跑整合測試 + 全量測試 + 人工關卡**

Run:`npm test`
Expected: 全部 PASS。
Run:`npm run dev`,開 `http://localhost:5173/?mock=1`,人工確認:輸入主題 → 逐格點亮 → 三道格式閘打勾 → 第 3 頁被標記→重生 → 完成可下載;`☀/☾` 切換淺色(學術淨白)/深色(控制室)兩種外觀皆正確、對比清楚。截圖存 `odforge/docs/screenshots/web/`(決賽佐證素材)。

- [ ] **Step 6: Commit**

```bash
git add odforge/web/src/App.tsx odforge/web/src/styles/app.css odforge/web/src/App.test.tsx
git commit -m "feat(web): assemble cockpit app with mock-driven end-to-end flow"
```

---

## F1 完成後(交接說明)

- **獨立可展示:** `npm run dev` → `/?mock=1` 就能演完整「一句話 → 逐格點亮 → 四道閘 → 下載」,不需後端。決賽若後端 Phase 18 未就緒,此模式即可 demo。
- **接真後端:** 後端 Phase 18 上線後,拿掉 `?mock=1` 即走 `postGenerate + subscribeJob`;需後端落實 spec §10 的 `unit_done` / `/units/{n}/regenerate` 詞彙。
- **`odforge serve` 掛載:** 待後端 `webapi.py` 存在時,加一步 `npm run build` 並讓 FastAPI 掛 `web/dist` 為靜態根(F1 之外、與後端協調)。
- **下一份計畫:** F2(大綱確認站 + 單元重生)——依賴 Phase 18 的 `/outline`、`/units/{n}/regenerate`。

---

## Self-Review(計畫對照 spec)

- **spec 覆蓋(F1 範圍):** §3 漸進式控制台→Task 12 shell;§4 兩主題→Task 7;§5 四區版面→Task 12;§6 型別無關+doctype 長寬比→Task 8/10/12;§7 狀態集(empty/outline/generating/complete/error)→Task 3 reducer + Task 12;§8 元件樹→Task 8–11;§9 狀態機→Task 3;§10 契約詞彙 unit→Task 2/3/5;§11 技術棧/結構→Task 1。(§7 之 await/qa 之 UI、確認站、QA 面板、樣式抽取屬 F2–F4,已於分階標明,非本計畫缺口。)
- **placeholder 掃描:** 無 TBD/TODO;每個 code step 有完整程式碼;Task 12 Step 4 樣式明確指向「從已核可 Artifact 移植」並列出需移植的選擇器清單(視覺真理來源已是完整程式碼,非佔位)。
- **型別一致:** `cockpitReducer`/`initialState`/`parseSseEvent`/`subscribeJob`/`postGenerate`/`previewUrl`/`downloadUrl` 全程同名同簽章;`DocType`/`Unit`/`GateId` 跨 Task 一致;事件名 `unit_done`(非 `slide_done`)貫穿。
- **依賴順序:** Task 1→2→3→4→(5 需 4;6、7 可與 5 平行)→8–11(可平行)→12。每個 Task 產獨立可測交付。
