"""Render a :class:`~odforge.ir.TextDoc` into a native ``.odt`` file via odfdo.

Single responsibility: this module only knows how to turn a ``TextDoc`` and its
blocks into an OpenDocument text document. The document-type dispatch lives in
:mod:`odforge.render`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from odfdo import Document, Header, List, ListItem, Paragraph, Style, TOC, Table

from odforge.ir import (
    HeadingBlock,
    ListBlock,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
    TextDoc,
    TocBlock,
)

# ---------------------------------------------------------------------------
# Named styles (deep-blue headings, quote, note, table header, page break)
# ---------------------------------------------------------------------------

FONT_NAME = "Noto Sans TC"
# svg:font-family lists the primary CJK face plus a Windows fallback.
FONT_FAMILY = '"Noto Sans TC", "微軟正黑體"'

HEADING_COLOR = "#1A3C6E"
NOTE_COLOR = "#666666"

# level -> (style name, font size)
_HEADING_STYLES = {
    1: ("ODForge_Heading_1", "18pt"),
    2: ("ODForge_Heading_2", "15pt"),
    3: ("ODForge_Heading_3", "13pt"),
}

_BODY_STYLE = "ODForge_Body"
_PARAGRAPH_STYLES = {
    "body": _BODY_STYLE,
    "quote": "ODForge_Quote",
    "note": "ODForge_Note",
}
_TABLE_HEADER_STYLE = "ODForge_TableHeader"
_PAGE_BREAK_STYLE = "ODForge_PageBreak"


# The font-name must be set across Western/Asian/Complex scripts so the CJK
# face actually applies to Chinese text in LibreOffice.
_FONT_PROPS = {
    "style:font-name": FONT_NAME,
    "style:font-name-asian": FONT_NAME,
    "style:font-name-complex": FONT_NAME,
}


def _register_styles(doc: Document) -> None:
    """Insert every named style (and the font-face declaration) into ``doc``."""
    # Font-face declaration: needed both in content.xml (automatic styles) and
    # styles.xml (common styles) so any style referencing the font resolves.
    font_face = Style(
        "font-face",
        name=FONT_NAME,
        font_name=FONT_NAME,
        font_family=FONT_FAMILY,
        font_family_generic="swiss",
        font_pitch="variable",
    )
    doc.insert_style(font_face)
    doc.insert_style(font_face.clone, default=True)

    # Body paragraph style: sets the CJK font for ordinary text.
    body = Style(family="paragraph", name=_BODY_STYLE, display_name="ODForge Body")
    body.set_properties(dict(_FONT_PROPS), area="text")
    doc.insert_style(body)

    # Heading 1-3: deep blue, bold, decreasing size, CJK font.
    for level, (name, size) in _HEADING_STYLES.items():
        heading = Style(
            family="paragraph",
            name=name,
            display_name=name.replace("_", " "),
            parent_style="Heading",
            area="text",
            bold=True,
            color=HEADING_COLOR,
        )
        heading.set_properties(
            {**_FONT_PROPS, "fo:font-size": size}, area="text"
        )
        doc.insert_style(heading)

    # Quote: 1cm left indent + italic.
    quote = Style(
        family="paragraph",
        name=_PARAGRAPH_STYLES["quote"],
        display_name="ODForge Quote",
        area="text",
        italic=True,
    )
    quote.set_properties(dict(_FONT_PROPS), area="text")
    quote.set_properties({"fo:margin-left": "1cm"})
    doc.insert_style(quote)

    # Note: grey text.
    note = Style(
        family="paragraph",
        name=_PARAGRAPH_STYLES["note"],
        display_name="ODForge Note",
        area="text",
        color=NOTE_COLOR,
    )
    note.set_properties(dict(_FONT_PROPS), area="text")
    doc.insert_style(note)

    # Table header: bold cells.
    table_header = Style(
        family="paragraph",
        name=_TABLE_HEADER_STYLE,
        display_name="ODForge Table Header",
        area="text",
        bold=True,
    )
    table_header.set_properties(dict(_FONT_PROPS), area="text")
    doc.insert_style(table_header)

    # Numbered / bullet list styles (distinguish ordered vs unordered lists).
    numbered = Style(family="list", name="ODForge_Numbered")
    numbered.set_level_style(1, num_format="1", suffix=".")
    doc.insert_style(numbered)

    bulleted = Style(family="list", name="ODForge_Bulleted")
    bulleted.set_level_style(1, bullet_char="•")
    doc.insert_style(bulleted)

    # Page break: empty paragraph carrying fo:break-before="page".
    page_break = Style(family="paragraph", name=_PAGE_BREAK_STYLE)
    page_break.set_properties({"fo:break-before": "page"})
    doc.insert_style(page_break)


# ---------------------------------------------------------------------------
# Block handlers (dict dispatch on block.kind — no if/elif chain)
# ---------------------------------------------------------------------------


def _render_heading(body, block: HeadingBlock) -> None:
    style_name, _ = _HEADING_STYLES.get(block.level, _HEADING_STYLES[3])
    body.append(Header(block.level, block.text, style=style_name))


def _render_paragraph(body, block: ParagraphBlock) -> None:
    style_name = _PARAGRAPH_STYLES.get(block.style, _BODY_STYLE)
    body.append(Paragraph(block.text, style=style_name))


def _render_list(body, block: ListBlock) -> None:
    style_name = "ODForge_Numbered" if block.ordered else "ODForge_Bulleted"
    odf_list = List(style=style_name)
    for item in block.items:
        odf_list.append(ListItem(item))
    body.append(odf_list)


def _render_table(body, block: TableBlock) -> None:
    table = Table("Table")
    table.set_values([block.header, *block.rows])
    # Bold the header row: style the paragraph inside each header cell.
    header_row = table.get_row(0)
    for cell in header_row.get_cells():
        for paragraph in cell.get_elements("text:p"):
            paragraph.style = _TABLE_HEADER_STYLE
    table.set_row(0, header_row)
    body.append(table)


def _render_toc(body, block: TocBlock) -> None:
    body.append(TOC())


def _render_pagebreak(body, block: PageBreakBlock) -> None:
    body.append(Paragraph("", style=_PAGE_BREAK_STYLE))


_HANDLERS: dict[str, Callable] = {
    "heading": _render_heading,
    "paragraph": _render_paragraph,
    "list": _render_list,
    "table": _render_table,
    "toc": _render_toc,
    "pagebreak": _render_pagebreak,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_odt(doc: TextDoc, out_path: Path) -> Path:
    """Render ``doc`` to a native ``.odt`` file at ``out_path`` and return it."""
    out_path = Path(out_path)

    document = Document("text")
    body = document.body
    body.clear()

    _register_styles(document)

    document.meta.set_title(doc.title)
    document.meta.set_generator("ODForge")
    document.language = doc.lang

    for block in doc.blocks:
        _HANDLERS[block.kind](body, block)

    document.save(str(out_path))
    return out_path
