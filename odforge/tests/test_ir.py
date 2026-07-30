import pytest
from pydantic import ValidationError

import odforge
from odforge.ir import (
    DesignSpec,
    FontPair,
    PageRole,
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


def test_image_layout_requires_safe_image_spec():
    slide = Presentation.model_validate(
        {
            "type": "presentation",
            "title": "圖片",
            "slides": [
                {
                    "layout": "image-split",
                    "title": "現場",
                    "bullets": ["觀察"],
                    "image": {
                        "src": "asset://hero",
                        "alt": "現場照片",
                        "fit": "contain",
                    },
                }
            ],
        }
    ).slides[0]
    assert slide.image is not None
    assert slide.image.src == "asset://hero"

    with pytest.raises(ValidationError, match="requires an ImageSpec"):
        Presentation.model_validate(
            {
                "type": "presentation",
                "title": "缺圖",
                "slides": [{"layout": "image-focus", "title": "x"}],
            }
        )


def test_image_spec_rejects_arbitrary_local_path():
    with pytest.raises(ValidationError, match="image src"):
        Presentation.model_validate(
            {
                "type": "presentation",
                "title": "不安全",
                "slides": [
                    {
                        "layout": "image-focus",
                        "title": "x",
                        "image": {
                            "src": "C:/Users/private/photo.png",
                            "alt": "x",
                        },
                    }
                ],
            }
        )


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


# --- Task 15.1 review fix: PageRole.gist is required (no silent "") ---------


def test_page_role_gist_required():
    # Missing gist must fail validation — stage 2 depends on it; a silent ""
    # default would lose the per-page guidance without any signal.
    with pytest.raises(ValidationError, match="gist"):
        PageRole.model_validate({"role": "title", "title": "封面"})


def test_page_role_empty_gist_rejected():
    # A blank gist is as useless as a missing one.
    with pytest.raises(ValidationError, match="gist"):
        PageRole(role="title", title="封面", gist="")


def test_page_role_shares_slide_layout_vocabulary():
    # DRY: PageRole.role and Slide.layout use one shared Literal — every layout
    # a Slide can render is a role an outline page can name, and vice versa.
    role_lit = PageRole.model_fields["role"].annotation
    layout_lit = Slide.model_fields["layout"].annotation
    assert role_lit == layout_lit


def test_visual_layouts_validate_required_content():
    process = Slide(
        layout="process",
        title="執行流程",
        steps=[
            {"title": "盤點", "detail": "確認目標與限制"},
            {"title": "實作", "detail": "完成核心功能"},
        ],
    )
    timeline = Slide(
        layout="timeline",
        title="發展歷程",
        events=[
            {"label": "Q1", "title": "啟動"},
            {"label": "Q2", "title": "上線"},
        ],
    )
    metrics = Slide(
        layout="metrics",
        title="成果",
        metrics=[
            {"value": "42%", "label": "轉換率"},
            {"value": "3.2x", "label": "成長"},
        ],
    )
    cards = Slide(layout="cards", title="四大支柱", bullets=["策略", "產品"])
    assert len(process.steps) == 2
    assert len(timeline.events) == 2
    assert len(metrics.metrics) == 2
    assert cards.bullets == ["策略", "產品"]


@pytest.mark.parametrize(
    ("layout", "payload"),
    [
        ("process", {"steps": [{"title": "只有一步"}]}),
        ("timeline", {"events": [{"label": "Q1", "title": "只有一項"}]}),
        ("metrics", {"metrics": [{"value": "1", "label": "只有一項"}]}),
        ("cards", {"bullets": ["只有一項"]}),
    ],
)
def test_visual_layouts_reject_too_little_content(layout, payload):
    with pytest.raises(ValidationError, match=layout):
        Slide(layout=layout, title="x", **payload)


def test_diagram_and_sources_validate():
    slide = Slide(
        layout="diagram",
        title="系統架構",
        diagram={
            "kind": "hub",
            "nodes": [
                {"id": "core", "title": "核心服務", "emphasis": True},
                {"id": "web", "title": "前端"},
                {"id": "data", "title": "資料層"},
            ],
            "edges": [
                {"source": "core", "target": "web", "label": "提供 API"},
                {"source": "core", "target": "data", "label": "讀寫"},
            ],
        },
        sources=[
            {"label": "系統設計文件", "url": "https://example.com/design"}
        ],
    )
    assert slide.diagram is not None
    assert slide.diagram.nodes[0].id == "core"
    assert slide.sources[0].url == "https://example.com/design"


@pytest.mark.parametrize(
    "diagram",
    [
        {
            "nodes": [{"id": "x", "title": "A"}, {"id": "x", "title": "B"}],
            "edges": [{"source": "x", "target": "x"}],
        },
        {
            "nodes": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
            "edges": [{"source": "a", "target": "missing"}],
        },
        {
            "nodes": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
            "edges": [{"source": "a", "target": "a"}],
        },
    ],
)
def test_diagram_rejects_invalid_graph(diagram):
    with pytest.raises(ValidationError, match="diagram"):
        Slide(layout="diagram", title="x", diagram=diagram)


def test_source_rejects_non_http_url():
    with pytest.raises(ValidationError, match="http"):
        Slide(
            layout="title-content",
            title="x",
            bullets=["a"],
            sources=[{"label": "本機檔案", "url": "file:///tmp/a"}],
        )
