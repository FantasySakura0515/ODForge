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
_DRAWING_PAGE_STYLE = "dp1"
_GRAPHIC_STYLE = "gr1"
_NOTES_SIZE_PT = 14


def _attr(value: str) -> str:
    """Escape a string for safe use inside a double-quoted XML attribute."""
    return escape(value, {'"': "&quot;"})


def _cm(value: float) -> str:
    """Format a centimetre measure, dropping a trailing ``.0`` (28.0 -> "28cm")."""
    if value == int(value):
        return f"{int(value)}cm"
    return f"{value}cm"


def _ns_decls(ns: dict[str, str]) -> str:
    """Render ``xmlns:`` declarations for the given namespace mapping."""
    return " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in ns.items())


# ---------------------------------------------------------------------------
# Paragraph style cache (de-duplicated on the visual property tuple)
# ---------------------------------------------------------------------------


class _ParagraphStyles:
    """Collects unique paragraph styles, assigning names P1, P2, … on demand."""

    def __init__(self, font: str) -> None:
        self._font = font
        self._names: dict[tuple[int, bool, bool, str], str] = {}

    def name_for(self, size_pt: int, bold: bool, center: bool, color: str) -> str:
        key = (size_pt, bold, center, color)
        name = self._names.get(key)
        if name is None:
            name = f"P{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _paragraph_style_xml(name, size_pt, bold, center, color, self._font)
            for (size_pt, bold, center, color), name in self._names.items()
        )


def _paragraph_style_xml(
    name: str, size_pt: int, bold: bool, center: bool, color: str, font: str
) -> str:
    """Build one ``style:family="paragraph"`` automatic style element."""
    align = "center" if center else "start"
    # LibreOffice applies fo:font-size / fo:font-weight to Western script
    # only; CJK glyphs need the *-asian variants (and *-complex for CTL).
    size = (
        f' fo:font-size="{size_pt}pt"'
        f' style:font-size-asian="{size_pt}pt"'
        f' style:font-size-complex="{size_pt}pt"'
    )
    weight = (
        ' fo:font-weight="bold"'
        ' style:font-weight-asian="bold"'
        ' style:font-weight-complex="bold"'
        if bold
        else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="paragraph">'
        f'<style:paragraph-properties fo:text-align="{align}"/>'
        f"<style:text-properties{size}{weight}"
        f' fo:color="{_attr(color)}" style:font-name="{_attr(font)}"'
        f' style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
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


def _frame_xml(
    frame: Frame, lines: list[str], style_name: str
) -> str:
    """Build a ``draw:frame`` (text box) for the given placeholder frame."""
    paragraphs = "".join(
        f'<text:p text:style-name="{_attr(style_name)}">{escape(line)}</text:p>'
        for line in lines
    )
    return (
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="{_cm(frame.x)}" svg:y="{_cm(frame.y)}"'
        f' svg:width="{_cm(frame.w)}" svg:height="{_cm(frame.h)}">'
        f"<draw:text-box>{paragraphs}</draw:text-box>"
        f"</draw:frame>"
    )


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


def _page_xml(
    index: int, slide: Slide, theme: Theme, styles: _ParagraphStyles
) -> str:
    """Build one ``draw:page`` for a slide, registering its paragraph styles."""
    parts: list[str] = []
    for frame in LAYOUTS[slide.layout]:
        lines = _role_lines(slide, frame.role)
        if not lines:
            continue
        color = theme.title_color if frame.role == "title" else theme.text_color
        style_name = styles.name_for(frame.size_pt, frame.bold, frame.center, color)
        parts.append(_frame_xml(frame, lines, style_name))

    if slide.notes:
        notes_style = styles.name_for(_NOTES_SIZE_PT, False, False, theme.text_color)
        parts.append(_notes_xml(slide.notes, notes_style))

    return (
        f'<draw:page draw:name="page{index}"'
        f' draw:style-name="{_DRAWING_PAGE_STYLE}"'
        f' draw:master-page-name="{_MASTER_PAGE_NAME}">'
        f"{''.join(parts)}"
        f"</draw:page>"
    )


# ---------------------------------------------------------------------------
# Pure builders for the three XML parts
# ---------------------------------------------------------------------------


def build_content_xml(p: Presentation, theme: Theme) -> str:
    """Build ``content.xml`` for a presentation. Pure function."""
    styles = _ParagraphStyles(theme.font)
    # Build pages first so every referenced paragraph style is registered.
    pages = "".join(
        _page_xml(i, slide, theme, styles) for i, slide in enumerate(p.slides)
    )

    automatic_styles = (
        "<office:automatic-styles>"
        f"{styles.xml()}"
        f'<style:style style:name="{_DRAWING_PAGE_STYLE}"'
        f' style:family="drawing-page">'
        f'<style:drawing-page-properties draw:fill="solid"'
        f' draw:fill-color="{_attr(theme.bg)}"/>'
        f"</style:style>"
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


def build_styles_xml(theme: Theme) -> str:
    """Build ``styles.xml`` (page layout + master page + bg). Pure function."""
    font_family = f"'{theme.font}','微軟正黑體',sans-serif"
    return (
        f"{_XML_DECL}"
        f"<office:document-styles {_ns_decls(_STYLES_NS)}"
        f' office:version="1.2">'
        f"<office:font-face-decls>"
        f'<style:font-face style:name="{_attr(theme.font)}"'
        f' svg:font-family="{_attr(font_family)}"/>'
        f"</office:font-face-decls>"
        f"<office:automatic-styles>"
        f'<style:page-layout style:name="PM1">'
        f'<style:page-layout-properties fo:page-width="{_cm(PAGE_W)}"'
        f' fo:page-height="{_cm(PAGE_H)}" style:print-orientation="landscape"'
        f' fo:margin-top="0cm" fo:margin-bottom="0cm"'
        f' fo:margin-left="0cm" fo:margin-right="0cm"/>'
        f"</style:page-layout>"
        f'<style:style style:name="{_DRAWING_PAGE_STYLE}"'
        f' style:family="drawing-page">'
        f'<style:drawing-page-properties draw:fill="solid"'
        f' draw:fill-color="{_attr(theme.bg)}"/>'
        f"</style:style>"
        f"</office:automatic-styles>"
        f"<office:master-styles>"
        f'<style:master-page style:name="{_MASTER_PAGE_NAME}"'
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
        "styles.xml": build_styles_xml(theme),
        "meta.xml": build_meta_xml(p.title),
    }
    return write_odf_package(Path(out_path), ODP_MIMETYPE, parts)
