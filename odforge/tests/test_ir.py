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


# ---------------------------------------------------------------------------
# Task 14.1: new page-role layouts + IR fields (quote/agenda/comparison/
# chart/closing), nested BulletItem, cross-field validators
# ---------------------------------------------------------------------------

from odforge.ir import BulletItem, ChartSpec, Slide  # noqa: E402


def test_all_ten_layout_literals_parse():
    """① Every new layout literal parses on a minimally-valid slide."""
    slides = [
        {"layout": "title", "title": "T"},
        {"layout": "title-content", "title": "T", "bullets": ["a"]},
        {"layout": "two-col", "title": "T", "left": ["a"], "right": ["b"]},
        {"layout": "section", "title": "S"},
        {"layout": "big-fact", "fact": "9"},
        {"layout": "quote", "quote": "引言", "attribution": "— 某人"},
        {"layout": "agenda", "title": "議程", "bullets": ["a", "b"]},
        {"layout": "comparison", "title": "比較",
         "left": ["A", "x"], "right": ["B", "y"]},
        {"layout": "chart", "title": "圖",
         "chart": {"labels": ["a"], "values": [1]}},
        {"layout": "closing", "title": "謝謝"},
    ]
    p = parse_ir({"type": "presentation", "title": "t", "slides": slides})
    assert [s.layout for s in p.slides] == [
        "title", "title-content", "two-col", "section", "big-fact",
        "quote", "agenda", "comparison", "chart", "closing",
    ]


def test_chart_layout_without_chart_rejected_with_message():
    """③ layout="chart" but chart is None → clear ValidationError."""
    with pytest.raises(ValidationError) as exc:
        Slide(layout="chart", title="圖")
    assert "chart" in str(exc.value).lower()


def test_quote_layout_without_quote_rejected_with_message():
    """③ layout="quote" but quote is empty → clear ValidationError."""
    with pytest.raises(ValidationError) as exc:
        Slide(layout="quote", title="x")
    assert "quote" in str(exc.value).lower()


def test_chart_layout_with_chart_accepted():
    s = Slide(layout="chart", title="圖",
              chart=ChartSpec(labels=["甲", "乙"], values=[3, 5]))
    assert s.chart is not None and s.chart.values == [3.0, 5.0]


def test_closing_agenda_comparison_need_no_extra_fields():
    # closing = title as message; agenda uses bullets; comparison uses left/right.
    Slide(layout="closing", title="結束")
    Slide(layout="agenda", title="議程", bullets=["x"])
    Slide(layout="comparison", title="比較", left=["a"], right=["b"])


def test_bullet_item_nested_and_plain_str_coexist():
    """Nested BulletItem and plain strings live together (v1 compat)."""
    s = Slide(layout="title-content", title="T",
              bullets=["純字串", BulletItem(text="父", children=["子一", "子二"])])
    assert s.bullets[0] == "純字串"
    assert isinstance(s.bullets[1], BulletItem)
    assert s.bullets[1].children == ["子一", "子二"]


def test_bullet_item_parses_from_dict():
    p = parse_ir({"type": "presentation", "title": "t", "slides": [
        {"layout": "title-content", "title": "T",
         "bullets": ["a", {"text": "父", "children": ["子"]}]}]})
    assert p.slides[0].bullets[0] == "a"
    assert isinstance(p.slides[0].bullets[1], BulletItem)
    assert p.slides[0].bullets[1].text == "父"


def test_plain_string_bullets_still_valid():
    # Hard v1-compat requirement: an all-string bullet list stays valid & plain.
    s = Slide(layout="title-content", title="T", bullets=["甲", "乙", "丙"])
    assert s.bullets == ["甲", "乙", "丙"]
    assert all(isinstance(b, str) for b in s.bullets)


def test_new_slide_fields_default_empty():
    s = Slide(layout="title", title="T")
    assert s.quote == "" and s.attribution == "" and s.kicker == ""
    assert s.chart is None
