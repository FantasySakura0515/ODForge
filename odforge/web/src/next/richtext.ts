import type { Paragraph, RichParagraph, RichRun } from "./types";

export const PT_TO_CM = 2.54 / 72;

type Align = NonNullable<RichParagraph["align"]>;

function hex(color: string): string | undefined {
  const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return color.startsWith("#") ? color.toUpperCase() : undefined;
  return (
    "#" +
    [m[1], m[2], m[3]].map((v) => Number(v).toString(16).padStart(2, "0")).join("").toUpperCase()
  );
}

function underlined(node: Element, stop: Element): boolean {
  for (let el: Element | null = node; el && el !== stop.parentElement; el = el.parentElement) {
    if (el.tagName === "U") return true;
    const deco = (el as HTMLElement).style?.textDecorationLine || (el as HTMLElement).style?.textDecoration || "";
    if (deco.includes("underline")) return true;
  }
  return false;
}

function sameLook(a: RichRun, b: RichRun): boolean {
  return (
    a.weight === b.weight &&
    a.italic === b.italic &&
    a.underline === b.underline &&
    a.color === b.color &&
    a.size === b.size &&
    a.family === b.family
  );
}

/** The first family in a CSS font-family list, unquoted. */
function firstFamily(css: string): string | undefined {
  const first = css.split(",")[0]?.trim().replace(/^["']|["']$/g, "");
  return first || undefined;
}

/**
 * What an editing session left in the contenteditable: paragraphs of styled
 * runs. ``s`` (px per cm) turns on-screen font sizes back into points.
 */
export function readRich(root: HTMLElement, s: number): RichParagraph[] {
  const blocks: HTMLElement[] = [];
  const loose: Node[] = [];
  root.childNodes.forEach((node) => {
    if (node.nodeType === Node.ELEMENT_NODE && /^(DIV|P)$/.test(node.nodeName)) blocks.push(node as HTMLElement);
    else loose.push(node);
  });
  if (!blocks.length) {
    const wrap = document.createElement("div");
    loose.forEach((n) => wrap.appendChild(n.cloneNode(true)));
    blocks.push(wrap);
  }

  return blocks.map((block) => {
    const runs: RichRun[] = [];
    const push = (text: string, from: Element) => {
      const cs = window.getComputedStyle(from);
      const run: RichRun = {
        text,
        weight: Number.parseInt(cs.fontWeight, 10) >= 600 || /^(B|STRONG)$/.test(from.tagName) ? 700 : 400,
        italic: cs.fontStyle === "italic" || /^(I|EM)$/.test(from.tagName),
        underline: underlined(from, block),
        color: hex(cs.color),
        family: firstFamily(cs.fontFamily),
      };
      const px = Number.parseFloat(cs.fontSize);
      if (px > 0 && s > 0) run.size = Math.round((px / (PT_TO_CM * s)) * 2) / 2;
      const last = runs[runs.length - 1];
      if (last && sameLook(last, run)) last.text += text;
      else runs.push(run);
    };
    const walk = (node: Node, parent: Element) => {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.textContent) push(node.textContent, parent);
      } else if (node.nodeName === "BR") {
        push("\n", parent);
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        node.childNodes.forEach((child) => walk(child, node as Element));
      }
    };
    block.childNodes.forEach((child) => walk(child, block));
    // The trailing <br> a browser keeps in an empty or last line is not content.
    const last = runs[runs.length - 1];
    if (last?.text.endsWith("\n")) {
      last.text = last.text.slice(0, -1);
      if (!last.text) runs.pop();
    }
    const raw = block.style.textAlign;
    const align: Align | undefined =
      raw === "center" || raw === "right" || raw === "justify" ? raw : raw === "left" || raw === "start" ? "left" : undefined;
    return { runs, ...(align ? { align } : {}) };
  });
}

/** The scene's paragraphs in the same shape, for comparison and whole-box formatting. */
export function richOf(paragraphs: Paragraph[]): RichParagraph[] {
  return paragraphs.map((p) => ({
    align: p.align ?? "left",
    runs: p.runs.map((r) => ({
      text: r.text,
      weight: (r.weight ?? 400) >= 600 ? 700 : 400,
      italic: !!r.italic,
      underline: !!r.underline,
      color: r.color?.toUpperCase(),
      size: r.size,
      family: r.family?.[0],
    })),
  }));
}

/** A stable form for "did anything change?" — texts, looks and alignment. */
export function normalise(paragraphs: RichParagraph[]): string {
  return JSON.stringify(
    paragraphs.map((p) => ({
      a: p.align ?? "left",
      r: p.runs
        .filter((r) => r.text)
        .reduce<RichRun[]>((acc, r) => {
          const run = {
            text: r.text,
            weight: (r.weight ?? 400) >= 600 ? 700 : 400,
            italic: !!r.italic,
            underline: !!r.underline,
            color: r.color?.toUpperCase(),
            size: r.size !== undefined ? Math.round(r.size * 2) / 2 : undefined,
            family: r.family,
          };
          const last = acc[acc.length - 1];
          if (last && sameLook(last, run)) last.text += run.text;
          else acc.push(run);
          return acc;
        }, []),
    })),
  );
}

export function plain(paragraphs: RichParagraph[]): string[] {
  return paragraphs.map((p) => p.runs.map((r) => r.text).join(""));
}

// ---------------------------------------------------------------------------
// Formatting a whole box (selected, not being edited)
// ---------------------------------------------------------------------------

export type Format =
  | { kind: "bold" }
  | { kind: "italic" }
  | { kind: "underline" }
  | { kind: "size"; delta: number }
  | { kind: "color"; value: string }
  | { kind: "family"; value: string }
  | { kind: "align"; value: Align };

export function formatAll(paragraphs: RichParagraph[], format: Format): RichParagraph[] {
  const runs = paragraphs.flatMap((p) => p.runs);
  const all = (pred: (r: RichRun) => boolean) => runs.length > 0 && runs.every(pred);
  return paragraphs.map((p) => {
    if (format.kind === "align") return { ...p, align: format.value };
    return {
      ...p,
      runs: p.runs.map((r) => {
        switch (format.kind) {
          case "bold":
            return { ...r, weight: all((x) => (x.weight ?? 400) >= 600) ? 400 : 700 };
          case "italic":
            return { ...r, italic: !all((x) => !!x.italic) };
          case "underline":
            return { ...r, underline: !all((x) => !!x.underline) };
          case "size":
            return { ...r, size: Math.max(4, (r.size ?? 18) + format.delta) };
          case "color":
            return { ...r, color: format.value.toUpperCase() };
          case "family":
            return { ...r, family: format.value };
          default:
            return r;
        }
      }),
    };
  });
}

// ---------------------------------------------------------------------------
// Formatting the selection inside a box being edited
// ---------------------------------------------------------------------------

export function activeEditor(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".ne-text.editing");
}

export function execFormat(format: Format, s: number): void {
  const editor = activeEditor();
  if (!editor) return;
  document.execCommand("styleWithCSS", false, "true");
  switch (format.kind) {
    case "bold":
    case "italic":
    case "underline":
      document.execCommand(format.kind);
      return;
    case "color":
      document.execCommand("foreColor", false, format.value);
      return;
    case "family":
      document.execCommand("fontName", false, format.value);
      return;
    case "size": {
      const sel = window.getSelection();
      const anchor = sel?.anchorNode ? (sel.anchorNode.nodeType === Node.ELEMENT_NODE ? (sel.anchorNode as Element) : sel.anchorNode.parentElement) : null;
      const currentPx = anchor ? Number.parseFloat(window.getComputedStyle(anchor).fontSize) : 18 * PT_TO_CM * s;
      const nextPt = Math.max(4, Math.round(currentPx / (PT_TO_CM * s)) + format.delta);
      // execCommand only knows sizes 1–7; mark the selection with 7, then turn
      // every marked span into the exact size.
      document.execCommand("fontSize", false, "7");
      editor.querySelectorAll<HTMLElement>('span[style*="xxx-large"], font[size="7"]').forEach((el) => {
        el.style.fontSize = `${nextPt * PT_TO_CM * s}px`;
        el.removeAttribute("size");
      });
      return;
    }
    case "align": {
      const sel = window.getSelection();
      const blocks = [...editor.children].filter((b): b is HTMLElement => b instanceof HTMLElement);
      const touched = blocks.filter((b) => sel && sel.rangeCount > 0 && sel.getRangeAt(0).intersectsNode(b));
      (touched.length ? touched : blocks).forEach((b) => {
        b.style.textAlign = format.value;
      });
    }
  }
}
