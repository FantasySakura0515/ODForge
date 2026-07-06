import re

from odforge.themes import LAYOUTS, THEMES, PAGE_W, PAGE_H, Frame, Theme


def test_all_five_layouts_exist():
    assert set(LAYOUTS) == {"title", "title-content", "two-col", "section", "big-fact"}


def test_frames_within_page_bounds():
    for name, frames in LAYOUTS.items():
        for f in frames:
            assert f.x >= 0 and f.y >= 0, (name, f.role)
            assert f.x + f.w <= PAGE_W, (name, f.role)
            assert f.y + f.h <= PAGE_H, (name, f.role)


def test_all_three_themes_exist():
    assert set(THEMES) == {"academic", "minimal", "dark"}


def test_theme_colors_are_valid_hex():
    hexre = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for t in THEMES.values():
        for c in (t.bg, t.title_color, t.text_color, t.accent):
            assert hexre.match(c), c


def test_frames_are_frozen():
    import pytest, dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        LAYOUTS["title"][0].x = 0


def test_layout_roles_match_expectation():
    assert [f.role for f in LAYOUTS["two-col"]] == ["title", "left", "right"]
    assert [f.role for f in LAYOUTS["big-fact"]] == ["fact", "bullets"]
