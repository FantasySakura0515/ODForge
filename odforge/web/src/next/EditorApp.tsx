import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import type { Actions } from "./Bars";
import { Canvas, leaveText, type Tool } from "./Canvas";
import { alignOps, distributeOps, flatten, geomOp, moved, riders } from "./geometry";
import { CommentDrawer } from "./Comments";
import { COMPONENTS, ElementsPanel } from "./Elements";
import { PageList } from "./PageList";
import { formatAll, normalise, richOf, type Format } from "./richtext";
import { useDocumentFonts } from "./SlideView";
import type { DocState, Op, RichParagraph, SceneElement } from "./types";

const NUDGE = 0.05;
const NUDGE_BIG = 0.5;
const NUDGE_SETTLE_MS = 400;
const ZOOM_STEPS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4, 6, 8];

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

/** Ids in reverse document order, so deleting one never shifts the next one's id. */
function byIndexDesc(ids: string[]): string[] {
  const key = (id: string) => id.split("/")[1].split(".").map(Number);
  return [...ids].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
      const d = (kb[i] ?? -1) - (ka[i] ?? -1);
      if (d) return d;
    }
    return 0;
  });
}

function mapTree(elements: SceneElement[], fn: (el: SceneElement) => SceneElement): SceneElement[] {
  return elements.map((el) => {
    const next = fn(el);
    return next.kind === "group" && next.children ? { ...next, children: mapTree(next.children, fn) } : next;
  });
}

/** Show edited text at once; the server's answer replaces it a moment later. */
function withText(doc: DocState, id: string, paragraphs: RichParagraph[]): DocState {
  const pages = doc.scene.pages.map((page) => ({
    ...page,
    elements: mapTree(page.elements, (el) => {
      if (el.id !== id || el.kind === "line" || !el.text) return el;
      const old = el.text.paragraphs;
      const next = paragraphs.map((p, i) => {
        const model = old[Math.min(i, old.length - 1)];
        const base = model?.runs[0] ?? { text: "" };
        return {
          ...model,
          align: p.align ?? model?.align,
          runs: p.runs.map((r) => ({
            ...base,
            text: r.text,
            weight: r.weight ?? base.weight,
            italic: r.italic,
            underline: r.underline,
            color: r.color ?? base.color,
            size: r.size ?? base.size,
            family: r.family ? [r.family] : base.family,
          })),
        };
      });
      return { ...el, text: { ...el.text, paragraphs: next } };
    }),
  }));
  return { ...doc, scene: { ...doc.scene, pages } };
}

function readImage(file: File): Promise<{ data: string; ratio: number }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("讀不到這個檔案"));
    reader.onload = () => {
      const url = String(reader.result);
      const img = new Image();
      img.onload = () => resolve({ data: url.split(",")[1] ?? "", ratio: img.naturalWidth / Math.max(img.naturalHeight, 1) });
      img.onerror = () => reject(new Error("這不是可以讀取的圖片"));
      img.src = url;
    };
    reader.readAsDataURL(file);
  });
}

export function EditorApp() {
  const [doc, setDoc] = useState<DocState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [exporting, setExporting] = useState(false);

  async function exportFile() {
    if (!doc || exporting) return;
    setExporting(true);
    leaveText();
    try {
      // Every edit already sent must be on disk before the file is read.
      await queue.current;
      await api.exportOdp(doc.name);
      setNotice(`已匯出 ${doc.name}`);
      window.setTimeout(() => setNotice(""), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }
  const [pageIndex, setPageIndex] = useState(0);
  const [selection, setSelection] = useState<string[]>([]);
  const [editing, setEditingState] = useState<{ id: string | null; selectAll: boolean }>({ id: null, selectAll: false });
  const [tool, setTool] = useState<Tool>("select");
  const [zoom, setZoom] = useState(1);
  const [openComment, setOpenComment] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [panel, setPanel] = useState<"pages" | "elements">("pages");
  const [nudge, setNudge] = useState<Map<string, SceneElement>>(new Map());
  // ``top`` marks which items were selected (the rest rode along inside a card).
  const [clipboard, setClipboard] = useState<{ items: string[]; top: boolean[]; page: number; pastes: number } | null>(null);
  const nudgeTimer = useRef<number>();
  const fresh = useRef<Set<string>>(new Set());
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const fileInput = useRef<HTMLInputElement>(null);

  useDocumentFonts(doc?.scene.fonts);

  useEffect(() => {
    api.loadDoc().then(setDoc, (e: Error) => setLoadError(e.message));
  }, []);

  useEffect(() => {
    if (doc) document.title = `${doc.name} · 文鍛`;
  }, [doc]);

  // Someone else (an agent through MCP, another window) may save the same file.
  // Poll its version and redraw when it moves — but never under a person who is
  // typing, and never while our own save is still on its way.
  const busy = useRef({ saving, editing: false, version: 0 });
  busy.current = { saving, editing: editing.id !== null, version: doc?.version ?? 0 };
  useEffect(() => {
    const timer = window.setInterval(async () => {
      if (busy.current.saving || busy.current.editing || document.hidden) return;
      try {
        const version = await api.getVersion();
        if (version === busy.current.version || busy.current.saving || busy.current.editing) return;
        const state = await api.loadDoc();
        setDoc(state);
        setSelection((ids) => ids.filter((id) => state.scene.pages.some((p) => p.elements.some((e) => e.id === id))));
        setNotice("檔案在別處被修改,已更新到最新版本");
        window.setTimeout(() => setNotice(""), 2500);
      } catch {
        // The server may be restarting; try again on the next tick.
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  /** Run server calls one after another so their results land in order. */
  const run = useCallback((call: () => Promise<DocState>): Promise<DocState | null> => {
    const next = queue.current.then(async () => {
      setSaving(true);
      try {
        const state = await call();
        setDoc(state);
        setError("");
        return state;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setSaving(false);
      }
    });
    queue.current = next;
    return next;
  }, []);

  const apply = useCallback(
    async (ops: Op[]) => {
      if (!ops.length) return [];
      const state = await run(() => api.sendOps(ops));
      return state ? state.created : null;
    },
    [run],
  );

  const scene = doc?.scene;
  const pages = scene?.pages ?? [];
  const page = pages[Math.min(pageIndex, Math.max(pages.length - 1, 0))];

  const commentCount = pages.reduce((n, p) => n + (p.comments?.length ?? 0), 0);
  const draftCount = pages.reduce((n, p) => n + (p.comments ?? []).filter((c) => c.state !== "submitted").length, 0);
  const workingCount = commentCount - draftCount;

  async function submitComments() {
    await apply([{ op: "comments_submit" }]);
    setTool("select");
  }

  const colors = useMemo(() => {
    const counts = new Map<string, number>();
    for (const pg of pages) {
      for (const el of pg.elements) {
        if (el.kind === "line" || !el.text) continue;
        for (const p of el.text.paragraphs) for (const r of p.runs) if (r.color) counts.set(r.color.toUpperCase(), (counts.get(r.color.toUpperCase()) ?? 0) + 1);
      }
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([c]) => c);
  }, [pages]);

  function setEditing(id: string | null, selectAll = false) {
    if (id && selectAll) fresh.current.add(id);
    setEditingState({ id, selectAll });
  }

  function goToPage(index: number) {
    leaveText();
    setPageIndex(Math.max(0, Math.min(index, pages.length - 1)));
    setSelection([]);
    setEditing(null);
  }

  async function commitText(id: string, paragraphs: RichParagraph[]) {
    setEditing(null);
    const el = page ? flatten(page.elements).find((e) => e.id === id) : undefined;
    const isNew = fresh.current.delete(id);
    if (!el || el.kind === "line") return;
    const empty = paragraphs.every((p) => !p.runs.some((r) => r.text.trim()));
    if (isNew && empty) {
      setSelection([]);
      await apply([{ op: "delete", id }]);
      return;
    }
    if (el.text && normalise(richOf(el.text.paragraphs)) === normalise(paragraphs)) return;
    setDoc((current) => (current ? withText(current, id, paragraphs) : current));
    await apply([{ op: "text", id, paragraphs }]);
  }

  async function pageOp(op: Op) {
    const state = await run(() => api.sendOps([op]));
    const target = state?.created.find((c) => c.startsWith("page:"));
    leaveText();
    setSelection([]);
    setEditing(null);
    if (target && state) {
      // Clamp against the deck as it is now, not as it was before the page was added.
      setPageIndex(Math.max(0, Math.min(Number(target.slice(5)), state.scene.pages.length - 1)));
    }
  }

  function flushNudge(map: Map<string, SceneElement>) {
    if (!map.size) return;
    void apply([...map.values()].map(geomOp)).then(() => setNudge(new Map()));
  }

  /** Add a ready-made part; (x, y) is where its centre should land. */
  async function insertComponent(kind: string, cx?: number, cy?: number) {
    if (!scene) return;
    const spec = COMPONENTS.find((c) => c.kind === kind);
    const w = spec?.w ?? 6;
    const h = spec?.h ?? 3;
    const x = Math.max(0, Math.min(scene.width - w, (cx ?? scene.width / 2) - w / 2));
    const y = Math.max(0, Math.min(scene.height - h, (cy ?? scene.height / 2) - h / 2));
    const created = await apply([{ op: "add_component", page: pageIndex, kind, x, y }]);
    if (created?.length) setSelection(created);
  }

  async function insertImage(file: File) {
    if (!scene) return;
    try {
      const { data, ratio } = await readImage(file);
      let w = Math.min(12, scene.width * 0.6);
      let h = w / ratio;
      if (h > scene.height * 0.6) {
        h = scene.height * 0.6;
        w = h * ratio;
      }
      const created = await apply([
        { op: "add_image", page: pageIndex, x: (scene.width - w) / 2, y: (scene.height - h) / 2, w, h, data },
      ]);
      if (created?.length) setSelection(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  /** The selection plus what sits inside each selected card, in paint order. */
  function withRiders(ids: string[]): string[] {
    if (!page) return ids;
    const all = new Set(ids);
    for (const id of ids) {
      const el = page.elements.find((e) => e.id === id);
      if (el) riders(el, page.elements).forEach((r) => all.add(r));
    }
    return byIndexDesc([...all]).reverse();
  }

  const actions: Actions = {
    canPaste: !!clipboard,
    copy: () => {
      if (!selection.length) return;
      void api.clip(withRiders(selection)).then(
        (items) => {
          const ids = withRiders(selection);
          setClipboard({ items, top: ids.map((id) => selection.includes(id)), page: pageIndex, pastes: 0 });
        },
        (e: Error) => setError(e.message),
      );
    },
    cut: () => {
      if (!selection.length) return;
      const ids = withRiders(selection);
      void api.clip(ids).then(
        async (items) => {
          setClipboard({ items, top: ids.map((id) => selection.includes(id)), page: pageIndex, pastes: -1 });
          setSelection([]);
          await apply(byIndexDesc(ids).map((id) => ({ op: "delete", id })));
        },
        (e: Error) => setError(e.message),
      );
    },
    paste: () => {
      if (!clipboard) return;
      const samePage = clipboard.page === pageIndex;
      const step = samePage ? (clipboard.pastes + 1) * 0.5 : 0;
      void apply([{ op: "paste", page: pageIndex, items: clipboard.items, dx: step, dy: step }]).then((created) => {
        if (created?.length) setSelection(created.filter((_, i) => clipboard.top[i]));
        setClipboard((c) => (c ? { ...c, pastes: samePage ? c.pastes + 1 : c.pastes } : c));
      });
    },
    duplicate: () => {
      if (!selection.length) return;
      const ids = withRiders(selection);
      void api.clip(ids).then(
        (items) =>
          apply([{ op: "paste", page: pageIndex, items, dx: 0.5, dy: 0.5 }]).then((created) => {
            // Select the copies of what was selected, not the text riding inside them.
            if (created?.length) setSelection(created.filter((_, i) => selection.includes(ids[i])));
          }),
        (e: Error) => setError(e.message),
      );
    },
    remove: () => {
      if (!selection.length) return;
      const ids = byIndexDesc(withRiders(selection));
      setSelection([]);
      void apply(ids.map((id) => ({ op: "delete", id })));
    },
    front: () => {
      if (selection.length !== 1) return;
      void apply([{ op: "order", id: selection[0], to: "front" }]).then((c) => c?.[0] && setSelection([c[0]]));
    },
    back: () => {
      if (selection.length !== 1) return;
      void apply([{ op: "order", id: selection[0], to: "back" }]).then((c) => c?.[0] && setSelection([c[0]]));
    },
    align: (kind) => {
      if (!page || !scene) return;
      void apply(alignOps(page.elements, selection, kind, scene) as Op[]);
    },
    distribute: (axis) => {
      if (!page) return;
      void apply(distributeOps(page.elements, selection, axis) as Op[]);
    },
    format: (format: Format) => {
      if (!page) return;
      const ops: Op[] = [];
      const all = flatten(page.elements);
      for (const id of selection) {
        // A selected group formats every text inside it.
        const targets = all.filter((e) => e.id === id || e.id.startsWith(`${id}.`));
        for (const el of targets) {
          if (el.kind === "line" || !el.text) continue;
          ops.push({ op: "text", id: el.id, paragraphs: formatAll(richOf(el.text.paragraphs), format) });
        }
      }
      void apply(ops);
    },
    canGroup: selection.length > 1 && selection.every((id) => !id.includes(".")),
    canUngroup:
      selection.length === 1 && !selection[0].includes(".") && page?.elements.find((e) => e.id === selection[0])?.kind === "group",
    group: () => {
      if (!(selection.length > 1 && selection.every((id) => !id.includes(".")))) return;
      void apply([{ op: "group", ids: withRiders(selection) }]).then((c) => c?.[0] && setSelection([c[0]]));
    },
    ungroup: () => {
      const id = selection[0];
      if (selection.length !== 1 || page?.elements.find((e) => e.id === id)?.kind !== "group") return;
      void apply([{ op: "ungroup", id }]).then((c) => c?.length && setSelection(c));
    },
  };

  function zoomBy(direction: 1 | -1) {
    const next = direction > 0 ? ZOOM_STEPS.find((z) => z > zoom + 0.01) : [...ZOOM_STEPS].reverse().find((z) => z < zoom - 0.01);
    if (next) setZoom(next);
  }

  // Keyboard: the shortcuts people expect from a drawing tool.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!page || isTyping(e.target)) return;
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();

      if (mod && key === "s") return e.preventDefault(), void exportFile();
      if (mod && (key === "=" || key === "+")) return e.preventDefault(), zoomBy(1);
      if (mod && key === "-") return e.preventDefault(), zoomBy(-1);
      if (mod && key === "0") return e.preventDefault(), setZoom(1);
      if (mod && key === "z") {
        e.preventDefault();
        setSelection([]);
        void run(e.shiftKey ? api.redo : api.undo);
        return;
      }
      if (mod && key === "y") {
        e.preventDefault();
        setSelection([]);
        void run(api.redo);
        return;
      }
      if (mod && key === "a") return e.preventDefault(), setSelection(page.elements.map((el) => el.id));
      if (mod && key === "c") return e.preventDefault(), actions.copy();
      if (mod && key === "x") return e.preventDefault(), actions.cut();
      if (mod && key === "v") return e.preventDefault(), actions.paste();
      if (mod && key === "d") return e.preventDefault(), actions.duplicate();
      if (mod && key === "g") return e.preventDefault(), e.shiftKey ? actions.ungroup() : actions.group();
      if (mod && (key === "b" || key === "i" || key === "u") && selection.length) {
        e.preventDefault();
        return actions.format({ kind: key === "b" ? "bold" : key === "i" ? "italic" : "underline" });
      }
      if (mod && e.shiftKey && (e.code === "BracketRight" || key === "]")) return e.preventDefault(), actions.front();
      if (mod && e.shiftKey && (e.code === "BracketLeft" || key === "[")) return e.preventDefault(), actions.back();
      if ((e.key === "Delete" || e.key === "Backspace") && selection.length) return e.preventDefault(), actions.remove();
      if (e.key === "Escape") {
        setSelection([]);
        setTool("select");
        return;
      }
      if (e.key === "Enter" && selection.length === 1) {
        const el = page.elements.find((x) => x.id === selection[0]);
        if (el && el.kind !== "line" && el.text) {
          e.preventDefault();
          setEditing(el.id);
        }
        return;
      }
      if (!mod && key === "c") return setTool("comment");
      if (!mod && key === "t") return setTool("text");
      if (!mod && key === "r") return setTool("rect");
      if (!mod && key === "v") return setTool("select");
      if (e.key === "PageDown" || (e.key === "ArrowDown" && !selection.length)) return e.preventDefault(), goToPage(pageIndex + 1);
      if (e.key === "PageUp" || (e.key === "ArrowUp" && !selection.length)) return e.preventDefault(), goToPage(pageIndex - 1);
      if (e.key.startsWith("Arrow") && selection.length) {
        e.preventDefault();
        const step = e.shiftKey ? NUDGE_BIG : NUDGE;
        const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
        const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
        setNudge((current) => {
          const next = new Map(current);
          for (const id of selection) {
            const base = next.get(id) ?? page.elements.find((x) => x.id === id);
            if (base) next.set(id, moved(base, dx, dy));
          }
          window.clearTimeout(nudgeTimer.current);
          nudgeTimer.current = window.setTimeout(() => flushNudge(next), NUDGE_SETTLE_MS);
          return next;
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (loadError) {
    return (
      <div className="ne-empty">
        <p>{loadError}</p>
      </div>
    );
  }
  if (!doc || !scene || !page) {
    return (
      <div className="ne-empty">
        <p>讀取中…</p>
      </div>
    );
  }

  return (
    <div className="ne-app">
      <header className="ne-top">
        <div className="ne-title">
          <span className="ne-mark">文鍛</span>
          <h1>{doc.name}</h1>
          <span className="ne-status" aria-live="polite">{notice || (saving ? "儲存中…" : "已儲存")}</span>
        </div>
        <div className="ne-tools" role="toolbar" aria-label="工具">
          <button type="button" aria-pressed={tool === "select"} title="選取 (V)" onClick={() => setTool("select")}>選取</button>
          <button type="button" aria-pressed={tool === "text"} title="文字 (T)" onClick={() => setTool("text")}>文字</button>
          <button type="button" aria-pressed={tool === "rect"} title="方塊 (R)" onClick={() => setTool("rect")}>方塊</button>
          <button type="button" title="插入圖片" onClick={() => fileInput.current?.click()}>圖片</button>
          <button type="button" aria-pressed={tool === "comment"} title="留言 (C):點投影片上要改的地方" onClick={() => setTool("comment")}>留言</button>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void insertImage(file);
              e.target.value = "";
            }}
          />
          <span className="ne-sep" />
          <button type="button" aria-pressed={drawer} title="查看全部留言" onClick={() => setDrawer((v) => !v)}>
            留言 {commentCount}
          </button>
          {draftCount > 0 && (
            <button type="button" className="ne-ai" title="把留言交給開著這個網頁的 AI" onClick={() => void submitComments()}>
              交給 AI 修改({draftCount})
            </button>
          )}
          {workingCount > 0 && <span className="ne-ai-working" aria-live="polite">AI 處理中 {workingCount} 則…</span>}
          <span className="ne-sep" />
          <button type="button" aria-label="復原" title="復原 (⌘Z)" disabled={!doc.canUndo} onClick={() => { setSelection([]); void run(api.undo); }}>↶</button>
          <button type="button" aria-label="重做" title="重做 (⇧⌘Z)" disabled={!doc.canRedo} onClick={() => { setSelection([]); void run(api.redo); }}>↷</button>
          <button type="button" className="ne-download" disabled={exporting} title="匯出目前這一版 (⌘S)" onClick={() => void exportFile()}>
            {exporting ? "匯出中…" : "匯出 .odp"}
          </button>
        </div>
      </header>

      <div className="ne-body">
        <div className="ne-left">
          <div className="ne-tabs" role="tablist" aria-label="側欄">
            <button type="button" role="tab" aria-selected={panel === "pages"} onClick={() => setPanel("pages")}>頁面</button>
            <button type="button" role="tab" aria-selected={panel === "elements"} onClick={() => setPanel("elements")}>元素</button>
          </div>
          {panel === "elements" ? (
            <ElementsPanel accent={doc.theme?.accent ?? "#1F3F99"} onInsert={(kind) => void insertComponent(kind)} />
          ) : (
        <PageList
          scene={scene}
          current={pageIndex}
          onSelect={goToPage}
          onAdd={() => void pageOp({ op: "page_add", after: pageIndex })}
          onDuplicate={(i) => void pageOp({ op: "page_duplicate", page: i })}
          onDelete={(i) => {
            void apply([{ op: "page_delete", page: i }]).then(() => goToPage(Math.min(pageIndex, pages.length - 2)));
          }}
          onMove={(from, to) => void pageOp({ op: "page_move", page: from, to })}
        />
          )}
        </div>
        <main className="ne-main">
          <div className="ne-hint">
            <span>第 {pageIndex + 1} 頁 / {pages.length}</span>
            <span className="ne-hint-keys">點一下選整張卡片,再點一下選裡面的字 · 雙擊改字 · 按住 ⌥ 不吸附 · 右鍵看更多</span>
            <span className="ne-zoom" role="group" aria-label="縮放">
              <button type="button" aria-label="縮小 (⌘−)" onClick={() => zoomBy(-1)}>−</button>
              <button type="button" className="ne-zoom-value" title="符合視窗 (⌘0)" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
              <button type="button" aria-label="放大 (⌘+)" onClick={() => zoomBy(1)}>＋</button>
            </span>
          </div>
          <Canvas
            page={page}
            pageIndex={pageIndex}
            width={scene.width}
            height={scene.height}
            selection={selection}
            setSelection={setSelection}
            editingId={editing.id}
            selectAll={editing.selectAll}
            setEditing={setEditing}
            tool={tool}
            setTool={setTool}
            apply={apply}
            nudge={nudge}
            onTextCommit={(id, paragraphs) => void commitText(id, paragraphs)}
            actions={actions}
            zoom={zoom}
            setZoom={setZoom}
            colors={colors}
            fonts={doc.fontChoices ?? []}
            openComment={openComment}
            setOpenComment={setOpenComment}
            onDropComponent={(kind, x, y) => void insertComponent(kind, x, y)}
          />
        </main>
        {drawer && (
          <CommentDrawer
            scene={scene}
            onGo={(page, id) => {
              goToPage(page);
              setOpenComment(id);
            }}
            onResolve={(id) => {
              setOpenComment(null);
              void apply([{ op: "comment_delete", comment: id }]);
            }}
            onClose={() => setDrawer(false)}
            onSubmit={() => void submitComments()}
            onDone={() => {
              void apply([{ op: "session", state: "done" }]).then(() => {
                setNotice("已告訴 AI 這輪改完了");
                window.setTimeout(() => setNotice(""), 2500);
              });
            }}
          />
        )}
      </div>

      {error && (
        <div className="ne-toast" role="alert">
          <span>{error}</span>
          <button type="button" aria-label="關閉" onClick={() => setError("")}>×</button>
        </div>
      )}
    </div>
  );
}
