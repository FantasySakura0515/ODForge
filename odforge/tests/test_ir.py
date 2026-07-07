import pytest
from pydantic import ValidationError

import odforge
from odforge.ir import (
    DesignSpec,
    FontPair,
    Palette,
    Presentation,
    Sheet,
    Spreadsheet,
    TextDoc,
    parse_ir,
)

# A palette whose contrasts all clear the WCAG thresholds against a white bg:
# text 21:1, muted ~4.8:1, accent ~5.2:1.
_GOOD_PALETTE = {
    "bg": "#FFFFFF",
    "surface": "#F5F5F5",
    "text": "#1A1A1A",
    "muted": "#6B7280",
    "accent": "#2563EB",
}
_GOOD_FONTS = {"display": "Noto Serif TC", "body": "Noto Sans TC"}


def test_import():
    assert odforge.__version__ == "0.1.0"


def test_parse_text_doc():
    ir = parse_ir({"type": "text", "title": "測試", "blocks": [
        {"kind": "heading", "level": 1, "text": "第一章"},
        {"kind": "paragraph", "text": "內文"}]})
    assert isinstance(ir, TextDoc) and ir.blocks[0].level == 1


def test_heading_level_out_of_range_rejected():
    with pytest.raises(ValidationError):
        parse_ir({"type": "text", "title": "x",
                  "blocks": [{"kind": "heading", "level": 9, "text": "x"}]})


def test_parse_presentation_default_theme():
    ir = parse_ir({"type": "presentation", "title": "簡報", "slides": [
        {"layout": "title", "title": "封面", "subtitle": "副標"}]})
    assert ir.theme == "academic"


def test_formula_must_use_of_namespace():
    with pytest.raises(ValidationError):
        Sheet(name="s", columns=["a"], rows=[[1]],
              formulas=[{"cell": "B1", "formula": "=SUM(A1)"}])  # 缺 "of:" 前綴


def test_sheet_rows_allow_none_cells():
    # Formula-target cells are often null in LLM output (the formula computes
    # them); the IR must accept None in rows.
    sheet = Sheet(name="s", columns=["a", "b"], rows=[[1, None]])
    assert sheet.rows[0][1] is None


def test_parse_ir_spreadsheet_with_null_cell():
    ir = parse_ir({"type": "spreadsheet", "title": "成績", "sheets": [
        {"name": "s", "columns": ["a", "b"], "rows": [[1, None], ["x", 2.5]]}]})
    assert isinstance(ir, Spreadsheet)
    assert ir.sheets[0].rows[0][1] is None


def test_empty_slides_presentation_rejected():
    # A presentation with zero slides is a degenerate document; reject it at
    # the IR boundary so the renderer never emits an empty deck.
    with pytest.raises(ValidationError):
        parse_ir({"type": "presentation", "title": "x", "slides": []})


def test_empty_sheets_spreadsheet_rejected():
    with pytest.raises(ValidationError):
        parse_ir({"type": "spreadsheet", "title": "x", "sheets": []})


def test_schema_exportable():
    assert "properties" in Presentation.model_json_schema()


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_ir({"type": "banana", "title": "x"})


def test_sample_text_doc_fixture(sample_text_doc):
    assert isinstance(sample_text_doc, TextDoc)
    kinds = [b.kind for b in sample_text_doc.blocks]
    assert "toc" in kinds and "pagebreak" in kinds


def test_sample_presentation_fixture(sample_presentation):
    assert isinstance(sample_presentation, Presentation)
    assert len(sample_presentation.slides) == 5
    assert {s.layout for s in sample_presentation.slides} == {
        "title", "title-content", "two-col", "section", "big-fact"}


def test_sample_spreadsheet_fixture(sample_spreadsheet):
    assert isinstance(sample_spreadsheet, Spreadsheet)
    assert sample_spreadsheet.sheets[0].columns == ["項目", "數量", "單價"]
    assert sample_spreadsheet.sheets[0].formulas[0].formula.startswith("of:=")


# ---------------------------------------------------------------------------
# Task 12.1: DesignSpec IR + contrast validation
# ---------------------------------------------------------------------------


def test_valid_design_spec_accepted():
    # ① A palette that clears every contrast threshold and whitelisted fonts
    #    must build cleanly, defaulting scale/mode.
    spec = DesignSpec(palette=Palette(**_GOOD_PALETTE), fonts=FontPair(**_GOOD_FONTS))
    assert spec.palette.accent == "#2563EB"
    assert spec.fonts.body == "Noto Sans TC"
    assert spec.scale == "standard"
    assert spec.mode == "presenter"


def test_low_contrast_text_rejected_with_message():
    # ② text #CCCCCC on white bg is ~1.6:1, far below the 4.5 floor.
    with pytest.raises(ValidationError) as exc:
        Palette(bg="#FFFFFF", surface="#FFFFFF", text="#CCCCCC",
                muted="#6B7280", accent="#2563EB")
    msg = str(exc.value)
    assert "contrast" in msg.lower()
    assert "text" in msg  # message names the offending pair


def test_font_not_in_whitelist_rejected():
    # ③ Fonts outside FONT_WHITELIST are rejected.
    with pytest.raises(ValidationError):
        FontPair(display="Comic Sans MS", body="Noto Sans TC")


def test_presentation_design_defaults_none():
    # ④ Backward compatibility: a v1 presentation without a design is valid and
    #    exposes design=None.
    ir = parse_ir({"type": "presentation", "title": "簡報", "slides": [
        {"layout": "title", "title": "封面"}]})
    assert isinstance(ir, Presentation)
    assert ir.design is None


def test_presentation_accepts_design_block():
    ir = parse_ir({
        "type": "presentation",
        "title": "簡報",
        "slides": [{"layout": "title", "title": "封面"}],
        "design": {"palette": _GOOD_PALETTE, "fonts": _GOOD_FONTS,
                   "scale": "display", "mode": "detailed"},
    })
    assert isinstance(ir.design, DesignSpec)
    assert ir.design.scale == "display"
    assert ir.design.mode == "detailed"


def test_malformed_hex_rejected():
    # 3-digit shorthand and named colors are not #RRGGBB — reject with a message
    # that mentions the format.
    with pytest.raises(ValidationError) as exc:
        Palette(bg="#FFF", surface="#FFFFFF", text="#000000",
                muted="#6B7280", accent="#2563EB")
    assert "#RRGGBB" in str(exc.value) or "hex" in str(exc.value).lower()


def test_low_contrast_message_reports_ratio():
    # The error must be actionable: it states the computed ratio so the LLM can
    # judge how far off it is on retry.
    with pytest.raises(ValidationError) as exc:
        Palette(bg="#FFFFFF", surface="#FFFFFF", text="#DDDDDD",
                muted="#6B7280", accent="#2563EB")
    msg = str(exc.value)
    assert "1." in msg  # a ratio like 1.35 appears in the message


def test_sample_presentation_fixture_has_no_design(sample_presentation):
    # v1 fixture stays green and design-free.
    assert sample_presentation.design is None


# ---------------------------------------------------------------------------
# Task 13.5: ChartSpec (standalone IR; wired into slides in Task 14.1)
# ---------------------------------------------------------------------------


def test_chart_spec_valid():
    from odforge.ir import ChartSpec

    c = ChartSpec(labels=["甲", "乙"], values=[1.5, 2], unit="%", highlight=0)
    assert c.values == [1.5, 2.0] and c.highlight == 0 and c.unit == "%"


def test_chart_spec_defaults_unit_empty_highlight_none():
    from odforge.ir import ChartSpec

    c = ChartSpec(labels=["x"], values=[1])
    assert c.unit == "" and c.highlight is None


# ③ more than 8 bars is rejected with a clear, actionable message.
def test_chart_spec_more_than_8_bars_rejected():
    from odforge.ir import ChartSpec

    with pytest.raises(ValidationError) as exc:
        ChartSpec(labels=[str(i) for i in range(9)], values=list(range(9)))
    assert "8" in str(exc.value)


def test_chart_spec_length_mismatch_rejected():
    from odforge.ir import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(labels=["a", "b"], values=[1])


def test_chart_spec_empty_rejected():
    from odforge.ir import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(labels=[], values=[])


def test_chart_spec_negative_value_rejected():
    from odforge.ir import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(labels=["a"], values=[-1])


def test_chart_spec_highlight_out_of_range_rejected():
    from odforge.ir import ChartSpec

    with pytest.raises(ValidationError):
        ChartSpec(labels=["a", "b"], values=[1, 2], highlight=5)
