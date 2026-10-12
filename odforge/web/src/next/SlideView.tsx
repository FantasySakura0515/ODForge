import { memo, useEffect, useLayoutEffect, useRef, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { fileUrl } from "./api";
import { execFormat, readRich } from "./richtext";
import type { BoxElement, LineElement, Page, Paragraph, RichParagraph, Run, SceneElement, TextBody, TextStyle } from "./types";

const PT_TO_CM = 2.54 / 72;

/**
 * LibreOffice's "single" (100%) line spacing is the font's own ascent + descent.
 * For the Noto CJK families ODForge embeds that is 1.448 em; browsers' "normal"
 * would match, but proportional spacing (115%, 130%…) needs the number.
 */
function lineFactor(family?: string[]): number {
  return family?.some((f) => /noto/i.test(f)) ? 1.448 : 1.17;
}

function fontFamily(family?: string[]): string | undefined {
  if (!family?.length) return undefined;
  return [...family.map((f) => `"${f}"`), "\"Noto Sans TC\"", "\"PingFang TC\"", "sans-serif"].join(", ");
}

function textCss(style: TextStyle, s: number): CSSProperties {
  const css: CSSProperties = {};
  if (style.size) css.fontSize = style.size * PT_TO_CM * s;
  if (style.color) css.color = style.color;
  if (style.weight) css.fontWeight = style.weight;
  if (style.italic) css.fontStyle = "italic";
  const family = fontFamily(style.family);
  if (family) css.fontFamily = family;
  if (style.letterSpacing) css.letterSpacing = style.letterSpacing * s;
  if (style.underline) css.textDecoration = "underline";
  return css;
}

function paragraphCss(p: Paragraph, s: number): CSSProperties {
  const base = textCss(p.style, s);
  const size = (p.style.size ?? p.runs[0]?.size ?? 18) * PT_TO_CM * s;
  const css: CSSProperties = {
    ...base,
    margin: 0,
    marginTop: (p.marginTop ?? 0) * s,
    marginBottom: (p.marginBottom ?? 0) * s,
    textAlign: p.align ?? "left",
    lineHeight: p.lineHeightCm
      ? `${p.lineHeightCm * s}px`
      : `${size * (p.lineHeight ?? 1) * lineFactor(p.style.family ?? p.runs[0]?.family)}px`,
    position: "relative",
  };
  const left = (p.marginLeft ?? 0) + (p.bullet ? p.bullet.indent + p.bullet.width : 0);
  if (left) css.paddingLeft = left * s;
  if (p.indent) css.textIndent = p.indent * s;
  if (p.bullet) {
    (css as Record<string, unknown>)["--bullet-left"] = `${((p.marginLeft ?? 0) + p.bullet.indent) * s}px`;
  }
  return css;
}

function RunSpan({ run, s }: { run: Run; s: number }) {
  return <span style={textCss(run, s)}>{run.text}</span>;
}

function TextBlock({
  body,
  s,
  editing,
  onCommit,
  selectAll,
}: {
  body: TextBody;
  s: number;
  editing?: boolean;
  onCommit?: (paragraphs: RichParagraph[]) => void;
  selectAll?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const committed = useRef(false);

  useLayoutEffect(() => {
    if (!editing || !ref.current) return;
    committed.current = false;
    const el = ref.current;
    el.focus();
    document.execCommand("styleWithCSS", false, "true");
    const range = document.createRange();
    range.selectNodeContents(el);
    if (!selectAll) range.collapse(false);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
  }, [editing, selectAll]);

  function commit() {
    if (committed.current || !ref.current || !onCommit) return;
    committed.current = true;
    onCommit(readRich(ref.current, s));
  }

  const justify = body.valign === "middle" ? "center" : body.valign === "bottom" ? "flex-end" : "flex-start";
  const pad = body.padding;
  return (
    <div
      className="ne-textbox"
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        justifyContent: justify,
        padding: `${pad.top * s}px ${pad.right * s}px ${pad.bottom * s}px ${pad.left * s}px`,
        overflow: body.clip ? "hidden" : "visible",
      }}
    >
      <div
        // A fresh node for each editing session: typing (and Enter) rewrites this
        // DOM behind React's back, so it must never be reconciled afterwards.
        key={editing ? "editing" : "view"}
        ref={ref}
        className={editing ? "ne-text editing" : "ne-text"}
        contentEditable={editing || undefined}
        suppressContentEditableWarning
        spellCheck={false}
        onBlur={editing ? commit : undefined}
        onKeyDown={
          editing
            ? (e) => {
                e.stopPropagation();
                const mod = e.metaKey || e.ctrlKey;
                const key = e.key.toLowerCase();
                if (mod && (key === "b" || key === "i" || key === "u")) {
                  e.preventDefault();
                  execFormat({ kind: key === "b" ? "bold" : key === "i" ? "italic" : "underline" }, s);
                  return;
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  (e.currentTarget as HTMLElement).blur();
                } else if (e.key === "Enter" && e.shiftKey) {
                  e.preventDefault();
                  document.execCommand("insertLineBreak");
                }
              }
            : undefined
        }
        onPaste={
          editing
            ? (e) => {
                e.preventDefault();
                document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
              }
            : undefined
        }
        onPointerDown={editing ? (e) => e.stopPropagation() : undefined}
      >
        {body.paragraphs.map((p, i) => (
          <div key={i} className="ne-p" data-bullet={p.bullet?.char} style={paragraphCss(p, s)}>
            {p.runs.length === 0 ? <br /> : p.runs.map((r, j) => <RunSpan key={j} run={r} s={s} />)}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The paragraphs a contenteditable now holds: one per block, ``<br>`` as "\n". */
export function readParagraphs(root: HTMLElement): string[] {
  const out: string[] = [];
  let current: string | null = null;

  function textOf(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (node.nodeName === "BR") return "\n";
    let text = "";
    node.childNodes.forEach((child) => {
      text += textOf(child);
    });
    return text;
  }

  root.childNodes.forEach((node) => {
    if (node.nodeType === Node.ELEMENT_NODE && /^(DIV|P)$/.test(node.nodeName)) {
      if (current !== null) out.push(current);
      current = null;
      out.push(textOf(node).replace(/\n$/, ""));
    } else {
      current = (current ?? "") + textOf(node);
    }
  });
  if (current !== null) out.push((current as string).replace(/\n$/, ""));
  return out.length ? out : [""];
}

function boxCss(el: BoxElement, s: number): CSSProperties {
  const css: CSSProperties = {
    position: "absolute",
    left: el.x * s,
    top: el.y * s,
    width: el.w * s,
    height: el.h * s,
    boxSizing: "border-box",
  };
  if (el.fill) {
    css.background = el.fill;
    if (el.fillOpacity !== undefined) css.opacity = el.fillOpacity;
  }
  if (el.stroke) {
    const width = Math.max((el.strokeWidth ?? 0) * s, 1);
    css.border = `${width}px ${el.dashed ? "dashed" : "solid"} ${el.stroke}`;
  }
  if (el.kind === "ellipse") css.borderRadius = "50%";
  else if (el.radius) css.borderRadius = el.radius * s;
  return css;
}

function LineView({ el, s }: { el: LineElement; s: number }) {
  const width = Math.max((el.strokeWidth ?? 0) * s, 1);
  const pad = width + 2;
  const x = Math.min(el.x1, el.x2) * s - pad;
  const y = Math.min(el.y1, el.y2) * s - pad;
  const w = Math.abs(el.x2 - el.x1) * s + pad * 2;
  const h = Math.abs(el.y2 - el.y1) * s + pad * 2;
  return (
    <svg
      style={{ position: "absolute", left: x, top: y, width: w, height: h, overflow: "visible", pointerEvents: "none" }}
      aria-hidden="true"
    >
      <line
        x1={el.x1 * s - x}
        y1={el.y1 * s - y}
        x2={el.x2 * s - x}
        y2={el.y2 * s - y}
        stroke={el.stroke ?? "#000"}
        strokeWidth={width}
        strokeDasharray={el.dashed ? `${width * 4} ${width * 3}` : undefined}
      />
    </svg>
  );
}

export interface ElementHandlers {
  onPointerDown?: (e: ReactPointerEvent, el: SceneElement) => void;
  onPointerEnter?: (el: SceneElement) => void;
  onPointerLeave?: (el: SceneElement) => void;
}

export const ElementView = memo(ElementViewInner, (a, b) =>
  // Handlers and commit callbacks are stable wrappers (see Canvas); only what
  // changes the picture decides whether this element redraws.
  a.el === b.el && a.s === b.s && a.editing === b.editing && a.selectAll === b.selectAll && !!a.handlers === !!b.handlers,
);

function ElementViewInner({
  el,
  s,
  editing,
  selectAll,
  onCommit,
  handlers,
}: {
  el: SceneElement;
  s: number;
  editing?: boolean;
  selectAll?: boolean;
  onCommit?: (paragraphs: RichParagraph[]) => void;
  handlers?: ElementHandlers;
}) {
  if (el.kind === "line") {
    return (
      <>
        <LineView el={el} s={s} />
        {handlers && (
          <LineHit
            el={el}
            s={s}
            onPointerDown={(e) => handlers.onPointerDown?.(e, el)}
            onPointerEnter={() => handlers.onPointerEnter?.(el)}
            onPointerLeave={() => handlers.onPointerLeave?.(el)}
          />
        )}
      </>
    );
  }
  const interactive = handlers
    ? {
        onPointerDown: (e: ReactPointerEvent) => handlers.onPointerDown?.(e, el),
        onPointerEnter: () => handlers.onPointerEnter?.(el),
        onPointerLeave: () => handlers.onPointerLeave?.(el),
      }
    : {};
  const css = boxCss(el, s);
  return (
    <div className={`ne-el ne-${el.kind}`} data-id={el.id} style={css} {...interactive}>
      {el.kind === "image" && el.href && (
        <img src={fileUrl(el.href)} alt="" draggable={false} style={{ width: "100%", height: "100%", display: "block" }} />
      )}
      {el.kind === "unsupported" && <span className="ne-unsupported">{el.reason ?? "無法編輯的物件"}</span>}
      {el.text && <TextBlock body={el.text} s={s} editing={editing} onCommit={onCommit} selectAll={selectAll} />}
    </div>
  );
}

/** A fat invisible stroke so a hairline can be grabbed. */
function LineHit({
  el,
  s,
  onPointerDown,
  onPointerEnter,
  onPointerLeave,
}: {
  el: LineElement;
  s: number;
  onPointerDown: (e: ReactPointerEvent) => void;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
}) {
  const pad = 6;
  const x = Math.min(el.x1, el.x2) * s - pad;
  const y = Math.min(el.y1, el.y2) * s - pad;
  return (
    <svg
      className="ne-el"
      data-id={el.id}
      style={{ position: "absolute", left: x, top: y, width: Math.abs(el.x2 - el.x1) * s + pad * 2, height: Math.abs(el.y2 - el.y1) * s + pad * 2, overflow: "visible" }}
    >
      <line
        x1={el.x1 * s - x}
        y1={el.y1 * s - y}
        x2={el.x2 * s - x}
        y2={el.y2 * s - y}
        stroke="transparent"
        strokeWidth={12}
        style={{ pointerEvents: "stroke", cursor: "move" }}
        onPointerDown={onPointerDown}
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
      />
    </svg>
  );
}

/** A whole slide at ``s`` px per cm; static unless handlers are given. */
export function SlideView({
  page,
  width,
  height,
  s,
  overrides,
  editingId,
  selectAll,
  onCommit,
  handlers,
}: {
  page: Page;
  width: number;
  height: number;
  s: number;
  overrides?: Map<string, SceneElement>;
  editingId?: string | null;
  selectAll?: boolean;
  onCommit?: (id: string, paragraphs: RichParagraph[]) => void;
  handlers?: ElementHandlers;
}) {
  return (
    <div className="ne-slide" style={{ position: "relative", width: width * s, height: height * s, background: page.background, overflow: "hidden" }}>
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
        {page.master.map((el) => (
          <ElementView key={el.id} el={el} s={s} />
        ))}
      </div>
      {page.elements.map((raw) => {
        const el = overrides?.get(raw.id) ?? raw;
        return (
          <ElementView
            key={raw.id}
            el={el}
            s={s}
            editing={editingId === raw.id}
            selectAll={selectAll}
            onCommit={onCommit ? (paragraphs) => onCommit(raw.id, paragraphs) : undefined}
            handlers={handlers}
          />
        );
      })}
    </div>
  );
}

/**
 * Register the document's embedded fonts so slides draw in its own typefaces.
 *
 * Through the FontFace API, never a generated stylesheet: family names come from
 * the document, which is untrusted, and pasted into CSS text they could close the
 * rule and add their own.
 */
export function useDocumentFonts(
  fonts: { family: string; href: string; weight: number; italic: boolean; rev?: string }[] | undefined,
) {
  const key = JSON.stringify(fonts ?? []);
  useEffect(() => {
    if (!fonts?.length || typeof FontFace === "undefined") return;
    const faces: FontFace[] = [];
    for (const f of fonts) {
      const url = `${fileUrl(f.href)}?v=${encodeURIComponent(f.rev ?? "")}`;
      try {
        const face = new FontFace(f.family, `url(${JSON.stringify(url)})`, {
          weight: String(f.weight),
          style: f.italic ? "italic" : "normal",
          display: "swap",
        });
        document.fonts.add(face);
        void face.load().catch(() => undefined);
        faces.push(face);
      } catch {
        // An unusable family name: the slide falls back to the default fonts.
      }
    }
    return () => {
      faces.forEach((face) => document.fonts.delete(face));
    };
    // Re-register only when a font file actually changed, not on every edit.
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
}
