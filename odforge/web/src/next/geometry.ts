import type { Rect, SceneElement } from "./types";

/** The element's bounding box in cm (a line's box spans its two ends). */
export function bounds(el: SceneElement): Rect {
  if (el.kind === "line") {
    const x = Math.min(el.x1, el.x2);
    const y = Math.min(el.y1, el.y2);
    return { x, y, w: Math.abs(el.x2 - el.x1), h: Math.abs(el.y2 - el.y1) };
  }
  return { x: el.x, y: el.y, w: el.w, h: el.h };
}

export function union(rects: Rect[]): Rect {
  const x = Math.min(...rects.map((r) => r.x));
  const y = Math.min(...rects.map((r) => r.y));
  const right = Math.max(...rects.map((r) => r.x + r.w));
  const bottom = Math.max(...rects.map((r) => r.y + r.h));
  return { x, y, w: right - x, h: bottom - y };
}

export function intersects(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

export function contains(outer: Rect, inner: Rect, slack = 0.02): boolean {
  return (
    inner.x >= outer.x - slack &&
    inner.y >= outer.y - slack &&
    inner.x + inner.w <= outer.x + outer.w + slack &&
    inner.y + inner.h <= outer.y + outer.h + slack
  );
}

/** Move an element by (dx, dy) cm; a group moves with everything in it. */
export function moved(el: SceneElement, dx: number, dy: number): SceneElement {
  if (el.kind === "line") {
    return { ...el, x1: el.x1 + dx, x2: el.x2 + dx, y1: el.y1 + dy, y2: el.y2 + dy };
  }
  if (el.kind === "group" && el.children) {
    return { ...el, x: el.x + dx, y: el.y + dy, children: el.children.map((c) => moved(c, dx, dy)) };
  }
  return { ...el, x: el.x + dx, y: el.y + dy };
}

/** Every element, groups' members included (each group before its members). */
export function flatten(elements: SceneElement[]): SceneElement[] {
  return elements.flatMap((el) =>
    el.kind === "group" && el.children ? [el, ...flatten(el.children)] : [el],
  );
}

/** Whether ``a`` is a group that (transitively) contains ``b``. */
export function isAncestor(a: string, b: string): boolean {
  return b.startsWith(`${a}.`);
}

function scaleInto(el: SceneElement, from: Rect, to: Rect): SceneElement {
  const sx = from.w ? to.w / from.w : 1;
  const sy = from.h ? to.h / from.h : 1;
  const mx = (x: number) => to.x + (x - from.x) * sx;
  const my = (y: number) => to.y + (y - from.y) * sy;
  if (el.kind === "line") return { ...el, x1: mx(el.x1), x2: mx(el.x2), y1: my(el.y1), y2: my(el.y2) };
  const box = { x: mx(el.x), y: my(el.y), w: el.w * sx, h: el.h * sy };
  if (el.kind === "group" && el.children) {
    return { ...el, ...box, children: el.children.map((c) => scaleInto(c, from, to)) };
  }
  return { ...el, ...box };
}

/** The geometry operation that puts the element where ``el`` now is. */
export function geomOp(el: SceneElement): Record<string, unknown> & { op: string } {
  const r = (v: number) => Math.round(v * 1000) / 1000;
  if (el.kind === "line") {
    return { op: "geom", id: el.id, x1: r(el.x1), y1: r(el.y1), x2: r(el.x2), y2: r(el.y2) };
  }
  return { op: "geom", id: el.id, x: r(el.x), y: r(el.y), w: r(el.w), h: r(el.h) };
}

/**
 * Elements that ride along when ``shape`` is dragged: drawn after it and lying
 * entirely inside it. A card is a rectangle with text frames on top; grabbing the
 * card should move the card, not peel its background away from its words.
 */
export function riders(shape: SceneElement, elements: SceneElement[]): string[] {
  if (shape.kind !== "rect" && shape.kind !== "ellipse") return [];
  const box = bounds(shape);
  if (box.w < 0.5 || box.h < 0.5) return [];
  const index = elements.findIndex((e) => e.id === shape.id);
  return elements
    .slice(index + 1)
    .filter((e) => contains(box, bounds(e)))
    .map((e) => e.id);
}

// ---------------------------------------------------------------------------
// Snapping
// ---------------------------------------------------------------------------

export interface Guide {
  /** "v": a vertical line at x = pos; "h": a horizontal line at y = pos. */
  axis: "v" | "h";
  pos: number;
  from: number;
  to: number;
}

export interface Gap {
  axis: "x" | "y";
  /** Where the gap starts and ends along its axis, and where it is drawn across. */
  from: number;
  to: number;
  at: number;
}

export interface SnapResult {
  dx: number;
  dy: number;
  guides: Guide[];
}

function lines(r: Rect, axis: "x" | "y"): number[] {
  return axis === "x" ? [r.x, r.x + r.w / 2, r.x + r.w] : [r.y, r.y + r.h / 2, r.y + r.h];
}

/**
 * Snap a moving box to the page and to other boxes.
 *
 * ``moving`` is where the selection would be with the raw drag applied;
 * ``threshold`` is in cm (a few screen pixels converted by the caller).
 * Returns the extra offset to apply and the guide lines to draw.
 */
export function snap(
  moving: Rect,
  others: Rect[],
  page: { width: number; height: number },
  threshold: number,
): SnapResult {
  const pageRect: Rect = { x: 0, y: 0, w: page.width, h: page.height };
  const targets = [pageRect, ...others];

  function best(axis: "x" | "y") {
    let found: { delta: number; pos: number; target: Rect } | null = null;
    for (const target of targets) {
      for (const t of lines(target, axis)) {
        for (const m of lines(moving, axis)) {
          const delta = t - m;
          if (Math.abs(delta) <= threshold && (!found || Math.abs(delta) < Math.abs(found.delta))) {
            found = { delta, pos: t, target };
          }
        }
      }
    }
    return found;
  }

  const bx = best("x");
  const by = best("y");
  const dx = bx ? bx.delta : 0;
  const dy = by ? by.delta : 0;
  const placed: Rect = { ...moving, x: moving.x + dx, y: moving.y + dy };
  const guides: Guide[] = [];
  if (bx) {
    const from = Math.min(placed.y, bx.target.y);
    const to = Math.max(placed.y + placed.h, bx.target.y + bx.target.h);
    guides.push({ axis: "v", pos: bx.pos, from, to });
  }
  if (by) {
    const from = Math.min(placed.x, by.target.x);
    const to = Math.max(placed.x + placed.w, by.target.x + by.target.w);
    guides.push({ axis: "h", pos: by.pos, from, to });
  }
  return { dx, dy, guides };
}

/** Distances from ``box`` to its nearest neighbour on each side that overlaps it. */
export function gaps(box: Rect, others: Rect[]): Gap[] {
  const out: Gap[] = [];
  const midY = box.y + box.h / 2;
  const midX = box.x + box.w / 2;
  const sameRow = others.filter((o) => o.y < box.y + box.h && box.y < o.y + o.h);
  const sameCol = others.filter((o) => o.x < box.x + box.w && box.x < o.x + o.w);
  const left = sameRow.filter((o) => o.x + o.w <= box.x).sort((a, b) => b.x + b.w - (a.x + a.w))[0];
  const right = sameRow.filter((o) => o.x >= box.x + box.w).sort((a, b) => a.x - b.x)[0];
  const above = sameCol.filter((o) => o.y + o.h <= box.y).sort((a, b) => b.y + b.h - (a.y + a.h))[0];
  const below = sameCol.filter((o) => o.y >= box.y + box.h).sort((a, b) => a.y - b.y)[0];
  if (left) out.push({ axis: "x", from: left.x + left.w, to: box.x, at: midY });
  if (right) out.push({ axis: "x", from: box.x + box.w, to: right.x, at: midY });
  if (above) out.push({ axis: "y", from: above.y + above.h, to: box.y, at: midX });
  if (below) out.push({ axis: "y", from: box.y + box.h, to: below.y, at: midX });
  return out;
}

export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

export const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/** The box after dragging ``handle`` by (dx, dy); ``keepRatio`` for Shift. */
export function resized(box: Rect, handle: Handle, dx: number, dy: number, keepRatio = false): Rect {
  let { x, y, w, h } = box;
  if (handle.includes("w")) {
    x += dx;
    w -= dx;
  }
  if (handle.includes("e")) w += dx;
  if (handle.includes("n")) {
    y += dy;
    h -= dy;
  }
  if (handle.includes("s")) h += dy;
  if (keepRatio && box.w > 0 && box.h > 0 && handle.length === 2) {
    const ratio = box.w / box.h;
    if (Math.abs(w / box.w) > Math.abs(h / box.h)) {
      const nh = w / ratio;
      if (handle.includes("n")) y += h - nh;
      h = nh;
    } else {
      const nw = h * ratio;
      if (handle.includes("w")) x += w - nw;
      w = nw;
    }
  }
  const min = 0.1;
  if (w < min) {
    if (handle.includes("w")) x -= min - w;
    w = min;
  }
  if (h < min) {
    if (handle.includes("n")) y -= min - h;
    h = min;
  }
  return { x, y, w, h };
}

export function withBox(el: SceneElement, box: Rect): SceneElement {
  if (el.kind === "group") return scaleInto(el, bounds(el), box);
  if (el.kind === "line") {
    const old = bounds(el);
    const sx = old.w ? box.w / old.w : 1;
    const sy = old.h ? box.h / old.h : 1;
    return {
      ...el,
      x1: box.x + (el.x1 - old.x) * sx,
      x2: box.x + (el.x2 - old.x) * sx,
      y1: box.y + (el.y1 - old.y) * sy,
      y2: box.y + (el.y2 - old.y) * sy,
    };
  }
  return { ...el, ...box };
}

export const fmt = (cm: number) => (Math.round(cm * 100) / 100).toFixed(2);

// ---------------------------------------------------------------------------
// Aligning and distributing
// ---------------------------------------------------------------------------

export type AlignKind = "left" | "hcenter" | "right" | "top" | "vcenter" | "bottom";

/** The element that most tightly encloses ``el`` as a container (a card behind its text). */
export function containerOf(el: SceneElement, elements: SceneElement[]): SceneElement | null {
  const index = elements.findIndex((e) => e.id === el.id);
  const box = bounds(el);
  let best: SceneElement | null = null;
  let bestArea = Infinity;
  for (const candidate of elements.slice(0, index)) {
    if (candidate.kind !== "rect" && candidate.kind !== "ellipse") continue;
    const c = bounds(candidate);
    if (c.w < 0.5 || c.h < 0.5 || !contains(c, box)) continue;
    const area = c.w * c.h;
    if (area < bestArea && area > box.w * box.h * 1.05) {
      best = candidate;
      bestArea = area;
    }
  }
  return best;
}

function moveWithRiders(
  elements: SceneElement[],
  ids: string[],
  delta: (box: Rect, id: string) => { dx: number; dy: number },
): Record<string, unknown>[] {
  const byId = new Map(elements.map((e) => [e.id, e]));
  const done = new Set<string>();
  const ops: Record<string, unknown>[] = [];
  for (const id of ids) {
    const el = byId.get(id);
    if (!el) continue;
    const { dx, dy } = delta(bounds(el), id);
    if (Math.abs(dx) < 1e-4 && Math.abs(dy) < 1e-4) continue;
    for (const member of [id, ...riders(el, elements)]) {
      if (done.has(member)) continue;
      done.add(member);
      const item = byId.get(member);
      if (item) ops.push(geomOp(moved(item, dx, dy)));
    }
  }
  return ops;
}

/** Align the selection to itself, or a single element to the page. */
export function alignOps(
  elements: SceneElement[],
  ids: string[],
  kind: AlignKind,
  page: { width: number; height: number },
): Record<string, unknown>[] {
  const boxes = ids.map((id) => elements.find((e) => e.id === id)).filter((e): e is SceneElement => !!e).map(bounds);
  if (!boxes.length) return [];
  const ref = boxes.length === 1 ? { x: 0, y: 0, w: page.width, h: page.height } : union(boxes);
  return moveWithRiders(elements, ids, (b) => {
    switch (kind) {
      case "left":
        return { dx: ref.x - b.x, dy: 0 };
      case "hcenter":
        return { dx: ref.x + ref.w / 2 - (b.x + b.w / 2), dy: 0 };
      case "right":
        return { dx: ref.x + ref.w - (b.x + b.w), dy: 0 };
      case "top":
        return { dx: 0, dy: ref.y - b.y };
      case "vcenter":
        return { dx: 0, dy: ref.y + ref.h / 2 - (b.y + b.h / 2) };
      case "bottom":
        return { dx: 0, dy: ref.y + ref.h - (b.y + b.h) };
    }
  });
}

/** Equal gaps between three or more elements, along x or y. */
export function distributeOps(elements: SceneElement[], ids: string[], axis: "x" | "y"): Record<string, unknown>[] {
  const items = ids
    .map((id) => elements.find((e) => e.id === id))
    .filter((e): e is SceneElement => !!e)
    .map((e) => ({ id: e.id, box: bounds(e) }));
  if (items.length < 3) return [];
  const start = (b: Rect) => (axis === "x" ? b.x : b.y);
  const size = (b: Rect) => (axis === "x" ? b.w : b.h);
  items.sort((a, b) => start(a.box) - start(b.box));
  const first = items[0].box;
  const last = items[items.length - 1].box;
  const span = start(last) + size(last) - start(first);
  const total = items.reduce((sum, it) => sum + size(it.box), 0);
  const gap = (span - total) / (items.length - 1);
  const target = new Map<string, number>();
  let cursor = start(first);
  for (const it of items) {
    target.set(it.id, cursor);
    cursor += size(it.box) + gap;
  }
  return moveWithRiders(elements, items.map((i) => i.id), (b, id) =>
    axis === "x" ? { dx: target.get(id)! - b.x, dy: 0 } : { dx: 0, dy: target.get(id)! - b.y },
  );
}

// ---------------------------------------------------------------------------
// Equal spacing (how Canva shows distances: only when they line up)
// ---------------------------------------------------------------------------

export interface SpacingResult {
  dx: number;
  dy: number;
  gaps: Gap[];
}

function spacingAxis(moving: Rect, others: Rect[], axis: "x" | "y", threshold: number): { delta: number; gaps: Gap[] } | null {
  const lo = (r: Rect) => (axis === "x" ? r.x : r.y);
  const size = (r: Rect) => (axis === "x" ? r.w : r.h);
  const hi = (r: Rect) => lo(r) + size(r);
  const across = (r: Rect) => (axis === "x" ? [r.y, r.y + r.h] : [r.x, r.x + r.w]);
  const [m0, m1] = across(moving);
  const lane = others.filter((o) => {
    const [o0, o1] = across(o);
    return o0 < m1 && m0 < o1;
  });
  const mid = (m0 + m1) / 2;
  const before = lane.filter((o) => hi(o) <= lo(moving) + threshold).sort((a, b) => hi(b) - hi(a));
  const after = lane.filter((o) => lo(o) >= hi(moving) - threshold).sort((a, b) => lo(a) - lo(b));
  const prev = before[0];
  const next = after[0];
  const gap = (from: number, to: number): Gap => ({ axis, from, to, at: mid });
  const candidates: { pos: number; gaps: (pos: number) => Gap[] }[] = [];
  if (prev && next) {
    // Centred between its two neighbours.
    candidates.push({
      pos: (hi(prev) + lo(next) - size(moving)) / 2,
      gaps: (p) => [gap(hi(prev), p), gap(p + size(moving), lo(next))],
    });
  }
  if (prev && before[1] && hi(before[1]) <= lo(prev)) {
    // Repeats the spacing of the two before it.
    const g = lo(prev) - hi(before[1]);
    candidates.push({ pos: hi(prev) + g, gaps: (p) => [gap(hi(before[1]), lo(prev)), gap(hi(prev), p)] });
  }
  if (next && after[1] && lo(after[1]) >= hi(next)) {
    const g = lo(after[1]) - hi(next);
    candidates.push({
      pos: lo(next) - g - size(moving),
      gaps: (p) => [gap(p + size(moving), lo(next)), gap(hi(next), lo(after[1]))],
    });
  }
  let best: { delta: number; gaps: Gap[] } | null = null;
  for (const c of candidates) {
    const delta = c.pos - lo(moving);
    if (Math.abs(delta) <= threshold && (!best || Math.abs(delta) < Math.abs(best.delta))) {
      best = { delta, gaps: c.gaps(c.pos) };
    }
  }
  return best;
}

/** Snap to equal spacing between neighbours; gaps are only reported when it happens. */
export function spacingSnap(moving: Rect, others: Rect[], threshold: number, skip: { x: boolean; y: boolean }): SpacingResult {
  const x = skip.x ? null : spacingAxis(moving, others, "x", threshold);
  const y = skip.y ? null : spacingAxis(moving, others, "y", threshold);
  return { dx: x?.delta ?? 0, dy: y?.delta ?? 0, gaps: [...(x?.gaps ?? []), ...(y?.gaps ?? [])] };
}
