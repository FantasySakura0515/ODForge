"""Render a :class:`~odforge.ir.Presentation` into a native ``.odp`` file.

This renderer deliberately hand-writes OpenDocument presentation XML rather
than delegating to ``odfdo`` (whose ``.odp`` support is weak). Every piece of
user-supplied text is passed through :func:`xml.sax.saxutils.escape` — including
attribute values — so that malformed content can never break well-formedness.

Layout is data-driven: :data:`odforge.themes.LAYOUTS` maps each slide layout to
a list of :class:`~odforge.themes.Frame` placeholders, and each frame's ``role``
selects which slide field supplies its content. Empty content frames are
skipped; speaker notes are emitted only when non-empty.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, MutableMapping
from dataclasses import replace
from pathlib import Path
from xml.sax.saxutils import escape

from odforge.ir import (
    BulletItem,
    ChartSpec,
    DiagramSpec,
    MetricSpec,
    Presentation,
    ProcessStep,
    Slide,
    TimelineEvent,
)
from odforge.media import (
    AssetBlob,
    AssetInput,
    ImageProvider,
    MediaError,
    configured_image_provider,
    normalize_assets,
    resolve_image,
)
from odforge.package import ODP_MIMETYPE, write_odf_package
from odforge.textmetrics import estimate_height_cm, fact_font_size_pt, text_width_cm
from odforge.themes import (
    LAYOUTS,
    LIST_ROLES,
    PAGE_H,
    PAGE_W,
    PLAIN_LAYOUTS,
    THEMES,
    Frame,
    Theme,
    resolve_design,
)

# ---------------------------------------------------------------------------
# Namespace declarations
# ---------------------------------------------------------------------------

_CONTENT_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    # Linked images (Task 13.4 title-page SVG) reference their package part via
    # xlink:href, so the prefix must be declared on office:document-content.
    "xlink": "http://www.w3.org/1999/xlink",
}

_STYLES_NS = dict(_CONTENT_NS)

_META_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_MASTER_PAGE_NAME = "Standard"
_PLAIN_MASTER_NAME = "Plain"
_DRAWING_PAGE_STYLE = "dp1"
_SECTION_DRAWING_PAGE_STYLE = "dpsec"
_GRAPHIC_STYLE = "gr1"
_GRADIENT_NAME = "grad-bg"
_NOTES_SIZE_PT = 14

# Task 13.3: master pages, inverted section pages, accent system.
#
# PLAIN_LAYOUTS (layouts rendered "bare": no page-number/footer/kicker furniture)
# is imported from odforge.themes — the single source shared with textmetrics.
# Layouts painted with a full-bleed accent background (inverted pages): the
# section divider and the closing page. The giant chapter-number watermark is
# gated on layout=="section" specifically (closing pages must not display or
# consume a section ordinal — see _page_xml / build_content_xml).
_ACCENT_BG_LAYOUTS = frozenset({"section", "closing"})

# Vertical accent bar flush with a content-page title's left edge.
_ACCENT_BAR_W = 0.18  # cm
# Giant watermark chapter number on section pages.
_SECTION_NUMBER_PT = 96
# The number is drawn as text tinted 15% from accent toward the theme bg —
# ODF 1.2 has no reliable per-run text opacity, so this precomputed blend
# emulates a bg-coloured number at ~15% opacity sitting on the accent fill.
_SECTION_NUMBER_BLEND = 0.15
# (x, y, w, h) cm — upper-right, right edge aligned with the footer line.
_SECTION_NUMBER_BOX = (18.5, 1.0, 8.0, 4.5)

# Task 14.1: quote layout's decorative quotation-mark watermark. Drawn as a big
# glyph tinted 75% from accent toward the bg (i.e. ~25% "opacity") — the same
# reliable precomputed-blend trick the section number uses, since ODF 1.2 has no
# dependable per-run text opacity. Deterministic (a pure function of the theme).
_QUOTE_MARK_GLYPH = "“"  # left double quotation mark
_QUOTE_MARK_PT = 120
_QUOTE_MARK_BLEND = 0.75  # _blend(accent, bg, this) → faint accent tint
_QUOTE_MARK_BOX = (2.2, 2.8, 6.0, 6.0)  # (x, y, w, h) cm — behind the quote text

# Master-page furniture geometry + style names.
_FOOTER_LINE_Y = 14.9
_FOOTER_LINE_X1 = 1.5
_FOOTER_LINE_X2 = 26.5
_FOOTER_LINE_WIDTH_PT = 0.75
_FURNITURE_Y = 14.95
_FURNITURE_H = 0.7
_PAGENUM_X = 24.0
_PAGENUM_W = 2.5
_KICKER_W = 18.0
_MP_LINE_STYLE = "MPline"
_MP_FRAME_STYLE = "MPframe"
_MP_PAGENUM_STYLE = "MPpage"
_MP_KICKER_STYLE = "MPkicker"
# LibreOffice Impress only paints master-page shapes when the document declares
# a <draw:layer-set> AND each shape sits on a named layer — without this, plain
# draw:line/draw:frame on the master silently vanish (only recognised
# presentation placeholders render). Furniture goes on "backgroundobjects".
_FURNITURE_LAYER = "backgroundobjects"
_LAYER_SET_XML = (
    "<draw:layer-set>"
    '<draw:layer draw:name="layout"/>'
    '<draw:layer draw:name="background"/>'
    '<draw:layer draw:name="backgroundobjects"/>'
    '<draw:layer draw:name="controls"/>'
    '<draw:layer draw:name="measurelines"/>'
    "</draw:layer-set>"
)

# Semantic-list typography (Task 13.2).
_LIST_STYLE_NAME = "L1"
_BULLET_LINE_HEIGHT = "145%"
_BULLET_MARGIN_BOTTOM = "0.35cm"
_KICKER_LETTER_SPACING = "0.15cm"
# LIST_ROLES (frame roles whose left-aligned multi-item content renders as a
# semantic bullet list) is imported from odforge.themes — the single source
# shared with textmetrics. Centred frames stay bare centred paragraphs.
# Per bullet level: (text:level, space-before cm, min-label-width cm). Level 2
# indents deeper so nested items read as a sub-list.
_LIST_LEVELS: tuple[tuple[int, float, float], ...] = (
    (1, 0.6, 0.6),
    (2, 1.4, 0.6),
)

# Task 13.4: title-page SVG decoration + card-backed two-col columns.
#
# The decoration is a single engine-generated (never LLM) SVG shared by every
# title page in a deck — one theme per deck means one accent, so one file. It is
# linked (not per-page embedded) from Pictures/ via xlink:href.
_DECO_HREF = "Pictures/deco.svg"
_DECO_LAYOUTS = frozenset({"title"})
# Low-key placement box (x, y, w, h cm) hugging the bottom-right page corner; the
# 4:3 box matches the SVG viewBox so dots stay circular.
_DECO_BOX = (21.0, 10.75, 6.0, 4.5)
# SVG dot-grid geometry (viewBox units): a cols×rows lattice fading out toward
# the top-left, densest (most opaque) at the bottom-right corner.
_DECO_VIEWBOX = (80.0, 60.0)
_DECO_GRID = (6, 4)  # cols, rows
_DECO_MARGIN = 8.0
_DECO_DOT_R = 2.6
_DECO_OPACITY_MIN = 0.06
_DECO_OPACITY_SPAN = 0.30

# Rounded "surface" card sitting behind each two-col column's text. Padded out
# from the text frame so the card reads as a container; emitted before the text
# frame so document order puts the text on top (ODF z-order == document order).
_CARD_ROLES = frozenset({"left", "right"})
_CARD_PAD = 0.3  # cm the card overhangs its text frame on every side

# Interior metrics for the idea-card grid (cm). Kept as named constants because
# both the card's height and its children's baseline are derived from them —
# a literal in one place and not the other is how the box and its contents
# drifted apart in the first place.
_CARD_PAD_X = 0.45
_CARD_PAD_TOP = 0.45
_CARD_PAD_BOTTOM = 0.5
_CARD_BADGE_H = 0.55
_CARD_BADGE_GAP = 0.35  # badge baseline → title top
_CARD_TITLE_GAP = 0.4  # title → children
_CARD_TITLE_LINE_HEIGHT = 1.2  # matches the title's line-height="120%"
_CARD_CHILD_LINE_HEIGHT = 1.35  # matches the children's line-height="135%"
_CARD_MIN_H = 3.2  # below this a card stops reading as a container
_CARD_MAX_H = 6.8

# Timeline event cards. The stem is the fixed distance from the axis to a card,
# so a taller row grows away from the line instead of into it.
_TIMELINE_STEM = 0.85
_TIMELINE_TITLE_TOP = 1.05  # label sits above, at +0.30
_TIMELINE_DETAIL_GAP = 0.3
_TIMELINE_DETAIL_LINE_HEIGHT = 1.3  # matches the detail's line-height="130%"
_TIMELINE_PAD_BOTTOM = 0.4
_TIMELINE_MIN_H = 3.0
_TIMELINE_MAX_H = 5.2

# Process step cards, same badge → title → detail stack.
_PROCESS_TITLE_TOP = 1.65
_PROCESS_TITLE_LINE_HEIGHT = 1.15  # matches the title's line-height="115%"
_PROCESS_DETAIL_GAP = 0.35
_PROCESS_DETAIL_LINE_HEIGHT = 1.35
_PROCESS_PAD_BOTTOM = 0.5
_PROCESS_MIN_H = 3.4
_PROCESS_MAX_H = 7.0

# Diagram edge labels. The chip is sized to its text; the clearance is the
# run of connector that must stay visible either side of it, and decides
# whether the chip can sit on the line at all.
_EDGE_LABEL_PAD = 0.22
_EDGE_LABEL_CLEARANCE = 0.35

# LibreOffice insets a draw text-box by this much on each side unless the
# graphic style says otherwise, and `gr1` does not. Measuring a wrap against
# the full frame width is therefore optimistic by half a centimetre — enough
# to under-count a line and size a box too short. Every box sized to its own
# text measures against `_wrap_width` instead.
_FRAME_INSET_X = 0.25


def _wrap_width(frame_w: float) -> float:
    """Usable text width inside a frame of ``frame_w`` (cm)."""
    return max(0.1, frame_w - 2 * _FRAME_INSET_X)
_CARD_CORNER = 0.3  # cm corner radius

# Task 13.5: shape-drawn horizontal bar charts. The "chart" layout that drops
# one onto a page arrives in Task 14.1 — here the renderer only learns to draw a
# ChartSpec into an arbitrary area. Each value becomes one horizontal draw:rect
# whose svg:width is proportional to the value (the largest value fills the
# track); the highlighted bar is filled accent, the rest a muted tone blended
# toward the bg so the one bar that matters reads first. Labels sit in a left
# gutter, value+unit text just past each bar's end.
_CHART_LABEL_W_FRAC = 0.22  # left gutter (fraction of area width) holding labels
_CHART_VALUE_W_FRAC = 0.14  # right gutter reserved so value text fits past bars
_CHART_GAP = 0.2  # cm — breathing space before a bar and before its value text
_CHART_BAR_H_FRAC = 0.42  # bar thickness as a fraction of its row's height
_CHART_BAR_CORNER = 0.08  # cm — bars are barely rounded
_CHART_OTHER_BLEND = 0.55  # non-highlight bars: _blend(muted, bg, this amount)

# Shape-rendered visual layouts.
_VISUAL_CARD_CORNER = 0.28
_VISUAL_GAP = 0.55
_VISUAL_LINE_WIDTH_PT = 1.5
_VISUAL_LINE_BLEND = 0.55
_PROCESS_BADGE_SIZE = 0.76
_TIMELINE_NODE_SIZE = 0.58
_METRIC_ACCENT_H = 0.14
_DIAGRAM_NODE_H = 2.45
_DIAGRAM_NODE_W = 5.2
_DIAGRAM_LINE_BLEND = 0.62
_SOURCE_FRAME = Frame("sources", 1.5, 14.08, 21.8, 0.75, 9)


def _attr(value: str) -> str:
    """Escape a string for safe use inside a double-quoted XML attribute."""
    return escape(value, {'"': "&quot;"})


def _cm(value: float) -> str:
    """Format a centimetre measure, dropping a trailing ``.0`` (28.0 -> "28cm")."""
    if value == int(value):
        return f"{int(value)}cm"
    return f"{value}cm"


def _pt(value: float) -> str:
    """Format a point measure, dropping a trailing ``.0`` (2.0 -> "2pt")."""
    if value == int(value):
        return f"{int(value)}pt"
    return f"{value}pt"


def _lighten(hex_color: str, amount: float) -> str:
    """Return ``#RRGGBB`` moved ``amount`` (0..1) of the way toward white."""

    def _mix(channel: int) -> int:
        return round(channel + (255 - channel) * amount)

    r = _mix(int(hex_color[1:3], 16))
    g = _mix(int(hex_color[3:5], 16))
    b = _mix(int(hex_color[5:7], 16))
    return f"#{r:02X}{g:02X}{b:02X}"


def _blend(from_hex: str, to_hex: str, amount: float) -> str:
    """Return ``from_hex`` moved ``amount`` (0..1) of the way toward ``to_hex``.

    Generalises :func:`_lighten` (which only blends toward white) to any target
    colour — used for the section-page watermark number (accent → bg).
    """

    def _mix(a: int, b: int) -> int:
        return round(a + (b - a) * amount)

    r = _mix(int(from_hex[1:3], 16), int(to_hex[1:3], 16))
    g = _mix(int(from_hex[3:5], 16), int(to_hex[3:5], 16))
    b = _mix(int(from_hex[5:7], 16), int(to_hex[5:7], 16))
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_light(hex_color: str) -> bool:
    """True when a colour's perceived luma sits in the upper half (a light ground)."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 140.0


# How far a full-bleed section/closing fill is deepened toward the ink. A raw
# mid-saturation accent flooded edge-to-edge reads cheap; blending it well toward
# the text colour turns ANY accent into a deep, premium panel (and keeps the
# inverted bg-coloured title high-contrast). Only applied to light-ground decks.
_SECTION_FILL_DEEPEN = 0.62


def _section_fill(theme: Theme) -> str:
    """Fill colour for full-bleed section/closing pages (:data:`_ACCENT_BG_LAYOUTS`).

    Deepens the accent toward the ink on light-ground decks so a mid-saturation
    per-deck accent becomes a rich, editorial panel instead of a flat, cheap
    wash. A dark theme's ground is already deep, so its bright accent is used
    as-is (the intended pop). The inverted title/number colours are unchanged —
    they key off ``theme.bg``, which still contrasts against the deepened fill.
    """
    if _is_light(theme.bg):
        return _blend(theme.accent, theme.text, _SECTION_FILL_DEEPEN)
    return theme.accent


def _uses_gradient_bg(theme: Theme) -> bool:
    """True when this theme paints pages with the background gradient.

    Only the built-in dark preset qualifies; light presets and per-deck
    ``DesignSpec`` themes keep a flat solid fill.
    """
    return theme is THEMES["dark"]


def _page_fill_attrs(theme: Theme) -> str:
    """Fill attributes for a drawing-page style (gradient for dark, else solid).

    Used by *both* ``build_styles_xml`` (master page) and ``build_content_xml``
    (per-page ``dp1``): per ODF style resolution a page's own drawing-page style
    overrides the master's, so the gradient must be referenced from content.xml
    too or it would never paint a slide. The ``<draw:gradient>`` definition
    lives in styles.xml ``office:styles`` and is document-scoped, hence
    referenceable from content.xml automatic styles.
    """
    if _uses_gradient_bg(theme):
        return (
            'draw:fill="gradient"'
            f' draw:fill-gradient-name="{_attr(_GRADIENT_NAME)}"'
        )
    return f'draw:fill="solid" draw:fill-color="{_attr(theme.bg)}"'


def _ns_decls(ns: dict[str, str]) -> str:
    """Render ``xmlns:`` declarations for the given namespace mapping."""
    return " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in ns.items())


# ---------------------------------------------------------------------------
# Paragraph style cache (de-duplicated on the visual property tuple)
# ---------------------------------------------------------------------------


class _ParagraphStyles:
    """Collects unique paragraph styles, assigning names P1, P2, … on demand.

    The de-duplication key is the visual property tuple. Beyond the original
    ``(size_pt, bold, center, color)`` dimensions it now also carries the
    paragraph-typography knobs Task 13.2 introduced — ``line_height``,
    ``margin_bottom`` (paragraph-properties) and ``letter_spacing``
    (text-properties). They default to ``None`` so existing call sites keep
    producing byte-identical styles (no line-height / margin / spacing emitted).
    """

    def __init__(self, font: str) -> None:
        self._font = font
        self._names: dict[tuple, str] = {}

    def name_for(
        self,
        size_pt: int,
        bold: bool,
        center: bool,
        color: str,
        *,
        line_height: str | None = None,
        margin_bottom: str | None = None,
        letter_spacing: str | None = None,
        font: str | None = None,
    ) -> str:
        key = (
            font or self._font,
            size_pt,
            bold,
            center,
            color,
            line_height,
            margin_bottom,
            letter_spacing,
        )
        name = self._names.get(key)
        if name is None:
            name = f"P{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _paragraph_style_xml(name, *key)
            for key, name in self._names.items()
        )


def _font_size_attrs(size_pt: int) -> str:
    """Three-track font-size attributes (Western + CJK ``*-asian`` + CTL ``*-complex``).

    LibreOffice applies ``fo:font-size`` to Western script only; CJK glyphs need
    the ``*-asian`` variant and complex scripts the ``*-complex`` one, so every
    text style must carry all three or CJK text renders at the wrong size.
    """
    return (
        f' fo:font-size="{size_pt}pt"'
        f' style:font-size-asian="{size_pt}pt"'
        f' style:font-size-complex="{size_pt}pt"'
    )


def _font_weight_attrs(bold: bool) -> str:
    """Three-track ``font-weight`` attributes (Western + ``*-asian`` + ``*-complex``)."""
    if not bold:
        return ""
    return (
        ' fo:font-weight="bold"'
        ' style:font-weight-asian="bold"'
        ' style:font-weight-complex="bold"'
    )


def _paragraph_style_xml(
    name: str,
    font: str,
    size_pt: int,
    bold: bool,
    center: bool,
    color: str,
    line_height: str | None = None,
    margin_bottom: str | None = None,
    letter_spacing: str | None = None,
) -> str:
    """Build one ``style:family="paragraph"`` automatic style element."""
    align = "center" if center else "start"
    size = _font_size_attrs(size_pt)
    weight = (
        ' fo:font-weight="bold"'
        ' style:font-weight-asian="bold"'
        ' style:font-weight-complex="bold"'
        if bold
        else ""
    )
    para_extra = ""
    if line_height is not None:
        para_extra += f' fo:line-height="{_attr(line_height)}"'
    if margin_bottom is not None:
        para_extra += f' fo:margin-bottom="{_attr(margin_bottom)}"'
    spacing = (
        f' fo:letter-spacing="{_attr(letter_spacing)}"'
        if letter_spacing is not None
        else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="paragraph">'
        f'<style:paragraph-properties fo:text-align="{align}"{para_extra}/>'
        f"<style:text-properties{size}{weight}"
        f' fo:color="{_attr(color)}"{spacing} style:font-name="{_attr(font)}"'
        f' style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
    )


# ---------------------------------------------------------------------------
# Graphic style cache (de-duplicated shape fills and strokes)
# ---------------------------------------------------------------------------


class _GraphicStyles:
    """Collects unique graphic styles for shapes, naming them G1, G2, … .

    Two kinds of style are de-duplicated, mirroring :class:`_ParagraphStyles`:

    * **fills** keyed on ``(fill_color, opacity)`` — emitted as a solid fill with
      no stroke; ``draw:opacity`` appears only when ``opacity < 1``. Corner radius
      is deliberately *not* part of the key: in ODF it is a ``draw:rect`` element
      attribute (see :func:`_rect_xml`), not a graphic-property, so it never
      varies the style body.
    * **strokes** keyed on ``(color, width_pt)`` — emitted as a solid stroke with
      no fill, for :func:`_line_xml`.
    """

    def __init__(self) -> None:
        self._names: dict[tuple, str] = {}

    def name_for_fill(self, fill: str, opacity: float = 1.0) -> str:
        return self._name(("fill", fill, opacity))

    def name_for_stroke(self, color: str, width_pt: float) -> str:
        return self._name(("stroke", color, width_pt))

    def _name(self, key: tuple) -> str:
        name = self._names.get(key)
        if name is None:
            name = f"G{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _graphic_style_xml(name, key) for key, name in self._names.items()
        )


def _graphic_style_xml(name: str, key: tuple) -> str:
    """Build one ``style:family="graphic"`` automatic style from a registry key."""
    if key[0] == "fill":
        _, fill, opacity = key
        opacity_attr = "" if opacity >= 1.0 else f' draw:opacity="{opacity * 100:g}%"'
        props = (
            f'draw:fill="solid" draw:fill-color="{_attr(fill)}"'
            f'{opacity_attr} draw:stroke="none"'
        )
    else:  # "stroke"
        _, color, width_pt = key
        props = (
            'draw:fill="none" draw:stroke="solid"'
            f' svg:stroke-color="{_attr(color)}"'
            f' svg:stroke-width="{_pt(width_pt)}"'
        )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="graphic">'
        f"<style:graphic-properties {props}/>"
        f"</style:style>"
    )


# ---------------------------------------------------------------------------
# Text-span style cache (inline runs whose colour/weight differs from the
# enclosing paragraph — e.g. agenda's accent-tinted 01/02 numbers)
# ---------------------------------------------------------------------------


class _SpanStyles:
    """Collects unique ``style:family="text"`` styles, naming them T1, T2, … .

    De-duplicated on ``(size_pt, bold, color)``. Mirrors :class:`_ParagraphStyles`
    but emits ``family="text"`` so the style can dress a ``<text:span>`` inside a
    paragraph without disturbing the surrounding text's colour.
    """

    def __init__(self, font: str) -> None:
        self._font = font
        self._names: dict[tuple, str] = {}

    def name_for(self, size_pt: int, bold: bool, color: str) -> str:
        key = (size_pt, bold, color)
        name = self._names.get(key)
        if name is None:
            name = f"T{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _text_span_style_xml(name, self._font, *key)
            for key, name in self._names.items()
        )


def _text_span_style_xml(name: str, font: str, size_pt: int, bold: bool, color: str) -> str:
    """Build one ``style:family="text"`` automatic style (three-track CJK sizing)."""
    return (
        f'<style:style style:name="{_attr(name)}" style:family="text">'
        f"<style:text-properties{_font_size_attrs(size_pt)}{_font_weight_attrs(bold)}"
        f' fo:color="{_attr(color)}" style:font-name="{_attr(font)}"'
        f' style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
    )


# ---------------------------------------------------------------------------
# Shape element builders (draw:rect / draw:line)
# ---------------------------------------------------------------------------


def _rect_xml(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    opacity: float = 1.0,
    corner_radius_cm: float = 0.0,
    style_name: str,
) -> str:
    """Build a ``draw:rect`` element referencing the graphic style ``style_name``.

    ``fill`` and ``opacity`` describe the fill that ``style_name`` must provide
    (register it via :class:`_GraphicStyles`); they are accepted so a call site
    reads as a complete rectangle spec and are intentionally *not* repeated on
    the element, because in ODF fill properties live in the referenced graphic
    style. ``draw:corner-radius`` is written only when ``corner_radius_cm > 0``.
    """
    radius = (
        f' draw:corner-radius="{_cm(corner_radius_cm)}"'
        if corner_radius_cm > 0
        else ""
    )
    return (
        f'<draw:rect draw:style-name="{_attr(style_name)}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}"{radius}/>'
    )


def _line_xml(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str,
    width_pt: float,
    style_name: str,
) -> str:
    """Build a ``draw:line`` element referencing the graphic style ``style_name``.

    ``color`` and ``width_pt`` describe the stroke that ``style_name`` must
    provide (register it via :meth:`_GraphicStyles.name_for_stroke`); like
    :func:`_rect_xml` the visual attributes are carried by the referenced graphic
    style rather than repeated on the element.
    """
    return (
        f'<draw:line draw:style-name="{_attr(style_name)}"'
        f' svg:x1="{_cm(x1)}" svg:y1="{_cm(y1)}"'
        f' svg:x2="{_cm(x2)}" svg:y2="{_cm(y2)}"/>'
    )


def _ellipse_xml(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    style_name: str,
) -> str:
    """Build a filled ellipse for process badges and timeline nodes."""
    return (
        f'<draw:ellipse draw:style-name="{_attr(style_name)}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}"/>'
    )


# ---------------------------------------------------------------------------
# Title-page SVG decoration (engine-generated, deterministic)
# ---------------------------------------------------------------------------


def _svg_decoration(theme: Theme) -> bytes:
    """Return a small, deterministic decorative SVG tinted with ``theme.accent``.

    A ``cols×rows`` lattice of dots that fades out toward the top-left: each dot's
    ``fill-opacity`` grows with its Manhattan distance from the origin so the
    cluster reads densest at the bottom-right — the page corner it is placed in.
    Purely a function of the theme (no randomness / time) for reproducible
    builds. Returns UTF-8 bytes so it drops straight into the ODF package as a
    ``Pictures/*.svg`` part (media-type inferred by :mod:`odforge.package`).
    """
    cols, rows = _DECO_GRID
    vb_w, vb_h = _DECO_VIEWBOX
    step_x = (vb_w - 2 * _DECO_MARGIN) / (cols - 1)
    step_y = (vb_h - 2 * _DECO_MARGIN) / (rows - 1)
    max_dist = (cols - 1) + (rows - 1)
    circles: list[str] = []
    for row in range(rows):
        for col in range(cols):
            cx = _DECO_MARGIN + col * step_x
            cy = _DECO_MARGIN + row * step_y
            opacity = _DECO_OPACITY_MIN + _DECO_OPACITY_SPAN * (
                (col + row) / max_dist
            )
            circles.append(
                f'<circle cx="{cx:g}" cy="{cy:g}" r="{_DECO_DOT_R:g}"'
                f' fill="{_attr(theme.accent)}"'
                f' fill-opacity="{round(opacity, 3):g}"/>'
            )
    svg = (
        f"{_XML_DECL}"
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' viewBox="0 0 {vb_w:g} {vb_h:g}"'
        f' width="{vb_w:g}" height="{vb_h:g}">'
        f"{''.join(circles)}"
        f"</svg>"
    )
    return svg.encode("utf-8")


def _deco_frame_xml() -> str:
    """Build the ``draw:frame`` linking the title-page decoration SVG.

    The frame carries the no-fill/no-stroke ``gr1`` graphic style and holds a
    single ``<draw:image>`` whose ``xlink:href`` points at the ``Pictures/*.svg``
    package part built by :func:`_svg_decoration`.
    """
    x, y, w, h = _DECO_BOX
    return (
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}">'
        f'<draw:image xlink:href="{_attr(_DECO_HREF)}"/>'
        f"</draw:frame>"
    )


# ---------------------------------------------------------------------------
# Frame content resolution (role -> slide field)
# ---------------------------------------------------------------------------


def _coerce_bullet_items(items) -> list:
    """Lower ``Slide.bullets`` (``str | BulletItem``) onto the renderer's list
    contract (``str | (text, children)`` — Task 13.2). A plain ``str`` passes
    through unchanged (v1 decks stay byte-identical); a ``BulletItem`` with
    children becomes a ``(text, children)`` tuple; a childless ``BulletItem``
    collapses to its bare text so it renders as a leaf bullet.
    """
    out: list = []
    for it in items:
        if isinstance(it, BulletItem):
            out.append((it.text, list(it.children)) if it.children else it.text)
        else:
            out.append(it)
    return out


def _line_text(item) -> str:
    """The display string for a list/bare item (``str`` or ``(text, children)``)."""
    return item[0] if isinstance(item, tuple) else item


def _role_lines(slide: Slide, role: str) -> list:
    """Return the content items a frame's ``role`` contributes, or [] if empty.

    Bullet-family roles (``bullets``/``items``/``insights``) all draw from
    ``slide.bullets`` and are coerced onto the semantic-list contract; the rest
    are plain single-line text fields.
    """
    if role == "title":
        return [slide.title] if slide.title else []
    if role == "subtitle":
        return [slide.subtitle] if slide.subtitle else []
    if role == "fact":
        return [slide.fact] if slide.fact else []
    if role == "quote":
        return [slide.quote] if slide.quote else []
    if role == "attribution":
        return [slide.attribution] if slide.attribution else []
    if role == "message":
        # closing's message is the slide title (subtitle handled in the branch).
        return [slide.title] if slide.title else []
    if role in ("bullets", "items", "insights"):
        return _coerce_bullet_items(slide.bullets)
    if role == "left":
        return list(slide.left)
    if role == "right":
        return list(slide.right)
    return []


def _frame_box_xml(frame: Frame, inner: str) -> str:
    """Wrap pre-built text-box ``inner`` XML in a positioned ``draw:frame``."""
    return (
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="{_cm(frame.x)}" svg:y="{_cm(frame.y)}"'
        f' svg:width="{_cm(frame.w)}" svg:height="{_cm(frame.h)}">'
        f"<draw:text-box>{inner}</draw:text-box>"
        f"</draw:frame>"
    )


def _frame_xml(
    frame: Frame, lines: list[str], style_name: str
) -> str:
    """Build a ``draw:frame`` of bare ``text:p`` paragraphs (non-list content)."""
    paragraphs = "".join(
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(_line_text(line))}</text:p>"
        for line in lines
    )
    return _frame_box_xml(frame, paragraphs)


def _image_area_xml(
    slide: Slide,
    frame: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    resolved: tuple[str, AssetBlob] | None,
) -> str:
    """Render a packaged raster image, or an honest editable placeholder."""

    if slide.image is None:
        return ""

    caption_parts = [part for part in (slide.image.caption, slide.image.credit) if part]
    caption = " · ".join(caption_parts)
    caption_h = 0.72 if caption else 0.0
    image_h = max(0.5, frame.h - caption_h)
    inset = 0.08
    target_x = frame.x + inset
    target_y = frame.y + inset
    target_w = frame.w - inset * 2
    target_h = image_h - inset * 2

    surface_style = graphics.name_for_fill(theme.surface)
    parts = [
        _rect_xml(
            frame.x,
            frame.y,
            frame.w,
            image_h,
            fill=theme.surface,
            corner_radius_cm=0.18,
            style_name=surface_style,
        )
    ]

    if resolved is not None:
        href, blob = resolved
        draw_x, draw_y, draw_w, draw_h = target_x, target_y, target_w, target_h
        if slide.image.fit == "contain":
            image_ratio = blob.width / blob.height
            frame_ratio = target_w / target_h
            if image_ratio > frame_ratio:
                draw_h = target_w / image_ratio
                draw_y += (target_h - draw_h) / 2
            else:
                draw_w = target_h * image_ratio
                draw_x += (target_w - draw_w) / 2
        parts.append(
            f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
            f' svg:x="{_cm(draw_x)}" svg:y="{_cm(draw_y)}"'
            f' svg:width="{_cm(draw_w)}" svg:height="{_cm(draw_h)}">'
            f'<draw:image xlink:href="{_attr(href)}" xlink:type="simple"'
            f' xlink:show="embed" xlink:actuate="onLoad"/>'
            f"</draw:frame>"
        )
    else:
        placeholder_style = styles.name_for(
            theme.caption_pt,
            False,
            True,
            theme.muted,
        )
        placeholder = Frame(
            "image-placeholder",
            target_x + 0.6,
            target_y + target_h / 2 - 0.75,
            max(0.5, target_w - 1.2),
            1.5,
            theme.caption_pt,
            center=True,
        )
        placeholder_text = f"圖片待補\n{slide.image.alt}"
        parts.append(
            _frame_box_xml(
                placeholder,
                "".join(
                    f'<text:p text:style-name="{_attr(placeholder_style)}">'
                    f"{escape(line)}</text:p>"
                    for line in placeholder_text.splitlines()
                ),
            )
        )

    if caption:
        caption_style = styles.name_for(
            theme.caption_pt,
            False,
            False,
            theme.muted,
        )
        caption_frame = Frame(
            "image-caption",
            frame.x,
            frame.y + image_h + 0.12,
            frame.w,
            max(0.45, caption_h - 0.12),
            theme.caption_pt,
        )
        parts.append(_frame_xml(caption_frame, [caption], caption_style))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Task 13.5: shape-drawn horizontal bar charts
# ---------------------------------------------------------------------------


def _fmt_number(value: float) -> str:
    """Format a chart value for display: whole numbers drop the ``.0``
    (42.0 -> "42"), fractions stay compact (3.5 -> "3.5")."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _text_line_h_cm(size_pt: int) -> float:
    """Approximate one text line's height in cm for a point size (1.2 leading).

    Lets a bar's label/value be positioned to sit vertically centred on the bar
    without a dedicated vertical-align graphic style — and stays deterministic
    (a pure function of the point size).
    """
    return size_pt / 72.0 * 2.54 * 1.2


def _chart_xml(
    chart: ChartSpec,
    area_x: float,
    area_y: float,
    area_w: float,
    area_h: float,
    theme: Theme,
    graphics: _GraphicStyles,
    para_styles: _ParagraphStyles,
) -> str:
    """Render a :class:`~odforge.ir.ChartSpec` as horizontal bars filling an area.

    Each value becomes one ``draw:rect`` whose ``svg:width`` is proportional to
    the value — the largest value fills the available track width, so bar-width
    ratios equal value ratios exactly. The bar named by ``chart.highlight`` is
    filled ``theme.accent``; every other bar takes a muted tone blended toward
    the background (:data:`_CHART_OTHER_BLEND`) so the key bar reads first. Each
    bar carries its label (left gutter) and value+unit text (just past the bar's
    end), both at ``theme.body_pt`` with three-track CJK sizing via
    ``para_styles``. Bars are barely rounded (:data:`_CHART_BAR_CORNER`).

    ``graphics`` / ``para_styles`` are the shared style registries the caller
    later serialises; this builder only *registers* styles and returns the shape
    + text XML (no ``<style:style>`` of its own). Deterministic.

    Zero-value edge: when every value is 0 the bars render at zero width (guarded
    against division by zero) — no data, no bar — while labels/values still show.
    """
    n = len(chart.values)
    max_v = max(chart.values)
    label_w = area_w * _CHART_LABEL_W_FRAC
    value_w = area_w * _CHART_VALUE_W_FRAC
    track_w = area_w - label_w - value_w
    row_h = area_h / n
    bar_x = area_x + label_w
    # Non-highlight bars share one muted-toned fill (deduped in ``graphics``).
    other_fill = _blend(theme.muted, theme.bg, _CHART_OTHER_BLEND)
    line_h = _text_line_h_cm(theme.body_pt)

    parts: list[str] = []
    for i, (label, value) in enumerate(zip(chart.labels, chart.values)):
        highlighted = i == chart.highlight
        bar_w = track_w * (value / max_v) if max_v > 0 else 0.0
        row_y = area_y + i * row_h
        bar_h = row_h * _CHART_BAR_H_FRAC
        bar_y = row_y + (row_h - bar_h) / 2.0
        # Vertically centre one text line on the bar.
        text_y = bar_y + (bar_h - line_h) / 2.0

        fill = theme.accent if highlighted else other_fill
        bar_style = graphics.name_for_fill(fill)
        parts.append(
            _rect_xml(
                bar_x,
                bar_y,
                bar_w,
                bar_h,
                fill=fill,
                corner_radius_cm=_CHART_BAR_CORNER,
                style_name=bar_style,
            )
        )

        # Label in the left gutter (the highlighted row's label is bolded too).
        label_style = para_styles.name_for(
            theme.body_pt, highlighted, False, theme.text_color
        )
        parts.append(
            _frame_box_xml(
                Frame(
                    "chart-label",
                    area_x,
                    text_y,
                    label_w - _CHART_GAP,
                    line_h,
                    theme.body_pt,
                ),
                f'<text:p text:style-name="{_attr(label_style)}">'
                f"{escape(label)}</text:p>",
            )
        )

        # Value + unit just past the bar's end; the frame runs to the area edge.
        value_x = bar_x + bar_w + _CHART_GAP
        value_frame_w = max((area_x + area_w) - value_x, _CHART_GAP)
        value_color = theme.accent if highlighted else theme.muted
        value_style = para_styles.name_for(
            theme.body_pt, highlighted, False, value_color
        )
        value_text = f"{_fmt_number(value)}{chart.unit}"
        parts.append(
            _frame_box_xml(
                Frame(
                    "chart-value",
                    value_x,
                    text_y,
                    value_frame_w,
                    line_h,
                    theme.body_pt,
                ),
                f'<text:p text:style-name="{_attr(value_style)}">'
                f"{escape(value_text)}</text:p>",
            )
        )

    return "".join(parts)


# ---------------------------------------------------------------------------
# Shape-rendered process, timeline, metric and idea-card layouts
# ---------------------------------------------------------------------------


def _visual_text_xml(
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    styles: _ParagraphStyles,
    *,
    size_pt: int,
    color: str,
    bold: bool = False,
    center: bool = False,
    font: str | None = None,
    line_height: str | None = None,
) -> str:
    """Render one styled paragraph in a positioned text frame."""
    style_name = styles.name_for(
        size_pt,
        bold,
        center,
        color,
        line_height=line_height,
        font=font,
    )
    frame = Frame("visual-text", x, y, w, h, size_pt, bold=bold, center=center)
    inner = (
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(text)}</text:p>"
    )
    return _frame_box_xml(frame, inner)


def _trim_to_boxes(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Clip the centre-to-centre segment between two boxes to their edges.

    Returns the visible span ``(x1, y1, x2, y2)`` — where the line leaves the
    source card to where it meets the target card. Degenerate cases (concentric
    or overlapping boxes) fall back to the centres, which is what the renderer
    drew before and is never worse.
    """
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    cx1, cy1 = sx + sw / 2, sy + sh / 2
    cx2, cy2 = tx + tw / 2, ty + th / 2
    dx, dy = cx2 - cx1, cy2 - cy1
    span = (dx * dx + dy * dy) ** 0.5
    if span == 0:
        return cx1, cy1, cx2, cy2

    def _exit(w: float, h: float) -> float:
        """Distance from a box's centre to its edge along (dx, dy)."""
        limits = []
        if dx:
            limits.append((w / 2) / abs(dx / span))
        if dy:
            limits.append((h / 2) / abs(dy / span))
        return min(limits) if limits else 0.0

    start, end = _exit(sw, sh), _exit(tw, th)
    if start + end >= span:  # cards touch or overlap: nothing to draw between
        return cx1, cy1, cx2, cy2
    ux, uy = dx / span, dy / span
    return (
        cx1 + ux * start,
        cy1 + uy * start,
        cx2 - ux * end,
        cy2 - uy * end,
    )


def _stacked_card_h(
    title_h: float,
    detail_h: float,
    *,
    title_top: float,
    detail_gap: float,
    pad_bottom: float,
    min_h: float,
    max_h: float,
    available: float,
) -> tuple[float, float]:
    """Height of a badge → title → detail card, and where its detail starts.

    Both numbers derive from the same measured ``title_h``, so the title's box
    can never end above the text it was drawn to hold and the detail can never
    be pinned at an offset the title has already grown past. That single shared
    derivation is the fix for the defect where a two-line timeline heading
    printed straight through the caption beneath it.

    Returns ``(card_h, detail_y)``, both offsets from the card's top edge.
    """
    detail_y = title_top + title_h + (detail_gap if detail_h else 0.0)
    needed = detail_y + detail_h + pad_bottom
    return max(min_h, min(needed, max_h, available)), detail_y


def _visual_card_xml(
    x: float,
    y: float,
    w: float,
    h: float,
    theme: Theme,
    graphics: _GraphicStyles,
) -> str:
    """Render the shared rounded surface used by visual-layout cards."""
    style_name = graphics.name_for_fill(theme.surface)
    return _rect_xml(
        x,
        y,
        w,
        h,
        fill=theme.surface,
        corner_radius_cm=_VISUAL_CARD_CORNER,
        style_name=style_name,
    )


def _process_xml(
    steps: list[ProcessStep],
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render 2–5 connected process cards with numbered accent badges."""
    n = len(steps)
    gap = _VISUAL_GAP
    card_w = (area.w - gap * (n - 1)) / n

    text_w = card_w - 0.96
    title_pt = min(theme.body_pt, 17)
    detail_pt = min(theme.caption_pt, 13)
    title_h = max(
        estimate_height_cm(
            s.title, title_pt, _wrap_width(text_w), _PROCESS_TITLE_LINE_HEIGHT
        )
        for s in steps
    )
    detail_h = max(
        (
            estimate_height_cm(
                s.detail, detail_pt, _wrap_width(text_w), _PROCESS_DETAIL_LINE_HEIGHT
            )
            for s in steps
            if s.detail
        ),
        default=0.0,
    )
    card_h, detail_offset = _stacked_card_h(
        title_h,
        detail_h,
        title_top=_PROCESS_TITLE_TOP,
        detail_gap=_PROCESS_DETAIL_GAP,
        pad_bottom=_PROCESS_PAD_BOTTOM,
        min_h=_PROCESS_MIN_H,
        max_h=_PROCESS_MAX_H,
        available=area.h - 0.8,
    )
    card_y = area.y + (area.h - card_h) / 2
    badge_y = card_y + 0.55
    badge_center_y = badge_y + _PROCESS_BADGE_SIZE / 2

    parts: list[str] = []
    line_color = _blend(theme.accent, theme.bg, _VISUAL_LINE_BLEND)
    line_style = graphics.name_for_stroke(line_color, _VISUAL_LINE_WIDTH_PT)
    first_center = area.x + 0.48 + _PROCESS_BADGE_SIZE / 2
    last_card_x = area.x + (n - 1) * (card_w + gap)
    last_center = last_card_x + 0.48 + _PROCESS_BADGE_SIZE / 2
    parts.append(
        _line_xml(
            first_center,
            badge_center_y,
            last_center,
            badge_center_y,
            color=line_color,
            width_pt=_VISUAL_LINE_WIDTH_PT,
            style_name=line_style,
        )
    )

    badge_style = graphics.name_for_fill(theme.accent)
    for i, step in enumerate(steps):
        card_x = area.x + i * (card_w + gap)
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics))
        parts.append(
            _ellipse_xml(
                card_x + 0.48,
                badge_y,
                _PROCESS_BADGE_SIZE,
                _PROCESS_BADGE_SIZE,
                fill=theme.accent,
                style_name=badge_style,
            )
        )
        parts.append(
            _visual_text_xml(
                str(i + 1),
                card_x + 0.43,
                badge_y + 0.08,
                _PROCESS_BADGE_SIZE + 0.1,
                0.48,
                styles,
                size_pt=theme.caption_pt,
                color=theme.bg,
                bold=True,
                center=True,
            )
        )
        parts.append(
            _visual_text_xml(
                step.title,
                card_x + 0.48,
                card_y + _PROCESS_TITLE_TOP,
                text_w,
                title_h,
                styles,
                size_pt=title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
                line_height="115%",
            )
        )
        if step.detail:
            parts.append(
                _visual_text_xml(
                    step.detail,
                    card_x + 0.48,
                    card_y + detail_offset,
                    text_w,
                    max(0.4, card_h - detail_offset - _PROCESS_PAD_BOTTOM),
                    styles,
                    size_pt=detail_pt,
                    color=theme.muted,
                    line_height="135%",
                )
            )
    return "".join(parts)


def _timeline_xml(
    events: list[TimelineEvent],
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render a horizontal timeline with alternating event cards."""
    n = len(events)
    line_y = area.y + area.h / 2
    slot_w = area.w / n
    card_w = min(slot_w - 0.35, 5.4)

    # Size the row to the tallest event, then hang the cards off the axis by a
    # fixed stem: cards above it grow *upward* so a taller row can never reach
    # down and collide with the line it is supposed to hang from.
    text_w = card_w - 0.7
    title_pt = min(theme.body_pt, 17)
    detail_pt = min(theme.caption_pt, 12)
    title_h = max(
        estimate_height_cm(e.title, title_pt, _wrap_width(text_w)) for e in events
    )
    detail_h = max(
        (
            estimate_height_cm(
                e.detail, detail_pt, _wrap_width(text_w), _TIMELINE_DETAIL_LINE_HEIGHT
            )
            for e in events
            if e.detail
        ),
        default=0.0,
    )
    room = min(
        line_y - _TIMELINE_STEM - area.y,
        area.y + area.h - (line_y + _TIMELINE_STEM),
    ) - 0.1
    card_h, detail_offset = _stacked_card_h(
        title_h,
        detail_h,
        title_top=_TIMELINE_TITLE_TOP,
        detail_gap=_TIMELINE_DETAIL_GAP,
        pad_bottom=_TIMELINE_PAD_BOTTOM,
        min_h=_TIMELINE_MIN_H,
        max_h=_TIMELINE_MAX_H,
        available=max(_TIMELINE_MIN_H, room),
    )
    top_y = max(area.y + 0.1, line_y - _TIMELINE_STEM - card_h)
    bottom_y = line_y + _TIMELINE_STEM
    line_color = _blend(theme.accent, theme.bg, _VISUAL_LINE_BLEND)
    line_style = graphics.name_for_stroke(line_color, _VISUAL_LINE_WIDTH_PT)
    node_style = graphics.name_for_fill(theme.accent)

    first_x = area.x + slot_w / 2
    last_x = area.x + area.w - slot_w / 2
    parts: list[str] = [
        _line_xml(
            first_x,
            line_y,
            last_x,
            line_y,
            color=line_color,
            width_pt=_VISUAL_LINE_WIDTH_PT,
            style_name=line_style,
        )
    ]
    for i, event in enumerate(events):
        center_x = area.x + slot_w * (i + 0.5)
        card_x = center_x - card_w / 2
        card_y = top_y if i % 2 == 0 else bottom_y
        stem_y = card_y + card_h if i % 2 == 0 else card_y
        parts.append(
            _line_xml(
                center_x,
                line_y,
                center_x,
                stem_y,
                color=line_color,
                width_pt=_VISUAL_LINE_WIDTH_PT,
                style_name=line_style,
            )
        )
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics))
        parts.append(
            _ellipse_xml(
                center_x - _TIMELINE_NODE_SIZE / 2,
                line_y - _TIMELINE_NODE_SIZE / 2,
                _TIMELINE_NODE_SIZE,
                _TIMELINE_NODE_SIZE,
                fill=theme.accent,
                style_name=node_style,
            )
        )
        parts.append(
            _visual_text_xml(
                event.label,
                card_x + 0.35,
                card_y + 0.3,
                card_w - 0.7,
                0.55,
                styles,
                size_pt=theme.caption_pt,
                color=theme.accent,
                bold=True,
            )
        )
        parts.append(
            _visual_text_xml(
                event.title,
                card_x + 0.35,
                card_y + _TIMELINE_TITLE_TOP,
                text_w,
                title_h,
                styles,
                size_pt=title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
            )
        )
        if event.detail:
            parts.append(
                _visual_text_xml(
                    event.detail,
                    card_x + 0.35,
                    card_y + detail_offset,
                    text_w,
                    max(0.4, card_h - detail_offset - _TIMELINE_PAD_BOTTOM),
                    styles,
                    size_pt=detail_pt,
                    color=theme.muted,
                    line_height="130%",
                )
            )
    return "".join(parts)


def _metrics_xml(
    metrics: list[MetricSpec],
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render 2–4 metrics as a balanced one- or two-row card grid."""
    n = len(metrics)
    cols = 2 if n == 4 else n
    rows = 2 if n == 4 else 1
    gap = _VISUAL_GAP
    card_w = (area.w - gap * (cols - 1)) / cols
    raw_h = (area.h - gap * (rows - 1)) / rows
    card_h = min(raw_h, 7.2 if rows == 1 else raw_h)
    grid_h = card_h * rows + gap * (rows - 1)
    start_y = area.y + (area.h - grid_h) / 2
    accent_style = graphics.name_for_fill(theme.accent)

    parts: list[str] = []
    for i, metric in enumerate(metrics):
        row, col = divmod(i, cols)
        card_x = area.x + col * (card_w + gap)
        card_y = start_y + row * (card_h + gap)
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics))
        parts.append(
            _rect_xml(
                card_x,
                card_y,
                card_w,
                _METRIC_ACCENT_H,
                fill=theme.accent,
                style_name=accent_style,
            )
        )
        value_size = fact_font_size_pt(
            metric.value,
            card_w - 0.9,
            min(theme.display_pt, 44),
            theme.h1_pt,
        )
        parts.append(
            _visual_text_xml(
                metric.value,
                card_x + 0.45,
                card_y + 0.75,
                card_w - 0.9,
                1.65,
                styles,
                size_pt=value_size,
                color=theme.accent,
                bold=True,
                font=theme.font_display,
            )
        )
        parts.append(
            _visual_text_xml(
                metric.label,
                card_x + 0.45,
                card_y + 2.65,
                card_w - 0.9,
                0.8,
                styles,
                size_pt=min(theme.body_pt, 17),
                color=theme.text,
                bold=True,
            )
        )
        if metric.detail:
            parts.append(
                _visual_text_xml(
                    metric.detail,
                    card_x + 0.45,
                    card_y + 3.65,
                    card_w - 0.9,
                    card_h - 4.1,
                    styles,
                    size_pt=min(theme.caption_pt, 12),
                    color=theme.muted,
                    line_height="130%",
                )
            )
    return "".join(parts)


def _cards_xml(
    items: list,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render 2–4 parallel ideas as editorial cards rather than a bullet list.

    Cards are sized to what they actually hold. The height used to be a flat
    ``min(area.h, 6.8)``, which drew a 6.8cm box around a one-line idea and left
    its bottom 60% empty, while the title itself was pinned to a 1.65cm frame a
    three-line CJK heading overran. Both come from the same measurement now:
    :func:`estimate_height_cm` gives the wrapped title height, the card grows to
    fit it (plus any children), and the tallest card sets the height for the
    whole row — uniform, but never larger than its content needs.
    """
    n = len(items)
    cols = 2 if n in (2, 4) else 3
    rows = 2 if n == 4 else 1
    gap = _VISUAL_GAP
    card_w = (area.w - gap * (cols - 1)) / cols
    raw_h = (area.h - gap * (rows - 1)) / rows

    text_w = card_w - 2 * _CARD_PAD_X
    title_pt = min(theme.body_pt, 18)
    child_pt = min(theme.caption_pt, 12)
    title_top = _CARD_PAD_TOP + _CARD_BADGE_H + _CARD_BADGE_GAP

    normalised = [
        item if isinstance(item, tuple) else (str(item), []) for item in items
    ]
    # One title height for the row so every card's children start on the same
    # baseline; one card height so the row stays visually even.
    title_h = max(
        estimate_height_cm(text, title_pt, _wrap_width(text_w), _CARD_TITLE_LINE_HEIGHT)
        for text, _ in normalised
    )
    child_texts = {
        i: " · ".join(str(child) for child in children)
        for i, (_, children) in enumerate(normalised)
        if children
    }
    child_h = max(
        (
            estimate_height_cm(text, child_pt, _wrap_width(text_w), _CARD_CHILD_LINE_HEIGHT)
            for text in child_texts.values()
        ),
        default=0.0,
    )
    needed = title_top + title_h + _CARD_PAD_BOTTOM
    if child_texts:
        needed += _CARD_TITLE_GAP + child_h
    card_h = max(_CARD_MIN_H, min(needed, _CARD_MAX_H, raw_h))

    grid_h = rows * card_h + gap * (rows - 1)
    start_y = area.y + (area.h - grid_h) / 2
    child_y_offset = title_top + title_h + _CARD_TITLE_GAP

    parts: list[str] = []
    for i, (text, _children) in enumerate(normalised):
        row, col = divmod(i, cols)
        card_x = area.x + col * (card_w + gap)
        card_y = start_y + row * (card_h + gap)
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics))
        parts.append(
            _visual_text_xml(
                f"{i + 1:02d}",
                card_x + _CARD_PAD_X,
                card_y + _CARD_PAD_TOP,
                1.6,
                _CARD_BADGE_H,
                styles,
                size_pt=theme.caption_pt,
                color=theme.accent,
                bold=True,
            )
        )
        parts.append(
            _visual_text_xml(
                text,
                card_x + _CARD_PAD_X,
                card_y + title_top,
                text_w,
                title_h,
                styles,
                size_pt=title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
                line_height="120%",
            )
        )
        child_text = child_texts.get(i)
        if child_text:
            parts.append(
                _visual_text_xml(
                    child_text,
                    card_x + _CARD_PAD_X,
                    card_y + child_y_offset,
                    text_w,
                    max(0.4, card_h - child_y_offset - _CARD_PAD_BOTTOM),
                    styles,
                    size_pt=child_pt,
                    color=theme.muted,
                    line_height="135%",
                )
            )
    return "".join(parts)


def _closing_actions_xml(
    items: list,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render concrete closing actions as compact cards on the inverted page."""
    if not items:
        return ""

    count = len(items)
    gap = 0.45
    card_w = min(10.0, (area.w - gap * (count - 1)) / count)
    grid_w = card_w * count + gap * (count - 1)
    start_x = area.x + (area.w - grid_w) / 2
    card_h = min(3.35, area.h)
    start_y = area.y + (area.h - card_h) / 2

    parts: list[str] = []
    for index, item in enumerate(items):
        card_x = start_x + index * (card_w + gap)
        text, children = item if isinstance(item, tuple) else (str(item), [])
        parts.append(
            _visual_card_xml(card_x, start_y, card_w, card_h, theme, graphics)
        )
        parts.append(
            _visual_text_xml(
                f"{index + 1:02d}",
                card_x + 0.4,
                start_y + 0.38,
                card_w - 0.8,
                0.5,
                styles,
                size_pt=theme.caption_pt,
                color=theme.accent,
                bold=True,
            )
        )
        parts.append(
            _visual_text_xml(
                text,
                card_x + 0.4,
                start_y + 1.15,
                card_w - 0.8,
                1.25 if children else 1.65,
                styles,
                size_pt=min(theme.body_pt, 17),
                color=theme.text,
                bold=True,
                font=theme.font_display,
                line_height="120%",
            )
        )
        if children:
            parts.append(
                _visual_text_xml(
                    " · ".join(str(child) for child in children),
                    card_x + 0.4,
                    start_y + 2.35,
                    card_w - 0.8,
                    0.65,
                    styles,
                    size_pt=min(theme.caption_pt, 12),
                    color=theme.muted,
                    line_height="125%",
                )
            )
    return "".join(parts)


def _diagram_positions(
    diagram: DiagramSpec,
    area: Frame,
) -> dict[str, tuple[float, float, float, float]]:
    """Return deterministic node boxes for hub or hierarchy diagrams."""
    if diagram.kind == "hub":
        center_x = area.x + area.w / 2
        center_y = area.y + area.h / 2
        positions = {
            diagram.nodes[0].id: (
                center_x - _DIAGRAM_NODE_W / 2,
                center_y - _DIAGRAM_NODE_H / 2,
                _DIAGRAM_NODE_W,
                _DIAGRAM_NODE_H,
            )
        }
        outer = diagram.nodes[1:]
        count = len(outer)
        radius_x = min(8.2, (area.w - _DIAGRAM_NODE_W) / 2)
        radius_y = min(3.45, (area.h - _DIAGRAM_NODE_H) / 2)
        for i, node in enumerate(outer):
            angle = -math.pi / 2 + 2 * math.pi * i / count
            node_x = center_x + radius_x * math.cos(angle)
            node_y = center_y + radius_y * math.sin(angle)
            positions[node.id] = (
                node_x - _DIAGRAM_NODE_W / 2,
                node_y - _DIAGRAM_NODE_H / 2,
                _DIAGRAM_NODE_W,
                _DIAGRAM_NODE_H,
            )
        return positions

    node_ids = [node.id for node in diagram.nodes]
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in diagram.edges:
        indegree[edge.target] += 1
    roots = [node_id for node_id in node_ids if indegree[node_id] == 0]
    if not roots:
        roots = [node_ids[0]]
    levels = {root: 0 for root in roots}
    for _ in node_ids:
        changed = False
        for edge in diagram.edges:
            if edge.source in levels:
                candidate = levels[edge.source] + 1
                if candidate > levels.get(edge.target, -1):
                    levels[edge.target] = candidate
                    changed = True
        if not changed:
            break
    for node_id in node_ids:
        levels.setdefault(node_id, 0)

    max_level = max(levels.values())
    # A cycle can make the relaxation above grow indefinitely. Collapse such a
    # graph to a single balanced row rather than drawing outside the page.
    if max_level >= len(node_ids):
        levels = {node_id: 0 for node_id in node_ids}
        max_level = 0
    groups: dict[int, list[str]] = {}
    for node_id in node_ids:
        groups.setdefault(levels[node_id], []).append(node_id)

    level_count = max_level + 1
    vertical_gap = 0.45
    node_h = min(
        _DIAGRAM_NODE_H,
        (area.h - vertical_gap * (level_count - 1)) / level_count,
    )
    grid_h = node_h * level_count + vertical_gap * (level_count - 1)
    start_y = area.y + (area.h - grid_h) / 2
    positions: dict[str, tuple[float, float, float, float]] = {}
    for level in range(level_count):
        row = groups.get(level, [])
        if not row:
            continue
        horizontal_gap = _VISUAL_GAP
        node_w = min(
            _DIAGRAM_NODE_W,
            (area.w - horizontal_gap * (len(row) - 1)) / len(row),
        )
        row_w = node_w * len(row) + horizontal_gap * (len(row) - 1)
        start_x = area.x + (area.w - row_w) / 2
        for i, node_id in enumerate(row):
            positions[node_id] = (
                start_x + i * (node_w + horizontal_gap),
                start_y + level * (node_h + vertical_gap),
                node_w,
                node_h,
            )
    return positions


def _diagram_xml(
    diagram: DiagramSpec,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render an editable hub or hierarchy diagram with labelled relations."""
    positions = _diagram_positions(diagram, area)
    line_color = _blend(theme.accent, theme.bg, _DIAGRAM_LINE_BLEND)
    line_style = graphics.name_for_stroke(line_color, _VISUAL_LINE_WIDTH_PT)
    parts: list[str] = []

    label_pt = min(theme.caption_pt, 11)
    for edge in diagram.edges:
        sx, sy, sw, sh = positions[edge.source]
        tx, ty, tw, th = positions[edge.target]
        # Draw the connector between the two cards' edges rather than their
        # centres. The old version relied on the cards to mask the overshoot,
        # which left the label chip masking everything that remained.
        x1, y1, x2, y2 = _trim_to_boxes(
            (sx, sy, sw, sh), (tx, ty, tw, th)
        )
        parts.append(
            _line_xml(
                x1,
                y1,
                x2,
                y2,
                color=line_color,
                width_pt=_VISUAL_LINE_WIDTH_PT,
                style_name=line_style,
            )
        )
        if edge.label:
            label_h = 0.58
            label_w = (
                text_width_cm(edge.label, label_pt)
                + 2 * _FRAME_INSET_X
                + 2 * _EDGE_LABEL_PAD
            )
            span = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            # A chip may only sit *on* the connector when it still leaves a
            # readable run of line either side of it; otherwise it steps aside
            # and the connector stays whole.
            on_line = span >= label_w + 2 * _EDGE_LABEL_CLEARANCE
            if on_line:
                label_x = mid_x - label_w / 2
                label_y = mid_y - label_h / 2
                label_bg = graphics.name_for_fill(theme.bg)
                parts.append(
                    _rect_xml(
                        label_x,
                        label_y,
                        label_w,
                        label_h,
                        fill=theme.bg,
                        corner_radius_cm=0.08,
                        style_name=label_bg,
                    )
                )
            else:
                # Offset perpendicular to the edge, on the side away from the
                # hub, so the label reads beside its line instead of over it.
                nx, ny = -(y2 - y1), (x2 - x1)
                norm = (nx * nx + ny * ny) ** 0.5 or 1.0
                push = label_w / 2 + _EDGE_LABEL_CLEARANCE
                label_x = mid_x + nx / norm * push - label_w / 2
                label_y = mid_y + ny / norm * push - label_h / 2
            parts.append(
                _visual_text_xml(
                    edge.label,
                    label_x,
                    label_y + 0.03,
                    label_w,
                    label_h,
                    styles,
                    size_pt=label_pt,
                    color=theme.muted,
                    bold=True,
                    center=True,
                )
            )

    accent_style = graphics.name_for_fill(theme.accent)
    surface_style = graphics.name_for_fill(theme.surface)
    for i, node in enumerate(diagram.nodes):
        x, y, w, h = positions[node.id]
        emphasized = node.emphasis or (diagram.kind == "hub" and i == 0)
        fill = theme.accent if emphasized else theme.surface
        card_style = accent_style if emphasized else surface_style
        parts.append(
            _rect_xml(
                x,
                y,
                w,
                h,
                fill=fill,
                corner_radius_cm=_VISUAL_CARD_CORNER,
                style_name=card_style,
            )
        )
        text_color = theme.bg if emphasized else theme.text
        detail_color = theme.bg if emphasized else theme.muted
        title_size = fact_font_size_pt(
            node.title,
            max(w - 1.5, 0.5),
            min(theme.body_pt, 17),
            min(theme.caption_pt, 12),
        )
        title_text_h = estimate_height_cm(
            node.title,
            title_size,
            max(w - 0.7, 0.5),
            line_height=1.15,
        )
        title_h = min(max(title_text_h + 0.1, 0.62), max(h - 0.75, 0.62))
        title_y = y + 0.28
        parts.append(
            _visual_text_xml(
                node.title,
                x + 0.35,
                title_y,
                w - 0.7,
                title_h,
                styles,
                size_pt=title_size,
                color=text_color,
                bold=True,
                center=True,
                font=theme.font_display,
            )
        )
        detail_y = title_y + title_h + 0.12
        detail_h = y + h - 0.22 - detail_y
        if node.detail and detail_h >= 0.45:
            parts.append(
                _visual_text_xml(
                    node.detail,
                    x + 0.35,
                    detail_y,
                    w - 0.7,
                    detail_h,
                    styles,
                    size_pt=min(theme.caption_pt, 11),
                    color=detail_color,
                    center=True,
                    line_height="125%",
                )
            )
    return "".join(parts)


def _sources_xml(
    sources,
    theme: Theme,
    styles: _ParagraphStyles,
    spans: _SpanStyles,
) -> str:
    """Render concise, clickable source references above the master footer."""
    paragraph_style = styles.name_for(
        _SOURCE_FRAME.size_pt,
        False,
        False,
        theme.muted,
    )
    label_style = spans.name_for(_SOURCE_FRAME.size_pt, True, theme.accent)
    source_style = spans.name_for(_SOURCE_FRAME.size_pt, False, theme.muted)
    body = (
        f'<text:span text:style-name="{_attr(label_style)}">來源</text:span>'
        f'<text:span text:style-name="{_attr(source_style)}">  </text:span>'
    )
    rendered = []
    for source in sources:
        label = escape(source.label)
        if source.url:
            rendered.append(
                f'<text:a text:style-name="{_attr(source_style)}"'
                f' xlink:type="simple" xlink:href="{_attr(source.url)}">'
                f"{label}</text:a>"
            )
        else:
            rendered.append(
                f'<text:span text:style-name="{_attr(source_style)}">'
                f"{label}</text:span>"
            )
    separator = (
        f'<text:span text:style-name="{_attr(source_style)}"> · </text:span>'
    )
    body += separator.join(rendered)
    inner = (
        f'<text:p text:style-name="{_attr(paragraph_style)}">{body}</text:p>'
    )
    return _frame_box_xml(_SOURCE_FRAME, inner)


# ---------------------------------------------------------------------------
# Semantic list rendering (Task 13.2)
# ---------------------------------------------------------------------------

# A list item is either plain ``str`` (a leaf bullet) or ``(text, children)``
# where ``children`` is a list of further items — this is the internal contract
# Task 14.1's nested ``BulletItem`` IR will lower onto. ``Slide.bullets`` is
# ``list[str]`` today and passes straight through as leaf items.
ListItem = "str | tuple[str, list]"


def _list_style_xml(name: str, theme: Theme) -> str:
    """Build a ``<text:list-style>`` with an accent-coloured bullet per level.

    Every level uses ``theme.bullet_char``; the bullet glyph is tinted with the
    accent colour via a ``<style:text-properties>`` child, and each level's
    indent grows through ``<style:list-level-properties>`` so nested items sit
    further in.
    """
    levels = "".join(
        f'<text:list-level-style-bullet text:level="{level}"'
        f' text:bullet-char="{_attr(theme.bullet_char)}">'
        f"<style:list-level-properties"
        f' text:space-before="{_cm(space_before)}"'
        f' text:min-label-width="{_cm(min_label)}"/>'
        f'<style:text-properties fo:color="{_attr(theme.accent)}"/>'
        f"</text:list-level-style-bullet>"
        for level, space_before, min_label in _LIST_LEVELS
    )
    return f'<text:list-style style:name="{_attr(name)}">{levels}</text:list-style>'


def _list_xml(
    items,
    styles: _ParagraphStyles,
    *,
    size_pt: int,
    color: str,
    style_name: str | None = None,
) -> str:
    """Build a ``<text:list>`` for ``items`` (see :data:`ListItem`).

    Only the outermost list carries ``style_name`` (``L1``); nested lists omit
    it so LibreOffice derives their level — and thus their level-2 bullet and
    indent — from the enclosing list-style. Each item's text lives in a
    ``<text:p>`` whose paragraph style carries the loose bullet typography
    (line-height + bottom margin); children recurse into a nested ``<text:list>``
    inside the same ``<text:list-item>``.
    """
    li_parts: list[str] = []
    for item in items:
        if isinstance(item, tuple):
            text, children = item
        else:
            text, children = item, ()
        pstyle = styles.name_for(
            size_pt,
            False,
            False,
            color,
            line_height=_BULLET_LINE_HEIGHT,
            margin_bottom=_BULLET_MARGIN_BOTTOM,
        )
        para = f'<text:p text:style-name="{_attr(pstyle)}">{escape(text)}</text:p>'
        nested = (
            _list_xml(children, styles, size_pt=size_pt, color=color)
            if children
            else ""
        )
        li_parts.append(f"<text:list-item>{para}{nested}</text:list-item>")
    style_attr = f' text:style-name="{_attr(style_name)}"' if style_name else ""
    return f"<text:list{style_attr}>{''.join(li_parts)}</text:list>"


def _kicker_paragraph_xml(text: str, styles: _ParagraphStyles, theme: Theme) -> str:
    """Build a wide-tracked "kicker" ``<text:p>`` (caption-size, accent, spaced).

    Not wired to any layout yet — provided for Tasks 13.3/14.1 to place an
    eyebrow/kicker line above a heading. The paragraph style carries
    ``fo:letter-spacing`` so the label reads as spaced small caps.
    """
    style_name = styles.name_for(
        theme.caption_pt,
        True,
        False,
        theme.accent,
        letter_spacing=_KICKER_LETTER_SPACING,
    )
    return f'<text:p text:style-name="{_attr(style_name)}">{escape(text)}</text:p>'


def _notes_xml(notes: str, style_name: str) -> str:
    """Build the ``presentation:notes`` block for a slide's speaker notes."""
    return (
        "<presentation:notes>"
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="2cm" svg:y="2cm" svg:width="24cm" svg:height="11cm">'
        f'<draw:text-box><text:p text:style-name="{_attr(style_name)}">'
        f"{escape(notes)}</text:p></draw:text-box>"
        f"</draw:frame>"
        "</presentation:notes>"
    )


def _section_number_xml(
    ordinal: int, theme: Theme, styles: _ParagraphStyles
) -> str:
    """Build the giant faded chapter-number watermark for a section page.

    ``ordinal`` is the section's 1-based position among section slides, rendered
    zero-padded (``01``, ``02`` …). The colour is :func:`_blend`\\ ed 15% from
    accent toward the theme bg so the number reads as a low-contrast watermark on
    the accent fill (see :data:`_SECTION_NUMBER_BLEND`). Emitted *before* the
    title so the title paints on top.
    """
    color = _blend(theme.accent, theme.bg, _SECTION_NUMBER_BLEND)
    style_name = styles.name_for(_SECTION_NUMBER_PT, True, True, color)
    x, y, w, h = _SECTION_NUMBER_BOX
    frame = Frame("section-number", x, y, w, h, _SECTION_NUMBER_PT, bold=True, center=True)
    para = (
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(f'{ordinal:02d}')}</text:p>"
    )
    return _frame_box_xml(frame, para)


def _quote_mark_xml(theme: Theme, styles: _ParagraphStyles) -> str:
    """Build the quote layout's big decorative quotation-mark watermark.

    A large glyph tinted :data:`_QUOTE_MARK_BLEND` of the way from accent toward
    the bg — faint enough to read as decoration behind the quote text. Emitted
    before the quote so the text paints on top. Deterministic.
    """
    color = _blend(theme.accent, theme.bg, _QUOTE_MARK_BLEND)
    style_name = styles.name_for(_QUOTE_MARK_PT, True, False, color)
    x, y, w, h = _QUOTE_MARK_BOX
    frame = Frame("quote-mark", x, y, w, h, _QUOTE_MARK_PT, bold=True)
    para = (
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(_QUOTE_MARK_GLYPH)}</text:p>"
    )
    return _frame_box_xml(frame, para)


def _numbered_items_xml(
    items: list,
    frame: Frame,
    theme: Theme,
    para_styles: _ParagraphStyles,
    span_styles: _SpanStyles,
) -> str:
    """Build the agenda layout's numbered items (accent-tinted 01/02… numbers).

    Explicit numbered paragraphs (not an ODF list): each item is one ``text:p``
    at ``body_pt`` whose leading ``<text:span>`` carries a zero-padded ordinal in
    the accent colour, followed by the item text in the body colour. Zero-padding
    ("01") is why this is drawn by hand — ``style:num-format`` cannot pad. Loose
    bullet typography (line-height + bottom margin) spaces the rows.
    """
    num_style = span_styles.name_for(theme.body_pt, True, theme.accent)
    text_style = para_styles.name_for(
        theme.body_pt,
        False,
        False,
        theme.text_color,
        line_height=_BULLET_LINE_HEIGHT,
        margin_bottom=_BULLET_MARGIN_BOTTOM,
    )
    paras = []
    for i, item in enumerate(items, start=1):
        paras.append(
            f'<text:p text:style-name="{_attr(text_style)}">'
            f'<text:span text:style-name="{_attr(num_style)}">'
            f"{escape(f'{i:02d}')}</text:span>"
            f"{escape('  ' + _line_text(item))}</text:p>"
        )
    return _frame_box_xml(frame, "".join(paras))


def _page_xml(
    index: int,
    slide: Slide,
    theme: Theme,
    styles: _ParagraphStyles,
    graphics: _GraphicStyles,
    spans: _SpanStyles,
    section_ordinal: int | None,
    resolved_image: tuple[str, AssetBlob] | None = None,
) -> str:
    """Build one ``draw:page`` for a slide, registering its paragraph styles.

    Master page + drawing-page style are chosen by layout: :data:`PLAIN_LAYOUTS`
    use the furniture-free "Plain" master, and :data:`_ACCENT_BG_LAYOUTS` swap in
    the full-accent drawing-page style. Content-page titles gain a vertical accent
    bar (and an optional ``kicker`` eyebrow); ``fact`` text is up-sized to display
    + accent; the Task 14.1 page-role layouts (quote/agenda/comparison/chart/
    closing) are dispatched by frame role below.
    """
    layout = slide.layout
    parts: list[str] = []
    # Pre-content decorations, emitted first so they sit behind the text.
    if layout in _DECO_LAYOUTS:
        parts.append(_deco_frame_xml())
    # Giant chapter-number watermark: section pages only (closing must neither
    # display nor consume an ordinal).
    if layout == "section" and section_ordinal is not None:
        parts.append(_section_number_xml(section_ordinal, theme, styles))
    if layout == "quote":
        parts.append(_quote_mark_xml(theme, styles))

    # big-fact fit-to-width (deterministic engine guarantee, not prompt advice):
    # shrink the fact toward one line (floor = theme.h1_pt) and, if it still
    # overruns its frame, push the caption down by the fact's real estimated
    # height so the two boxes can never intersect. Both quantities come from the
    # shared textmetrics model so the size drawn == the size the budget gate
    # charges. Computed once here; consumed in the fact / caption branches below.
    bigfact_fact_pt: int | None = None
    bigfact_caption_y: float | None = None
    if layout == "big-fact" and slide.fact:
        fact_frame, caption_frame = LAYOUTS["big-fact"]  # (fact, bullets)
        bigfact_fact_pt = fact_font_size_pt(
            slide.fact, fact_frame.w, theme.display_pt, theme.h1_pt
        )
        fact_h = estimate_height_cm(slide.fact, bigfact_fact_pt, fact_frame.w)
        gap = caption_frame.y - (fact_frame.y + fact_frame.h)
        bigfact_caption_y = fact_frame.y + max(fact_frame.h, fact_h) + gap

    for frame in LAYOUTS[layout]:
        role = frame.role

        # chart-area draws a ChartSpec (shapes + labels), not text lines.
        if role == "chart-area":
            if slide.chart is not None:
                parts.append(
                    _chart_xml(
                        slide.chart, frame.x, frame.y, frame.w, frame.h,
                        theme, graphics, styles,
                    )
                )
            continue
        if role == "process-area":
            parts.append(_process_xml(slide.steps, frame, theme, graphics, styles))
            continue
        if role == "timeline-area":
            parts.append(_timeline_xml(slide.events, frame, theme, graphics, styles))
            continue
        if role == "metrics-area":
            parts.append(_metrics_xml(slide.metrics, frame, theme, graphics, styles))
            continue
        if role == "cards-area":
            parts.append(
                _cards_xml(
                    _coerce_bullet_items(slide.bullets),
                    frame,
                    theme,
                    graphics,
                    styles,
                )
            )
            continue
        if role == "closing-actions":
            parts.append(
                _closing_actions_xml(
                    _coerce_bullet_items(slide.bullets),
                    frame,
                    theme,
                    graphics,
                    styles,
                )
            )
            continue
        if role == "diagram-area":
            if slide.diagram is not None:
                parts.append(
                    _diagram_xml(slide.diagram, frame, theme, graphics, styles)
                )
            continue
        if role == "image-area":
            parts.append(
                _image_area_xml(
                    slide, frame, theme, graphics, styles, resolved_image
                )
            )
            continue

        lines = _role_lines(slide, role)
        if not lines:
            continue

        # Column frames (two-col + comparison left/right) get a rounded surface
        # card behind them — emitted before the text frame so document order
        # keeps the text on top.
        if role in _CARD_ROLES:
            card_style = graphics.name_for_fill(theme.surface)
            parts.append(
                _rect_xml(
                    frame.x - _CARD_PAD,
                    frame.y - _CARD_PAD,
                    frame.w + 2 * _CARD_PAD,
                    frame.h + 2 * _CARD_PAD,
                    fill=theme.surface,
                    corner_radius_cm=_CARD_CORNER,
                    style_name=card_style,
                )
            )

        # closing: inverted message (title + optional subtitle) in the bg colour.
        if role == "message":
            if layout == "closing" and not slide.bullets:
                frame = replace(frame, y=6)
            msg_style = styles.name_for(
                theme.h1_pt,
                frame.bold,
                frame.center,
                theme.bg,
                font=theme.font_display,
            )
            inner = (
                f'<text:p text:style-name="{_attr(msg_style)}">'
                f"{escape(slide.title)}</text:p>"
            )
            if slide.subtitle:
                sub_style = styles.name_for(
                    theme.body_pt, False, frame.center, theme.bg
                )
                inner += (
                    f'<text:p text:style-name="{_attr(sub_style)}">'
                    f"{escape(slide.subtitle)}</text:p>"
                )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # agenda: explicit numbered items with accent-tinted 01/02… numbers.
        if role == "items":
            parts.append(_numbered_items_xml(lines, frame, theme, styles, spans))
            continue

        # chart insights: a caption-size bullet list.
        if role == "insights":
            inner = _list_xml(
                lines,
                styles,
                size_pt=theme.caption_pt,
                color=theme.text_color,
                style_name=_LIST_STYLE_NAME,
            )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # comparison columns: first item is a bold accent column header, the rest
        # are normal bullets (contract decided by the controller).
        if layout == "comparison" and role in ("left", "right"):
            header_style = styles.name_for(theme.body_pt, True, False, theme.accent)
            inner = (
                f'<text:p text:style-name="{_attr(header_style)}">'
                f"{escape(_line_text(lines[0]))}</text:p>"
            )
            rest = lines[1:]
            if rest:
                inner += _list_xml(
                    rest,
                    styles,
                    size_pt=theme.body_pt,
                    color=theme.text_color,
                    style_name=_LIST_STYLE_NAME,
                )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # Content-page title: optional vertical accent bar + optional kicker
        # eyebrow (accent, letter-spaced) rendered above the title text.
        if role == "title":
            if layout not in PLAIN_LAYOUTS:
                bar_style = graphics.name_for_fill(theme.accent)
                parts.append(
                    _rect_xml(
                        frame.x, frame.y, _ACCENT_BAR_W, frame.h,
                        fill=theme.accent, style_name=bar_style,
                    )
                )
            # Section/closing titles invert onto the accent background.
            color = theme.bg if layout in _ACCENT_BG_LAYOUTS else theme.title_color
            title_style = styles.name_for(
                frame.size_pt,
                frame.bold,
                frame.center,
                color,
                font=theme.font_display,
            )
            inner = ""
            if slide.kicker and layout not in PLAIN_LAYOUTS:
                inner += _kicker_paragraph_xml(slide.kicker, styles, theme)
            inner += "".join(
                f'<text:p text:style-name="{_attr(title_style)}">'
                f"{escape(_line_text(line))}</text:p>"
                for line in lines
            )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # Standard semantic list (title-content bullets, two-col columns).
        if role in LIST_ROLES and not frame.center:
            inner = _list_xml(
                lines,
                styles,
                size_pt=frame.size_pt,
                color=theme.text_color,
                style_name=_LIST_STYLE_NAME,
            )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # Bare centred / single-line text (subtitle, fact, quote, attribution,
        # big-fact caption bullets).
        size_pt = frame.size_pt
        if role == "fact":
            # fit-to-width: draw the fact at the largest size that keeps it on
            # one line (floor = h1_pt), computed above.
            size_pt = (
                bigfact_fact_pt if bigfact_fact_pt is not None else theme.display_pt
            )
            color = theme.accent
        elif role == "quote":
            size_pt = theme.h1_pt
            color = theme.text_color
        elif role == "attribution":
            size_pt = theme.caption_pt
            color = theme.muted
        elif role == "bullets" and layout == "big-fact":
            color = theme.muted
            # caption never overlaps: drop it below the fact's real height.
            if bigfact_caption_y is not None:
                frame = replace(frame, y=bigfact_caption_y)
        else:
            color = theme.text_color

        display_font = (
            theme.font_display if role in {"fact", "quote"} else theme.font_body
        )
        style_name = styles.name_for(
            size_pt,
            frame.bold,
            frame.center,
            color,
            font=display_font,
        )
        parts.append(_frame_xml(frame, lines, style_name))

    if slide.sources:
        parts.append(_sources_xml(slide.sources, theme, styles, spans))

    if slide.notes:
        notes_style = styles.name_for(_NOTES_SIZE_PT, False, False, theme.text_color)
        parts.append(_notes_xml(slide.notes, notes_style))

    master = _PLAIN_MASTER_NAME if layout in PLAIN_LAYOUTS else _MASTER_PAGE_NAME
    dp_style = (
        _SECTION_DRAWING_PAGE_STYLE
        if layout in _ACCENT_BG_LAYOUTS
        else _DRAWING_PAGE_STYLE
    )
    return (
        f'<draw:page draw:name="page{index}"'
        f' draw:style-name="{dp_style}"'
        f' draw:master-page-name="{master}">'
        f"{''.join(parts)}"
        f"</draw:page>"
    )


# ---------------------------------------------------------------------------
# Pure builders for the three XML parts
# ---------------------------------------------------------------------------


def _drawing_page_style_xml(
    name: str, fill_attrs: str, *, display_page_number: bool = False
) -> str:
    """Build a ``style:family="drawing-page"`` automatic style element."""
    extra = (
        ' presentation:display-page-number="true"' if display_page_number else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="drawing-page">'
        f"<style:drawing-page-properties {fill_attrs}{extra}/>"
        f"</style:style>"
    )


def build_content_xml(
    p: Presentation,
    theme: Theme,
    resolved_images: Mapping[int, tuple[str, AssetBlob]] | None = None,
) -> str:
    """Build ``content.xml`` for a presentation. Pure function."""
    styles = _ParagraphStyles(theme.font)
    # Graphic styles for shapes drawn on pages (accent bars, etc.).
    graphics = _GraphicStyles()
    # Text-span styles for inline accent runs (agenda's 01/02… numbers).
    spans = _SpanStyles(theme.font)

    # Only "section" slides carry an auto-computed 1-based ordinal (the giant
    # chapter number); closing pages are inverted too but must not consume or
    # display an ordinal, so they are excluded here.
    section_ordinal: dict[int, int] = {}
    for i, slide in enumerate(p.slides):
        if slide.layout == "section":
            section_ordinal[i] = len(section_ordinal) + 1

    # Build pages first so every referenced paragraph/graphic/span style is
    # registered.
    pages = "".join(
        _page_xml(
            i,
            slide,
            theme,
            styles,
            graphics,
            spans,
            section_ordinal.get(i),
            (resolved_images or {}).get(i),
        )
        for i, slide in enumerate(p.slides)
    )

    # Default content drawing-page carries the bg fill and surfaces the master's
    # page-number placeholder. The accent (inverted) drawing-page is emitted
    # whenever any inverted page (section OR closing) needs it — decoupled from
    # the section-ordinal count so a closing-only deck still gets its accent fill.
    drawing_pages = _drawing_page_style_xml(
        _DRAWING_PAGE_STYLE, _page_fill_attrs(theme), display_page_number=True
    )
    if any(slide.layout in _ACCENT_BG_LAYOUTS for slide in p.slides):
        drawing_pages += _drawing_page_style_xml(
            _SECTION_DRAWING_PAGE_STYLE,
            f'draw:fill="solid" draw:fill-color="{_attr(_section_fill(theme))}"',
        )

    automatic_styles = (
        "<office:automatic-styles>"
        f"{styles.xml()}"
        f"{graphics.xml()}"
        f"{spans.xml()}"
        f"{_list_style_xml(_LIST_STYLE_NAME, theme)}"
        f"{drawing_pages}"
        f'<style:style style:name="{_GRAPHIC_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="none" draw:stroke="none"/>'
        f"</style:style>"
        "</office:automatic-styles>"
    )

    return (
        f"{_XML_DECL}"
        f"<office:document-content {_ns_decls(_CONTENT_NS)}"
        f' office:version="1.2">'
        f"{automatic_styles}"
        f"<office:body><office:presentation>"
        f"{pages}"
        f"</office:presentation></office:body>"
        f"</office:document-content>"
    )


def _furniture_para_xml(
    name: str,
    font: str,
    size_pt: int,
    color: str,
    *,
    align: str,
    letter_spacing: str | None = None,
) -> str:
    """Build a master-page furniture paragraph style (three-track CJK sizing)."""
    spacing = (
        f' fo:letter-spacing="{_attr(letter_spacing)}"'
        if letter_spacing is not None
        else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="paragraph">'
        f'<style:paragraph-properties fo:text-align="{align}"/>'
        f"<style:text-properties{_font_size_attrs(size_pt)}"
        f' fo:color="{_attr(color)}"{spacing}'
        f' style:font-name="{_attr(font)}" style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
    )


def _furniture_styles_xml(theme: Theme) -> str:
    """Automatic styles the "Standard" master's furniture frames/line reference."""
    line = (
        f'<style:style style:name="{_MP_LINE_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="none" draw:stroke="solid"'
        f' svg:stroke-color="{_attr(theme.muted)}"'
        f' svg:stroke-width="{_pt(_FOOTER_LINE_WIDTH_PT)}"/>'
        f"</style:style>"
    )
    frame = (
        f'<style:style style:name="{_MP_FRAME_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="none" draw:stroke="none"/>'
        f"</style:style>"
    )
    page_number = _furniture_para_xml(
        _MP_PAGENUM_STYLE, theme.font, theme.caption_pt, theme.muted, align="end"
    )
    kicker = _furniture_para_xml(
        _MP_KICKER_STYLE,
        theme.font,
        theme.caption_pt,
        theme.muted,
        align="start",
        letter_spacing=_KICKER_LETTER_SPACING,
    )
    return line + frame + page_number + kicker


def _master_furniture_xml(theme: Theme, title: str) -> str:
    """Standard master-page furniture: footer line, page number, kicker title.

    The kicker carries the presentation title (a per-page kicker field arrives in
    Task 14.1). The page-number frame is a ``presentation:class="page-number"``
    placeholder holding ``<text:page-number/>``; content pages surface it via the
    drawing-page ``presentation:display-page-number`` flag.
    """
    footer_line = (
        f'<draw:line draw:style-name="{_MP_LINE_STYLE}"'
        f' draw:layer="{_FURNITURE_LAYER}"'
        f' svg:x1="{_cm(_FOOTER_LINE_X1)}" svg:y1="{_cm(_FOOTER_LINE_Y)}"'
        f' svg:x2="{_cm(_FOOTER_LINE_X2)}" svg:y2="{_cm(_FOOTER_LINE_Y)}"/>'
    )
    page_number = (
        f'<draw:frame draw:style-name="{_MP_FRAME_STYLE}"'
        f' draw:layer="{_FURNITURE_LAYER}"'
        f' presentation:class="page-number"'
        f' svg:x="{_cm(_PAGENUM_X)}" svg:y="{_cm(_FURNITURE_Y)}"'
        f' svg:width="{_cm(_PAGENUM_W)}" svg:height="{_cm(_FURNITURE_H)}">'
        f'<draw:text-box><text:p text:style-name="{_MP_PAGENUM_STYLE}">'
        f"<text:page-number/></text:p></draw:text-box></draw:frame>"
    )
    kicker = (
        f'<draw:frame draw:style-name="{_MP_FRAME_STYLE}"'
        f' draw:layer="{_FURNITURE_LAYER}"'
        f' svg:x="{_cm(_FOOTER_LINE_X1)}" svg:y="{_cm(_FURNITURE_Y)}"'
        f' svg:width="{_cm(_KICKER_W)}" svg:height="{_cm(_FURNITURE_H)}">'
        f'<draw:text-box><text:p text:style-name="{_MP_KICKER_STYLE}">'
        f"{escape(title)}</text:p></draw:text-box></draw:frame>"
    )
    return footer_line + page_number + kicker


def build_styles_xml(theme: Theme, title: str = "") -> str:
    """Build ``styles.xml`` (page layout + master pages + bg). Pure function.

    Two master pages are defined: "Standard" (footer line, page-number frame,
    kicker) for content pages, and "Plain" (no furniture) for title/section
    pages. ``title`` supplies the kicker text. The dark preset gets a subtle
    two-stop linear gradient background; light presets keep a flat solid fill.
    """
    font_family = f"'{theme.font}','微軟正黑體',sans-serif"
    # office:styles carries the layer-set (required for master furniture to
    # render — see _LAYER_SET_XML) plus, for the dark preset, the bg gradient.
    gradient = ""
    if _uses_gradient_bg(theme):
        gradient = (
            f'<draw:gradient draw:name="{_attr(_GRADIENT_NAME)}"'
            f' draw:display-name="ODForge Background" draw:style="linear"'
            f' draw:start-color="{_attr(theme.bg)}"'
            f' draw:end-color="{_attr(_lighten(theme.bg, 0.16))}"'
            f' draw:start-intensity="100%" draw:end-intensity="100%"'
            f' draw:angle="450" draw:border="0%"/>'
        )
    office_styles = f"<office:styles>{_LAYER_SET_XML}{gradient}</office:styles>"
    page_fill = _page_fill_attrs(theme)
    return (
        f"{_XML_DECL}"
        f"<office:document-styles {_ns_decls(_STYLES_NS)}"
        f' office:version="1.2">'
        f"<office:font-face-decls>"
        f'<style:font-face style:name="{_attr(theme.font)}"'
        f' svg:font-family="{_attr(font_family)}"/>'
        f"</office:font-face-decls>"
        f"{office_styles}"
        f"<office:automatic-styles>"
        f'<style:page-layout style:name="PM1">'
        f'<style:page-layout-properties fo:page-width="{_cm(PAGE_W)}"'
        f' fo:page-height="{_cm(PAGE_H)}" style:print-orientation="landscape"'
        f' fo:margin-top="0cm" fo:margin-bottom="0cm"'
        f' fo:margin-left="0cm" fo:margin-right="0cm"/>'
        f"</style:page-layout>"
        f'<style:style style:name="{_DRAWING_PAGE_STYLE}"'
        f' style:family="drawing-page">'
        f"<style:drawing-page-properties {page_fill}/>"
        f"</style:style>"
        f"{_furniture_styles_xml(theme)}"
        f"</office:automatic-styles>"
        f"<office:master-styles>"
        f'<style:master-page style:name="{_MASTER_PAGE_NAME}"'
        f' style:page-layout-name="PM1"'
        f' draw:style-name="{_DRAWING_PAGE_STYLE}">'
        f"{_master_furniture_xml(theme, title)}"
        f"</style:master-page>"
        f'<style:master-page style:name="{_PLAIN_MASTER_NAME}"'
        f' style:page-layout-name="PM1"'
        f' draw:style-name="{_DRAWING_PAGE_STYLE}"/>'
        f"</office:master-styles>"
        f"</office:document-styles>"
    )


def build_meta_xml(title: str) -> str:
    """Build ``meta.xml`` carrying the document title and generator. Pure."""
    return (
        f"{_XML_DECL}"
        f"<office:document-meta {_ns_decls(_META_NS)} office:version=\"1.2\">"
        f"<office:meta>"
        f"<meta:generator>ODForge</meta:generator>"
        f"<dc:title>{escape(title)}</dc:title>"
        f"</office:meta>"
        f"</office:document-meta>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_odp(
    p: Presentation,
    out_path: Path,
    *,
    assets: Mapping[str, AssetInput] | None = None,
    image_provider: ImageProvider | None = None,
) -> Path:
    """Render presentation ``p`` to a native ``.odp`` file at ``out_path``."""
    # resolve_design returns the preset THEMES[p.theme] for v1 decks (design is
    # None) and a custom Theme built from the per-deck DesignSpec otherwise, so a
    # deck carrying design tokens actually renders with them.
    theme = resolve_design(p)
    normalized_assets = normalize_assets(assets)
    provider = image_provider
    if provider is None and any(
        slide.image is not None and bool(slide.image.prompt)
        for slide in p.slides
    ):
        provider = configured_image_provider()

    # Materialize provider output once. The slide is rewritten to asset:// and,
    # when the caller supplied a mutable asset map (Web/CLI do), the bytes are
    # cached there for deterministic QA re-renders and single-page regeneration.
    if provider is not None:
        for slide in p.slides:
            image = slide.image
            if image is None or image.src or not image.prompt:
                continue
            try:
                generated = provider.generate(image.prompt)
            except MediaError:
                continue
            digest = hashlib.sha256(generated.data).hexdigest()[:16]
            asset_id = f"generated-{digest}"
            normalized_assets[asset_id] = generated
            if isinstance(assets, MutableMapping):
                assets[asset_id] = generated
            slide.image = image.model_copy(update={"src": f"asset://{asset_id}"})

    resolved_images: dict[int, tuple[str, AssetBlob]] = {}
    image_parts: dict[str, bytes] = {}
    for index, slide in enumerate(p.slides):
        if slide.image is None:
            continue
        try:
            blob = resolve_image(slide.image, normalized_assets)
        except MediaError:
            # Unsafe/unavailable model-proposed sources render as a visible
            # placeholder. Explicit app-owned uploads are validated earlier.
            blob = None
        if blob is None:
            continue
        digest = hashlib.sha256(blob.data).hexdigest()[:16]
        href = f"Pictures/image-{digest}{blob.extension}"
        image_parts[href] = blob.data
        resolved_images[index] = (href, blob)

    parts: dict[str, str | bytes] = {
        "content.xml": build_content_xml(p, theme, resolved_images),
        "styles.xml": build_styles_xml(theme, p.title),
        "meta.xml": build_meta_xml(p.title),
    }
    parts.update(image_parts)
    # Only ship the decoration SVG when a title page actually references it —
    # keeps non-title decks free of an unused Pictures part. build_content_xml
    # emits the <draw:image> under the same title-layout condition.
    if any(slide.layout in _DECO_LAYOUTS for slide in p.slides):
        parts[_DECO_HREF] = _svg_decoration(theme)
    return write_odf_package(Path(out_path), ODP_MIMETYPE, parts)
