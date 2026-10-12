// The scene the backend reads out of an .odp (odforge/scene.py). All lengths are
// centimetres in page coordinates; font sizes are points.

export interface TextStyle {
  size?: number;
  color?: string;
  weight?: number;
  italic?: boolean;
  family?: string[];
  letterSpacing?: number;
  underline?: boolean;
}

export interface Run extends TextStyle {
  text: string;
}

export interface Bullet {
  char: string;
  indent: number;
  width: number;
}

export interface Paragraph {
  align?: "left" | "right" | "center" | "justify";
  lineHeight?: number;
  lineHeightCm?: number;
  marginTop?: number;
  marginBottom?: number;
  marginLeft?: number;
  indent?: number;
  runs: Run[];
  style: TextStyle;
  bullet?: Bullet;
}

export interface TextBody {
  paragraphs: Paragraph[];
  padding: { top: number; right: number; bottom: number; left: number };
  valign: "top" | "middle" | "bottom" | string;
  clip?: boolean;
}

interface Base {
  id: string;
  name?: string;
}

interface Paint {
  fill?: string;
  fillOpacity?: number;
  stroke?: string;
  strokeWidth?: number;
  dashed?: boolean;
}

export interface BoxElement extends Base, Paint {
  kind: "rect" | "ellipse" | "text" | "image" | "unsupported";
  x: number;
  y: number;
  w: number;
  h: number;
  radius?: number;
  text?: TextBody;
  href?: string;
  reason?: string;
  placeholder?: string;
}

export interface LineElement extends Base, Paint {
  kind: "line";
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export type SceneElement = BoxElement | LineElement;

export interface Comment {
  id: string;
  text: string;
  author: string;
  date: string;
  x: number;
  y: number;
  /** The element the comment is about, when it still exists. */
  target: string | null;
}

export interface Page {
  id: string;
  name: string;
  background: string;
  master: SceneElement[];
  elements: SceneElement[];
  comments?: Comment[];
}

export interface FontFace {
  family: string;
  href: string;
  weight: number;
  italic: boolean;
  /** Changes when the editor re-embeds the font with more characters. */
  rev?: string;
}

export interface FontChoice {
  family: string;
  embeddable: boolean;
}

export interface Scene {
  width: number;
  height: number;
  pages: Page[];
  fonts: FontFace[];
}

export interface DocState {
  name: string;
  version: number;
  scene: Scene;
  canUndo: boolean;
  canRedo: boolean;
  created: string[];
  fontChoices: FontChoice[];
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type Op = Record<string, unknown> & { op: string };

/** A run as the editor sends it back: text plus the look the person gave it. */
export interface RichRun {
  text: string;
  weight?: number;
  italic?: boolean;
  underline?: boolean;
  color?: string;
  size?: number;
  family?: string;
}

export interface RichParagraph {
  runs: RichRun[];
  align?: "left" | "center" | "right" | "justify";
}
