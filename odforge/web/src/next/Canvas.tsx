import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ALIGN_LABEL, ContextMenu, SelectionBar, type Actions, type MenuItem } from "./Bars";
import { CommentComposer, CommentPins } from "./Comments";
import {
  HANDLES,
  bounds,
  containerOf,
  fmt,
  gaps as findGaps,
  geomOp,
  intersects,
  moved,
  resized,
  riders,
  snap,
  union,
  withBox,
  type AlignKind,
  type Gap,
  type Guide,
  type Handle,
} from "./geometry";
import { execFormat } from "./richtext";
import { SlideView, type ElementHandlers } from "./SlideView";
import type { LineElement, Op, Page, RichParagraph, Rect, SceneElement } from "./types";

export type Tool = "select" | "text" | "rect" | "comment";

const SNAP_PX = 5;
const DRAG_START_PX = 3;
const DOUBLE_CLICK_MS = 400;

/** End text editing the way a person would: by leaving the text (its blur commits). */
export function leaveText() {
  const active = document.activeElement as HTMLElement | null;
  if (active?.isContentEditable) active.blur();
}

type Drag =
  | {
      kind: "move";
      x0: number;
      y0: number;
      ids: string[];
      origin: Map<string, SceneElement>;
      base: Rect;
      others: Rect[];
      started: boolean;
      clickedId: string;
      drillId: string | null;
      wasSelected: boolean;
      shift: boolean;
    }
  | { kind: "resize"; x0: number; y0: number; handle: Handle; origin: SceneElement; box: Rect; others: Rect[] }
  | { kind: "line"; x0: number; y0: number; end: 1 | 2; origin: LineElement }
  | { kind: "marquee"; x0: number; y0: number; additive: boolean; initial: string[] };

export interface CanvasProps {
  page: Page;
  pageIndex: number;
  width: number;
  height: number;
  selection: string[];
  setSelection: (ids: string[]) => void;
  editingId: string | null;
  selectAll: boolean;
  setEditing: (id: string | null, selectAll?: boolean) => void;
  tool: Tool;
  setTool: (tool: Tool) => void;
  apply: (ops: Op[]) => Promise<string[] | null>;
  nudge: Map<string, SceneElement>;
  onTextCommit: (id: string, paragraphs: RichParagraph[]) => void;
  actions: Actions;
  zoom: number;
  setZoom: (zoom: number) => void;
  colors: string[];
  fonts: { family: string; embeddable: boolean }[];
  openComment: string | null;
  setOpenComment: (id: string | null) => void;
}

export function Canvas(props: CanvasProps) {
  const { page, width, height, selection, setSelection, tool, zoom } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(30);
  const s = fit * zoom;
  const drag = useRef<Drag | null>(null);
  const [dragging, setDragging] = useState(false);
  const [overrides, setOverrides] = useState<Map<string, SceneElement>>(new Map());
  const [guides, setGuides] = useState<Guide[]>([]);
  const [gapList, setGaps] = useState<Gap[]>([]);
  const [marquee, setMarquee] = useState<Rect | null>(null);
  const [readout, setReadout] = useState<{ x: number; y: number; text: string } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [composing, setComposing] = useState<{ target: string | null; x: number; y: number } | null>(null);
  const lastPress = useRef<{ id: string; at: number } | null>(null);

  // Fit the slide to the space available; zoom multiplies on top of that.
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const measure = () => {
      const pad = 56;
      const w = Math.max(wrap.clientWidth - pad * 2, 200);
      const h = Math.max(wrap.clientHeight - pad * 2, 120);
      setFit(Math.min(w / width, h / height));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [width, height]);

  // Pinch / ctrl-wheel zoom.
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      props.setZoom(Math.min(8, Math.max(0.25, zoom * Math.exp(-e.deltaY * 0.01))));
    };
    wrap.addEventListener("wheel", onWheel, { passive: false });
    return () => wrap.removeEventListener("wheel", onWheel);
  });

  const byId = useMemo(() => new Map(page.elements.map((e) => [e.id, e])), [page]);
  const live = (id: string) => overrides.get(id) ?? props.nudge.get(id) ?? byId.get(id);
  const merged = useMemo(() => {
    const m = new Map(props.nudge);
    overrides.forEach((v, k) => m.set(k, v));
    return m;
  }, [overrides, props.nudge]);

  /** What a press on ``el`` selects: its card first, the element itself once the card is selected. */
  function pickFor(el: SceneElement, deep: boolean): { pick: SceneElement; drill: string | null } {
    if (deep || selection.includes(el.id)) return { pick: el, drill: null };
    const container = containerOf(el, page.elements);
    if (!container) return { pick: el, drill: null };
    return { pick: container, drill: selection.includes(container.id) ? el.id : null };
  }

  function point(e: { clientX: number; clientY: number }) {
    const rect = stageRef.current!.getBoundingClientRect();
    return { x: (e.clientX - rect.left) / s, y: (e.clientY - rect.top) / s };
  }

  function othersExcept(ids: Set<string>): Rect[] {
    return page.elements.filter((e) => !ids.has(e.id)).map(bounds);
  }

  function capture(e: ReactPointerEvent) {
    stageRef.current?.setPointerCapture(e.pointerId);
    setDragging(true);
  }

  function startComment(el: SceneElement | null, at?: { x: number; y: number }) {
    props.setOpenComment(null);
    if (el) {
      const b = bounds(el);
      setComposing({ target: el.id, x: b.x + b.w, y: b.y });
    } else if (at) {
      setComposing({ target: null, x: at.x, y: at.y });
    }
  }

  async function submitComment(text: string) {
    if (!composing) return;
    const op = composing.target
      ? { op: "comment_add", id: composing.target, text }
      : { op: "comment_add", page: props.pageIndex, x: composing.x, y: composing.y, text };
    setComposing(null);
    // Stay in the comment tool: people usually leave several in a row.
    await props.apply([op]);
  }

  function onElementPointerDown(e: ReactPointerEvent, el: SceneElement) {
    if (tool === "comment" && e.button === 0) {
      e.stopPropagation();
      startComment(el);
      return;
    }
    if (tool !== "select") return;
    if (e.button === 2) {
      e.stopPropagation();
      if (!selection.includes(el.id)) {
        const { pick } = pickFor(el, e.metaKey || e.ctrlKey);
        if (!selection.includes(pick.id)) setSelection([pick.id]);
      }
      return;
    }
    if (e.button !== 0) return;
    e.stopPropagation();
    if (props.editingId === el.id) return;
    if (props.editingId) leaveText();
    setMenu(null);

    const now = Date.now();
    const previous = lastPress.current;
    lastPress.current = { id: el.id, at: now };
    if (previous && previous.id === el.id && now - previous.at < DOUBLE_CLICK_MS && el.kind !== "line" && el.text) {
      lastPress.current = null;
      e.preventDefault(); // keep the browser from moving focus; the editor takes it
      setSelection([el.id]);
      props.setEditing(el.id);
      return;
    }

    const { pick, drill } = pickFor(el, e.metaKey || e.ctrlKey);
    const wasSelected = selection.includes(pick.id);
    let next = selection;
    if (e.shiftKey) next = wasSelected ? selection.filter((id) => id !== pick.id) : [...selection, pick.id];
    else if (!wasSelected) next = [pick.id];
    setSelection(next);
    if (!next.includes(pick.id)) return;

    const ids = new Set(next);
    for (const id of next) {
      const item = byId.get(id);
      if (item) riders(item, page.elements).forEach((r) => ids.add(r));
    }
    const origin = new Map<string, SceneElement>();
    ids.forEach((id) => {
      const item = byId.get(id);
      if (item) origin.set(id, item);
    });
    const p = point(e);
    drag.current = {
      kind: "move",
      x0: p.x,
      y0: p.y,
      ids: [...ids],
      origin,
      base: union([...origin.values()].map(bounds)),
      others: othersExcept(ids),
      started: false,
      clickedId: pick.id,
      drillId: drill,
      wasSelected,
      shift: e.shiftKey,
    };
    capture(e);
  }

  // Elements keep one handler object forever and reach the current closure through
  // this ref — that is what lets them skip re-rendering while something else moves.
  const latest = useRef({ onElementPointerDown, setHoverId });
  latest.current = { onElementPointerDown, setHoverId };
  const handlers = useMemo<ElementHandlers>(
    () => ({
      onPointerDown: (e, el) => latest.current.onElementPointerDown(e, el),
      onPointerEnter: (el) => latest.current.setHoverId(el.id),
      onPointerLeave: (el) => latest.current.setHoverId((h) => (h === el.id ? null : h)),
    }),
    [],
  );
  const commitRef = useRef(props.onTextCommit);
  commitRef.current = props.onTextCommit;
  const onCommit = useMemo(() => (id: string, paragraphs: RichParagraph[]) => commitRef.current(id, paragraphs), []);

  function onHandlePointerDown(e: ReactPointerEvent, handle: Handle, el: SceneElement) {
    e.stopPropagation();
    const p = point(e);
    drag.current = { kind: "resize", x0: p.x, y0: p.y, handle, origin: el, box: bounds(el), others: othersExcept(new Set([el.id])) };
    capture(e);
  }

  function onLineEndPointerDown(e: ReactPointerEvent, end: 1 | 2, el: LineElement) {
    e.stopPropagation();
    const p = point(e);
    drag.current = { kind: "line", x0: p.x, y0: p.y, end, origin: el };
    capture(e);
  }

  async function onStagePointerDown(e: ReactPointerEvent) {
    if (e.button !== 0) return;
    setMenu(null);
    props.setOpenComment(null);
    const p = point(e);
    if (props.editingId) leaveText();
    if (tool === "comment") {
      startComment(null, p);
      return;
    }
    if (composing) {
      setComposing(null);
      return;
    }
    if (tool === "text") {
      props.setTool("select");
      const created = await props.apply([{ op: "add_text", page: props.pageIndex, x: p.x, y: p.y, w: 8, h: 1.2, text: "" }]);
      if (created?.[0]) {
        setSelection([created[0]]);
        props.setEditing(created[0], true);
      }
      return;
    }
    if (tool === "rect") {
      props.setTool("select");
      const created = await props.apply([{ op: "add_rect", page: props.pageIndex, x: p.x, y: p.y, w: 6, h: 3.5 }]);
      if (created?.[0]) setSelection([created[0]]);
      return;
    }
    drag.current = { kind: "marquee", x0: p.x, y0: p.y, additive: e.shiftKey, initial: e.shiftKey ? selection : [] };
    if (!e.shiftKey) setSelection([]);
    capture(e);
  }

  function snapAxis(value: number, targets: number[], threshold: number): number | null {
    let best: number | null = null;
    for (const t of targets) {
      if (Math.abs(t - value) <= threshold && (best === null || Math.abs(t - value) < Math.abs(best - value))) best = t;
    }
    return best;
  }

  function onPointerMove(e: ReactPointerEvent) {
    const d = drag.current;
    if (!d) return;
    const p = point(e);
    const dx = p.x - d.x0;
    const dy = p.y - d.y0;
    const free = e.altKey;

    if (d.kind === "move") {
      if (!d.started && Math.hypot(dx * s, dy * s) < DRAG_START_PX) return;
      d.started = true;
      const raw = { ...d.base, x: d.base.x + dx, y: d.base.y + dy };
      const snapped = free ? { dx: 0, dy: 0, guides: [] } : snap(raw, d.others, { width, height }, SNAP_PX / s);
      const fx = dx + snapped.dx;
      const fy = dy + snapped.dy;
      const next = new Map<string, SceneElement>();
      d.origin.forEach((el, id) => next.set(id, moved(el, fx, fy)));
      setOverrides(next);
      setGuides(snapped.guides);
      const placed = { ...d.base, x: d.base.x + fx, y: d.base.y + fy };
      setGaps(findGaps(placed, d.others));
      setReadout({ x: placed.x + placed.w / 2, y: placed.y + placed.h, text: `x ${fmt(placed.x)} · y ${fmt(placed.y)} cm` });
      return;
    }

    if (d.kind === "resize") {
      let box = resized(d.box, d.handle, dx, dy, e.shiftKey);
      const lines: Guide[] = [];
      if (!free) {
        const xs = [0, width / 2, width, ...d.others.flatMap((o) => [o.x, o.x + o.w / 2, o.x + o.w])];
        const ys = [0, height / 2, height, ...d.others.flatMap((o) => [o.y, o.y + o.h / 2, o.y + o.h])];
        const t = SNAP_PX / s;
        const hit = (v: number | null, axis: "v" | "h") => {
          if (v !== null) lines.push(axis === "v" ? { axis, pos: v, from: 0, to: height } : { axis, pos: v, from: 0, to: width });
          return v;
        };
        if (d.handle.includes("w")) {
          const v = hit(snapAxis(box.x, xs, t), "v");
          if (v !== null) box = { ...box, w: box.w + box.x - v, x: v };
        }
        if (d.handle.includes("e")) {
          const v = hit(snapAxis(box.x + box.w, xs, t), "v");
          if (v !== null) box = { ...box, w: v - box.x };
        }
        if (d.handle.includes("n")) {
          const v = hit(snapAxis(box.y, ys, t), "h");
          if (v !== null) box = { ...box, h: box.h + box.y - v, y: v };
        }
        if (d.handle.includes("s")) {
          const v = hit(snapAxis(box.y + box.h, ys, t), "h");
          if (v !== null) box = { ...box, h: v - box.y };
        }
      }
      setOverrides(new Map([[d.origin.id, withBox(d.origin, box)]]));
      setGuides(lines);
      setReadout({ x: box.x + box.w / 2, y: box.y + box.h, text: `${fmt(box.w)} × ${fmt(box.h)} cm` });
      return;
    }

    if (d.kind === "line") {
      const o = d.origin;
      let x = (d.end === 1 ? o.x1 : o.x2) + dx;
      let y = (d.end === 1 ? o.y1 : o.y2) + dy;
      const ax = d.end === 1 ? o.x2 : o.x1;
      const ay = d.end === 1 ? o.y2 : o.y1;
      if (e.shiftKey) {
        if (Math.abs(x - ax) > Math.abs(y - ay)) y = ay;
        else x = ax;
      }
      const next: LineElement = d.end === 1 ? { ...o, x1: x, y1: y } : { ...o, x2: x, y2: y };
      setOverrides(new Map([[o.id, next]]));
      setReadout({ x, y, text: `${fmt(Math.hypot(next.x2 - next.x1, next.y2 - next.y1))} cm` });
      return;
    }

    const rect = { x: Math.min(d.x0, p.x), y: Math.min(d.y0, p.y), w: Math.abs(dx), h: Math.abs(dy) };
    setMarquee(rect);
    const hit = page.elements.filter((el) => intersects(rect, bounds(el))).map((el) => el.id);
    setSelection(d.additive ? [...new Set([...d.initial, ...hit])] : hit);
  }

  async function onPointerUp() {
    const d = drag.current;
    drag.current = null;
    setDragging(false);
    setGuides([]);
    setGaps([]);
    setMarquee(null);
    setReadout(null);
    if (!d) return;
    if (d.kind === "move") {
      if (!d.started) {
        if (d.drillId) setSelection([d.drillId]);
        else if (d.wasSelected && !d.shift && selection.length > 1) setSelection([d.clickedId]);
        return;
      }
      const ops = d.ids.map((id) => overrides.get(id)).filter((el): el is SceneElement => !!el).map(geomOp);
      await props.apply(ops);
      setOverrides(new Map());
      return;
    }
    if (d.kind === "resize" || d.kind === "line") {
      const el = overrides.get(d.origin.id);
      if (el) await props.apply([geomOp(el)]);
      setOverrides(new Map());
    }
  }

  function onContextMenu(e: React.MouseEvent) {
    e.preventDefault();
    if (props.editingId) leaveText();
    setMenu({ x: e.clientX, y: e.clientY });
  }

  const selected = selection.map(live).filter((el): el is SceneElement => !!el);
  const single = selected.length === 1 ? selected[0] : null;
  const selBox = selected.length ? union(selected.map(bounds)) : null;
  const editing = props.editingId !== null;
  const editingEl = editing ? live(props.editingId!) : undefined;
  const textSelected = selected.filter((el) => el.kind !== "line" && !!el.text);
  const firstFamily = (() => {
    const el = editingEl ?? textSelected[0];
    if (!el || el.kind === "line") return undefined;
    const p = el.text?.paragraphs[0];
    return p?.runs[0]?.family?.[0] ?? p?.style.family?.[0];
  })();
  const firstSize = (() => {
    const el = editingEl ?? textSelected[0];
    if (!el || el.kind === "line") return undefined;
    return el.text?.paragraphs[0]?.runs[0]?.size ?? el.text?.paragraphs[0]?.style.size;
  })();

  const hoverEl = hoverId && !dragging ? byId.get(hoverId) : undefined;
  const hoverTarget = hoverEl ? pickFor(hoverEl, false).pick : undefined;
  const showHover = hoverTarget && !selection.includes(hoverTarget.id) && tool === "select" && !editing;

  const barAnchor = editingEl ? bounds(editingEl) : selBox;
  const barAbove = barAnchor ? barAnchor.y * s > 56 : true;

  const menuItems: (MenuItem | null)[] = selection.length
    ? [
        { label: "剪下", hint: "⌘X", onSelect: props.actions.cut },
        { label: "複製", hint: "⌘C", onSelect: props.actions.copy },
        { label: "貼上", hint: "⌘V", onSelect: props.actions.paste, disabled: !props.actions.canPaste },
        { label: "複製一份", hint: "⌘D", onSelect: props.actions.duplicate },
        null,
        { label: "移到最上層", hint: "⇧⌘]", onSelect: props.actions.front, disabled: selection.length !== 1 },
        { label: "移到最下層", hint: "⇧⌘[", onSelect: props.actions.back, disabled: selection.length !== 1 },
        null,
        ...(["left", "hcenter", "right", "top", "vcenter", "bottom"] as AlignKind[]).map((k) => ({
          label: selection.length === 1 ? `${ALIGN_LABEL[k]}(頁面)` : ALIGN_LABEL[k],
          onSelect: () => props.actions.align(k),
        })),
        null,
        {
          label: "新增留言",
          hint: "C",
          onSelect: () => startComment(byId.get(selection[0]) ?? null),
          disabled: selection.length !== 1,
        },
        null,
        { label: "刪除", hint: "⌫", onSelect: props.actions.remove, danger: true },
      ]
    : [
        { label: "貼上", hint: "⌘V", onSelect: props.actions.paste, disabled: !props.actions.canPaste },
        { label: "全選", hint: "⌘A", onSelect: () => setSelection(page.elements.map((el) => el.id)) },
        { label: "在這裡留言", hint: "C", onSelect: () => menu && startComment(null, point({ clientX: menu.x, clientY: menu.y })) },
      ];

  return (
    <div className="ne-stage-wrap" ref={wrapRef} data-tool={tool}>
      <div
        className="ne-stage"
        ref={stageRef}
        style={{ width: width * s, height: height * s }}
        onPointerDown={onStagePointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onContextMenu={onContextMenu}
      >
        <SlideView
          page={page}
          width={width}
          height={height}
          s={s}
          overrides={merged}
          editingId={props.editingId}
          selectAll={props.selectAll}
          onCommit={onCommit}
          handlers={handlers}
        />

        <svg className="ne-overlay" width={width * s} height={height * s} aria-hidden="true">
          {showHover && hoverTarget && (() => {
            const b = bounds(hoverTarget);
            return <rect x={b.x * s} y={b.y * s} width={b.w * s} height={b.h * s} className="ne-hover" />;
          })()}
          {guides.map((g, i) =>
            g.axis === "v" ? (
              <line key={i} x1={g.pos * s} x2={g.pos * s} y1={g.from * s} y2={g.to * s} className="ne-guide" />
            ) : (
              <line key={i} y1={g.pos * s} y2={g.pos * s} x1={g.from * s} x2={g.to * s} className="ne-guide" />
            ),
          )}
          {gapList.map((g, i) => {
            const mid = ((g.from + g.to) / 2) * s;
            const label = fmt(g.to - g.from);
            return g.axis === "x" ? (
              <g key={i}>
                <line x1={g.from * s} x2={g.to * s} y1={g.at * s} y2={g.at * s} className="ne-gap" />
                <rect x={mid - 18} y={g.at * s - 9} width={36} height={18} rx={3} className="ne-gap-tag" />
                <text x={mid} y={g.at * s + 4} className="ne-gap-text">{label}</text>
              </g>
            ) : (
              <g key={i}>
                <line y1={g.from * s} y2={g.to * s} x1={g.at * s} x2={g.at * s} className="ne-gap" />
                <rect x={g.at * s - 18} y={mid - 9} width={36} height={18} rx={3} className="ne-gap-tag" />
                <text x={g.at * s} y={mid + 4} className="ne-gap-text">{label}</text>
              </g>
            );
          })}
        </svg>

        {!editing &&
          selected.map((el) => {
            if (el.kind === "line") {
              return (
                <svg key={el.id} className="ne-overlay" width={width * s} height={height * s} aria-hidden="true">
                  <line x1={el.x1 * s} y1={el.y1 * s} x2={el.x2 * s} y2={el.y2 * s} className="ne-sel-line" />
                </svg>
              );
            }
            const b = bounds(el);
            return <div key={el.id} className="ne-sel" style={{ left: b.x * s, top: b.y * s, width: b.w * s, height: b.h * s }} />;
          })}
        {editing && editingEl && (() => {
          const b = bounds(editingEl);
          return <div className="ne-sel editing" style={{ left: b.x * s, top: b.y * s, width: b.w * s, height: b.h * s }} />;
        })()}

        {!editing && !dragging && single && single.kind !== "line" &&
          HANDLES.map((h) => {
            const b = bounds(single);
            const x = h.includes("w") ? b.x : h.includes("e") ? b.x + b.w : b.x + b.w / 2;
            const y = h.includes("n") ? b.y : h.includes("s") ? b.y + b.h : b.y + b.h / 2;
            return (
              <span key={h} className={`ne-handle ne-handle-${h}`} style={{ left: x * s, top: y * s }} onPointerDown={(e) => onHandlePointerDown(e, h, single)} />
            );
          })}

        {!editing && !dragging && single && single.kind === "line" &&
          ([1, 2] as const).map((end) => (
            <span
              key={end}
              className="ne-handle ne-handle-point"
              style={{ left: (end === 1 ? single.x1 : single.x2) * s, top: (end === 1 ? single.y1 : single.y2) * s }}
              onPointerDown={(e) => onLineEndPointerDown(e, end, single)}
            />
          ))}

        {marquee && <div className="ne-marquee" style={{ left: marquee.x * s, top: marquee.y * s, width: marquee.w * s, height: marquee.h * s }} />}

        {readout && (
          <div className="ne-readout" style={{ left: readout.x * s, top: readout.y * s + 30 }}>
            {readout.text}
          </div>
        )}

        {!dragging && barAnchor && (selected.length > 0 || editing) && (
          <SelectionBar
            left={(barAnchor.x + barAnchor.w / 2) * s}
            top={barAbove ? barAnchor.y * s - 50 : (barAnchor.y + barAnchor.h) * s + 14}
            count={editing ? 1 : selected.length}
            hasText={editing || textSelected.length > 0}
            editing={editing}
            size={firstSize}
            family={firstFamily}
            fonts={props.fonts}
            colors={props.colors}
            onFormat={(f) => (editing ? execFormat(f, s) : props.actions.format(f))}
            actions={props.actions}
            onEdit={() => single && props.setEditing(single.id)}
          />
        )}
        <CommentPins
          comments={page.comments ?? []}
          s={s}
          live={live}
          openId={props.openComment}
          onOpen={props.setOpenComment}
          onResolve={(id) => {
            props.setOpenComment(null);
            void props.apply([{ op: "comment_delete", comment: id }]);
          }}
        />
        {composing && (
          <CommentComposer
            x={composing.x}
            y={composing.y}
            s={s}
            about={composing.target ? "對這個物件的意見" : "對這一頁的意見"}
            onSubmit={(text) => void submitComment(text)}
            onCancel={() => {
              setComposing(null);
              props.setTool("select");
            }}
          />
        )}
      </div>
      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems} onClose={() => setMenu(null)} />}
    </div>
  );
}
