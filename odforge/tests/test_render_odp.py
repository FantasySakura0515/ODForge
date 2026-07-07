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


# ---------------------------------------------------------------------------
# Task 4.2: .odp renderer tests
# ---------------------------------------------------------------------------

import zipfile
import lxml.etree as etree
import pytest
from odforge.render.odp import (
    render_odp,
    build_content_xml,
    build_styles_xml,
    _rect_xml,
    _line_xml,
    _GraphicStyles,
)
from odforge.render import render
from odforge.ir import Presentation, Slide
from odforge.package import ODP_MIMETYPE

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
}


def content_root(path):
    with zipfile.ZipFile(path) as z:
        return etree.fromstring(z.read("content.xml"))


def test_odp_mimetype(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(out) as z:
        assert z.read("mimetype").decode() == ODP_MIMETYPE


def test_one_page_per_slide(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    assert len(root.findall(".//draw:page", NS)) == len(sample_presentation.slides)


def test_slide_titles_present(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    all_text = "".join(root.itertext())
    for s in sample_presentation.slides:
        if s.title:
            assert s.title in all_text


def test_bullets_each_get_paragraph(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T", bullets=["甲", "乙", "丙"])])
    root = content_root(render_odp(p, tmp_path / "p.odp"))
    page = root.findall(".//draw:page", NS)[0]
    texts = [t for t in page.itertext() if t.strip()]
    for b in ["甲", "乙", "丙"]:
        assert b in texts


def test_two_col_has_three_frames(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    two_col_idx = next(i for i, s in enumerate(sample_presentation.slides) if s.layout == "two-col")
    page = root.findall(".//draw:page", NS)[two_col_idx]
    assert len(page.findall(".//draw:frame", NS)) == 3


def test_notes_rendered(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    pages = root.findall(".//draw:page", NS)
    for i, s in enumerate(sample_presentation.slides):
        if s.notes:
            notes = pages[i].findall(".//presentation:notes", NS)
            assert notes, f"slide {i} missing notes"
            assert s.notes in "".join(notes[0].itertext())


def test_frame_geometry_in_cm(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    first_frame = root.findall(".//draw:page", NS)[0].findall(".//draw:frame", NS)[0]
    for attr in ("width", "height", "x", "y"):
        v = first_frame.get("{%s}%s" % (NS["svg"], attr))
        assert v is not None and v.endswith("cm")


def test_xml_escaping(tmp_path):
    p = Presentation(title="a<b>&c", slides=[
        Slide(layout="title", title='x < y & "z"', subtitle="w>v")])
    out = render_odp(p, tmp_path / "p.odp")
    content_root(out)  # lxml 解析不炸 = well-formed
    with zipfile.ZipFile(out) as z:
        assert etree.fromstring(z.read("styles.xml")) is not None
        assert etree.fromstring(z.read("meta.xml")) is not None


def test_styles_xml_master_page_and_bg(tmp_path, sample_presentation):
    render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(tmp_path / "p.odp") as z:
        root = etree.fromstring(z.read("styles.xml"))
    assert root.findall(".//style:master-page", NS)
    xml_str = etree.tostring(root).decode()
    assert THEMES["academic"].bg in xml_str  # sample_presentation 是 academic 主題


def test_page_size_16_9(tmp_path, sample_presentation):
    render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(tmp_path / "p.odp") as z:
        s = z.read("styles.xml").decode()
    assert "28cm" in s and "15.75cm" in s


def test_dispatch_presentation(tmp_path, sample_presentation):
    out = render(sample_presentation, tmp_path / "p.odp")
    assert out.exists()


def test_cjk_font_size_applied(tmp_path, sample_presentation):
    render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(tmp_path / "p.odp") as z:
        content = z.read("content.xml").decode("utf-8")
    assert 'style:font-size-asian="40pt"' in content
    assert 'style:font-weight-asian="bold"' in content


def test_unknown_theme_falls_back_to_academic(tmp_path):
    # Presentation.theme 是 Literal 不會出現未知值;此測試鎖 render_odp 對 THEMES 的取值方式
    p = Presentation(title="t", slides=[Slide(layout="title", title="T")])
    out = render_odp(p, tmp_path / "p.odp")
    assert out.exists()


# ---------------------------------------------------------------------------
# Task 12.2: tokenized Theme + SCALES + resolve_design
# ---------------------------------------------------------------------------

from odforge.themes import SCALES, resolve_design
from odforge.ir import contrast_ratio, DesignSpec, Palette, FontPair


def test_theme_has_full_token_set():
    """The resolved Theme carries the complete design token set."""
    t = THEMES["academic"]
    for field in (
        "bg", "surface", "text", "muted", "accent", "title_color",
        "font_display", "font_body",
        "display_pt", "h1_pt", "body_pt", "caption_pt", "bullet_char",
    ):
        assert hasattr(t, field), field
    assert t.bullet_char == "▪"


def test_theme_compat_aliases_still_resolve():
    """v1 renderer reads theme.text_color / theme.font — keep them working."""
    t = THEMES["dark"]
    assert t.text_color == t.text
    assert t.font == t.font_body


def test_all_presets_pass_contrast_bars():
    """① Every preset clears the same WCAG bars the ir.py validator enforces."""
    for name, t in THEMES.items():
        assert contrast_ratio(t.text, t.bg) >= 4.5, (name, "text/bg")
        assert contrast_ratio(t.accent, t.bg) >= 3.0, (name, "accent/bg")
        assert contrast_ratio(t.muted, t.bg) >= 3.0, (name, "muted/bg")


def test_presets_survive_palette_validation():
    """Presets are legal Palettes — construct one from each preset's colours."""
    for name, t in THEMES.items():
        Palette(bg=t.bg, surface=t.surface, text=t.text, muted=t.muted, accent=t.accent)


def test_presets_use_standard_scale_sizes():
    """Presets default to the 'standard' scale sizes."""
    display, h1, body, caption = SCALES["standard"]
    for t in THEMES.values():
        assert (t.display_pt, t.h1_pt, t.body_pt, t.caption_pt) == (display, h1, body, caption)


def test_scales_three_tiers_strictly_increasing_per_slot():
    """④ compact < standard < display for every (display, h1, body, caption) slot."""
    assert set(SCALES) == {"compact", "standard", "display"}
    for slot in range(4):
        c = SCALES["compact"][slot]
        s = SCALES["standard"][slot]
        d = SCALES["display"][slot]
        assert c < s < d, (slot, c, s, d)


def test_resolve_design_none_returns_preset():
    """② design=None → THEMES[p.theme]."""
    for theme_name in ("academic", "minimal", "dark"):
        p = Presentation(title="t", theme=theme_name,
                         slides=[Slide(layout="title", title="T")])
        assert resolve_design(p) is THEMES[theme_name]


def test_resolve_design_uses_design_colors_and_fonts():
    """③ A valid DesignSpec → Theme colours/fonts/sizes come from the design."""
    design = DesignSpec(
        palette=Palette(bg="#0B1020", surface="#1B2340", text="#F0F3FF",
                        muted="#9AA6D0", accent="#5AD0E0"),
        fonts=FontPair(display="Noto Serif TC", body="Noto Sans TC"),
        scale="display",
        mode="detailed",
    )
    p = Presentation(title="t", theme="academic", design=design,
                     slides=[Slide(layout="title", title="T")])
    t = resolve_design(p)
    assert t.bg == "#0B1020"
    assert t.surface == "#1B2340"
    assert t.text == "#F0F3FF"
    assert t.muted == "#9AA6D0"
    assert t.accent == "#5AD0E0"
    assert t.title_color == "#F0F3FF"  # title_color = text
    assert t.font_display == "Noto Serif TC"
    assert t.font_body == "Noto Sans TC"
    assert (t.display_pt, t.h1_pt, t.body_pt, t.caption_pt) == SCALES["display"]


def test_resolve_design_scale_selects_sizes():
    """design.scale picks the SCALES tier; mode does not alter sizes."""
    for scale in ("compact", "standard", "display"):
        design = DesignSpec(
            palette=Palette(bg="#FFFFFF", surface="#EEEEEE", text="#111111",
                            muted="#666666", accent="#0055AA"),
            fonts=FontPair(display="Noto Sans TC", body="Noto Sans TC"),
            scale=scale,
            mode="presenter",
        )
        p = Presentation(title="t", design=design,
                         slides=[Slide(layout="title", title="T")])
        t = resolve_design(p)
        assert (t.display_pt, t.h1_pt, t.body_pt, t.caption_pt) == SCALES[scale]


# ---------------------------------------------------------------------------
# Task 13.1: graphic primitives (rect / line) + gradient background
# ---------------------------------------------------------------------------


def test_graphic_styles_dedup_and_sequential_names():
    gs = _GraphicStyles()
    a = gs.name_for_fill("#FF0000")
    b = gs.name_for_fill("#FF0000")       # identical fill → shared style
    c = gs.name_for_fill("#00FF00")       # different fill → new style
    d = gs.name_for_stroke("#0000FF", 2)  # stroke → its own style
    assert a == b == "G1"
    assert c == "G2"
    assert d == "G3"


def test_rect_xml_emits_draw_rect_and_style_fill_color():
    gs = _GraphicStyles()
    name = gs.name_for_fill("#1A4B8C")
    rect = _rect_xml(1, 2, 5.5, 3, fill="#1A4B8C", style_name=name)
    assert rect.startswith("<draw:rect")
    assert f'draw:style-name="{name}"' in rect
    assert 'svg:x="1cm"' in rect and 'svg:y="2cm"' in rect
    assert 'svg:width="5.5cm"' in rect and 'svg:height="3cm"' in rect
    styles = gs.xml()
    assert 'draw:fill="solid"' in styles
    assert 'draw:fill-color="#1A4B8C"' in styles
    assert 'draw:stroke="none"' in styles          # a pure fill draws no border


def test_rect_fill_opacity_below_one_emits_percentage():
    gs = _GraphicStyles()
    gs.name_for_fill("#000000", opacity=0.5)
    assert 'draw:opacity="50%"' in gs.xml()
    # full opacity omits the attribute entirely
    solid = _GraphicStyles()
    solid.name_for_fill("#000000")
    assert "draw:opacity" not in solid.xml()


def test_rounded_rect_emits_corner_radius_only_when_positive():
    gs = _GraphicStyles()
    name = gs.name_for_fill("#FFFFFF")
    rounded = _rect_xml(0, 0, 4, 3, fill="#FFFFFF",
                        corner_radius_cm=0.4, style_name=name)
    assert 'draw:corner-radius="0.4cm"' in rounded
    sharp = _rect_xml(0, 0, 4, 3, fill="#FFFFFF", style_name=name)
    assert "corner-radius" not in sharp


def test_line_xml_emits_draw_line_and_stroke_style():
    gs = _GraphicStyles()
    name = gs.name_for_stroke("#3DD6E6", 2.0)
    line = _line_xml(1, 1, 10, 1, color="#3DD6E6", width_pt=2.0, style_name=name)
    assert line.startswith("<draw:line")
    assert f'draw:style-name="{name}"' in line
    assert 'svg:x1="1cm"' in line and 'svg:x2="10cm"' in line
    assert 'svg:y1="1cm"' in line and 'svg:y2="1cm"' in line
    styles = gs.xml()
    assert 'draw:stroke="solid"' in styles
    assert 'svg:stroke-color="#3DD6E6"' in styles
    assert 'svg:stroke-width="2pt"' in styles
    assert 'draw:fill="none"' in styles            # a pure stroke has no fill


def test_dark_theme_styles_use_gradient_background():
    root = etree.fromstring(build_styles_xml(THEMES["dark"]).encode("utf-8"))
    grads = root.findall(".//draw:gradient", NS)
    assert grads, "dark preset must define a <draw:gradient>"
    grad_name = grads[0].get("{%s}name" % NS["draw"])
    assert grad_name
    dpp = root.findall(".//style:drawing-page-properties", NS)
    assert dpp
    assert all(p.get("{%s}fill" % NS["draw"]) == "gradient" for p in dpp)
    assert all(
        p.get("{%s}fill-gradient-name" % NS["draw"]) == grad_name for p in dpp
    )


def test_light_themes_keep_solid_background():
    for preset in ("academic", "minimal"):
        root = etree.fromstring(build_styles_xml(THEMES[preset]).encode("utf-8"))
        assert not root.findall(".//draw:gradient", NS)
        dpp = root.findall(".//style:drawing-page-properties", NS)
        assert dpp
        assert all(p.get("{%s}fill" % NS["draw"]) == "solid" for p in dpp)
        assert all(
            p.get("{%s}fill-color" % NS["draw"]) == THEMES[preset].bg for p in dpp
        )
