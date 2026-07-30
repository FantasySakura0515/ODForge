"""Tests for :mod:`odforge.textmetrics` — the layout-budget overflow gate.

These are pure-function tests (no rendering, no I/O): they exercise the height
estimator and the per-slide budget check that decides whether a slide's text
fits its frames. The four scenarios mirror the Task 14.2 brief.
"""

from __future__ import annotations


import pytest

from odforge.ir import BulletItem, Slide
from odforge.textmetrics import PT_TO_CM, check_budget, estimate_height_cm
from odforge.themes import THEMES

THEME = THEMES["academic"]


# ── ① a single short line is ~ one line tall ───────────────────────────────
def test_single_short_line_height_one_line():
    # A short string that comfortably fits one line: height must be within 10%
    # of one line = size_pt * PT_TO_CM * 1.45.
    size_pt = 18
    expected = size_pt * PT_TO_CM * 1.45
    for text in ("Hi", "短句", "ODF 好"):
        h = estimate_height_cm(text, size_pt, width_cm=25)
        assert h == pytest.approx(expected, rel=0.10), (text, h)


def test_single_short_line_exact_one_line():
    # A string that fits one line yields exactly one line's height.
    h = estimate_height_cm("測試", 20, width_cm=24)
    assert h == pytest.approx(20 * PT_TO_CM * 1.45, rel=1e-9)


# ── ② a slide stuffed with 40 bullets overflows ────────────────────────────
def test_forty_bullets_overflows_with_title_and_role():
    slide = Slide(
        layout="title-content",
        title="測試標題",
        bullets=[f"這是第{i}條項目的內容說明文字" for i in range(40)],
    )
    msgs = check_budget(slide, THEME)
    assert msgs, "40 bullets must overflow the content frame"
    # At least one message names both the slide title and the frame role.
    assert any("測試標題" in m and "bullets" in m for m in msgs), msgs


# ── ③ a normal 5-bullet slide fits ─────────────────────────────────────────
def test_normal_five_bullets_ok():
    slide = Slide(
        layout="title-content",
        title="正常頁",
        bullets=["第一點", "第二點", "第三點", "第四點", "第五點"],
    )
    assert check_budget(slide, THEME) == []


# ── ④ CJK wraps into more lines than same-count ASCII ──────────────────────
def test_cjk_estimates_more_lines_than_ascii():
    # Same character count, same size/width: CJK (1.0 em) must wrap into a
    # taller block than ASCII (0.55 em).
    n = 100
    width = 10.0
    ascii_h = estimate_height_cm("a" * n, 18, width_cm=width)
    cjk_h = estimate_height_cm("字" * n, 18, width_cm=width)
    assert cjk_h > ascii_h


# ── extra coverage ─────────────────────────────────────────────────────────
def test_newlines_split_into_paragraphs():
    # Two short lines separated by a newline are twice one line's height.
    one = estimate_height_cm("甲", 18, width_cm=25)
    two = estimate_height_cm("甲\n乙", 18, width_cm=25)
    assert two == pytest.approx(2 * one, rel=1e-9)


def test_min_one_line_for_empty_text():
    assert estimate_height_cm("", 18, width_cm=25) == pytest.approx(
        18 * PT_TO_CM * 1.45, rel=1e-9
    )


def test_check_budget_returns_empty_for_small_deck():
    slide = Slide(layout="title", title="ODForge", subtitle="自然語言轉 ODF")
    assert check_budget(slide, THEME) == []


def test_chart_area_skipped_but_insights_estimated():
    # chart-area holds auto-fitting bars → never a text-overflow source; the
    # insights bullet list, however, is estimated and can overflow.
    from odforge.ir import ChartSpec

    chart = ChartSpec(labels=["甲", "乙"], values=[1.0, 2.0], highlight=1)
    slide = Slide(
        layout="chart",
        title="圖表頁",
        chart=chart,
        bullets=[f"洞見第{i}條，一段較長的說明文字補充" for i in range(30)],
    )
    msgs = check_budget(slide, THEME)
    # Overflow must be attributed to insights, never to chart-area.
    assert any("insights" in m for m in msgs), msgs
    assert not any("chart-area" in m for m in msgs), msgs


def test_agenda_items_ignore_nested_children():
    # The renderer's agenda path (_numbered_items_xml) draws only each item's
    # top-level text via _line_text — BulletItem children are silently dropped.
    # The estimator must not charge height the renderer never draws: an agenda
    # slide with children estimates exactly like the same slide without them.
    from odforge.textmetrics import _frame_content_height
    from odforge.themes import LAYOUTS

    items_frame = LAYOUTS["agenda"][1]
    assert items_frame.role == "items"
    flat = Slide(layout="agenda", title="議程", bullets=["一", "二"])
    nested = Slide(
        layout="agenda",
        title="議程",
        bullets=[BulletItem(text="一", children=["子項一", "子項二"]), "二"],
    )
    flat_h = _frame_content_height(flat, THEME, items_frame, "agenda")
    nested_h = _frame_content_height(nested, THEME, items_frame, "agenda")
    assert nested_h == pytest.approx(flat_h)


def test_nested_children_add_height():
    flat = Slide(layout="title-content", title="t", bullets=["一", "二"])
    nested = Slide(
        layout="title-content",
        title="t",
        bullets=[BulletItem(text="一", children=["子項一", "子項二"]), "二"],
    )
    from odforge.textmetrics import _frame_content_height
    from odforge.themes import LAYOUTS

    bullets_frame = LAYOUTS["title-content"][1]
    flat_h = _frame_content_height(flat, THEME, bullets_frame, "title-content")
    nested_h = _frame_content_height(nested, THEME, bullets_frame, "title-content")
    assert nested_h > flat_h


# ── big-fact budget blind spot ─────────────────────────────────────────────
def test_big_fact_short_fact_fits_no_warning():
    # A normal big-fact (short fact + short caption) must not warn: the engine
    # auto-shrinks/repositions, so budget stays quiet.
    slide = Slide(layout="big-fact", fact="99%", bullets=["涵蓋率支撐說明"])
    assert check_budget(slide, THEME) == []


def test_big_fact_extreme_fact_reports_with_title():
    # Even at the h1_pt shrink floor an extreme fact plus its caption cannot fit
    # above the footer line → check_budget must fire, naming the slide.
    slide = Slide(
        layout="big-fact",
        title="關鍵數據頁",
        fact="字" * 200,  # unrescuable: overflows even shrunk to h1_pt
        bullets=["補充說明"],
    )
    msgs = check_budget(slide, THEME)
    assert msgs, "an unfittable big-fact must overflow the budget"
    assert any("關鍵數據頁" in m and "fact" in m for m in msgs), msgs
