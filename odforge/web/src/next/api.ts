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

export async function getVersion(): Promise<number> {
  const response = await fetch("/api/doc/version");
  if (!response.ok) throw new EditorError("連不上編輯器的伺服器");
  return ((await response.json()) as { version: number }).version;
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

/** Fetch the checked file and hand it to the browser as a download. */
export async function exportOdp(name: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/doc/export", { cache: "no-store" });
  } catch {
    throw new EditorError("連不上編輯器的伺服器,無法匯出。");
  }
  if (!response.ok) {
    let detail = `匯出失敗(${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status
    }
    throw new EditorError(detail);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name.toLowerCase().endsWith(".odp") ? name : `${name}.odp`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
