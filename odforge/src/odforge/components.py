"""Ready-made slide parts for the editor's 元素 panel, styled like the deck they go into.

ODForge decks are built from a small vocabulary — cards, numbered steps, big
metrics, quote and takeaway bands, kickers, timeline nodes. A person editing a
deck by hand should be able to add one that looks like the ones already there,
so each part takes its colours and fonts from the document itself
(:func:`infer_theme`), and reuses the deck's own card style when it has one.

Every part is a list of plain element specs; :mod:`odforge.odpedit` turns them
into ODF inside a ``draw:g`` so the part moves as one.
"""

from __future__ import annotations

import colorsys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from lxml import etree

from .scene import NS, Styles, cm, drawables, q


@dataclass
class Theme:
    ink: str
    muted: str
    accent: str
    display_font: Optional[str]
    body_font: Optional[str]
    card_style: Optional[str]
    card_radius: float


def _rgb(color: str):
    color = color.lstrip("#")
    if len(color) != 6:
        return None
    try:
        return tuple(int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def _hls(color: str):
    rgb = _rgb(color)
    return colorsys.rgb_to_hls(*rgb) if rgb else None


def infer_theme(content: etree._Element, styles_root: etree._Element) -> Theme:
    styles = Styles((content, styles_root))
    text_colors: Counter[str] = Counter()
    fills: Counter[str] = Counter()
    sizes: Dict[str, float] = {}
    fonts: Counter[str] = Counter()
    for root in (content, styles_root):
        for props in root.iter(q("style", "text-properties")):
            color = props.get(q("fo", "color"))
            if color:
                text_colors[color.upper()] += 1
            font = props.get(q("style", "font-name"))
            if font:
                fonts[font] += 1
                size = props.get(q("fo", "font-size"), "")
                if size.endswith("pt"):
                    sizes[font] = max(sizes.get(font, 0), float(size[:-2]))
        for props in root.iter(q("style", "graphic-properties")):
            if props.get(q("draw", "fill")) == "solid" and props.get(q("draw", "fill-color")):
                fills[props.get(q("draw", "fill-color")).upper()] += 1

    def lightness(c: str) -> float:
        hls = _hls(c)
        return hls[1] if hls else 0.5

    def saturation(c: str) -> float:
        hls = _hls(c)
        return hls[2] if hls else 0.0

    darks = [c for c, _ in text_colors.most_common() if lightness(c) < 0.3]
    mids = [
        c
        for c, _ in text_colors.most_common()
        if 0.3 <= lightness(c) < 0.65 and saturation(c) < 0.3
    ]
    chroma = [
        c
        for c, _ in (text_colors + fills).most_common()
        if saturation(c) >= 0.3 and 0.2 < lightness(c) < 0.75
    ]

    display = max(sizes, key=sizes.get) if sizes else None
    body = fonts.most_common(1)[0][0] if fonts else None

    card_style, radius = None, 0.12
    for page in content.iter(q("draw", "page")):
        for el in drawables(page):
            if el.tag != q("draw", "rect"):
                continue
            if cm(el.get(q("svg", "width"))) < 3 or cm(el.get(q("svg", "height"))) < 2:
                continue
            name = el.get(q("draw", "style-name"))
            props = styles.props("graphic", name, "graphic-properties")
            if props.get("draw:stroke", "none") != "none":
                card_style = name
                radius = cm(el.get(q("draw", "corner-radius")), 0.0)
                break
        if card_style:
            break

    return Theme(
        ink=darks[0] if darks else "#1F2733",
        muted=mids[0] if mids else "#5B6470",
        accent=chroma[0] if chroma else "#1F3F99",
        display_font=display,
        body_font=body,
        card_style=card_style,
        card_radius=radius,
    )


# A spec: {"tag": "rect"|"ellipse"|"line"|"text", box..., "fill"/"stroke"/"text"/"style"...}
Spec = Dict[str, Any]


def _text(
    x,
    y,
    w,
    h,
    text,
    size,
    color,
    *,
    weight=400,
    font=None,
    align="left",
    italic=False,
    spacing=None,
    valign="top",
) -> Spec:
    return {
        "tag": "text",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "text": text,
        "size": size,
        "color": color,
        "weight": weight,
        "font": font,
        "align": align,
        "italic": italic,
        "spacing": spacing,
        "valign": valign,
    }


def build(kind: str, x: float, y: float, t: Theme) -> List[Spec]:
    """The element specs of one part, top-left at (x, y)."""
    display, body = t.display_font or t.body_font, t.body_font
    if kind == "heading":
        return [_text(x, y, 16, 1.4, "標題", 28, t.ink, weight=700, font=display)]
    if kind == "body":
        return [_text(x, y, 12, 1.2, "內文，雙擊即可修改。", 16, t.ink, font=body)]
    if kind == "kicker":
        return [
            _text(x, y, 8, 0.7, "01 章節標籤", 13, t.accent, weight=700, font=body, spacing=0.08)
        ]
    if kind == "card":
        card = {"tag": "rect", "x": x, "y": y, "w": 6, "h": 4.4, "radius": t.card_radius}
        if t.card_style:
            card["style"] = t.card_style
        else:
            card.update(fill="#FFFFFF", stroke="#C9CDD6")
        return [
            card,
            _text(x + 0.35, y + 0.3, 5.3, 0.6, "標籤", 13, t.accent, weight=700, font=body),
            _text(x + 0.35, y + 1.0, 5.3, 1.0, "卡片標題", 17, t.ink, weight=700, font=display),
            _text(x + 0.35, y + 2.1, 5.3, 1.9, "說明文字，雙擊即可修改。", 12, t.muted, font=body),
        ]
    if kind == "step":
        return [
            {
                "tag": "ellipse",
                "x": x,
                "y": y,
                "w": 0.9,
                "h": 0.9,
                "fill": t.accent,
                "label": _text(
                    0, 0, 0, 0, "1", 14, "#FFFFFF", weight=700, font=body, align="center"
                ),
            },
            _text(
                x + 1.2, y, 6, 0.9, "步驟標題", 16, t.ink, weight=700, font=display, valign="middle"
            ),
            _text(x + 1.2, y + 1.0, 6, 1.2, "這一步要做什麼。", 12, t.muted, font=body),
        ]
    if kind == "metric":
        return [
            _text(x, y, 6, 1.8, "87%", 40, t.accent, weight=700, font=display),
            _text(x, y + 1.9, 6, 0.8, "指標說明", 12, t.muted, font=body),
        ]
    if kind == "quote":
        return [
            {"tag": "rect", "x": x, "y": y, "w": 0.15, "h": 2.2, "fill": t.accent},
            _text(
                x + 0.45, y, 11, 1.5, "「引述的一句話。」", 20, t.ink, font=display, italic=False
            ),
            _text(x + 0.45, y + 1.6, 11, 0.6, "— 出處", 12, t.muted, font=body),
        ]
    if kind == "takeaway":
        return [
            {
                "tag": "rect",
                "x": x,
                "y": y,
                "w": 20,
                "h": 1.35,
                "fill": "#FFFFFF",
                "stroke": t.accent,
                "radius": t.card_radius,
            },
            {"tag": "rect", "x": x, "y": y, "w": 0.2, "h": 1.35, "fill": t.accent},
            _text(
                x + 0.45,
                y,
                19.3,
                1.35,
                "這一頁的結論。",
                18,
                t.ink,
                weight=700,
                font=display,
                valign="middle",
            ),
        ]
    if kind == "timeline":
        return [
            {"tag": "ellipse", "x": x, "y": y + 0.1, "w": 0.5, "h": 0.5, "fill": t.accent},
            _text(x + 0.8, y, 5, 0.7, "2026 年", 13, t.accent, weight=700, font=body),
            _text(x + 0.8, y + 0.75, 5, 1.0, "事件說明", 12, t.muted, font=body),
        ]
    if kind == "divider":
        return [{"tag": "line", "x1": x, "y1": y, "x2": x + 10, "y2": y, "stroke": "#A3A7AB"}]
    if kind == "ellipse":
        return [{"tag": "ellipse", "x": x, "y": y, "w": 3, "h": 3, "fill": t.accent}]
    if kind == "rect":
        return [{"tag": "rect", "x": x, "y": y, "w": 6, "h": 3.5, "fill": "#E8ECF4"}]
    raise ValueError(kind)


KINDS = (
    "heading",
    "body",
    "kicker",
    "card",
    "step",
    "metric",
    "quote",
    "takeaway",
    "timeline",
    "divider",
    "rect",
    "ellipse",
)

__all__ = ["KINDS", "NS", "Theme", "build", "infer_theme"]
