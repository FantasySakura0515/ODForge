"""Tests for the page facts handed to the design critic.

This module supplies context, not verdicts. Its job is to state what the
renderer emitted — accurately, completely, and without editorialising — so the
critic can check its own observations instead of estimating them from pixels.
The tests therefore care about two things: that every fact is right, and that
nothing in the dump could be mistaken for a defect that is not there.
"""

from pathlib import Path

import pytest

from odforge.ir import BulletItem, Presentation, Slide
from odforge.pagefacts import grounding_text, page_measurements
from odforge.render import render

_LONG_TITLE = "策略清楚並且能讓每個部門對齊同一份年度目標與共同衡量指標"


def _deck(*slides) -> Presentation:
    return Presentation(title="測試", theme="academic", slides=list(slides))


@pytest.fixture
def cards_odp(tmp_path) -> Path:
    out = tmp_path / "cards.odp"
    render(
        _deck(
            Slide(
                layout="cards",
                title="三個重點",
                bullets=["跨平台相容", "長期可讀", "零授權成本"],
            )
        ),
        out,
    )
    return out


# ---------------------------------------------------------------------------
# the facts themselves
# ---------------------------------------------------------------------------


def test_shapes_and_runs_are_reported_with_geometry(cards_odp):
    page = page_measurements(cards_odp)[1]
    cards = [s for s in page.shapes if s.box.w >= 2.0 and s.box.h >= 1.0]
    assert len(cards) == 3
    assert len({round(c.box.h, 4) for c in cards}) == 1
    assert any("跨平台相容" in run.text for run in page.runs)


def test_paint_order_is_recorded(cards_odp):
    page = page_measurements(cards_odp)[1]
    card = next(s for s in page.shapes if s.box.w >= 2.0 and s.box.h >= 1.0)
    on_card = next(r for r in page.runs if "跨平台相容" in r.text)
    assert card.order < on_card.order, "a card is painted before the text on it"


def test_backdrop_is_the_shape_beneath_not_the_one_above(cards_odp):
    page = page_measurements(cards_odp)[1]
    run = next(r for r in page.runs if "跨平台相容" in r.text)
    assert page.backdrop(run) == "#EFEADD", "the card the text sits on"


def test_font_size_is_reported(cards_odp):
    page = page_measurements(cards_odp)[1]
    assert all(run.size_pt > 0 for run in page.runs if run.text.strip())


def test_connectors_are_reported(tmp_path):
    out = tmp_path / "hub.odp"
    render(
        _deck(
            Slide(
                layout="diagram",
                title="架構",
                diagram={
                    "kind": "hub",
                    "nodes": [
                        {"id": "core", "title": "核心", "emphasis": True},
                        {"id": "web", "title": "前端"},
                        {"id": "data", "title": "資料"},
                    ],
                    "edges": [
                        {"source": "core", "target": "web", "label": "提供"},
                        {"source": "core", "target": "data", "label": "讀寫"},
                    ],
                },
            )
        ),
        out,
    )
    page = page_measurements(out)[1]
    assert len([s for s in page.shapes if s.fill == "line"]) == 2


def test_speaker_notes_are_not_reported(tmp_path):
    out = tmp_path / "notes.odp"
    render(
        _deck(Slide(layout="section", title="第一章", notes="這段講稿不會出現在畫面上")),
        out,
    )
    page = page_measurements(out)[1]
    assert page.runs, "the slide's own text is still reported"
    assert all("講稿" not in run.text for run in page.runs)


# ---------------------------------------------------------------------------
# the dump the critic reads
# ---------------------------------------------------------------------------


def test_grounding_carries_the_numbers_a_critic_would_otherwise_guess(cards_odp):
    text = grounding_text(cards_odp)
    assert "paint=" in text
    assert "#EFEADD" in text, "the card fill"
    assert "其後方=" in text, "what is actually behind each run"
    assert "pt" in text, "the size each run is set at"
    assert "第 1 頁" in text


def test_grounding_never_truncates_slide_text(tmp_path):
    # A clipped string reads to the model as text the *renderer* cut off, and it
    # reports the truncation as a defect — an experiment produced exactly that
    # false 「文字被截斷」 error from a 20-character cap in the dump.
    out = tmp_path / "long.odp"
    render(
        _deck(
            Slide(
                layout="cards",
                title="標題",
                bullets=[BulletItem(text=_LONG_TITLE), BulletItem(text="流程順暢")],
            )
        ),
        out,
    )
    assert _LONG_TITLE in grounding_text(out)


def test_grounding_states_that_the_numbers_are_authoritative(cards_odp):
    # Without this the model treats the dump as a second opinion and keeps
    # trusting its own pixel estimate.
    assert "以資料為準" in grounding_text(cards_odp)


def test_grounding_offers_no_verdicts(cards_odp):
    # Facts only: the moment this file starts saying what is *wrong*, it is the
    # rule engine it replaced.
    text = grounding_text(cards_odp)
    for verdict in ("錯誤", "不足", "失衡", "違反", "應該", "建議"):
        assert verdict not in text, f"grounding must not judge: {verdict!r}"


# ---------------------------------------------------------------------------
# never break the QA loop
# ---------------------------------------------------------------------------


def test_missing_file_yields_no_facts(tmp_path):
    assert page_measurements(tmp_path / "nope.odp") == {}
    assert grounding_text(tmp_path / "nope.odp") == ""


def test_unreadable_package_yields_no_facts(tmp_path):
    broken = tmp_path / "broken.odp"
    broken.write_bytes(b"not a zip at all")
    assert page_measurements(broken) == {}
    assert grounding_text(broken) == ""




# ---------------------------------------------------------------------------
# Arithmetic the model cannot do is done for it.
#
# Handed #1D3555 and #FBF9F4 it reported 2.8:1 (the pair measures ~13:1) and
# cited WCAG against its own wrong answer, at error severity. Handed four
# x-coordinates it called a 0.01cm difference 「肉眼可見」 unevenness — while
# quoting the 0.05cm tolerance the preamble had just given it. Raw numbers it
# must reduce itself are worse than none: they lend authority to a bad sum.
# ---------------------------------------------------------------------------




def test_grounding_reduces_height_and_gap_differences_for_the_model(cards_odp):
    text = grounding_text(cards_odp)
    assert "高度差" in text
    assert "完全相同" in text, "identical geometry must be stated as identical"


def test_uneven_containers_are_reported_as_uneven(tmp_path):
    # The summary must not flatter the page: a real difference has to show up.
    out = tmp_path / "timeline.odp"
    from odforge.ir import TimelineEvent

    render(
        _deck(
            Slide(
                layout="timeline",
                title="路徑",
                events=[
                    TimelineEvent(label="一", title="甲", detail="x"),
                    TimelineEvent(label="二", title="乙", detail="y"),
                ],
            )
        ),
        out,
    )
    page = page_measurements(out)[1]
    boxes = [s.box for s in page.shapes if s.fill != "line" and s.box.w >= 2.0]
    spread = max(b.h for b in boxes) - min(b.h for b in boxes)
    assert f"高度差 {spread:.2f}cm" in grounding_text(out)


def test_grounding_supplies_no_contrast_numbers(cards_odp):
    """Colour ratios are out of scope — supplying them tripled the error rate.

    Computed for the model, 11.8:1 came back as 「低於 WCAG AA 標準建議的 12:1」,
    a standard it invented, and a 96pt watermark faint by design was reported as
    an error. It cannot judge a contrast number, so it is not given one.
    """
    text = grounding_text(cards_odp)
    assert "對比=" not in text
    assert ":1" not in text
