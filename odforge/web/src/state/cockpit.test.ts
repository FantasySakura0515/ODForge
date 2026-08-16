import { describe, expect, test } from "vitest";
import { cockpitReducer, initialState } from "./cockpit";
import type { CockpitAction, Outline } from "./types";

const outline: Outline = {
  design: { palette: { bg: "#fff", surface: "#eee", text: "#111", muted: "#888", accent: "#234e9e" }, fonts: { display: "Noto Serif TC", body: "Noto Sans TC" } },
  mode: "presenter",
  pages: [ { role: "title", title: "封面", gist: "開場" }, { role: "title-content", title: "內文", gist: "重點" } ],
};
// CockpitAction, not SseEvent:reducer 同時吃伺服器事件與本地動作
// (reset / regen_*)。用 SseEvent 宣告時,測試裡的 regen_start 只是型別錯誤
// 沒人看見 —— 因為 tsconfig.json 根本把 *.test.ts 排除在外。
const send = (s = initialState(), ...evs: CockpitAction[]) => evs.reduce(cockpitReducer, s);

describe("cockpitReducer", () => {
  test("初始狀態", () => {
    const s = initialState();
    expect(s.phase).toBe("empty");
    expect(s.docType).toBe("odp");
    expect(s.units).toEqual([]);
    expect(s.gates).toEqual({ zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" });
  });

  test("reset 回到空白控制台但沿用 docType", () => {
    const generating = send(
      initialState("odp"),
      { type: "outline", data: outline },
      { type: "slide_done", data: { n: 1, slide: { layout: "title", title: "封面!" } } },
      { type: "complete", data: { download_url: "/d" } },
    );
    const s = cockpitReducer(generating, { type: "reset" });
    expect(s.phase).toBe("empty");
    expect(s.units).toEqual([]);
    expect(s.downloadUrl).toBeUndefined();
    expect(s.docType).toBe("odp");
  });

  test("outline 建立骨架單元", () => {
    const s = send(initialState(), { type: "outline", data: outline });
    expect(s.phase).toBe("outline");
    expect(s.units.map((u) => u.status)).toEqual(["skeleton", "skeleton"]);
    expect(s.units[0].title).toBe("封面");
  });

  test("slide_done 填內容、進 generating、保留 role", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "slide_done", data: { n: 1, slide: { layout: "title", title: "封面!" } } });
    expect(s.phase).toBe("generating");
    expect(s.units[0].status).toBe("filling");
    expect(s.units[0].title).toBe("封面!");
    expect(s.units[0].role).toBe("title");
  });

  test("unit_done 與 slide_done 等價(reducer 收斂成同一 case)", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "unit_done", data: { n: 1, slide: { layout: "title", title: "封面!" } } },
    );
    expect(s.phase).toBe("generating");
    expect(s.units[0].status).toBe("filling");
    expect(s.units[0].title).toBe("封面!");
    expect(s.units[0].role).toBe("title");
  });

  test("preview_ready 設縮圖但不再偽造 gates", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } });
    expect(s.units[0].previewUrl).toContain("preview/1.png");
    expect(s.units[0].status).toBe("preview");
    // preview_ready 不得再無中生有把格式閘設 pass(那是假綠勾)
    expect(s.gates.zip).toBe("pending");
    expect(s.gates.xml).toBe("pending");
    expect(s.gates.libreoffice).toBe("pending");
  });

  test("gate_result 逐一點亮各道閘", () => {
    let s = send(initialState(), { type: "outline", data: outline });
    s = cockpitReducer(s, { type: "gate_result", data: { gate: "zip", status: "pass" } });
    expect(s.gates).toEqual({ zip: "pass", xml: "pending", libreoffice: "pending", design: "pending" });
    s = cockpitReducer(s, { type: "gate_result", data: { gate: "xml", status: "pass" } });
    expect(s.gates.xml).toBe("pass");
    s = cockpitReducer(s, { type: "gate_result", data: { gate: "libreoffice", status: "skipped" } });
    expect(s.gates.libreoffice).toBe("skipped");
    s = cockpitReducer(s, { type: "gate_result", data: { gate: "design", status: "fail" } });
    expect(s.gates.design).toBe("fail");
  });

  test("complete:全單元 done、gates 保持 gate_result 累積的真值(不強制全綠)", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "gate_result", data: { gate: "zip", status: "pass" } },
      { type: "gate_result", data: { gate: "xml", status: "pass" } },
      { type: "gate_result", data: { gate: "libreoffice", status: "skipped" } },
      { type: "gate_result", data: { gate: "design", status: "skipped" } },
      { type: "complete", data: { download_url: "/api/jobs/x/download" } },
    );
    expect(s.phase).toBe("complete");
    expect(s.downloadUrl).toContain("download");
    expect(s.units.every((u) => u.status === "done")).toBe(true);
    // complete 不得無條件把四閘塗綠;保持真值
    expect(s.gates).toEqual({ zip: "pass", xml: "pass", libreoffice: "skipped", design: "skipped" });
  });

  test("complete 不改動未回報的 gates(維持 pending)", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "complete", data: { download_url: "/d" } });
    expect(s.gates).toEqual({ zip: "pending", xml: "pending", libreoffice: "pending", design: "pending" });
  });

  test("qa_round 標記 error 單元並讓設計閘 active", () => {
    const s = send(initialState(), { type: "outline", data: outline }, { type: "qa_round", data: { round: 1, findings: [{ slide_no: 2, issue: "溢出", severity: "error", fix_hint: "減字" }] } });
    expect(s.phase).toBe("qa");
    // 品檢維度獨立:被標紅的是 qa,不是生成用的 status(擠在同一欄位時,
    // complete 會把兩者一起洗掉)。
    expect(s.units[1].qa).toBe("flagged");
    expect(s.units[1].status).toBe("skeleton");
    expect(s.units[0].qa).toBe("clear");
    expect(s.gates.design).toBe("active");
    expect(s.qaRounds[0].round).toBe(1);
  });

  test("qa_round 每輪整批重算:第 2 輪修好的頁面不得繼續掛著紅記號", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "qa_round", data: { round: 1, findings: [
        { slide_no: 1, issue: "溢出", severity: "error", fix_hint: "減字" },
        { slide_no: 2, issue: "留白", severity: "warn", fix_hint: "收緊" },
      ] } },
      { type: "qa_round", data: { round: 2, findings: [
        { slide_no: 2, issue: "仍偏鬆", severity: "error", fix_hint: "再收" },
      ] } },
    );
    expect(s.units[0].qa).toBe("clear");   // 第 1 頁第 2 輪已修好
    expect(s.units[1].qa).toBe("flagged"); // warn -> error 升級
    expect(s.qaRounds).toHaveLength(2);
  });

  test("complete 不得把品檢未過的頁面洗成一片乾淨", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "qa_round", data: { round: 1, findings: [
        { slide_no: 2, issue: "溢出", severity: "error", fix_hint: "減字" },
      ] } },
      { type: "complete", data: { download_url: "/d" } },
    );
    expect(s.units.every((u) => u.status === "done")).toBe(true);
    expect(s.units[1].qa).toBe("flagged"); // 生成完成 != 品檢通過
  });

  test("error 進 error phase、存訊息,但不再用 stage 映射偽造 gate fail", () => {
    // 後端 stage 詞彙(outline/slides/render/preview/qa/validate/connect)與 GATE_IDS 永不重疊;
    // gate 失敗改由 gate_result{fail} 直接表達,error 不得再猜測性地把某道閘塗紅。
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "gate_result", data: { gate: "zip", status: "fail" } },
      { type: "error", data: { message: "封裝結構錯誤", stage: "validate" } },
    );
    expect(s.phase).toBe("error");
    expect(s.error?.message).toBe("封裝結構錯誤");
    // gate 值只反映 gate_result,error 不動它
    expect(s.gates).toEqual({ zip: "fail", xml: "pending", libreoffice: "pending", design: "pending" });
  });

  test("regen_start 把該單元設為 regen", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } },
      { type: "regen_start", data: { n: 1 } },
    );
    expect(s.units[0].status).toBe("regen");
  });

  test("regen_done 更新 ir/title、還原狀態,並為 previewUrl 加 cache-bust", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } },
      { type: "regen_start", data: { n: 1 } },
      { type: "regen_done", data: { n: 1, slide: { layout: "title", title: "新封面" }, preview_url: "/api/jobs/x/preview/1.png" } },
    );
    expect(s.units[0].status).toBe("preview");
    expect(s.units[0].title).toBe("新封面");
    expect(s.units[0].ir).toMatchObject({ title: "新封面" });
    // 同 URL 需 cache-bust:base 不變但帶遞增的 ?t=
    expect(s.units[0].previewUrl).toMatch(/preview\/1\.png\?t=\d+/);
  });

  test("regen_done preview_url 為 null 時,清掉舊圖而不是沿用", () => {
    // 舊行為是退回上一版的縮圖:新內容配舊圖片,看起來完全正常,而且是錯的。
    // 算不出圖就是沒有圖 —— 後端此時也不會再供應上一版的 PNG(404)。
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } },
      { type: "regen_start", data: { n: 1 } },
      { type: "regen_done", data: { n: 1, slide: { layout: "title", title: "新封面" }, preview_url: null } },
    );
    expect(s.units[0].previewUrl).toBeUndefined();
    expect(s.units[0].title).toBe("新封面");
  });

  test("preview_ready 帶 url:null 代表這一頁沒有縮圖,不是沒有消息", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } },
      { type: "preview_ready", data: { n: 1, url: null } },
    );
    expect(s.units[0].previewUrl).toBeUndefined();
  });

  test("qa_invalidated 清空上一輪品檢:findings 指的內容已經不在了", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      {
        type: "qa_round",
        data: {
          round: 1,
          findings: [{ slide_no: 1, issue: "文字溢出", severity: "error", fix_hint: "縮短" }],
        },
      },
      { type: "qa_invalidated", data: { n: 1, reason: "重生後尚未品檢" } },
    );
    expect(s.units[0].qa).toBeUndefined();
    expect(s.qaRounds).toEqual([]);
  });

  test("regen_done 也把整批品檢狀態作廢(HTTP 路徑沒有 SSE 可收)", () => {
    // 重生是同步端點,回應直接進 reducer;live client 的 SSE 在 complete 就關了,
    // 所以清理必須在這裡也發生一次,否則畫面留著上一版的紅記號與綠勾。
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      {
        type: "qa_round",
        data: {
          round: 1,
          findings: [{ slide_no: 2, issue: "重疊", severity: "error", fix_hint: "移開" }],
        },
      },
      { type: "regen_start", data: { n: 1 } },
      {
        type: "regen_done",
        data: { n: 1, slide: { layout: "title", title: "新封面" }, preview_url: "/api/jobs/x/preview/1.png" },
      },
    );
    expect(s.qaRounds).toEqual([]);
    expect(s.units.every((u) => u.qa === undefined)).toBe(true);
  });

  test("regen_done 從 done 還原回 done", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "complete", data: { download_url: "/api/jobs/x/download" } },
      { type: "regen_start", data: { n: 1 } },
      { type: "regen_done", data: { n: 1, slide: { layout: "title", title: "改" }, preview_url: "/api/jobs/x/preview/1.png" } },
    );
    expect(s.units[0].status).toBe("done");
  });

  test("regen_error 把狀態還原成 regen 前的值", () => {
    const s = send(
      initialState(),
      { type: "outline", data: outline },
      { type: "preview_ready", data: { n: 1, url: "/api/jobs/x/preview/1.png" } },
      { type: "regen_start", data: { n: 1 } },
      { type: "regen_error", data: { n: 1 } },
    );
    expect(s.units[0].status).toBe("preview");
  });
});
