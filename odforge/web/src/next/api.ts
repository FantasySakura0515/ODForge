import type { DocState, Op } from "./types";

export class EditorError extends Error {}

async function call(path: string, init?: RequestInit): Promise<DocState> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new EditorError("連不上編輯器的伺服器,請確認 odforge edit 還在執行。");
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status code
    }
    throw new EditorError(detail);
  }
  return (await response.json()) as DocState;
}

export function loadDoc(): Promise<DocState> {
  return call("/api/doc");
}

export function sendOps(ops: Op[]): Promise<DocState> {
  return call("/api/doc/ops", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ops }),
  });
}

export function undo(): Promise<DocState> {
  return call("/api/doc/undo", { method: "POST" });
}

export function redo(): Promise<DocState> {
  return call("/api/doc/redo", { method: "POST" });
}

export async function clip(ids: string[]): Promise<string[]> {
  const response = await fetch("/api/doc/clip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  if (!response.ok) throw new EditorError("無法複製這些物件");
  return ((await response.json()) as { items: string[] }).items;
}

export function fileUrl(href: string): string {
  return `/api/doc/file/${href.split("/").map(encodeURIComponent).join("/")}`;
}

export const downloadUrl = "/api/doc/download";
