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

from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from odforge.ir import Presentation, Slide
from odforge.package import ODP_MIMETYPE, write_odf_package
from odforge.themes import LAYOUTS, PAGE_H, PAGE_W, THEMES, Frame, Theme

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
# Layouts that render "bare" (no page-number/footer/kicker furniture): the
# opening title and the full-accent section divider. "closing" (Task 14.1)
# joins both these sets — keeping them as small frozensets makes that a
# one-word edit with no branching to touch.
_PLAIN_LAYOUTS = frozenset({"title", "section"})
# Layouts painted with a full-bleed accent background (inverted pages).
_ACCENT_BG_LAYOUTS = frozenset({"section"})

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
# Frame roles whose (left-aligned) multi-item content renders as a bullet list.
# Centred frames (e.g. the big-fact caption) stay bare centred paragraphs.
_LIST_ROLES = frozenset({"bullets", "left", "right"})
# Per bullet level: (text:level, space-before cm, min-label-width cm). Level 2
# indents deeper so nested items read as a sub-list.
_LIST_LEVELS: tuple[tuple[int, float, float], ...] = (
    (1, 0.6, 0.6),
    (2, 1.4, 0.6),
)


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
    ) -> str:
        key = (size_pt, bold, center, color, line_height, margin_bottom, letter_spacing)
        name = self._names.get(key)
        if name is None:
            name = f"P{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _paragraph_style_xml(name, self._font, *key)
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


# ---------------------------------------------------------------------------
# Frame content resolution (role -> slide field)
# ---------------------------------------------------------------------------


def _role_lines(slide: Slide, role: str) -> list[str]:
    """Return the text lines a frame's ``role`` contributes, or [] if empty."""
    if role == "title":
        return [slide.title] if slide.title else []
    if role == "subtitle":
        return [slide.subtitle] if slide.subtitle else []
    if role == "fact":
        return [slide.fact] if slide.fact else []
    if role == "bullets":
        return list(slide.bullets)
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
        f'<text:p text:style-name="{_attr(style_name)}">{escape(line)}</text:p>'
        for line in lines
    )
    return _frame_box_xml(frame, paragraphs)


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


def _page_xml(
    index: int,
    slide: Slide,
    theme: Theme,
    styles: _ParagraphStyles,
    graphics: _GraphicStyles,
    section_ordinal: int | None,
) -> str:
    """Build one ``draw:page`` for a slide, registering its paragraph styles.

    Master page + drawing-page style are chosen by layout: :data:`_PLAIN_LAYOUTS`
    use the furniture-free "Plain" master, and :data:`_ACCENT_BG_LAYOUTS` swap in
    the full-accent drawing-page style. Content-page titles gain a vertical accent
    bar; ``fact`` text is up-sized to display + accent; big-fact bullets go muted.
    """
    parts: list[str] = []
    if slide.layout in _ACCENT_BG_LAYOUTS and section_ordinal is not None:
        parts.append(_section_number_xml(section_ordinal, theme, styles))

    for frame in LAYOUTS[slide.layout]:
        lines = _role_lines(slide, frame.role)
        if not lines:
            continue
        if frame.role in _LIST_ROLES and not frame.center:
            inner = _list_xml(
                lines,
                styles,
                size_pt=frame.size_pt,
                color=theme.text_color,
                style_name=_LIST_STYLE_NAME,
            )
            parts.append(_frame_box_xml(frame, inner))
            continue

        size_pt = frame.size_pt
        if frame.role == "title":
            # Section titles invert onto the accent background.
            color = theme.bg if slide.layout in _ACCENT_BG_LAYOUTS else theme.title_color
        elif frame.role == "fact":
            size_pt = theme.display_pt
            color = theme.accent
        elif frame.role == "bullets" and slide.layout == "big-fact":
            color = theme.muted
        else:
            color = theme.text_color

        # Vertical accent bar flush with a content-page title's left edge (never
        # on Plain layouts — the opening title / inverted section stand alone).
        if frame.role == "title" and slide.layout not in _PLAIN_LAYOUTS:
            bar_style = graphics.name_for_fill(theme.accent)
            parts.append(
                _rect_xml(
                    frame.x, frame.y, _ACCENT_BAR_W, frame.h,
                    fill=theme.accent, style_name=bar_style,
                )
            )

        style_name = styles.name_for(size_pt, frame.bold, frame.center, color)
        parts.append(_frame_xml(frame, lines, style_name))

    if slide.notes:
        notes_style = styles.name_for(_NOTES_SIZE_PT, False, False, theme.text_color)
        parts.append(_notes_xml(slide.notes, notes_style))

    master = _PLAIN_MASTER_NAME if slide.layout in _PLAIN_LAYOUTS else _MASTER_PAGE_NAME
    dp_style = (
        _SECTION_DRAWING_PAGE_STYLE
        if slide.layout in _ACCENT_BG_LAYOUTS
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


def build_content_xml(p: Presentation, theme: Theme) -> str:
    """Build ``content.xml`` for a presentation. Pure function."""
    styles = _ParagraphStyles(theme.font)
    # Graphic styles for shapes drawn on pages (accent bars, etc.).
    graphics = _GraphicStyles()

    # Section slides carry an auto-computed 1-based ordinal (the giant chapter
    # number); non-section slides map to None.
    section_ordinal: dict[int, int] = {}
    for i, slide in enumerate(p.slides):
        if slide.layout in _ACCENT_BG_LAYOUTS:
            section_ordinal[i] = len(section_ordinal) + 1

    # Build pages first so every referenced paragraph/graphic style is registered.
    pages = "".join(
        _page_xml(i, slide, theme, styles, graphics, section_ordinal.get(i))
        for i, slide in enumerate(p.slides)
    )

    # Default content drawing-page carries the bg fill and surfaces the master's
    # page-number placeholder. The accent (inverted) drawing-page is emitted only
    # when a section slide actually needs it.
    drawing_pages = _drawing_page_style_xml(
        _DRAWING_PAGE_STYLE, _page_fill_attrs(theme), display_page_number=True
    )
    if section_ordinal:
        drawing_pages += _drawing_page_style_xml(
            _SECTION_DRAWING_PAGE_STYLE,
            f'draw:fill="solid" draw:fill-color="{_attr(theme.accent)}"',
        )

    automatic_styles = (
        "<office:automatic-styles>"
        f"{styles.xml()}"
        f"{graphics.xml()}"
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


def render_odp(p: Presentation, out_path: Path) -> Path:
    """Render presentation ``p`` to a native ``.odp`` file at ``out_path``."""
    theme = THEMES.get(p.theme, THEMES["academic"])
    parts = {
        "content.xml": build_content_xml(p, theme),
        "styles.xml": build_styles_xml(theme, p.title),
        "meta.xml": build_meta_xml(p.title),
    }
    return write_odf_package(Path(out_path), ODP_MIMETYPE, parts)
