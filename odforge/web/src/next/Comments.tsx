import { useEffect, useRef, useState } from "react";
import { bounds, flatten } from "./geometry";
import type { Comment, Page, Scene, SceneElement } from "./types";

/** Where a comment's pin sits: on its element's top-right corner, or where it was left. */
export function pinPoint(comment: Comment, live: (id: string) => SceneElement | undefined): { x: number; y: number } {
  const el = comment.target ? live(comment.target) : undefined;
  if (!el) return { x: comment.x, y: comment.y };
  const b = bounds(el);
  return { x: b.x + b.w, y: b.y };
}

function when(date: string): string {
  const d = new Date(date);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function CommentPins({
  comments,
  s,
  live,
  openId,
  onOpen,
  onResolve,
}: {
  comments: Comment[];
  s: number;
  live: (id: string) => SceneElement | undefined;
  openId: string | null;
  onOpen: (id: string | null) => void;
  onResolve: (id: string) => void;
}) {
  return (
    <>
      {comments.map((c, i) => {
        const p = pinPoint(c, live);
        const open = openId === c.id;
        return (
          <div key={c.id} className="ne-pin-wrap" style={{ left: p.x * s, top: p.y * s }} onPointerDown={(e) => e.stopPropagation()}>
            <button
              type="button"
              className={["ne-pin", open ? "open" : "", c.state === "submitted" ? "working" : ""].join(" ")}
              aria-label={`留言 ${i + 1}:${c.text}`}
              aria-expanded={open}
              onClick={() => onOpen(open ? null : c.id)}
            >
              {i + 1}
            </button>
            {open && (
              <div className="ne-note" role="dialog" aria-label={`留言 ${i + 1}`}>
                <p className="ne-note-text">{c.text}</p>
                <div className="ne-note-meta">
                  <span>
                    {c.state === "submitted" ? "AI 處理中…" : c.author}
                    {c.state !== "submitted" && c.date && ` · ${when(c.date)}`}
                  </span>
                  <span className="ne-note-actions">
                    <button type="button" onClick={() => onResolve(c.id)}>{c.state === "submitted" ? "撤回" : "刪除"}</button>
                    <button type="button" onClick={() => onOpen(null)}>關閉</button>
                  </span>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

export function CommentComposer({
  x,
  y,
  s,
  about,
  onSubmit,
  onCancel,
}: {
  x: number;
  y: number;
  s: number;
  about: string;
  onSubmit: (text: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const send = () => text.trim() && onSubmit(text.trim());
  return (
    <div className="ne-pin-wrap" style={{ left: x * s, top: y * s }} onPointerDown={(e) => e.stopPropagation()}>
      <span className="ne-pin open" aria-hidden="true">＋</span>
      <div className="ne-note composer">
        <label className="ne-note-about" htmlFor="ne-comment-input">{about}</label>
        <textarea
          id="ne-comment-input"
          ref={ref}
          rows={3}
          placeholder="寫下要怎麼改,例如:這句太長,縮成一行"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Escape") onCancel();
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <div className="ne-note-meta">
          <span>⌘Enter 送出</span>
          <span className="ne-note-actions">
            <button type="button" onClick={onCancel}>取消</button>
            <button type="button" className="primary" disabled={!text.trim()} onClick={send}>留言</button>
          </span>
        </div>
      </div>
    </div>
  );
}

function excerpt(page: Page, id: string | null): string {
  if (!id) return "整頁";
  const el = flatten(page.elements).find((e) => e.id === id);
  if (!el) return "已刪除的物件";
  if (el.kind !== "line" && el.text) {
    const text = el.text.paragraphs.map((p) => p.runs.map((r) => r.text).join("")).join(" ");
    return `「${text.length > 18 ? `${text.slice(0, 18)}…` : text}」`;
  }
  return { rect: "方塊", ellipse: "圓形", line: "線條", image: "圖片", text: "文字框", unsupported: "物件", group: "群組" }[el.kind];
}

export function CommentDrawer({
  scene,
  onGo,
  onResolve,
  onClose,
  onSubmit,
  onDone,
}: {
  scene: Scene;
  onGo: (page: number, id: string) => void;
  onResolve: (id: string) => void;
  onClose: () => void;
  onSubmit: () => void;
  onDone: () => void;
}) {
  const all = scene.pages.flatMap((p) => p.comments ?? []);
  const total = all.length;
  const drafts = all.filter((c) => c.state !== "submitted").length;
  const working = total - drafts;
  return (
    <aside className="ne-drawer" aria-label="留言">
      <header>
        <h2>留言 {total}</h2>
        <button type="button" aria-label="關閉留言" onClick={onClose}>×</button>
      </header>
      <div className="ne-drawer-actions">
        <button type="button" className="primary" disabled={!drafts} onClick={onSubmit}>
          交給 AI 修改{drafts ? `(${drafts})` : ""}
        </button>
        <button type="button" onClick={onDone} title="告訴 AI 這輪改完了">完成</button>
      </div>
      <p className="ne-drawer-hint">
        {working
          ? `AI 正在處理 ${working} 則留言,改好的地方會直接出現在投影片上。`
          : "用「留言」(C)點在要改的地方寫意見,寫完按「交給 AI 修改」。開著這個網頁的 AI 會自動照著改。"}
      </p>
      {total === 0 ? (
        <p className="ne-drawer-empty">還沒有留言。按工具列的「留言」(C),再點投影片上要改的地方。</p>
      ) : (
        <ol>
          {scene.pages.map((page, pi) =>
            (page.comments ?? []).map((c, i) => (
              <li key={c.id}>
                <button type="button" className="ne-drawer-item" onClick={() => onGo(pi, c.id)}>
                  <span className="ne-drawer-where">
                    第 {pi + 1} 頁 · {i + 1} · {excerpt(page, c.target)}
                    {c.state === "submitted" && <em className="ne-working">AI 處理中</em>}
                  </span>
                  <span className="ne-drawer-text">{c.text}</span>
                </button>
                <button type="button" className="ne-drawer-done" aria-label="完成並刪除這則留言" title="完成並刪除" onClick={() => onResolve(c.id)}>
                  ✓
                </button>
              </li>
            )),
          )}
        </ol>
      )}
    </aside>
  );
}
