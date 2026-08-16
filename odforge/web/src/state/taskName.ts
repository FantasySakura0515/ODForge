import type { Outline } from "./types";

/**
 * 這份工作的名字,取自模型定的文件標題(封面頁 title;沒有封面頁就用第一頁)。
 *
 * 使用者輸入的是指令(「參考文件後製作一個助理申請簡報」),不是文件名稱;拿指令當
 * 任務名稱既冗長,也和實際產出的封面不一致。前端契約:優先封面頁(role="title"),
 * 退而求其次第一頁。後端 `_session_title` 目前偏好 ir.title 再 pages[0],正另行
 * 對齊為同樣的封面頁優先;對齊完成前兩邊的名字可能短暫不一致。
 *
 * 大綱還沒到(或標題全空白)時回 undefined,呼叫端自行退回原始需求。
 */
export function taskNameFromOutline(outline?: Outline): string | undefined {
  if (!outline?.pages.length) return undefined;
  const cover = outline.pages.find((page) => page.role === "title");
  const title = (cover ?? outline.pages[0]).title.trim();
  return title || undefined;
}
