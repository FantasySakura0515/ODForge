"""Render a :class:`~odforge.ir.Spreadsheet` into a native ``.ods`` file via odfdo.

Single responsibility: this module only knows how to turn a ``Spreadsheet`` and
its sheets/rows/formulas into an OpenDocument spreadsheet. Document-type dispatch
lives in :mod:`odforge.render`.

Cell semantics:

* Numbers (``int``/``float``) become numeric cells (``office:value-type="float"``
  with an ``office:value``) — never string cells.
* Strings become text cells.
* Each :class:`~odforge.ir.FormulaSpec` sets ``table:formula`` verbatim on the
  target cell (the spec guarantees the ``of:=`` OpenFormula prefix); the value
  type is left for LibreOffice to recompute on open.
"""

from __future__ import annotations

import re
from pathlib import Path

from odfdo import Cell, Document, Row, Style, Table

from odforge.ir import FormulaSpec, Sheet, Spreadsheet

# ---------------------------------------------------------------------------
# Fonts / styles (mirrors the .odt renderer's -asian CJK pattern)
# ---------------------------------------------------------------------------

FONT_NAME = "Noto Sans TC"
# svg:font-family lists the primary CJK face plus a Windows fallback.
FONT_FAMILY = '"Noto Sans TC", "微軟正黑體"'

_HEADER_STYLE = "ODForge_SheetHeader"

# The font-name must be set across Western/Asian/Complex scripts so the CJK
# face actually applies to Chinese text in LibreOffice.
_FONT_PROPS = {
    "style:font-name": FONT_NAME,
    "style:font-name-asian": FONT_NAME,
    "style:font-name-complex": FONT_NAME,
}

# Bold across the same three scripts (Western fo: + Asian/Complex variants).
_BOLD_PROPS = {
    "fo:font-weight": "bold",
    "style:font-weight-asian": "bold",
    "style:font-weight-complex": "bold",
}

# ``!!`` and friends must be rejected; a well-formed ref is column letters
# followed by a 1-based row number (e.g. "B5", "AA12").
_CELL_REF_RE = re.compile(r"^[A-Za-z]+[0-9]+$")


def _register_styles(doc: Document) -> None:
    """Insert the font-face declaration and the bold CJK header cell style."""
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

    # Header cells: bold + CJK font (family="table-cell" carries text-properties).
    header = Style(
        family="table-cell",
        name=_HEADER_STYLE,
        display_name="ODForge Sheet Header",
    )
    header.set_properties({**_FONT_PROPS, **_BOLD_PROPS}, area="text")
    doc.insert_style(header)


def _validate_cell_ref(ref: str) -> None:
    """Raise ``ValueError`` (with the offending ref) for a malformed cell ref.

    Accepts column letters + a 1-based row number (single-letter A-Z at minimum,
    multi-letter AA+ also supported). Anything else is out of range.
    """
    if not _CELL_REF_RE.match(ref):
        raise ValueError(f"invalid cell reference: {ref!r}")


def _cell_for_value(value: object) -> Cell:
    """Build a cell: numeric for int/float (never a string), text otherwise.

    ``None`` becomes an empty cell (no value, no text) that still occupies its
    position, keeping later cells in their correct columns — typical for
    formula-target cells the LLM leaves null. ``bool`` is excluded from the
    numeric path (it is an ``int`` subclass) so a boolean never silently
    becomes a float.
    """
    if value is None:
        return Cell()
    if isinstance(value, bool):
        return Cell(str(value))
    if isinstance(value, (int, float)):
        return Cell(value, cell_type="float")
    return Cell(str(value))


def _build_table(sheet: Sheet) -> Table:
    """Build one ``table:table`` for a sheet: bold header row then data rows."""
    table = Table(sheet.name)

    header = Row()
    for column in sheet.columns:
        header.append_cell(Cell(str(column), style=_HEADER_STYLE))
    table.append_row(header)

    for row in sheet.rows:
        odf_row = Row()
        for value in row:
            odf_row.append_cell(_cell_for_value(value))
        table.append_row(odf_row)

    _apply_formulas(table, sheet.formulas)
    return table


def _apply_formulas(table: Table, formulas: list[FormulaSpec]) -> None:
    """Set ``table:formula`` verbatim on each formula's target cell."""
    for spec in formulas:
        _validate_cell_ref(spec.cell)
        cell = table.get_cell(spec.cell)
        cell.formula = spec.formula
        table.set_cell(spec.cell, cell)


def render_ods(s: Spreadsheet, out_path: Path) -> Path:
    """Render spreadsheet ``s`` to a native ``.ods`` file at ``out_path``."""
    out_path = Path(out_path)

    document = Document("spreadsheet")
    body = document.body
    # A fresh spreadsheet ships with one empty default table — drop it so the
    # sheet count matches the IR exactly.
    for table in body.get_tables():
        table.delete()

    _register_styles(document)

    document.meta.set_title(s.title)
    document.meta.set_generator("ODForge")

    for sheet in s.sheets:
        body.append(_build_table(sheet))

    document.save(str(out_path))
    return out_path
