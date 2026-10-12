import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AlignKind } from "./geometry";
import type { Format } from "./richtext";

export interface Actions {
  copy: () => void;
  cut: () => void;
  paste: () => void;
  duplicate: () => void;
  remove: () => void;
  front: () => void;
  back: () => void;
  align: (kind: AlignKind) => void;
  distribute: (axis: "x" | "y") => void;
  format: (format: Format) => void;
  canPaste: boolean;
}

const BASIC_COLORS = ["#16181D", "#5B6470", "#FFFFFF", "#1F3F99", "#2F6BFF", "#C62828", "#E0457B", "#ED6C02", "#2E7D32", "#6A1B9A"];

/** Keeps focus (and the text selection) in the editor when a bar button is pressed. */
const keepFocus = (e: React.MouseEvent | React.PointerEvent) => e.preventDefault();

function Icon({ d, label }: { d: string; label: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" role="img" aria-label={label} fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d={d} />
    </svg>
  );
}

const ICONS: Record<string, string> = {
  left: "M2 2v12M5 5h8M5 11h5",
  hcenter: "M8 2v12M3 5h10M5 11h6",
  right: "M14 2v12M3 5h8M6 11h5",
  top: "M2 2h12M5 5v8M11 5v5",
  vcenter: "M2 8h12M5 3v10M11 5v6",
  bottom: "M2 14h12M5 3v8M11 6v5",
  dx: "M2 3v10M14 3v10M6 6h4v4H6z",
  dy: "M3 2h10M3 14h10M6 6h4v4H6z",
  alignL: "M2 4h12M2 8h8M2 12h10",
  alignC: "M2 4h12M4 8h8M3 12h10",
  alignR: "M2 4h12M6 8h8M4 12h10",
};

function Button({
  label,
  onClick,
  children,
  pressed,
  className,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  pressed?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={pressed}
      className={className}
      onPointerDown={keepFocus}
      onMouseDown={keepFocus}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function ColorButton({ colors, onPick }: { colors: string[]; onPick: (c: string) => void }) {
  const [open, setOpen] = useState(false);
  const palette = [...new Set([...colors.map((c) => c.toUpperCase()), ...BASIC_COLORS])].slice(0, 18);
  return (
    <span className="ne-colorpick">
      <Button label="文字顏色" onClick={() => setOpen((v) => !v)}>
        <span className="ne-color-a">A</span>
      </Button>
      {open && (
        <div className="ne-palette" onPointerDown={keepFocus} onMouseDown={keepFocus}>
          {palette.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`顏色 ${c}`}
              title={c}
              style={{ background: c }}
              onClick={() => {
                onPick(c);
                setOpen(false);
              }}
            />
          ))}
          <label className="ne-palette-custom" title="自訂顏色">
            <input
              type="color"
              aria-label="自訂顏色"
              onChange={(e) => {
                onPick(e.target.value);
                setOpen(false);
              }}
            />
            ＋
          </label>
        </div>
      )}
    </span>
  );
}

const FONT_NAMES: Record<string, string> = {
  "Noto Sans TC": "思源黑體",
  "Noto Serif TC": "思源宋體",
  "Noto Sans CJK TC": "思源黑體",
  "Noto Serif CJK TC": "思源宋體",
};

export function fontLabel(family?: string): string {
  if (!family) return "字型";
  return FONT_NAMES[family] ?? family;
}

function FontButton({
  current,
  choices,
  onPick,
}: {
  current?: string;
  choices: { family: string; embeddable: boolean }[];
  onPick: (family: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="ne-colorpick">
      <Button label="字型" className="ne-font-button" onClick={() => setOpen((v) => !v)}>
        <span style={{ fontFamily: current ? `"${current}"` : undefined }}>{fontLabel(current)}</span>
        <span aria-hidden="true" className="ne-caret">▾</span>
      </Button>
      {open && (
        <div className="ne-fontlist" role="listbox" onPointerDown={keepFocus} onMouseDown={keepFocus}>
          {choices.map((c) => (
            <button
              key={c.family}
              type="button"
              role="option"
              aria-selected={c.family === current}
              onClick={() => {
                onPick(c.family);
                setOpen(false);
              }}
            >
              <span style={{ fontFamily: `"${c.family}"` }}>{fontLabel(c.family)}</span>
              <small>{c.embeddable ? "會嵌入檔案" : "對方要有這套字型"}</small>
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

/** The floating bar over a selection. */
export function SelectionBar({
  left,
  top,
  count,
  hasText,
  editing,
  size,
  family,
  fonts,
  colors,
  onFormat,
  actions,
  onEdit,
}: {
  left: number;
  top: number;
  count: number;
  hasText: boolean;
  editing: boolean;
  size?: number;
  family?: string;
  fonts: { family: string; embeddable: boolean }[];
  colors: string[];
  onFormat: (format: Format) => void;
  actions: Actions;
  onEdit: () => void;
}) {
  return (
    <div className="ne-bar" style={{ left, top }} onPointerDown={(e) => e.stopPropagation()}>
      {hasText && (
        <>
          <FontButton current={family} choices={fonts} onPick={(f) => onFormat({ kind: "family", value: f })} />
          <span className="ne-bar-sep" />
          <Button label="粗體 (⌘B)" onClick={() => onFormat({ kind: "bold" })}><b>B</b></Button>
          <Button label="斜體 (⌘I)" onClick={() => onFormat({ kind: "italic" })}><i>I</i></Button>
          <Button label="底線 (⌘U)" onClick={() => onFormat({ kind: "underline" })}><u>U</u></Button>
          <span className="ne-bar-sep" />
          <Button label="縮小字級" onClick={() => onFormat({ kind: "size", delta: -1 })}>−</Button>
          <span className="ne-size" aria-label="字級">{size ? `${Math.round(size * 2) / 2}` : "—"}</span>
          <Button label="放大字級" onClick={() => onFormat({ kind: "size", delta: 1 })}>＋</Button>
          <ColorButton colors={colors} onPick={(c) => onFormat({ kind: "color", value: c })} />
          <span className="ne-bar-sep" />
          <Button label="靠左" onClick={() => onFormat({ kind: "align", value: "left" })}><Icon d={ICONS.alignL} label="靠左" /></Button>
          <Button label="置中" onClick={() => onFormat({ kind: "align", value: "center" })}><Icon d={ICONS.alignC} label="置中" /></Button>
          <Button label="靠右" onClick={() => onFormat({ kind: "align", value: "right" })}><Icon d={ICONS.alignR} label="靠右" /></Button>
        </>
      )}
      {!editing && (
        <>
          {hasText && <span className="ne-bar-sep" />}
          {count > 1 &&
            (["left", "hcenter", "right", "top", "vcenter", "bottom"] as AlignKind[]).map((k) => (
              <Button key={k} label={ALIGN_LABEL[k]} onClick={() => actions.align(k)}>
                <Icon d={ICONS[k]} label={ALIGN_LABEL[k]} />
              </Button>
            ))}
          {count > 2 && (
            <>
              <Button label="水平等距" onClick={() => actions.distribute("x")}><Icon d={ICONS.dx} label="水平等距" /></Button>
              <Button label="垂直等距" onClick={() => actions.distribute("y")}><Icon d={ICONS.dy} label="垂直等距" /></Button>
            </>
          )}
          {count > 1 && <span className="ne-bar-sep" />}
          {hasText && count === 1 && <Button label="改字 (Enter)" onClick={onEdit}>改字</Button>}
          <Button label="複製一份 (⌘D)" onClick={actions.duplicate}>複製</Button>
          <Button label="刪除 (Delete)" className="danger" onClick={actions.remove}>刪除</Button>
        </>
      )}
    </div>
  );
}

export const ALIGN_LABEL: Record<AlignKind, string> = {
  left: "靠左對齊",
  hcenter: "水平置中",
  right: "靠右對齊",
  top: "靠上對齊",
  vcenter: "垂直置中",
  bottom: "靠下對齊",
};

export interface MenuItem {
  label: string;
  hint?: string;
  onSelect?: () => void;
  disabled?: boolean;
  danger?: boolean;
}

/** A right-click menu; ``null`` entries are separators. */
export function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: (MenuItem | null)[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: Event) => {
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      onClose();
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("pointerdown", close, true);
    window.addEventListener("keydown", esc, true);
    window.addEventListener("blur", onClose);
    return () => {
      window.removeEventListener("pointerdown", close, true);
      window.removeEventListener("keydown", esc, true);
      window.removeEventListener("blur", onClose);
    };
  }, [onClose]);
  const left = Math.min(x, window.innerWidth - 240);
  const top = Math.min(y, window.innerHeight - items.length * 32 - 16);
  return (
    <div className="ne-menu" role="menu" ref={ref} style={{ left, top }}>
      {items.map((item, i) =>
        item === null ? (
          <div key={i} className="ne-menu-sep" role="separator" />
        ) : (
          <button
            key={i}
            type="button"
            role="menuitem"
            disabled={item.disabled}
            className={item.danger ? "danger" : undefined}
            onClick={() => {
              onClose();
              item.onSelect?.();
            }}
          >
            <span>{item.label}</span>
            {item.hint && <kbd>{item.hint}</kbd>}
          </button>
        ),
      )}
    </div>
  );
}
