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
