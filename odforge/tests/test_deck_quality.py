"""Deck-quality layer: lede / takeaway frames, table rendering, per-style
content treatments, and the outline's occasion-picked style.

These pin the fixes for what made generated decks read as sparse and
interchangeable: fields the renderer silently dropped (subtitle on content
pages, takeaway, table), a metrics grid that overlapped inside the dashboard
strip, a cover whose subtitle printed through a two-line title, and six styles
that drew identical content cards.
"""

import json

import lxml.etree as etree
import pytest

from odforge import llm
from odforge.ir import Outline, Presentation, Slide
from odforge.render.odp import build_content_xml
from odforge.textmetrics import check_budget
from odforge.themes import (
    CONTENT_BOTTOM_WITH_TAKEAWAY,
    CONTENT_TOP_WITH_LEDE,
    LAYOUTS,
    STYLES,
    TAKEAWAY_Y,
    THEMES,
    get_style,
    page_frames,
)

NS = {
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
THEME = THEMES["navy"]


def _cm(el, attr):
    return float(el.get("{%s}%s" % (NS["svg"], attr)).removesuffix("cm"))


def _page(slide, style="report"):
    p = Presentation(title="t", slides=[slide], style=style)
    root = etree.fromstring(build_content_xml(p, THEME, style=get_style(style)).encode("utf-8"))
    return root.find(".//draw:page", NS)


def _frame_with(page, text):
    for frame in page.findall(".//draw:frame", NS):
        if text in "".join(frame.itertext()):
            return frame
    raise AssertionError(f"no frame carries {text!r}")


CARDS = {
    "layout": "cards",
    "title": "三個目標",
    "bullets": [
        {"text": "資料集", "children": ["30 物種"]},
        {"text": "模型", "children": ["離線可用"]},
        {"text": "App", "children": ["野外拍照"]},
    ],
}


# -- geometry -----------------------------------------------------------------


def test_page_frames_without_lede_or_takeaway_is_the_plain_layout():
    for layout in LAYOUTS:
        assert page_frames(layout) == LAYOUTS[layout]


@pytest.mark.parametrize("layout", ["cards", "process", "metrics", "dashboard", "table"])
def test_lede_and_takeaway_reflow_the_body_between_them(layout):
    frames = page_frames(layout, lede=True, takeaway=True)
    roles = [f.role for f in frames]
    assert "lede" in roles and "takeaway" in roles
    body = [f for f in frames if f.role not in {"title", "lede", "takeaway"}]
    assert min(f.y for f in body) >= CONTENT_TOP_WITH_LEDE - 1e-6
    assert max(f.y + f.h for f in body) <= CONTENT_BOTTOM_WITH_TAKEAWAY + 1e-6


def test_plain_pages_never_get_a_lede_or_band():
    for layout in ("title", "section", "closing", "quote"):
        roles = [f.role for f in page_frames(layout, lede=True, takeaway=True)]
        assert "lede" not in roles and "takeaway" not in roles


# -- rendering ----------------------------------------------------------------


def test_content_page_draws_its_lede_and_takeaway():
    page = _page(Slide(**CARDS, subtitle="說明這頁的脈絡", takeaway="這就是結論"))
    lede = _frame_with(page, "說明這頁的脈絡")
    band = _frame_with(page, "這就是結論")
    assert _cm(lede, "y") < _cm(band, "y")
    # The band follows the body (no dead band above it), never below its slot.
    assert _cm(band, "y") <= TAKEAWAY_Y + 0.01
    body_bottom = max(
        _cm(f, "y") + _cm(f, "height")
        for f in page.findall(".//draw:frame", NS)
        if _cm(lede, "y") < _cm(f, "y") < _cm(band, "y") - 0.3
    )
    assert body_bottom < _cm(band, "y") < body_bottom + 1.0


def test_table_layout_draws_every_cell():
    slide = Slide(
        layout="table",
        title="評估方式",
        table={
            "columns": ["面向", "方法", "指標"],
            "rows": [["模型", "測試集", "F1"], ["使用者", "A/B 試用", "SUS"]],
            "highlight": 1,
        },
    )
    page = _page(slide)
    texts = {"".join(f.itertext()) for f in page.findall(".//draw:frame", NS)}
    for cell in ["面向", "方法", "指標", "模型", "測試集", "F1", "使用者", "A/B 試用", "SUS"]:
        assert cell in texts


def test_dashboard_metrics_sit_on_one_row_without_overlap():
    slide = Slide(
        layout="dashboard",
        title="成果",
        metrics=[{"value": v, "label": "標籤"} for v in ["12,000", "≥ 90%", "< 15 MB", "+20 pt"]],
        bullets=[
            {"text": "保育", "children": ["降低人力依賴"]},
            {"text": "學術", "children": ["公開資料集"]},
        ],
    )
    page = _page(slide, style="classic")
    # (the space after an operator is written non-breaking, so match the tail)
    values = [_frame_with(page, v) for v in ["12,000", "90%", "15 MB", "+20 pt"]]
    ys = {round(_cm(f, "y"), 2) for f in values}
    assert len(ys) == 1
    xs = sorted(_cm(f, "x") for f in values)
    widths = [_cm(f, "width") for f in values]
    assert all(xs[i] + widths[i] <= xs[i + 1] + 1e-6 for i in range(3))


def test_big_fact_draws_its_heading():
    page = _page(Slide(layout="big-fact", kicker="09 經費", title="經費需求", fact="NT$ 73,000"))
    assert _frame_with(page, "經費需求") is not None


def test_cover_subtitle_clears_a_two_line_title():
    title = "以輕量化深度學習辨識臺灣常見外來入侵植物之行動應用研究"
    slide = Slide(layout="title", title=title, subtitle="審查簡報")
    for style in STYLES:
        page = _page(slide, style=style)
        t, s = _frame_with(page, title), _frame_with(page, "審查簡報")
        # The title's box is sized for one line; the subtitle must start below
        # where two lines of title ink end, not at the box's nominal bottom.
        assert _cm(s, "y") > _cm(t, "y") + 2.0, style


def test_styles_draw_visibly_different_card_containers():
    signatures = {}
    for style in STYLES:
        page = _page(Slide(**CARDS), style=style)
        rects = page.findall(".//draw:rect", NS)
        heights = sorted(round(_cm(r, "height"), 2) for r in rects)
        signatures[style] = (len(rects), tuple(heights))
    # zen draws no container at all; ruled/rule-ink draw hairlines, not boxes.
    assert signatures["zen"][0] == 0
    assert all(h < 0.2 for h in signatures["report"][1])
    assert all(h < 0.2 for h in signatures["editorial"][1])
    assert signatures["classic"] != signatures["report"]


def test_budget_gate_charges_the_lede_and_band_frames():
    slide = Slide(**CARDS, subtitle="長" * 80, takeaway="結" * 60)
    messages = check_budget(slide, THEME)
    assert any("lede" in m for m in messages)


# -- LLM layer ----------------------------------------------------------------


def test_outline_keeps_a_known_style_and_drops_an_unknown_one():
    base = {"pages": [{"role": "title", "title": "t", "gist": "g"}]}
    assert Outline.model_validate({**base, "style": "report"}).style == "report"
    assert Outline.model_validate({**base, "style": "baroque"}).style is None


def test_prompts_carry_the_quality_contract():
    assert "takeaway" in llm.SLIDES_SYSTEM_PROMPT
    assert "kicker" in llm.SLIDES_SYSTEM_PROMPT
    assert "寧缺勿雜" not in llm.SLIDES_SYSTEM_PROMPT
    assert "無明確線索時預設 detailed" in llm.OUTLINE_SYSTEM_PROMPT
    for theme in THEMES.values():
        assert theme.accent in llm.OUTLINE_SYSTEM_PROMPT


def test_stage_two_deck_inherits_the_outline_style(monkeypatch):
    from tests.test_llm import _backend, _make_response

    outline = Outline.model_validate(
        {
            "style": "editorial",
            "pages": [
                {"role": "title", "title": "封面", "gist": "開場"},
                {"role": "closing", "title": "結語", "gist": "收尾"},
            ],
        }
    )
    deck = {
        "title": "t",
        "style": "zen",  # whatever stage 2 says, stage 1 owns the style
        "slides": [
            {"layout": "title", "title": "封面", "notes": "n"},
            {"layout": "closing", "title": "結語", "notes": "n"},
        ],
    }
    backend = _backend(monkeypatch, [_make_response(json.dumps(deck))])
    assert backend.generate_slides(outline).style == "editorial"


# -- round-2 fixes: budget false positives, truncation reporting --------------


def test_kicker_title_and_lede_on_one_line_each_raise_no_budget_warning():
    slide = Slide(
        **CARDS,
        kicker="01 研究動機",
        subtitle="訪談 3 縣市、42 位移除活動志工",
    )
    slide = slide.model_copy(update={"title": "志工分不清入侵種，專家人力成了移除活動的瓶頸"})
    assert check_budget(slide, THEME) == []


def test_a_two_line_heading_is_still_reported():
    slide = Slide(**CARDS, kicker="01 動機")
    slide = slide.model_copy(update={"title": "標" * 60})
    assert any("title" in m for m in check_budget(slide, THEME))


def test_timeline_details_cut_to_an_ellipsis_are_reported():
    slide = Slide(
        layout="timeline",
        title="時程",
        events=[{"label": f"{i} 月", "title": "階段", "detail": "說" * 48} for i in range(1, 6)],
        subtitle="說明",
        takeaway="結論",
    )
    assert any("截斷" in m for m in check_budget(slide, THEME))


def test_short_timeline_areas_hang_every_card_below_the_axis():
    slide = Slide(
        layout="timeline",
        title="時程",
        events=[{"label": f"{i} 月", "title": "階段", "detail": "說明"} for i in range(1, 6)],
        subtitle="說明",
        takeaway="結論",
    )
    page = _page(slide, style="classic")
    ys = {round(_cm(_frame_with(page, f"{i} 月"), "y"), 2) for i in range(1, 6)}
    assert len(ys) == 1


def test_heading_steps_down_rather_than_orphan_one_character():
    from odforge.textmetrics import heading_pt

    frame_w = 25
    fits = int((frame_w - 0.5) / (28 * 0.0352778))
    assert heading_pt("字" * (fits + 1), frame_w, 28) < 28
    assert heading_pt("字" * (fits * 2), frame_w, 28) == 28


def test_table_columns_are_wide_enough_for_their_headers():
    from odforge.render.odp import _table_geometry
    from odforge.textmetrics import text_width_cm

    slide = Slide(
        layout="table",
        title="t",
        table={
            "columns": ["評估軌道", "方法", "量測指標", "判定重點", "對象"],
            "rows": [["模型", "測試集分類與混淆矩陣逐對檢視", "Top-1", "細粒度", "30 位"]],
        },
    )
    area = next(f for f in LAYOUTS["table"] if f.role == "table-area")
    widths, header_pt, *_ = _table_geometry(slide.table, area, THEME)
    for label, w in zip(slide.table.columns, widths, strict=True):
        assert w >= text_width_cm(label, header_pt) + 1.1


def test_deep_chain_hierarchy_flows_left_to_right_in_a_short_area():
    from odforge.render.odp import _diagram_positions
    from odforge.themes import Frame

    slide = Slide(
        layout="diagram",
        title="t",
        diagram={
            "kind": "hierarchy",
            "nodes": [{"id": f"n{i}", "title": f"節點{i}"} for i in range(5)],
            "edges": [{"source": f"n{i}", "target": f"n{i + 1}"} for i in range(4)],
        },
    )
    area = Frame("diagram-area", 1.5, 4.6, 25, 7.4, 16)
    boxes = _diagram_positions(slide.diagram, area)
    assert all(h >= 1.8 for _x, _y, _w, h in boxes.values())
    xs = [boxes[f"n{i}"][0] for i in range(5)]
    assert xs == sorted(xs) and len(set(xs)) == 5


def test_chart_values_carry_thousands_separators():
    from odforge.render.odp import _fmt_number

    assert _fmt_number(48000) == "48,000"
    assert _fmt_number(3.5) == "3.5"


# -- round-3: pipeline guards -------------------------------------------------


def test_degrade_never_takes_a_dashboard_below_two_cards():
    from odforge.llm import _degrade_slide

    slide = Slide(
        layout="dashboard",
        title="成果",
        metrics=[{"value": "1", "label": "標" * 24}, {"value": "2", "label": "標" * 24}],
        bullets=[{"text": "卡" * 30, "children": ["說" * 40]}, {"text": "卡" * 30}],
    )
    _degrade_slide(slide, THEME)
    Slide.model_validate(slide.model_dump())  # still a valid dashboard
    assert len(slide.bullets) >= 2


def test_takeaways_are_capped_and_kept_on_evidence_pages():
    from odforge.llm import _cap_takeaways

    slides = [Slide(layout="title", title="封面")]
    for i in range(5):
        slides.append(Slide(**{**CARDS, "title": f"卡片{i}"}, takeaway="結論"))
    slides.append(
        Slide(
            layout="table",
            title="表",
            table={"columns": ["a", "b"], "rows": [["1", "2"]]},
            takeaway="表的結論",
        )
    )
    pres = _cap_takeaways(Presentation(title="t", slides=slides))
    carried = [s for s in pres.slides if s.takeaway]
    assert len(carried) == 3  # ceil(6 × 0.4)
    assert pres.slides[-1].takeaway == "表的結論"


def test_image_pages_without_media_become_cards():
    from odforge.llm import _polish_outline

    outline = Outline.model_validate(
        {
            "pages": [
                {"role": "title", "title": "t", "gist": "g"},
                {"role": "image-split", "title": "圖", "gist": "g"},
            ]
        }
    )
    assert [p.role for p in _polish_outline(outline).pages] == ["title", "cards"]


# -- round-4: passport style, CJK line box, table budget ------------------------


TWO_CARDS = {**CARDS, "bullets": CARDS["bullets"][:2]}


def test_passport_takeaway_becomes_a_right_hand_panel():
    from odforge.themes import PANEL_BODY_RIGHT, PANEL_X

    slide = Slide(**TWO_CARDS, subtitle="說明", takeaway="結論")
    frames = {f.role: f for f in _slide_frames(slide, "passport")}
    panel = frames["takeaway"]
    assert panel.x == PANEL_X and panel.h > 5
    assert frames["cards-area"].x + frames["cards-area"].w <= PANEL_BODY_RIGHT + 1e-6


def test_wide_layouts_keep_the_band_under_passport():
    slide = Slide(
        layout="timeline",
        title="時程",
        events=[{"label": f"{i} 月", "title": "階段"} for i in range(1, 6)],
        takeaway="結論",
    )
    band = next(f for f in _slide_frames(slide, "passport") if f.role == "takeaway")
    assert band.h < 2 and band.w > 20


def _slide_frames(slide, style_id):
    from odforge.themes import slide_frames

    return slide_frames(slide, get_style(style_id))


def test_passport_draws_framed_cards_and_a_dark_panel():
    page = _page(Slide(**TWO_CARDS, takeaway="這是結論"), style="passport")
    assert _frame_with(page, "這是結論") is not None
    assert _frame_with(page, "重點") is not None


def test_proportional_line_heights_are_written_against_the_cjk_line_box():
    from odforge.render.odp import _lo_line_height

    assert _lo_line_height("145%") == "100%"
    assert _lo_line_height("135%") == "100%"
    assert _lo_line_height("200%") == "138%"
    assert _lo_line_height("0.6cm") == "0.6cm"


def test_a_table_too_tall_for_its_area_is_reported():
    slide = Slide(
        layout="table",
        title="清單",
        table={
            "columns": ["項目", "說明"],
            "rows": [[f"第{i}項", "說" * 40] for i in range(8)],
        },
        takeaway="結論",
    )
    assert any("表格" in m for m in check_budget(slide, THEME))


def test_outline_stage_trims_structure_a_short_deck_has_not_earned():
    from odforge.llm import _trim_structure

    pages = [
        {"role": "title", "title": "t", "gist": "g"},
        {"role": "agenda", "title": "a", "gist": "g"},
        {"role": "section", "title": "s", "gist": "g"},
    ]
    pages += [{"role": "cards", "title": f"c{i}", "gist": "g"} for i in range(8)]
    trimmed = _trim_structure(Outline.model_validate({"pages": pages}))
    assert [p.role for p in trimmed.pages].count("agenda") == 0
    assert [p.role for p in trimmed.pages].count("section") == 0


# -- round-5: engine-side line layout -------------------------------------------


def _lines(text, w=4.4, pt=14):
    from odforge.render.odp import _layout_lines, _wrap_width

    return _layout_lines(text, pt, _wrap_width(w))


def test_numbers_keep_their_units_and_operators():
    lines = _lines("每台月費 1,200 元，由社區認養", w=3.5)
    assert any("1,200 元" in line for line in lines)
    assert any("≥ 90%" in line for line in _lines("Top-1 準確率 ≥ 90%，單張推論 < 300 ms"))


def test_latin_runs_are_not_split_inside_a_word():
    for line in _lines("以 MobileNetV3-Large 作為學生模型", w=5.0):
        assert not line.startswith("rge") and "Lar" not in line.replace("Large", "")


def test_no_line_starts_with_closing_punctuation():
    text = "降低對專家人力的依賴，讓移除活動得以擴大，並釋出資料集。"
    for w in (3.0, 3.6, 4.2, 5.0):
        assert all(line[0] not in "，。、）」" for line in _lines(text, w=w))


def test_last_line_is_never_a_lone_character():
    text = "拍照即判斷並給處理建議有網路時回報發現位置"
    for w in (3.2, 3.8, 4.4, 5.0):
        lines = _lines(text, w=w)
        assert len(lines) == 1 or len(lines[-1]) >= 3


def test_laid_out_lines_are_what_the_page_draws():
    page = _page(
        Slide(
            **{
                **CARDS,
                "bullets": [
                    {"text": "卡片", "children": ["拍照即判斷並給處理建議，有網路時回報發現位置"]},
                    {"text": "卡片二", "children": ["短"]},
                ],
            }
        ),
        style="classic",
    )
    frame = _frame_with(page, "拍照即判斷")
    breaks = frame.findall(".//text:line-break", NS)
    assert len(breaks) >= 1


def test_three_cards_keep_the_band_under_passport():
    # Three cards in two-thirds of the width set text 4–6 characters wide.
    slide = Slide(**CARDS, takeaway="結論")
    band = next(f for f in _slide_frames(slide, "passport") if f.role == "takeaway")
    assert band.w > 20


def test_consistent_author_chapter_numbers_are_kept():
    from odforge.render.odp import _renumber_chapters

    kickers = ["00 開場", "01 研究方法 · 資料", "01 研究方法 · 模型", "02 評估"]
    p = Presentation(
        title="t",
        slides=[Slide(**{**CARDS, "kicker": k}) for k in kickers],
    )
    assert [s.kicker for s in _renumber_chapters(p).slides] == kickers


def test_drifting_chapter_numbers_are_renumbered_by_first_appearance():
    from odforge.render.odp import _renumber_chapters

    kickers = ["01 動機", "02 方法", "03 評估", "01 預算"]
    p = Presentation(
        title="t",
        slides=[Slide(**{**CARDS, "kicker": k}) for k in kickers],
    )
    assert [s.kicker for s in _renumber_chapters(p).slides] == [
        "01 動機",
        "02 方法",
        "03 評估",
        "04 預算",
    ]


def test_cover_bullets_become_an_info_card_beside_the_title():
    slide = Slide(
        layout="title",
        title="輕量化深度學習辨識入侵植物",
        subtitle="審查簡報",
        kicker="國科會大專學生研究計畫",
        bullets=["資訊管理學系 林○○", "指導教授 陳○○", "115/7–116/2"],
    )
    page = _page(slide, style="passport")
    card_row = _frame_with(page, "指導教授")
    title = _frame_with(page, "輕量化深度學習")
    assert _cm(card_row, "x") > _cm(title, "x") + _cm(title, "width")
    assert _frame_with(page, "國科會大專學生研究計畫") is not None


def test_mixed_cjk_latin_runs_get_a_space():
    from odforge.render.odp import _space_text

    assert _space_text("雲端GPU運算") == "雲端 GPU 運算"
    assert _space_text("平板6台") == "平板 6 台"
    assert _space_text("已有 空格") == "已有 空格"


def test_cover_subtitle_follows_a_one_line_title_and_block_sits_high():
    slide = Slide(
        layout="title",
        title="輕量化深度學習辨識入侵植物",
        subtitle="以手機離線模型協助志工分辨外來種",
        kicker="大專學生研究計畫審查",
        bullets=["申請人 林○○", "指導教授 陳○○"],
    )
    page = _page(slide, style="passport")
    title = _frame_with(page, "輕量化深度學習")
    sub = _frame_with(page, "以手機離線模型")
    gap = _cm(sub, "y") - (_cm(title, "y") + _cm(title, "height"))
    assert 0 <= gap < 0.8
    # The report cover no longer hangs its title in the lower half.
    assert _cm(title, "y") < 7.0


def test_closing_page_draws_its_kicker():
    slide = Slide(
        layout="closing",
        title="讓志工手機成為第一位專家",
        kicker="結語 · 審查請求",
        bullets=["核定研究計畫", "核定經費"],
    )
    assert _frame_with(_page(slide, style="passport"), "結語 · 審查請求") is not None


def test_dates_and_grouped_numbers_are_never_split():
    from odforge.render.odp import _layout_lines, _tokens

    assert _tokens("115 年 3 月 18 日") == ["115 年 3 月 18 日"]
    for size in range(12, 17):
        for line in _layout_lines("核定 73,000 元", size, 3.5):
            assert not line.endswith(","), line


def test_grow_to_fill_never_adds_a_line():
    from odforge.render.odp import _fit_stacked_texts, _line_count

    fitted = _fit_stacked_texts(
        ["團體合作"] * 4,
        ["串聯 2 個保育團體"] * 4,
        title_pt=14,
        detail_pt=12,
        wrap_w=4.2,
        title_line_height=1.15,
        detail_line_height=1.3,
        title_top=1.2,
        detail_gap=0.2,
        pad_bottom=0.4,
        min_h=2.0,
        max_h=9.0,
        available=9.0,
    )
    assert _line_count("串聯 2 個保育團體", fitted.detail_pt, 4.2) == _line_count(
        "串聯 2 個保育團體", 12, 4.2
    )


def test_deck_warnings_flag_pacing_and_count_mismatch():
    from odforge.ir import TableSpec
    from odforge.textmetrics import deck_warnings

    filler = [
        Slide(layout="title-content", title=f"第 {i} 頁", bullets=["a", "b"]) for i in range(8)
    ]
    slides = [
        Slide(layout="title", title="封面"),
        Slide(layout="section", title="一"),
        Slide(layout="quote", title="q", quote="一句話"),
        Slide(layout="quote", title="q2", quote="又一句"),
        Slide(
            layout="table",
            title="七項指標六項達標",
            table=TableSpec(columns=["指標", "值"], rows=[["a", "1"]] * 6),
        ),
        *filler,
    ]
    text = "\n".join(deck_warnings(slides))
    assert "結構頁過多" in text
    assert "章節分隔頁" in text
    assert "引言頁" in text
    assert "七項" in text


def test_long_cjk_heading_breaks_between_words():
    from odforge.render.odp import _broken_xml

    xml = _broken_xml("114 年度一站通數位櫃台業務執行報告與明年度規劃", 34, 15.8)
    assert "<text:line-break/>" in xml
    first = xml.split("<text:line-break/>")[0]
    assert not first.endswith(("業", "執", "規"))


def test_cards_beside_a_panel_share_its_top_and_bottom():
    slide = Slide(
        layout="cards",
        title="三項發現都指向同一個瓶頸",
        takeaway="誤除原生種與漏掉入侵種都在消耗人力",
        bullets=[
            {"text": "無法確定", "children": ["志工自認分不清"]},
            {"text": "每場需專家", "children": ["專家須逐株確認"]},
        ],
    )
    page = _page(slide, style="passport")
    tall = [
        (_cm(r, "x"), _cm(r, "y"), _cm(r, "height"))
        for r in page.findall(".//draw:rect", NS)
        if _cm(r, "height") > 3
    ]
    panel = [t for t in tall if t[0] >= 19]
    cards = [t for t in tall if t[0] < 19]
    assert panel and cards
    for _x, y, h in cards:
        assert abs(y - panel[0][1]) < 0.01
        assert abs(y + h - (panel[0][1] + panel[0][2])) < 0.01


def test_explicit_page_limit_is_enforced_structural_pages_first():
    roles = ["title", "agenda", "problem", "cards", "section", "metrics",
             "table", "quote", "chart", "cards", "closing"]
    roles = [r if r != "problem" else "big-fact" for r in roles]
    outline = Outline.model_validate(
        {"pages": [{"role": r, "title": f"p{i}", "gist": "g"}
                                 for i, r in enumerate(roles)]}
    )
    assert llm._page_cap("幫我生 10 頁以內的 pitch deck") == 10
    assert llm._page_cap("做成 15 分鐘的簡報") is None
    capped = llm._cap_pages(outline, 8)
    kept = [p.role for p in capped.pages]
    assert len(kept) == 8
    assert kept[0] == "title" and kept[-1] == "closing"
    assert "agenda" not in kept and "section" not in kept and "quote" not in kept
